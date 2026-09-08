#!/usr/bin/env python3
"""Preview or apply an evidence-bound cursor relocation after a source rewrite."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile

from memory_cli import (MemoryStore, atomic_write_json, load_simple_yaml,
                        now_iso, raw_record_sha256, redact_secrets)
from platform_lock import exclusive_lock


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def signature(record):
    timestamp = dt.datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))
    return (record['speaker'], timestamp.astimezone(dt.timezone.utc).isoformat(),
            record['source']['phase'], record['text'])


def align(archived, current):
    """Align an unambiguous retained suffix without rewriting older history."""
    if not archived:
        raise ValueError('No archived prefix; no safe relocation anchor')
    if not current:
        raise ValueError('Retained source has fewer visible records than the archive')
    old_keys, new_keys = [signature(r) for r in archived], [signature(r) for r in current]
    matches = []
    for start, key in enumerate(old_keys):
        remaining = len(archived) - start
        if key == new_keys[0] and remaining <= len(current) and (start == 0 or remaining >= 2):
            if old_keys[start:] == new_keys[:remaining]:
                matches.append(remaining)
    if len(matches) != 1:
        raise ValueError('Visible source prefix differs or lacks a unique complete suffix; manual reconciliation required')
    return current[matches[0] - 1]['source']['line']


class ProjectionStore(MemoryStore):
    def append_message(self, **record):
        record['text'] = redact_secrets(record['text'])[0] if self.config.get('safety', {}).get('redact_secrets', True) else record['text']
        self.projected.append(record)
        return {'status': 'duplicate'}


def pending_for_source(root, source):
    pending = {}
    path = root / 'imports/codex/capture-wal.jsonl'
    if path.exists():
        for line in path.read_text().splitlines():
            if not line:
                continue
            event = json.loads(line)
            key = event['transaction_id']
            if event['phase'] == 'prepared':
                pending[key] = event['intent']
            else:
                pending.pop(key, None)
    return [v for v in pending.values() if v['source_path'] == str(source)]


def plan(root, source, config):
    root, source = root.resolve(), source.resolve()
    before = sha(source)
    store = MemoryStore(root, config)
    with tempfile.TemporaryDirectory(prefix='memory-source-projection-') as directory:
        projected = ProjectionStore(Path(directory), config)
        projected.init()
        projected.projected = []
        result = projected.sync_codex_file(source)
        if result.get('excluded_reason'):
            raise ValueError('Excluded session does not require visible-history relocation')
        session_id, segment_id = result['session_id'], result['segment_id']
        cursor_path = store.codex_cursor_path(segment_id)
        cursor = json.loads(cursor_path.read_text())
        transcript = store.conversation_transcript_path('codex:' + session_id)
        archived = [r for r in store.read_raw_file(transcript)
                    if r.get('source', {}).get('path') == str(source)]
        archived.sort(key=lambda r: r['sequence'])
        # Verify routing copies against authoritative daily raw records.
        raw_by_id = {}
        raw_hashes = {}
        for path in {store.raw_path_for_timestamp(r['timestamp']) for r in archived}:
            raw_hashes[str(path)] = sha(path)
            raw_by_id.update({r['message_id']: r for r in store.read_raw_file(path)})
            if sha(path) != raw_hashes[str(path)]:
                raise ValueError('Raw authority changed during verification')
        for record in archived:
            if record.get('content_sha256') != raw_record_sha256(record):
                raise ValueError('Archived record hash mismatch')
            if raw_by_id.get(record['message_id']) != record:
                raise ValueError('Transcript differs from raw authority')
        anchor = align(archived, projected.projected)
        matched_count = next(i + 1 for i, r in enumerate(projected.projected) if r['source']['line'] == anchor)
        if pending_for_source(root, source):
            raise ValueError('Unresolved WAL transaction requires separate transaction reconciliation')
        token_path = root / 'imports/codex/token-usage' / (segment_id + '.json')
        ledger = json.loads(token_path.read_text()) if token_path.exists() else None
        token_anchor = 0
        byte_offsets = {0: 0}
        last_marker = (ledger or {}).get('last_token_event')
        token_matches = []
        offset = 0
        with source.open('rb') as handle:
            for number, line in enumerate(handle, 1):
                offset += len(line)
                if number == anchor:
                    byte_offsets[number] = offset
                if last_marker:
                    event = json.loads(line)
                    payload = event.get('payload') or {}
                    if (event.get('type') == 'event_msg' and payload.get('type') == 'token_count'
                            and event.get('timestamp') == last_marker.get('timestamp')
                            and (payload.get('info') or {}).get('total_token_usage') == ledger.get('current_segment_usage')):
                        token_matches.append((number, offset))
        if last_marker:
            if len(token_matches) != 1:
                raise ValueError('Latest token telemetry anchor is missing or ambiguous')
            token_anchor, token_offset = token_matches[0]
            byte_offsets[token_anchor] = token_offset
        start = min(anchor, token_anchor) if last_marker else anchor
        generation = before[:24]
        next_cursor = dict(cursor, source_generation=generation, last_line=start,
                           message_last_line=anchor, committed_byte_offset=byte_offsets[start],
                           source_size=byte_offsets[start], observed_source_size=source.stat().st_size,
                           complete=False, source_byte_sha256=None, updated_at=now_iso())
        next_ledger = None
        if ledger:
            next_ledger = dict(ledger, scanned_through_line=token_anchor,
                               source_generation=generation)
            if last_marker:
                next_ledger['last_token_event'] = dict(last_marker, line=token_anchor)
        if sha(source) != before:
            raise ValueError('Source changed during reconciliation; retry after a stable boundary')
        return {'status': 'ready', 'source': str(source), 'source_sha256': before,
                'cursor_path': str(cursor_path), 'cursor_sha256': sha(cursor_path),
                'old_cursor': cursor, 'new_cursor': next_cursor,
                'token_path': str(token_path), 'token_sha256': sha(token_path) if ledger else None,
                'old_ledger': ledger, 'new_ledger': next_ledger,
                'transcript_path': str(transcript), 'transcript_sha256': sha(transcript),
                'raw_hashes': raw_hashes,
                'verified_records': len(archived), 'matched_retained_records': matched_count,
                'pending_source_records': len(projected.projected) - matched_count}


def apply(root, proposal):
    root = root.resolve()
    source = Path(proposal['source'])
    with exclusive_lock(root / '.locks/archive.lock'):
        for name, expected in proposal['raw_hashes'].items():
            if sha(Path(name)) != expected:
                raise ValueError('Raw authority changed before apply')
        for name, expected in [('source', 'source_sha256'), ('cursor_path', 'cursor_sha256'),
                               ('transcript_path', 'transcript_sha256')]:
            if sha(Path(proposal[name])) != proposal[expected]:
                raise ValueError('Evidence changed before apply: ' + name)
        token_path = Path(proposal['token_path'])
        actual_token = sha(token_path) if token_path.exists() else None
        if actual_token != proposal['token_sha256'] or pending_for_source(root, source):
            raise ValueError('Token or WAL state changed before apply')
        receipt = root / 'imports/codex/source-reconciliations' / (proposal['source_sha256'] + '.json')
        atomic_write_json(receipt, dict(proposal, status='prepared'))
        try:
            if proposal['new_ledger'] is not None:
                atomic_write_json(token_path, proposal['new_ledger'])
            atomic_write_json(Path(proposal['cursor_path']), proposal['new_cursor'])
            digest = hashlib.sha256(str(source).encode()).hexdigest()
            error = root / 'imports/codex/source-errors' / (digest + '.json')
            if error.exists():
                # Retain the old diagnostic in the receipt before clearing its cache.
                proposal['previous_source_error'] = json.loads(error.read_text())
            error.unlink(missing_ok=True)
            atomic_write_json(receipt, dict(proposal, status='applied', applied_at=now_iso()))
        except BaseException:
            atomic_write_json(Path(proposal['cursor_path']), proposal['old_cursor'])
            if proposal['old_ledger'] is not None:
                atomic_write_json(token_path, proposal['old_ledger'])
            raise
    return {'status': 'applied', 'receipt': str(receipt),
            'verified_records': proposal['verified_records'],
            'pending_source_records': proposal['pending_source_records']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config.yaml')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        proposal = plan(args.root, args.source, load_simple_yaml(args.config))
        result = apply(args.root, proposal) if args.apply else {
            k: proposal[k] for k in ('status', 'source', 'source_sha256', 'verified_records', 'pending_source_records')}
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
