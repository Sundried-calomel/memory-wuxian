"""Explicit legacy archive migration; retain original files and capture boundaries."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from archive import ArchiveStore
from collector import session_identity
from native_bridge import NativeArchiveBridge
from storage import atomic_write_json, bytes_sha256
from summary_migration import migrate_summaries


def relocated_cursor(old_root, source, legacy_scripts, config):
    # Use the installed, raw-authority-verified reconciliation algorithm once.
    # Never apply its legacy cursor/ledger changes or import it in normal capture.
    sys.path.append(str(Path(legacy_scripts).resolve(strict=True)))
    from source_reconcile import plan, sha
    from memory_cli import load_simple_yaml
    proposal = plan(Path(old_root), source, load_simple_yaml(Path(config)))
    line = proposal['new_cursor']['message_last_line']
    offset = 0
    with source.open('rb') as handle:
        for _ in range(line):
            raw = handle.readline()
            if not raw.endswith(b'\n'):
                raise ValueError('relocated boundary is incomplete')
            offset += len(raw)
        handle.seek(max(0, offset - 256))
        anchor = bytes_sha256(handle.read(min(offset, 256)))
    if sha(source) != proposal['source_sha256']:
        raise ValueError('source changed after exact reconciliation')
    return dict(offset=offset, line=line, session=proposal['old_cursor']['session_id'], anchor=anchor)


def checkpoint_plan(old_root, sessions, legacy_scripts=None, config=None):
    sessions = Path(sessions).resolve(strict=True)
    planned, skipped, errors = {}, 0, []
    for filename in sorted((Path(old_root) / 'imports/codex').glob('*.json')):
        old = {}
        try:
            old = json.loads(filename.read_text('utf-8'))
            if not old.get('source_path'):
                continue
            source = Path(old['source_path'])
            if not source.is_file() or not source.resolve().is_relative_to(sessions):
                skipped += 1
                continue
            source = source.resolve()
            offset = old.get('committed_byte_offset', old.get('source_size'))
            size = old.get('source_size')
            if not isinstance(offset, int) or offset < 0 or offset != size:
                raise ValueError('legacy source boundary is not fully committed')
            digest, lines, remaining = hashlib.sha256(), 0, offset
            with source.open('rb') as handle:
                header = json.loads(handle.readline())
                session, excluded = session_identity(header['payload'])
                if excluded:
                    skipped += 1
                    continue
                if session != old['session_id']:
                    raise ValueError('session ID differs from legacy cursor')
                handle.seek(0)
                last = b''
                while remaining:
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('source shorter than committed boundary')
                    digest.update(chunk)
                    lines += chunk.count(b'\n')
                    remaining -= len(chunk)
                    last = chunk[-1:]
                if offset and last != b'\n':
                    raise ValueError('legacy cursor splits a source line')
                if digest.hexdigest() != old.get('source_byte_sha256'):
                    raise ValueError('source prefix no longer matches legacy capture')
                if lines != old.get('last_line'):
                    raise ValueError('legacy line count differs from source boundary')
                handle.seek(max(0, offset - 256))
                anchor = bytes_sha256(handle.read(min(offset, 256)))
            key = bytes_sha256(str(source).encode())
            cursor = dict(offset=offset, line=lines, session=session, anchor=anchor)
            if key in planned and planned[key] != cursor:
                raise ValueError('multiple legacy cursors disagree on one source')
            planned[key] = cursor
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if legacy_scripts and config and old.get('source_path'):
                try:
                    source = Path(old['source_path']).resolve(strict=True)
                    source.relative_to(sessions)
                    planned[bytes_sha256(str(source).encode())] = relocated_cursor(
                        old_root, source, legacy_scripts, config)
                    continue
                except (OSError, ValueError, KeyError, TypeError) as relocation_error:
                    exc = relocation_error
            errors.append(dict(cursor=filename.name, source=old.get('source_path'), error=str(exc)))
    return planned, dict(planned=len(planned), skipped=skipped, errors=errors)


def migrate(old_root, new_root, sessions, config, legacy_scripts=None):
    # The caller must first quiesce old writers. Import completion precedes cursors.
    planned, check = checkpoint_plan(old_root, sessions, legacy_scripts, config)
    bridge = NativeArchiveBridge(new_root, Path(old_root) / 'raw')
    result = bridge.run_once(progress=lambda counts: print(json.dumps(counts), flush=True))
    if result['pending_file']:
        raise ValueError('legacy raw file contains an incomplete record')
    summary = migrate_summaries(Path(new_root), config_path=Path(config), source_root=Path(old_root))
    for issue in check['errors']:
        if not issue['source']:
            raise ValueError('legacy cursor has no source identity')
        key = bytes_sha256(str(Path(issue['source']).resolve()).encode())
        planned[key] = {'legacy_error': issue['error'], 'legacy_cursor': issue['cursor']}
    for key, cursor in planned.items():
        path = Path(new_root) / 'collectors' / (key + '.json')
        if path.exists():
            current = json.loads(path.read_text('utf-8'))
            if current != cursor:
                raise ValueError('existing direct capture cursor differs; refusing reset')
        else:
            atomic_write_json(path, cursor)
    return dict(status='migrated-with-legacy-issues' if check['errors'] or summary['status']=='partial' else 'migrated',
                capture=check, raw=result, summary_status=summary['status'],
                summaries=summary['completed_summary_count'], archive=ArchiveStore(new_root).status())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-root', required=True)
    parser.add_argument('--sessions', required=True)
    parser.add_argument('--new-root')
    parser.add_argument('--config')
    parser.add_argument('--legacy-scripts', help='installed legacy scripts, used only for exact rewritten-source reconciliation')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply:
        if not args.new_root or not args.config:
            parser.error('--apply requires --new-root and --config')
        result = migrate(args.old_root, args.new_root, args.sessions, args.config, args.legacy_scripts)
    else:
        _, result = checkpoint_plan(args.old_root, args.sessions, args.legacy_scripts, args.config)
    print(json.dumps(result), flush=True)
    return 2 if result.get('errors') else 0


if __name__ == '__main__':
    raise SystemExit(main())
