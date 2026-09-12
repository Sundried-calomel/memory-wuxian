"""Memory Plane ownership of immutable V2 completion links and source coverage."""
from __future__ import annotations

import hashlib
import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any

from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json, canonical_json_bytes

FORMAT = 'memory-wuxian-summary-v2-completion-v1'


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_digest(record: dict) -> str:
    return digest({k: v for k, v in record.items() if k not in {'_path', 'content_sha256'}})


class SummaryV2Links:
    def __init__(self, store):
        self.store = store
        self.directory = store.root / 'summary-v2' / 'completions'

    def bindings(self) -> dict[str, Path]:
        from summary_v2_runtime import runtime_root
        roots = {'runtime': runtime_root(self.store)}
        for key, value in self.store.config.get('summary_v2', {}).get('bundle_roots', {}).items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', str(key)):
                raise ValueError('Unsafe Summary V2 root binding ID')
            if key == 'runtime':
                raise ValueError('The reserved runtime binding cannot be overridden')
            roots[str(key)] = Path(value).expanduser().resolve()
        restored_path = self.store.root / 'summary-v2/restored-bindings.json'
        if restored_path.exists():
            restored = json.loads(restored_path.read_text('utf-8'))
            roots.update({key: Path(value).resolve() for key, value in restored['roots'].items()})
        return roots

    def resolve_bundle(self, bundle: dict) -> Path:
        root = self.bindings().get(bundle['root_binding_id'])
        relative = Path(bundle['relative_path'])
        if root is None or relative.is_absolute() or relative.drive or '..' in relative.parts or ':' in relative.as_posix():
            raise ValueError('Unknown or unsafe Summary V2 bundle binding')
        path = root / relative
        if not path.resolve().is_relative_to(root) or any(
            p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in [path, *path.parents]
        ):
            raise ValueError('Summary V2 bundle path escapes its binding')
        return path

    def bundle_identity(self, path: Path, sidecar: dict) -> dict:
        path = path.resolve()
        matches = [(key, root) for key, root in self.bindings().items() if path.is_relative_to(root)]
        if len(matches) != 1:
            raise ValueError('Summary V2 bundle needs exactly one configured root binding')
        key, root = matches[0]
        return {'root_binding_id': key, 'relative_path': path.relative_to(root).as_posix(),
                'summary_v2_id': sidecar['summary_v2_id'], 'projection_sha256': sidecar['projection_sha256'],
                'summary_json_sha256': file_digest(path / 'summary.json'),
                'summary_markdown_sha256': file_digest(path / 'summary.md')}

    def read_object(self, bundle: dict) -> dict:
        path = self.resolve_bundle(bundle)
        for name, field in [('summary.json', 'summary_json_sha256'), ('summary.md', 'summary_markdown_sha256')]:
            if file_digest(path / name) != bundle[field]:
                raise ValueError('Summary V2 bundle hash changed: ' + str(path / name))
        sidecar = json.loads((path / 'summary.json').read_text('utf-8'))
        if sidecar['summary_v2_id'] != bundle['summary_v2_id'] or sidecar['projection_sha256'] != bundle['projection_sha256']:
            raise ValueError('Summary V2 object identity disagrees with its bundle')
        return sidecar

    def read_bundle(self, completion: dict) -> dict:
        sidecar = self.read_object(completion['bundle'])
        if (sidecar['parallel_summary_id'] != completion['target_summary_id'] or
            sidecar['conversation_id'] != completion['conversation_id'] or
            sidecar['summary_level'] != completion['summary_level']):
            raise ValueError('Summary V2 completion disagrees with its bundle')
        return sidecar

    def read_completions(self, *, verify_bundles=True) -> list[dict]:
        paths = sorted(self.directory.glob('*.json'))
        fingerprint = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        cached = getattr(self.store, '_v2_completion_metadata_cache', None)
        records = cached[1] if cached is not None and cached[0] == fingerprint else []
        for path in ([] if cached is not None and cached[0] == fingerprint else paths):
            record = json.loads(path.read_text('utf-8'))
            if record.get('format') != FORMAT or record.get('completion_sha256') != digest({
                k: v for k, v in record.items() if k != 'completion_sha256'
            }):
                raise ValueError('Summary V2 completion identity changed: ' + str(path))
            if path.stem != record['target_summary_id']:
                raise ValueError('Summary V2 completion filename disagrees with its identity')
            records.append(record)
        by_id = {r['target_summary_id']: r for r in records}
        for record in records:
            if record['source'].get('source_identity_repair') is not None:
                from memory_identity import verify_job_binding
                job_path = self.store.pending_dir / (record['job_id'] + '.json')
                if not job_path.exists():
                    job_path = self.store.archive_dir / (record['job_id'] + '-ingested.json')
                job = json.loads(job_path.read_text('utf-8'))
                if digest(job) != record['job_sha256'] or job.get('source_identity_repair') != record['source']['source_identity_repair']:
                    raise ValueError('Completed identity repair lost its frozen job binding')
                verify_job_binding(self.store, job)
            for child in record['source']['children']:
                current = by_id.get(child['target_summary_id'])
                if current is None or current['completion_sha256'] != child['completion_sha256']:
                    raise ValueError('Summary V2 direct child completion changed or is missing')
                if current['conversation_id'] != record['conversation_id'] or current['summary_level'] + 1 != record['summary_level']:
                    raise ValueError('Summary V2 direct child has wrong conversation or level')
        self.store._v2_completion_metadata_cache = (fingerprint, records)
        if verify_bundles:
            for record in records:
                self.read_bundle(record)
        return copy.deepcopy(records)

    def descriptor(self, completion: dict) -> dict:
        self.read_bundle(completion)
        return {'path': str(self.resolve_bundle(completion['bundle'])),
                'json_sha256': completion['bundle']['summary_json_sha256'],
                'markdown_sha256': completion['bundle']['summary_markdown_sha256']}

    def effective_summary_records(self, legacy: list[dict]) -> list[dict]:
        """Read-only status projection; never feed this view to archive-v1 export."""
        rows = {r['summary_id']: r for r in legacy}
        for record in self.read_completions(verify_bundles=False):
            rows[record['target_summary_id']] = {
                'summary_id': record['target_summary_id'], 'level': record['summary_level'],
                'conversation_id': record['conversation_id'], 'summary_format': 2,
            }
        return list(rows.values())

    def status_fields(self, legacy: list[dict], grouped: list[dict]) -> dict:
        from summary_v2_runtime import enabled
        records = self.read_completions(verify_bundles=False)
        if not records and not enabled(self.store.config):
            return {}
        aliases = {r['target_summary_id'] for r in records}
        pairs = {(r['parent_summary_id'], r['child_summary_id']) for r in grouped if r['parent_summary_id'] not in aliases}
        pairs.update((r['target_summary_id'], c['target_summary_id']) for r in records for c in r['source']['children'])
        return {'summary_generation_format': 2 if enabled(self.store.config) else 1,
                'summary_v1_counts': {str(level): sum(r['level'] == level for r in legacy) for level in range(1, self.store.maximum_depth + 1)},
                'summary_v2_counts': {str(level): sum(r['summary_level'] == level for r in records) for level in range(1, self.store.maximum_depth + 1)},
                'grouped_child_summaries': len(pairs), 'summary_v2_exchange': 'local-only'}

    def extend_snapshot(self, snapshot: dict) -> dict:
        snapshot['summary_v2_by_alias'] = {r['target_summary_id']: r for r in self.read_completions(verify_bundles=False)}
        return snapshot

    def verify_source(self, job: dict, snapshot: dict) -> list[dict]:
        if job.get('source_identity_repair') is not None:
            from memory_identity import verify_job_binding
            verify_job_binding(self.store, job, snapshot['raw_records'])
        if int(job['summary_level']) == 1:
            ids = job['source_message_ids']
            by_id = snapshot['raw_by_id']
            if not ids or len(ids) != len(set(ids)) or any(i not in by_id for i in ids):
                raise ValueError('Summary V2 source range is incomplete or duplicated')
            records = [by_id[i] for i in ids]
            if any(r['conversation_id'] != job['conversation_id'] for r in records):
                raise ValueError('Summary V2 source conversation changed')
            current = self.store.current_job_source_sha256(job, snapshot)
            if current != job['source_sha256']:
                raise ValueError('Summary V2 raw source hash changed')
            if [r['message_id'] for r in sorted(records, key=lambda r: int(r['sequence']))] != ids:
                raise ValueError('Summary V2 source order changed')
            for key, value in [('source_start', ids[0]), ('source_end', ids[-1]),
                               ('source_start_sequence', records[0]['sequence']),
                               ('source_end_sequence', records[-1]['sequence'])]:
                if job.get(key) != value:
                    raise ValueError('Summary V2 source boundary changed: ' + key)
            rounds = self.store.completed_rounds_by_conversation(list(by_id.values())).get(job['conversation_id'], [])
            selected = [r for group in rounds if job['source_round_start'] <= int(group[0]['round_number']) <= job['source_round_end'] for r in group]
            if [r['message_id'] for r in selected] != ids:
                raise ValueError('Summary V2 job does not cover exact closed rounds')
            return []
        records = snapshot.get('summary_v2_by_alias')
        if records is None:
            records = self.extend_snapshot(snapshot)['summary_v2_by_alias']
        selected = [records[key] for key in job['source_summaries']]
        children = [{'target_summary_id': c['target_summary_id'], 'completion_sha256': c['completion_sha256']} for c in selected]
        if job.get('summary_format') == 2:
            if digest(children) != job['source_sha256'] or children != job['summary_v2_children']:
                raise ValueError('Summary V2 parent completion binding changed')
        elif self.store.current_job_source_sha256(job, snapshot) != job['source_sha256']:
            raise ValueError('Legacy frozen parent source changed before V2 execution')
        if any(c['conversation_id'] != job['conversation_id'] or c['summary_level'] + 1 != job['summary_level'] for c in selected):
            raise ValueError('Summary V2 parent children disagree with the job')
        return [self.descriptor(c) for c in selected]

    def source_record(self, job: dict, by_alias: dict) -> dict:
        children = []
        for alias in job.get('source_summaries', []):
            child = by_alias[alias]
            children.append({'target_summary_id': alias, 'completion_sha256': child['completion_sha256'],
                             'summary_v2_id': child['bundle']['summary_v2_id'],
                             'projection_sha256': child['bundle']['projection_sha256']})
        return {**{k: job.get(k) for k in ('source_sha256', 'source_message_ids', 'source_round_start',
                'source_round_end', 'source_start', 'source_end', 'source_start_sequence',
                'source_end_sequence', 'start_time', 'end_time')}, 'children': children,
                **({'source_identity_repair': job['source_identity_repair']} if job.get('source_identity_repair') else {})}

    def complete_job(self, job_path: Path, result: dict, *, source_snapshot: dict,
                     defer_derived_updates: bool, dispatched_job: dict) -> dict:
        from summary_v2_runtime import artifact_identity
        job = json.loads(job_path.read_text('utf-8'))
        if digest(job) != digest(dispatched_job) or result['request_job_sha256'] != digest(dispatched_job):
            raise ValueError('Pending V2 job changed after dispatch')
        with exclusive_lock(self.store.locks_dir / 'archive.lock'):
            with exclusive_lock(self.store.locks_dir / 'summary-ingest.lock'):
                self.verify_source(job, source_snapshot)
                sidecar = result['sidecar']
                if (sidecar['source']['source_sha256'] != result['source_sha256'] or
                    sidecar['source']['job_id'] != result['core_source_job_id'] or
                    (job['summary_level'] == 1 and result['source_sha256'] != job['source_sha256'])):
                    raise ValueError('Completed V2 formal source disagrees with its request')
                if (sidecar['parallel_summary_id'] != job['target_summary_id'] or
                    sidecar['conversation_id'] != job['conversation_id'] or
                    sidecar['summary_level'] != job['summary_level']):
                    raise ValueError('Completed V2 result belongs to a different job')
                raw_manifest = sidecar['source']['raw_message_manifest']
                for item in raw_manifest:
                    raw = source_snapshot['raw_by_id'].get(item['message_id'])
                    if raw is None or raw_digest(raw) != item['content_sha256'] or raw['sequence'] != item['sequence']:
                        raise ValueError('Completed V2 raw provenance changed')
                bundle = self.bundle_identity(Path(result['bundle']), sidecar)
                if bundle['summary_json_sha256'] != result['json_sha256'] or bundle['summary_markdown_sha256'] != result['markdown_sha256']:
                    raise ValueError('Completed V2 bundle changed after engine exit')
                completion = {'format': FORMAT, 'job_id': job['job_id'], 'job_sha256': digest(job),
                              'source_signature': job['source_signature'],
                              'target_summary_id': job['target_summary_id'], 'conversation_id': job['conversation_id'],
                              'summary_level': job['summary_level'], 'core_source_job_id': result['core_source_job_id'],
                              'source': self.source_record(job, source_snapshot.get('summary_v2_by_alias', {})),
                              'bundle': bundle, 'engine_artifact_sha256': artifact_identity(), 'adopted': False}
                completion['objects'] = []
                for item in result['objects']:
                    identity = self.bundle_identity(Path(item['path']), item)
                    if (identity['summary_json_sha256'] != item['json_sha256'] or
                        identity['summary_markdown_sha256'] != item['markdown_sha256']):
                        raise ValueError('Node-owned V2 bundle changed after engine exit')
                    completion['objects'].append(identity)
                completion['completion_sha256'] = digest(completion)
                output = self.directory / (job['target_summary_id'] + '.json')
                if output.exists():
                    if json.loads(output.read_text('utf-8')) != completion:
                        raise ValueError('A different Summary V2 completion already claims this job')
                else:
                    atomic_write_canonical_json(output, completion)
                self.finish_commit(completion, job_path)
                if not defer_derived_updates:
                    self.store.refresh_unsummarized_registry()
        return {'status': 'ingested', 'summary_format': 2, 'job_id': job['job_id'],
                'summary': str(output), 'bundle': str(result['bundle']),
                'derived_updates_deferred': defer_derived_updates}

    def bind_job_route(self, job: dict) -> None:
        path = self.store.root / 'summary-v2/routes' / (job['job_id'] + '.json')
        binding = {'summary_format': 2, 'job_sha256': digest(job), 'target_summary_id': job['target_summary_id']}
        if path.exists():
            if json.loads(path.read_text('utf-8')) != binding:
                raise ValueError('Persistent V2 job route changed')
        else:
            atomic_write_canonical_json(path, binding)

    def finish_commit(self, completion: dict, job_path: Path) -> None:
        """Repair the derived commit tail; the immutable completion is authority."""
        archived = self.store.archive_dir / (completion['job_id'] + '-ingested.json')
        source = job_path if job_path.exists() else archived
        if digest(json.loads(source.read_text('utf-8'))) != completion['job_sha256']:
            raise ValueError('Committed Summary V2 job bytes changed')
        finalized = self.store.root / 'summary-v2/commit-tail' / (completion['target_summary_id'] + '.json')
        if finalized.exists():
            if json.loads(finalized.read_text('utf-8')) != {'completion_sha256': completion['completion_sha256']}:
                raise ValueError('Summary V2 finalized receipt changed')
            return
        debt_path = self.store.backup_debt_path
        debt = json.loads(debt_path.read_text('utf-8')) if debt_path.exists() else {'mutation_count': 0}
        commits = list(debt.get('summary_v2_commits', []))
        if completion['completion_sha256'] not in commits:
            debt['mutation_count'] = int(debt.get('mutation_count', 0)) + 1
            debt['summary_v2_commits'] = [*commits, completion['completion_sha256']]
            atomic_write_canonical_json(debt_path, debt)
        self.store.save_state(self.merge_recovered_state(self.store.load_state()))
        if not archived.exists():
            job_path.rename(archived)
        elif job_path.exists():
            raise ValueError('Both pending and archived paths claim the same V2 job')
        atomic_write_canonical_json(finalized, {'completion_sha256': completion['completion_sha256']})

    def recover_committed_job(self, job_path: Path, snapshot: dict | None = None):
        archived = self.store.archive_dir / (job_path.stem + '-ingested.json')
        source = job_path if job_path.exists() else archived
        if not source.exists():
            return None
        job = json.loads(source.read_text('utf-8'))
        matches = [r for r in self.read_completions(verify_bundles=False) if not r['adopted'] and r['job_id'] == job['job_id']]
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError('Multiple V2 completions claim the same job')
        completion = matches[0]
        self.read_bundle(completion)
        if snapshot is not None:
            self.verify_source(job, snapshot)
        with exclusive_lock(self.store.locks_dir / 'archive.lock'):
            with exclusive_lock(self.store.locks_dir / 'summary-ingest.lock'):
                self.finish_commit(completion, job_path)
        return {'status': 'ingested', 'summary_format': 2, 'job_id': job['job_id'],
                'summary': str(self.directory / (completion['target_summary_id'] + '.json')),
                'bundle': str(self.resolve_bundle(completion['bundle'])),
                'derived_updates_deferred': True, 'recovered_commit': True, 'ai_invocations': 0}

    def reconcile_queue(self, queue) -> list[dict]:
        records = {r['job_id']: r for r in self.read_completions(verify_bundles=False) if not r['adopted']}
        recovered = []
        for queued in queue.jobs():
            if queued['kind'] != 'semantic-summary-eligibility':
                continue
            record = records.get(queued['payload'].get('summary_job_id'))
            if record is None:
                continue
            derived = self.store.root / 'summary-v2/derived-finalized' / (record['target_summary_id'] + '.json')
            if queued['state'] == 'completed' and derived.exists():
                if json.loads(derived.read_text('utf-8')) != {'completion_sha256': record['completion_sha256']}:
                    raise ValueError('V2 derived finalization receipt changed')
                continue
            path = self.store.pending_dir / (record['job_id'] + '.json')
            result = self.recover_committed_job(path)
            if queued['state'] == 'completed' or queue.complete_semantic_commit(queued['job_id'], record, result) is not None:
                recovered.append(result)
        return recovered

    def merge_recovered_state(self, state: dict) -> dict:
        for record in self.read_completions(verify_bundles=False):
            match = re.fullmatch(r'L(\d+)-(\d+)', record['target_summary_id'])
            if match:
                level, number = match.groups()
                state['next_summary_ids'][level] = max(int(state['next_summary_ids'].get(level, 1)), int(number) + 1)
            if record['summary_level'] == 1:
                end = int(record['source']['source_round_end'])
                state['last_summarized_round'] = max(int(state.get('last_summarized_round', 0)), end)
                rounds = state.setdefault('last_summarized_rounds', {})
                conversation = record['conversation_id']
                rounds[conversation] = max(int(rounds.get(conversation, 0)), end)
        return state

    def due_parent_groups(self, existing: list[dict]):
        records = self.read_completions(verify_bundles=False)
        unavailable = {c['target_summary_id'] for r in records for c in r['source']['children']}
        unavailable.update(c for job in existing for c in job.get('source_summaries', []))
        for level in range(1, self.store.maximum_depth):
            for conversation in sorted({r['conversation_id'] for r in records if r['summary_level'] == level}):
                candidates = [r for r in records if r['summary_level'] == level and r['conversation_id'] == conversation
                              and r['target_summary_id'] not in unavailable]
                candidates.sort(key=lambda r: (int(r['source']['source_start_sequence'] or 0), r['target_summary_id']))
                if len(candidates) < self.store.higher_trigger:
                    continue
                yield level, conversation, candidates[:self.store.higher_trigger]

    def build_due_parent_job(self, state: dict, existing: list[dict]) -> Path | None:
        for level, conversation, selected in self.due_parent_groups(existing):
            children = [{'target_summary_id': c['target_summary_id'], 'completion_sha256': c['completion_sha256']} for c in selected]
            aliases = [c['target_summary_id'] for c in selected]
            job = {'format_version': 1, 'summary_format': 2, 'job_id': f"job-{int(state['next_job_id']):06d}",
                   'summary_level': level + 1, 'conversation_id': conversation,
                   'source_signature': f'conversation:{conversation}:v2-children:' + ','.join(aliases),
                   'source_summaries': aliases, 'summary_v2_children': children, 'source_sha256': digest(children),
                   'source_start': selected[0]['source']['source_start'], 'source_end': selected[-1]['source']['source_end'],
                   'source_start_sequence': selected[0]['source']['source_start_sequence'],
                   'source_end_sequence': selected[-1]['source']['source_end_sequence'],
                   'start_time': min(c['source']['start_time'] for c in selected),
                   'end_time': max(c['source']['end_time'] for c in selected)}
            return self.store.persist_job(state, job)
        return None

    def finalize_batch(self, results: list[dict]) -> dict:
        wanted = {Path(r['summary']).stem for r in results if r.get('summary_format') == 2}
        records = [r for r in self.read_completions(verify_bundles=False) if r['target_summary_id'] in wanted]
        for record in records:
            self.read_bundle(record)
        if len(records) != len(wanted):
            raise ValueError('Deferred V2 completion missing from its immutable catalog')
        self.store.refresh_unsummarized_registry()
        for record in records:
            path = self.store.root / 'summary-v2/derived-finalized' / (record['target_summary_id'] + '.json')
            value = {'completion_sha256': record['completion_sha256']}
            if path.exists() and json.loads(path.read_text('utf-8')) != value:
                raise ValueError('V2 derived finalization receipt changed')
            if not path.exists():
                atomic_write_canonical_json(path, value)
        return {'status': 'completed', 'summaries': len(records), 'summary_ids': sorted(wanted), 'summary_format': 2}

    def collect_closure(self, records: list[dict]) -> dict[str, tuple[dict, dict]]:
        inventory = {}
        for record in records:
            for bundle in [record['bundle'], *record.get('objects', [])]:
                key = bundle['summary_v2_id']
                if key in inventory and inventory[key] != bundle:
                    raise ValueError('Conflicting Summary V2 object bindings')
                inventory[key] = bundle
        visited = {}
        active = set()
        def visit(key):
            if key in active:
                raise ValueError('Cyclic Summary V2 dependency graph')
            if key in visited:
                return
            if key not in inventory:
                raise ValueError('Missing Summary V2 dependency object: ' + key)
            active.add(key)
            bundle = inventory[key]
            sidecar = self.read_object(bundle)
            dependencies = [*sidecar['source']['source_manifest'].get('children', []),
                            *sidecar.get('internal_routes', [])]
            for child in dependencies:
                visit(child['summary_v2_id'])
                actual = visited[child['summary_v2_id']][1]
                if (actual['projection_sha256'] != child['projection_sha256'] or
                    actual['summary_level'] != child['summary_level'] or
                    actual['conversation_id'] != sidecar['conversation_id']):
                    raise ValueError('Summary V2 dependency projection changed')
            active.remove(key)
            visited[key] = (bundle, sidecar)
        for record in records:
            visit(record['bundle']['summary_v2_id'])
        return visited

    @staticmethod
    def semantic_lines(sidecar: dict, *, current=False) -> list[str]:
        lines = []
        if not current:
            lines.extend('- Overview: ' + item['text'] for item in sidecar['overview'])
            lines.extend('- Scene: ' + item['title'] + ' — ' + item['summary'] for item in sidecar['scenes'])
        for item in sidecar['atoms']:
            lines.append(f"- [{item['epistemic_status']}] {item['statement']} (scope: {item['scope']}; item: {item['item_id']})")
        for relation in sidecar['relations']:
            lines.append(f"- Relation {relation['relation_type']}: {relation['from_item_id']} → {relation['to_item_id']}")
        lines.extend('- Anchor: ' + item['text'] for item in sidecar['retrieval_anchors'])
        lines.extend('- Internal route: ' + item['summary_v2_id'] for item in sidecar.get('internal_routes', []))
        return lines

    def retrieve(self, query: str, mode: str, *, raw_records=None):
        if mode not in {'historical', 'current-policy'}:
            raise ValueError('Retrieval mode must be historical or current-policy')
        if not self.store.normalize_search_text(query):
            raise ValueError('Query must not be empty')
        records = self.read_completions(verify_bundles=False)
        closure = self.collect_closure(records)
        documents = [{'summary_v2_id': key, 'sidecar': sidecar, 'bundle': bundle,
                      'text': '\n'.join(self.semantic_lines(sidecar))} for key, (bundle, sidecar) in closure.items()]
        matches = self.store.ranked_search(documents, self.store.normalize_search_text(query),
                                           self.store.search_terms(query), lambda r: r['text'])
        if not matches:
            return None
        selected = matches[:10]
        raw_records = raw_records if raw_records is not None else self.store.read_all_raw()
        if any(r['source'].get('source_identity_repair') for r in records):
            from memory_identity import verified_resolution
            if verified_resolution(self.store, raw_records) is None:
                raise ValueError('Completed identity repair lost its source resolution')
        by_raw = {r['message_id']: r for r in raw_records}
        raw_context = {}
        lines = ['# Memory無限 Retrieval', '', f'- Query: {query}', f'- Mode: `{mode}`',
                 '- Summary format: `2`', '- Source verification checks original bytes; semantic claims retain their recorded status.', '']
        for match in selected:
            record = match['record']
            sidecar = record['sidecar']
            for item in sidecar['source']['raw_message_manifest']:
                raw = by_raw.get(item['message_id'])
                if raw is None or raw_digest(raw) != item['content_sha256'] or raw['sequence'] != item['sequence']:
                    raise ValueError('Summary V2 retrieval raw provenance changed')
            lines.extend([f"## {sidecar['parallel_summary_id']} / {sidecar['summary_v2_id']}", ''])
            lines.extend(self.semantic_lines(sidecar, current=mode == 'current-policy'))
            lines.append('- Bundle: ' + str(self.resolve_bundle(record['bundle'])))
            for item in sidecar['source']['raw_message_manifest'][:8]:
                raw_context[item['message_id']] = by_raw[item['message_id']]
        if mode == 'current-policy':
            lines.extend(['', 'Explicit statuses and correction/conflict relations are preserved. Recency alone does not establish a current rule.'])
        lines.extend(['', '## Verified Raw Context', ''])
        for raw in list(raw_context.values())[:20]:
            lines.extend([f"### {raw['message_id']} ({raw['speaker']})", '- Raw file: ' + raw['_path'],
                          self.store.deterministic_excerpt(raw.get('text', ''), 800), ''])
        metadata = {'query': query, 'mode': mode, 'verification': 'verified', 'summary_format': 2,
                    'summaries': [m['record']['sidecar']['parallel_summary_id'] for m in selected],
                    'raw_files': list(dict.fromkeys(r['_path'] for r in raw_context.values())),
                    'raw_matches': [{'message_id': key} for key in raw_context], 'policy_events': [],
                    'query_log': 'skipped-read-only'}
        return '\n'.join(lines).rstrip() + '\n', metadata

    def capsule_section(self, conversation: str) -> tuple[list[str], list[str], set[str]]:
        records = [r for r in self.read_completions(verify_bundles=False) if r['conversation_id'] == conversation]
        covered = {c['target_summary_id'] for r in records for c in r['source']['children']}
        selected = sorted((r for r in records if r['target_summary_id'] not in covered),
                          key=lambda r: (r['source']['source_start_sequence'], -r['summary_level']))
        lines = []
        for record in selected:
            sidecar = self.read_bundle(record)
            lines.extend([f"## {record['target_summary_id']} (Summary V2, Level {record['summary_level']})", ''])
            lines.extend(['- Raw route: ' + ', '.join(sidecar['source']['raw_message_ids'][:8]),
                          '- Bundle: ' + str(self.resolve_bundle(record['bundle'])), ''])
            lines.extend(self.semantic_lines(sidecar, current=True))
        return lines, [r['target_summary_id'] for r in selected], {r['target_summary_id'] for r in records}

    @staticmethod
    def budgeted_capsule(history: str, recent: str, token_budget: int) -> str:
        """Conservative UTF-8 token bound, reserving space for recent task state."""
        def clip(text, limit):
            encoded = text.encode('utf-8')
            notice = b'\n[Truncated; follow bundle/raw routes for full context.]\n'
            if len(encoded) <= limit:
                return text
            if limit < len(notice):
                return notice[:limit].decode('ascii')
            return encoded[:limit - len(notice)].decode('utf-8', errors='ignore') + notice.decode()
        tail = clip(recent, token_budget // 3) if recent else ''
        return clip(history, token_budget - len(tail.encode('utf-8'))) + tail

    def prepare_adoption(self, bundles: list[Path], internal_bundles: list[Path], snapshot: dict) -> list[dict]:
        """Validate existing bundles and raw provenance; produce links without writing history."""
        from summary_v2_runtime import call_engine, artifact_identity
        paths = list(dict.fromkeys(Path(p).resolve() for p in [*bundles, *internal_bundles]))
        descriptors = [{'path': str(p), 'json_sha256': file_digest(p / 'summary.json'),
                        'markdown_sha256': file_digest(p / 'summary.md')} for p in paths]
        inspected = call_engine(self.store, {'children': descriptors})['sidecars']
        inventory = {s['summary_v2_id']: (p, s) for p, s in zip(paths, inspected)}
        if len(inventory) != len(paths):
            raise ValueError('Multiple adoption bundles claim the same V2 ID')
        root_paths = {Path(p).resolve() for p in bundles}
        roots = sorted(((p, s) for p, s in zip(paths, inspected) if p in root_paths),
                       key=lambda item: (item[1]['summary_level'], item[1]['parallel_summary_id']))
        prepared = []
        by_alias = {}
        for path, sidecar in roots:
            raw = []
            for item in sidecar['source']['raw_message_manifest']:
                record = snapshot['raw_by_id'].get(item['message_id'])
                if (record is None or raw_digest(record) != item['content_sha256'] or
                    record['sequence'] != item['sequence'] or record['conversation_id'] != sidecar['conversation_id']):
                    raise ValueError('Adoption raw provenance changed: ' + item['message_id'])
                raw.append(record)
            raw.sort(key=lambda r: int(r['sequence']))
            children = [inventory[c['summary_v2_id']][1]['parallel_summary_id']
                        for c in sidecar['source']['source_manifest'].get('children', [])]
            job = {'source_sha256': sidecar['source']['source_sha256'],
                   'source_message_ids': [r['message_id'] for r in raw] if sidecar['summary_level'] == 1 else [],
                   'source_round_start': min(int(r['round_number']) for r in raw),
                   'source_round_end': max(int(r['round_number']) for r in raw),
                   'source_start': raw[0]['message_id'], 'source_end': raw[-1]['message_id'],
                   'source_start_sequence': raw[0]['sequence'], 'source_end_sequence': raw[-1]['sequence'],
                   'start_time': min(r['timestamp'] for r in raw), 'end_time': max(r['timestamp'] for r in raw),
                   'source_summaries': children}
            owned = []
            pending = [sidecar]
            seen = set()
            while pending:
                current = pending.pop()
                if current['summary_v2_id'] in seen:
                    continue
                seen.add(current['summary_v2_id'])
                current_path = inventory[current['summary_v2_id']][0]
                owned.append(self.bundle_identity(current_path, current))
                for route in current.get('internal_routes', []):
                    pending.append(inventory[route['summary_v2_id']][1])
            completion = {'format': FORMAT, 'adopted': True, 'job_id': None, 'job_sha256': None,
                          'source_signature': 'adopted-v2:' + sidecar['projection_sha256'],
                          'target_summary_id': sidecar['parallel_summary_id'],
                          'conversation_id': sidecar['conversation_id'], 'summary_level': sidecar['summary_level'],
                          'core_source_job_id': sidecar['source']['job_id'],
                          'source': self.source_record(job, by_alias), 'bundle': self.bundle_identity(path, sidecar),
                          'objects': owned, 'engine_artifact_sha256': artifact_identity()}
            completion['completion_sha256'] = digest(completion)
            if completion['target_summary_id'] in by_alias:
                raise ValueError('Multiple adoption roots claim the same alias')
            by_alias[completion['target_summary_id']] = completion
            prepared.append(completion)
        self.collect_closure(prepared)
        self.validate_pending_adoption(prepared)
        return prepared

    def validate_pending_adoption(self, prepared: list[dict]) -> None:
        aliases = {r['target_summary_id'] for r in prepared}
        covered = {}
        for record in prepared:
            if record['summary_level'] == 1:
                covered.setdefault(record['conversation_id'], set()).update(record['source']['source_message_ids'])
        for job in self.store.pending_jobs():
            if job.get('target_summary_id') in aliases:
                raise ValueError('Adoption collides with a pending summary alias')
            if job['summary_level'] == 1 and covered.get(job['conversation_id'], set()).intersection(job['source_message_ids']):
                raise ValueError('Adoption overlaps a pending L1 source range')

    def apply_adoption(self, prepared: list[dict]) -> dict:
        """Publish only reviewed immutable links. Repeating the same plan is a no-op."""
        self.collect_closure(prepared)
        with exclusive_lock(self.store.locks_dir / 'archive.lock'):
            with exclusive_lock(self.store.locks_dir / 'summary-ingest.lock'):
                self.validate_pending_adoption(prepared)
                for record in prepared:
                    if not re.fullmatch(r'L[1-9][0-9]*-[0-9]{6,}', record['target_summary_id']):
                        raise ValueError('Unsafe adopted Summary V2 alias')
                    if not record.get('adopted') or record['completion_sha256'] != digest({
                        k: v for k, v in record.items() if k != 'completion_sha256'
                    }):
                        raise ValueError('Adoption plan identity changed')
                    path = self.directory / (record['target_summary_id'] + '.json')
                    if path.exists() and json.loads(path.read_text('utf-8')) != record:
                        raise ValueError('Adoption conflicts with an existing completion')
                created = 0
                for record in prepared:
                    path = self.directory / (record['target_summary_id'] + '.json')
                    if not path.exists():
                        atomic_write_canonical_json(path, record)
                        created += 1
                adoption_id = digest([r['completion_sha256'] for r in prepared])
                receipt = self.store.root / 'summary-v2/adoptions' / (adoption_id + '.json')
                receipt_value = {'adoption_sha256': adoption_id, 'roots': len(prepared)}
                if receipt.exists() and json.loads(receipt.read_text('utf-8')) != receipt_value:
                    raise ValueError('V2 adoption finalization receipt changed')
                if not receipt.exists():
                    before = self.store.load_state()
                    after = self.merge_recovered_state(copy.deepcopy(before))
                    if before != after:
                        self.store.save_state(after)
                    debt = self.store.backup_debt_path
                    current = json.loads(debt.read_text('utf-8')) if debt.exists() else {'mutation_count': 0}
                    adoptions = list(current.get('summary_v2_adoptions', []))
                    if adoption_id not in adoptions:
                        current['mutation_count'] = int(current.get('mutation_count', 0)) + len(prepared)
                        current['summary_v2_adoptions'] = [*adoptions, adoption_id]
                        atomic_write_canonical_json(debt, current)
                    atomic_write_canonical_json(receipt, receipt_value)
        return {'status': 'adopted' if created else 'no-change', 'created': created, 'roots': len(prepared)}

    def stage_backup(self, snapshot_root: Path) -> dict:
        """Copy the immutable closure referenced by the snapshot's own completion links."""
        from summary_v2_runtime import runtime_root, call_engine
        directory = snapshot_root / 'summary-v2/completions'
        records = [json.loads(p.read_text('utf-8')) for p in sorted(directory.glob('*.json'))]
        route_dir = snapshot_root / 'summary-v2/routes'
        pending = {p.stem for p in (snapshot_root / 'pending').glob('job-*.json')}
        if not records and not pending.intersection(p.stem for p in route_dir.glob('*.json')):
            return {'status': 'no-change'}
        for record in records:
            if record['completion_sha256'] != digest({k: v for k, v in record.items() if k != 'completion_sha256'}):
                raise ValueError('Snapshot V2 completion identity changed')
        closure = self.collect_closure(records)
        inventories = {key: bundle for key, (bundle, _) in closure.items()}
        checkpoints = []
        engine_root = runtime_root(self.store)
        for route_path in sorted(route_dir.glob('*.json')):
            if route_path.stem not in pending:
                continue
            route = json.loads(route_path.read_text('utf-8'))
            alias = route['target_summary_id']
            if not re.fullmatch(r'L[1-9][0-9]*-[0-9]{6,}', alias):
                raise ValueError('Unsafe checkpoint alias')
            for state_path in sorted((engine_root / 'backfill/rescue/node-state').glob('*/' + alias + '.json')):
                content = state_path.read_bytes()
                state = json.loads(content)
                relative = state_path.relative_to(engine_root).as_posix()
                destination = snapshot_root / 'summary-v2/backup-checkpoints/runtime' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                # These are opaque recovery evidence. Changed-root restore pauses unresolved jobs.
                from platform_atomic import atomic_replace_bytes
                atomic_replace_bytes(destination, content)
                checkpoints.append({'binding_id': 'runtime', 'relative_path': relative,
                                    'sha256': hashlib.sha256(content).hexdigest(),
                                    'target_summary_id': alias, 'node_state': state['attempt_status']})
                groups = [('map/', state['maps']), ('reduction/', state['reductions'])]
                if state.get('completed'):
                    groups.append(('', {'final': state['completed']}))
                for prefix, group in groups:
                    for stage, saved in group.items():
                        path = Path(saved['bundle'])
                        sidecar = json.loads((path / 'summary.json').read_text('utf-8'))
                        bundle = self.bundle_identity(path, sidecar)
                        descriptor = {'path': str(path), 'json_sha256': bundle['summary_json_sha256'],
                                      'markdown_sha256': bundle['summary_markdown_sha256']}
                        sidecar = call_engine(self.store, {'children': [descriptor]})['sidecars'][0]
                        receipt = saved.get('receipt', saved.get('stage_receipt'))
                        if (not receipt or receipt['output_projection_sha256'] != sidecar['projection_sha256'] or
                            receipt['source_sha256'] != sidecar['source']['source_sha256']):
                            raise ValueError('V2 checkpoint bundle disagrees with its saved success receipt')
                        binding = {k: v for k, v in receipt.items() if k != 'output_projection_sha256'}
                        if prefix and state['dispatches'].get(prefix + stage) != binding:
                            raise ValueError('V2 checkpoint success receipt disagrees with its dispatch claim')
                        if not prefix and receipt != state.get('completed_receipt'):
                            raise ValueError('V2 checkpoint final receipt changed')
                        if not prefix and binding not in [state['dispatches'].get(key) for key in ('final/direct', 'final/reduce')]:
                            raise ValueError('V2 checkpoint final receipt disagrees with its dispatch claim')
                        inventories[sidecar['summary_v2_id']] = bundle
        objects = []
        for key, bundle in sorted(inventories.items()):
            if not re.fullmatch(r'summary-v2-[0-9a-f]{32}', key):
                raise ValueError('Unsafe checkpoint bundle ID')
            source = self.resolve_bundle(bundle)
            destination = snapshot_root / 'summary-v2/backup-objects' / key
            if not destination.resolve().is_relative_to(snapshot_root.resolve()):
                raise ValueError('V2 backup object destination escapes the snapshot')
            destination.mkdir(parents=True, exist_ok=True)
            for name, field in [('summary.json', 'summary_json_sha256'), ('summary.md', 'summary_markdown_sha256')]:
                shutil.copyfile(source / name, destination / name)
                if file_digest(destination / name) != bundle[field]:
                    raise ValueError('Summary V2 object changed during backup')
            objects.append(bundle)
        manifest = {'format': 'memory-wuxian-summary-v2-backup-v1', 'objects': objects,
                    'root_completions': [r['completion_sha256'] for r in records],
                    'checkpoints': checkpoints, 'unresolved_jobs_require_review_after_restore': True}
        manifest['manifest_sha256'] = digest(manifest)
        atomic_write_canonical_json(snapshot_root / 'summary-v2/backup-bundles.json', manifest)
        return {'status': 'staged', 'objects': len(objects), 'checkpoints': len(checkpoints)}
