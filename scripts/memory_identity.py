"""Explicit historical identity resolutions over unchanged physical raw records.

Sequence is an old allocation number, not an occurrence identity. This module
never rewrites raw records or changes the physical view used by V1 and exports.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json

from platform_transaction import canonical_json_bytes
import hashlib

FORMAT = 'memory-wuxian-source-identity-resolution-v1'
POLICY = 'existing-last-sequence-for-proven-source-replay'


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def raw_digest(record):
    return digest({k: v for k, v in record.items() if k not in {'_path', 'content_sha256'}})


def logical_digest(record):
    return digest({k: v for k, v in record.items()
                   if k not in {'_path', 'content_sha256', 'sequence', 'round_number'}})


def resolution_path(store):
    return store.root / 'source-identity' / 'resolution.json'


def duplicate_groups(records):
    by_id, by_sequence = defaultdict(list), defaultdict(list)
    for record in records:
        by_id[str(record['message_id'])].append(record)
        by_sequence[int(record['sequence'])].append(record)
    return [(kind, key, members) for kind, index in [('message_id', by_id), ('sequence', by_sequence)]
            for key, members in sorted(index.items()) if len(members) > 1]


def build_resolution(store, records):
    """Model-free plan. Physical file ordinals distinguish identical copies."""
    groups = duplicate_groups(records)
    wanted = {str(r['message_id']) for _, _, members in groups for r in members}
    paths = sorted({r['_path'] for _, _, members in groups for r in members})
    locations = defaultdict(list)
    for relative in paths:
        path = store.root / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(store.raw_dir.resolve()):
            raise ValueError('Identity source path escapes raw history')
        for ordinal, record in enumerate(store.read_raw_file(path)):
            if str(record['message_id']) not in wanted:
                continue
            sha = raw_digest(record)
            if record.get('content_sha256') not in {None, sha}:
                raise ValueError('Raw content changed during identity inspection')
            key = (relative, sha)
            locations[key].append({'raw_file': relative, 'record_index': ordinal,
                'record_sha256': sha, 'message_id': record['message_id'],
                'sequence': record['sequence'], 'round_number': record.get('round_number')})
    planned = []
    for kind, key, members in groups:
        counts = Counter((r['_path'], raw_digest(r)) for r in members)
        occurrences = []
        for location, count in sorted(counts.items()):
            if len(locations[location]) != count:
                raise ValueError('Raw occurrence multiplicity changed during identity inspection')
            occurrences.extend(locations[location])
        if kind == 'message_id':
            if len({raw_digest(r) for r in members}) == 1:
                decision = 'exact-payload-replay'
            else:
                sources = [r.get('source') for r in members]
                required = {'kind', 'session_id', 'path', 'line', 'phase'}
                if (any(not isinstance(s, dict) or not required <= set(s) for s in sources)
                    or sources[0].get('kind') != 'codex-rollout-jsonl'
                    or not sources[0].get('session_id') or not sources[0].get('path')
                    or not isinstance(sources[0].get('line'), int) or sources[0]['line'] < 1
                    or len({logical_digest(r) for r in members}) != 1):
                    raise ValueError('Conflicting message identity cannot be resolved: ' + str(key))
                decision = POLICY
        else:
            decision = 'retain-distinct-physical-occurrences-with-original-sequence'
        group = {'kind': kind, 'key': key, 'decision': decision,
                 'occurrences': sorted(occurrences, key=lambda r: (r['raw_file'], r['record_index']))}
        group['group_sha256'] = digest(group)
        planned.append(group)
    result = {'format': FORMAT, 'policy': POLICY, 'groups': planned}
    result['resolution_sha256'] = digest(result)
    return result


def verified_resolution(store, records):
    path = resolution_path(store)
    if not path.exists():
        return None
    recorded = json.loads(path.read_text('utf-8'))
    activation_path = path.with_name('activation.json')
    if not activation_path.exists():
        raise ValueError('Historical identity resolution lacks explicit activation')
    activation = json.loads(activation_path.read_text('utf-8'))
    if (activation.get('format') != 'memory-wuxian-source-identity-activation-v1'
        or activation.get('resolution_sha256') != recorded.get('resolution_sha256')
        or activation.get('resolution_file_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise ValueError('Historical identity activation does not bind the current resolution')
    plan_sha = activation.get('approved_plan_sha256', '')
    if not isinstance(plan_sha, str) or len(plan_sha) != 64 or any(c not in '0123456789abcdef' for c in plan_sha):
        raise ValueError('Invalid activated repair plan identity')
    plan = json.loads((store.root / 'source-identity/repairs' / plan_sha / 'plan.json').read_text('utf-8'))
    if (plan.get('plan_sha256') != plan_sha or digest({k: v for k, v in plan.items() if k != 'plan_sha256'}) != plan_sha
        or not isinstance(plan.get('replacement'), dict) or not plan['replacement'].get('source_identity_repair')):
        raise ValueError('Historical identity resolution lost its approved plan')
    verify_job_binding(store, plan['replacement'])
    current = build_resolution(store, records)
    if recorded != current:
        raise ValueError('Historical identity resolution changed or an unreviewed collision appeared')
    return recorded


def projected_records(store, records):
    """A V2 input view only; do not use for V1, indexes, state, backup or export."""
    groups = duplicate_groups(records)
    if groups and verified_resolution(store, records) is None:
        raise ValueError('Duplicate source identities require an explicit verified resolution')
    by_id = {str(r['message_id']): r for r in sorted(records, key=lambda r: int(r['sequence']))}
    return sorted(by_id.values(), key=lambda r: int(r['sequence']))


class IdentityRepairIncomplete(ValueError):
    """A validated repair has not yet published its final commit marker."""


def repair_commit_record(plan):
    root = Path(plan['archive_root'])
    return {'status': 'applied', 'plan_sha256': plan['plan_sha256'],
        'replacement_job': str(root / 'pending' / (plan['replacement']['job_id'] + '.json')),
        'retained_original': str(root / 'pending/superseded' / plan['plan_sha256'] / Path(plan['original_job_path']).name),
        'physical_raw_unchanged': True, 'model_calls': 0}


def verify_job_binding(store, job, records=None):
    """Repair authority travels with the frozen job, including through retries."""
    binding = job.get('source_identity_repair')
    if binding is None:
        return
    if not isinstance(binding, dict) or set(binding) != {'resolution_sha256', 'original_job_sha256'}:
        raise ValueError('Invalid source identity repair binding')
    path = resolution_path(store)
    activation = json.loads(path.with_name('activation.json').read_text('utf-8'))
    resolution = json.loads(path.read_text('utf-8'))
    if (activation.get('format') != 'memory-wuxian-source-identity-activation-v1'
        or activation.get('resolution_file_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()
        or activation.get('resolution_sha256') != binding['resolution_sha256']
        or resolution.get('resolution_sha256') != binding['resolution_sha256']):
        raise ValueError('Frozen job identity resolution changed')
    plan_sha = activation.get('approved_plan_sha256', '')
    if not isinstance(plan_sha, str) or len(plan_sha) != 64 or any(c not in '0123456789abcdef' for c in plan_sha):
        raise ValueError('Invalid activated repair plan identity')
    plan = json.loads((store.root / 'source-identity/repairs' / plan_sha / 'plan.json').read_text('utf-8'))
    if (plan.get('plan_sha256') != plan_sha or digest({k: v for k, v in plan.items() if k != 'plan_sha256'}) != plan_sha
        or plan.get('original_job_sha256') != binding['original_job_sha256']
        or plan.get('replacement_job_sha256') != digest(job) or plan.get('replacement') != job):
        raise ValueError('Frozen job is not the activated identity replacement')
    from platform_atomic import native_filesystem_path
    committed = native_filesystem_path(store.root / 'source-identity/repairs' / plan_sha / 'committed.json')
    if not committed.exists():
        raise IdentityRepairIncomplete('Identity repair is not durably committed')
    if json.loads(committed.read_text('utf-8')) != repair_commit_record(plan):
        raise ValueError('Identity repair commit evidence changed')
    original = native_filesystem_path(store.pending_dir / 'superseded' / plan_sha / Path(plan['original_job_path']).name)
    if hashlib.sha256(original.read_bytes()).hexdigest() != plan['original_job_sha256']:
        raise ValueError('Identity repair lost its retained original job')
    from memory_jobs import MaintenanceQueue
    queue = MaintenanceQueue(store.root)
    for owner in plan['quarantined_owners']:
        queue.verify_superseded(owner['job_id'], plan)
    if records is not None:
        verified_resolution(store, records)


def plan_job_replacement(store, job_path: Path, records, resolution):
    """Build one additive replacement from existing source objects, never raw edits."""
    original_bytes = job_path.read_bytes()
    original = json.loads(original_bytes)
    if original['summary_level'] != 1 or len(original['source_message_ids']) == len(set(original['source_message_ids'])):
        raise ValueError('Identity replacement requires a duplicate-ID Level-1 job')
    embedded = original['source_records']
    if original['source_message_ids'] != [r['message_id'] for r in embedded]:
        raise ValueError('Original frozen message IDs disagree with embedded source records')
    if any(r['conversation_id'] != original['conversation_id'] for r in embedded):
        raise ValueError('Original frozen conversation disagrees with embedded records')
    source = Counter(raw_digest(r) for r in embedded)
    wanted = set(original['source_message_ids'])
    actual = [r for r in records if r['message_id'] in wanted]
    if source != Counter(raw_digest(r) for r in actual):
        raise ValueError('Frozen source occurrences no longer exactly match raw history')
    source_manifest = [{'sequence': int(r['sequence']), 'message_id': r['message_id'], 'content_sha256': raw_digest(r)}
                       for r in sorted(embedded, key=lambda r: int(r['sequence']))]
    if digest(source_manifest) != original['source_sha256']:
        raise ValueError('Original frozen source hash changed')
    if resolution != build_resolution(store, records):
        raise ValueError('Replacement requires the exact current resolution plan')
    by_id = {r['message_id']: r for r in sorted(actual, key=lambda r: int(r['sequence']))}
    selected = sorted(by_id.values(), key=lambda r: int(r['sequence']))
    if len({r['sequence'] for r in selected}) != len(selected):
        raise ValueError('Replacement still has a sequence collision; scientific core requires unique sequences')
    rounds = store.completed_rounds_by_conversation(selected).get(original['conversation_id'], [])
    flattened = sorted((r for group in rounds for r in group), key=lambda r: int(r['sequence']))
    if flattened != selected or any(r['conversation_id'] != original['conversation_id'] for r in selected):
        raise ValueError('Replacement does not consist of exact closed conversation rounds')
    state = store.load_state()
    # Existing pending IDs also reserve numbers, even if state is stale.
    largest = max([int(state['next_job_id']) - 1] + [int(p.stem.split('-')[1]) for p in store.pending_dir.glob('job-*.json')])
    state['next_job_id'] = largest + 1
    signature = 'identity-repair:' + hashlib.sha256(original_bytes).hexdigest()
    replacement = store.build_level_1_job(state, selected, int(rounds[0][0]['round_number']),
        int(rounds[-1][0]['round_number']), signature, original['conversation_id'])
    replacement['created_at'] = original['created_at']  # Deterministic plan/replay; receipt records repair time separately.
    replacement['target_summary_id'] = original['target_summary_id']
    replacement['summary_format'] = 2
    replacement['source_identity_repair'] = {
        'resolution_sha256': resolution['resolution_sha256'],
        'original_job_sha256': hashlib.sha256(original_bytes).hexdigest()}
    return {'original_job_sha256': hashlib.sha256(original_bytes).hexdigest(),
            'original_source_sha256': original['source_sha256'],
            'resolution_sha256': resolution['resolution_sha256'],
            'replacement': replacement, 'replacement_job_sha256': digest(replacement),
            'physical_source_records': len(embedded), 'selected_source_records': len(selected),
            'selection_policy': POLICY, 'round_number_claim': 'existing allocation selected; no unique historical renumbering claimed'}
