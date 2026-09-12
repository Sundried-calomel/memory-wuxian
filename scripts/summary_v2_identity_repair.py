"""Preview and apply one hash-bound historical source identity repair."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from memory_cli import MemoryStore, load_simple_yaml
from memory_identity import build_resolution, digest, plan_job_replacement, resolution_path, verified_resolution, verify_job_binding, repair_commit_record
from memory_jobs import MaintenanceQueue, semantic_eligibility_payload, stable_path_identity
from platform_lock import exclusive_lock
from platform_atomic import native_filesystem_path
from platform_transaction import atomic_write_canonical_json, canonical_json_bytes

FORMAT = 'memory-wuxian-summary-identity-repair-v1'


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable_json(path, value):
    payload = canonical_json_bytes(value)
    path = native_filesystem_path(path)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError('Identity repair artifact changed: ' + str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_canonical_json(path, value)


def preview(store, job_path):
    job_path = job_path.resolve()
    if job_path.parent != store.pending_dir.resolve():
        raise ValueError('Repair must name one current pending job')
    records = store.read_all_raw()
    resolution = build_resolution(store, records)
    replacement = plan_job_replacement(store, job_path, records, resolution)
    old = json.loads(job_path.read_text('utf-8'))
    queue = MaintenanceQueue(store.root)
    owners = [job for job in queue.jobs() if job['kind'] == 'semantic-summary-eligibility'
              and stable_path_identity(job['payload'].get('summary_job', '')) == stable_path_identity(job_path)]
    if not owners or any(job['state'] != 'quarantined' for job in owners):
        raise ValueError('Historical repair requires its current quarantined eligibility evidence')
    plan = {'format': FORMAT, 'archive_root': str(store.root.resolve()),
        'original_job_path': store.relative(job_path), 'original_job_id': old['job_id'],
        'resolution': resolution, **replacement,
        'quarantined_owners': [{'job_id': job['job_id'], 'sha256': file_sha(queue._path(job['job_id']))} for job in owners]}
    plan['plan_sha256'] = digest(plan)
    return plan


def apply(store, plan, expected_sha256):
    if plan.get('format') != FORMAT or plan.get('plan_sha256') != expected_sha256 or digest({k: v for k, v in plan.items() if k != 'plan_sha256'}) != expected_sha256:
        raise ValueError('Repair plan hash mismatch')
    if str(store.root.resolve()) != plan['archive_root']:
        raise ValueError('Repair plan belongs to a different archive')
    original = store.root / plan['original_job_path']
    replacement = plan['replacement']
    target = store.pending_dir / (replacement['job_id'] + '.json')
    retired = store.pending_dir / 'superseded' / expected_sha256 / original.name
    directory = store.root / 'source-identity/repairs' / expected_sha256
    queue = MaintenanceQueue(store.root)
    result = repair_commit_record(plan)
    for path in (original, target, retired, directory):
        if not path.resolve().is_relative_to(store.root.resolve()):
            raise ValueError('Repair target escapes archive')
    if original.parent.resolve() != store.pending_dir.resolve() or target.parent.resolve() != store.pending_dir.resolve():
        raise ValueError('Invalid repair job path')
    with exclusive_lock(store.locks_dir / 'archive.lock'):
        with exclusive_lock(store.locks_dir / 'summary-jobs.lock'):
            if native_filesystem_path(directory / 'committed.json').exists():
                immutable_json(directory / 'committed.json', result)
                immutable_json(directory / 'plan.json', plan)
                if not retired.exists() or file_sha(retired) != plan['original_job_sha256']:
                    raise ValueError('Committed repair lost its original job evidence')
                if target.exists() and file_sha(target) != plan['replacement_job_sha256']:
                    raise ValueError('Committed replacement job changed')
                if not target.exists():
                    from memory_summary_v2_links import SummaryV2Links
                    links = SummaryV2Links(store)
                    completion = next((r for r in links.read_completions(verify_bundles=False)
                        if r['target_summary_id'] == replacement['target_summary_id']), None)
                    if completion is None or completion['job_sha256'] != plan['replacement_job_sha256']:
                        raise ValueError('Committed repair lost its replacement and completion')
                    links.read_bundle(completion)
                verify_job_binding(store, replacement, store.read_all_raw())
                return {'status': 'already-applied', 'plan_sha256': expected_sha256, 'model_calls': 0}
            source = original if original.exists() else retired
            if not source.exists() or file_sha(source) != plan['original_job_sha256']:
                raise ValueError('Original quarantined job changed')
            if target.exists() and file_sha(target) != plan['replacement_job_sha256']:
                raise ValueError('Planned replacement ID was occupied by another job')
            records = store.read_all_raw()
            check = plan_job_replacement(store, source, records, plan['resolution'])
            # The planned fresh ID remains reserved across crash recovery; other
            # source fields are independently rebuilt from the original job.
            check['replacement']['job_id'] = replacement['job_id']
            if check['replacement'] != replacement or digest(replacement) != plan['replacement_job_sha256']:
                raise ValueError('Planned replacement no longer matches current raw sources')
            alias = replacement['target_summary_id']
            if any(r['summary_id'] == alias for r in store.summary_records()) or (store.root / 'summary-v2/completions' / (alias + '.json')).exists():
                raise ValueError('Replacement target already has a persisted summary')
            source_ids = set(replacement['source_message_ids'])
            for other in store.pending_jobs():
                if other['job_id'] in {plan['original_job_id'], replacement['job_id']}:
                    continue
                if other['target_summary_id'] == alias or source_ids.intersection(other.get('source_message_ids') or []):
                    raise ValueError('Replacement overlaps another active pending assignment')
            completed_ids = [set(r.get('source_message_ids') or []) for r in store.summary_records()]
            from memory_summary_v2_links import SummaryV2Links
            completed_ids.extend(set(r['source'].get('source_message_ids') or []) for r in SummaryV2Links(store).read_completions(verify_bundles=False))
            if any(source_ids & ids for ids in completed_ids):
                raise ValueError('Replacement overlaps persisted summary coverage')
            for owner in plan['quarantined_owners']:
                path = queue._path(owner['job_id'])
                if path.exists() and file_sha(path) != owner['sha256']:
                    raise ValueError('Quarantined eligibility changed since preview')
            immutable_json(directory / 'plan.json', plan)
            immutable_json(resolution_path(store), plan['resolution'])
            immutable_json(resolution_path(store).with_name('activation.json'), {
                'format': 'memory-wuxian-source-identity-activation-v1',
                'resolution_sha256': plan['resolution_sha256'],
                'resolution_file_sha256': file_sha(resolution_path(store)),
                'approved_plan_sha256': expected_sha256})
            for owner in plan['quarantined_owners']:
                queue.supersede_quarantined(owner['job_id'], plan)
            if original.exists():
                if retired.exists():
                    raise ValueError('Both active and retired original jobs exist')
                retired.parent.mkdir(parents=True, exist_ok=True)
                native_filesystem_path(original).rename(native_filesystem_path(retired))
            # Advance the allocator before publishing the replacement. No summary
            # ID is allocated again: this job inherits the old reservation.
            state = store.load_state()
            state['next_job_id'] = max(int(state['next_job_id']), int(replacement['job_id'].split('-')[1]) + 1)
            store.save_state(state)
            immutable_json(target, replacement)
            queue.enqueue_semantic(semantic_eligibility_payload(target), max_attempts=4)
            immutable_json(directory / 'committed.json', result)
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--job')
    parser.add_argument('--plan', required=True)
    parser.add_argument('--apply-sha256')
    args = parser.parse_args()
    store = MemoryStore(Path(args.root).resolve(), load_simple_yaml(Path(args.config)))
    path = Path(args.plan).resolve()
    if args.apply_sha256:
        result = apply(store, json.loads(path.read_text('utf-8')), args.apply_sha256)
    else:
        if not args.job:
            parser.error('--job is required for preview')
        result = preview(store, Path(args.job))
        immutable_json(path, result)
        result = {k: result[k] for k in ('plan_sha256', 'original_job_id', 'original_source_sha256', 'replacement_job_sha256', 'physical_source_records', 'selected_source_records')}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
