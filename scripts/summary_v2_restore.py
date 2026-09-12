"""Restore a verified V2 backup extension to explicitly selected local bundle roots."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

from memory_summary_v2_links import digest, file_digest
from platform_atomic import atomic_replace_bytes
from platform_transaction import atomic_write_canonical_json


def checked_destination(target: Path, relative: Path, archive: Path, snapshot: Path) -> Path:
    if relative.is_absolute() or relative.drive or '..' in relative.parts or ':' in relative.as_posix():
        raise ValueError('Unsafe relative V2 restore path')
    destination = target / relative
    for path in [destination, *destination.parents]:
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('V2 restore destination contains a link or junction')
    resolved = destination.resolve()
    if (not resolved.is_relative_to(target) or resolved == archive or resolved.is_relative_to(archive)
        or resolved == snapshot or resolved.is_relative_to(snapshot)):
        raise ValueError('V2 restore destination escapes its selected boundary')
    return destination


def preview_restore(snapshot: Path, archive: Path, target: Path) -> dict:
    snapshot, archive, target = snapshot.resolve(), archive.resolve(), target.resolve()
    if target == archive or target.is_relative_to(archive) or target == snapshot or target.is_relative_to(snapshot):
        raise ValueError('Restore bundle destination must be outside the archive and backup snapshot')
    manifest_path = snapshot / 'summary-v2/backup-bundles.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    if manifest['format'] != 'memory-wuxian-summary-v2-backup-v1' or manifest['manifest_sha256'] != digest({
        k: v for k, v in manifest.items() if k != 'manifest_sha256'
    }):
        raise ValueError('V2 backup extension identity changed')
    files = []
    bindings = {}
    for bundle in manifest['objects']:
        binding = bundle['root_binding_id']
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', binding):
            raise ValueError('Unsafe restored binding ID')
        relative = Path(bundle['relative_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe restored bundle path')
        key = bundle['summary_v2_id']
        if not re.fullmatch(r'summary-v2-[0-9a-f]{32}', key):
            raise ValueError('Unsafe restored bundle ID')
        bindings[binding] = str(target / binding)
        for name, field in [('summary.json', 'summary_json_sha256'), ('summary.md', 'summary_markdown_sha256')]:
            source = snapshot / 'summary-v2/backup-objects' / key / name
            destination = checked_destination(target, Path(binding) / relative / name, archive, snapshot)
            if not source.resolve().is_relative_to(snapshot):
                raise ValueError('V2 restore source escapes its backup snapshot')
            if file_digest(source) != bundle[field]:
                raise ValueError('V2 backup object changed')
            if destination.exists() and file_digest(destination) != bundle[field]:
                raise ValueError('Restore would overwrite a different V2 object')
            files.append({'source': str(source), 'destination': str(destination), 'sha256': bundle[field]})
    completions = []
    expected_names = {p.name for p in (snapshot / 'summary-v2/completions').glob('*.json')}
    actual_names = {p.name for p in (archive / 'summary-v2/completions').glob('*.json')}
    if actual_names != expected_names:
        raise ValueError('V2 restore requires the exact matching archive completion set')
    for path in sorted((snapshot / 'summary-v2/completions').glob('*.json')):
        current = archive / 'summary-v2/completions' / path.name
        if not current.exists() or current.read_bytes() != path.read_bytes():
            raise ValueError('Restore the matching archive snapshot before its V2 extension')
        completions.append(json.loads(path.read_text('utf-8'))['completion_sha256'])
    if completions != manifest['root_completions']:
        raise ValueError('V2 backup root completion set changed')
    for checkpoint in manifest['checkpoints']:
        relative = Path(checkpoint['relative_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe restored checkpoint path')
        source = snapshot / 'summary-v2/backup-checkpoints/runtime' / relative
        destination = checked_destination(target, Path('runtime') / relative, archive, snapshot)
        if not source.resolve().is_relative_to(snapshot):
            raise ValueError('V2 restore checkpoint escapes its backup snapshot')
        if file_digest(source) != checkpoint['sha256']:
            raise ValueError('V2 backup checkpoint changed')
        if destination.exists() and file_digest(destination) != checkpoint['sha256']:
            raise ValueError('Restore would overwrite a different V2 checkpoint')
        files.append({'source': str(source), 'destination': str(destination), 'sha256': checkpoint['sha256']})
        bindings['runtime'] = str(target / 'runtime')
    plan = {'format': 'memory-wuxian-summary-v2-restore-plan-v1', 'archive_root': str(archive),
            'snapshot_root': str(snapshot), 'bundle_root': str(target),
            'snapshot_manifest_sha256': file_digest(manifest_path), 'bindings': bindings, 'files': files,
            'paused_jobs': sorted(p.stem for p in (archive / 'pending').glob('job-*.json'))}
    plan['plan_sha256'] = digest(plan)
    return plan


def apply_restore(plan: dict) -> dict:
    if plan['plan_sha256'] != digest({k: v for k, v in plan.items() if k != 'plan_sha256'}):
        raise ValueError('V2 restore plan changed')
    if preview_restore(Path(plan['snapshot_root']), Path(plan['archive_root']), Path(plan['bundle_root'])) != plan:
        raise ValueError('V2 restore boundaries changed after preview')
    binding = {'format': 'memory-wuxian-summary-v2-restored-bindings-v1', 'roots': plan['bindings'],
               'paused_jobs': plan['paused_jobs'], 'restore_plan_sha256': plan['plan_sha256']}
    path = Path(plan['archive_root']) / 'summary-v2/restored-bindings.json'
    if path.exists() and json.loads(path.read_text('utf-8')) != binding:
        raise ValueError('Restore conflicts with an existing local binding')
    for item in plan['files']:
        source, destination = Path(item['source']), Path(item['destination'])
        if file_digest(source) != item['sha256'] or (destination.exists() and file_digest(destination) != item['sha256']):
            raise ValueError('V2 restore source or destination changed after preview')
    created = 0
    for item in plan['files']:
        destination = Path(item['destination'])
        checked_destination(Path(plan['bundle_root']), destination.relative_to(plan['bundle_root']),
                            Path(plan['archive_root']), Path(plan['snapshot_root']))
        if not destination.exists():
            atomic_replace_bytes(destination, Path(item['source']).read_bytes())
            created += 1
    if not path.exists():
        atomic_write_canonical_json(path, binding)
    return {'status': 'restored' if created else 'no-change', 'created_files': created,
            'paused_jobs': plan['paused_jobs'], 'plan_sha256': plan['plan_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--bundle-root', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    plan = preview_restore(Path(args.snapshot), Path(args.archive), Path(args.bundle_root))
    path = Path(args.plan)
    if args.apply:
        if json.loads(path.read_text('utf-8')) != plan:
            raise ValueError('V2 restore preview changed')
        result = apply_restore(plan)
    else:
        if path.exists() and json.loads(path.read_text('utf-8')) != plan:
            raise ValueError('Use a new path for a changed restore preview')
        atomic_write_canonical_json(path, plan)
        result = {'status': 'preview', 'files': len(plan['files']), 'plan_sha256': plan['plan_sha256']}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
