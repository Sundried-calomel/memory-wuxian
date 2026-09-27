"""Incremental, read-only input adapter for native collector Markdown archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from archive import ArchiveStore, record_hash
from legacy import normalize_raw, validate_raw
from storage import atomic_write_json, bytes_sha256, canonical_json_bytes, exclusive_lock, safe_target

_MARKER = b'<!-- memory-wuxian-record -->'


def internal_session_reason(source, conversation_id, cache=None):
    """Classify an exactly associated session_meta first line, never message text."""
    if not isinstance(source, dict) or source.get('kind') != 'codex-rollout-jsonl':
        return None
    session_id, name = source.get('session_id'), source.get('path')
    if not isinstance(session_id, str) or conversation_id != 'codex:' + session_id or not isinstance(name, str):
        return None
    key = (name, session_id)
    if cache is not None and key in cache:
        return cache[key]
    path = Path(name)
    if not path.is_absolute() or not path.is_file():
        return None  # Missing historical metadata is unknown, not inferred internal.
    with path.open('rb') as handle:
        line = handle.readline(4 * 1024 * 1024 + 1)
    if len(line) > 4 * 1024 * 1024 or not line.endswith(b'\n'):
        raise ValueError('associated session metadata header is incomplete or oversized')
    event = json.loads(line)
    payload = event.get('payload', {})
    if event.get('type') != 'session_meta' or payload.get('id', payload.get('session_id')) != session_id:
        raise ValueError('associated session metadata identity mismatch')
    category = payload.get('source')
    result = None
    if isinstance(category, dict) and 'subagent' in category:
        subtype = category['subagent']
        label = subtype.get('other') if isinstance(subtype, dict) else subtype
        result = {'reason': 'session_meta.source explicitly identifies a subagent',
                  'source_kind': 'subagent' + (':' + label if isinstance(label, str) else '')}
    elif category == 'subagent':
        result = {'reason': 'session_meta.source explicitly identifies a subagent', 'source_kind': 'subagent'}
    if cache is not None:
        cache[key] = result
    return result


def record_exclusions(root, evidence):
    """Append classifications; preserve archived records and earlier evidence."""
    path = safe_target(root, 'excluded-conversations.json')
    with exclusive_lock(safe_target(root, '.exclusions.lock')):
        previous = json.loads(path.read_text('utf-8')) if path.exists() else {'conversations': [], 'evidence': {}}
        merged = dict(previous.get('evidence', {}))
        for conversation, reason in evidence.items():
            merged.setdefault(conversation, reason)
        current = {'conversations': sorted(set(previous['conversations']) | set(evidence)), 'evidence': merged}
        if current != previous:
            atomic_write_json(path, current)
        return current['conversations']


def _stamp(value):
    # Windows stat/fstat disagree on ctime (creation versus metadata time).
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def _prefix(handle, length):
    handle.seek(0)
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError('native source was truncated')
        digest.update(chunk)
        remaining -= len(chunk)
    return digest


class NativeArchiveBridge:
    def __init__(self, root, source):
        self.source = Path(source).absolute()
        if not self.source.is_dir() or self.source.is_symlink():
            raise ValueError('explicit native raw directory required')
        self.store = ArchiveStore(root)
        if (self.store.root.resolve().is_relative_to(self.source.resolve())
                or self.source.resolve().is_relative_to(self.store.root.resolve())):
            raise ValueError('source and destination archives must be separate')
        source_key = bytes_sha256(str(self.source.resolve()).encode('utf-8'))
        self.relative = 'native-bridge/' + source_key[:16]
        self.cursor_root = safe_target(self.store.root, self.relative)
        self.cursor_root.mkdir(parents=True, exist_ok=True)
        self._session_cache = {}
        self._excluded = set()

    def _conflict_record(self, record):
        path = safe_target(self.store.root, 'legacy/conflicts/' +
                           bytes_sha256(record['message_id'].encode()) + '.json')
        evidence = dict(message_id=record['message_id'], legacy_sha256=record['legacy_sha256'],
                        source_root=str(self.source.resolve()),
                        **record['legacy_identity_conflict'])
        if path.exists():
            if json.loads(path.read_text('utf-8')) != evidence:
                raise ValueError('legacy conflict evidence changed')
        else:
            atomic_write_json(path, evidence)

    def _append(self, original):
        """Caller holds the archive lock; journal recovery owns durable insertion."""
        validate_raw(original)
        reason = internal_session_reason(original.get('source'), original['conversation_id'], self._session_cache)
        if reason:
            conversation = original['conversation_id']
            if conversation not in self._excluded:
                record_exclusions(self.store.root, {conversation: reason})
                self._excluded.add(conversation)
            return 'excluded'
        self.store._recover()
        existing = self.store.message_by_id(original['message_id'])
        conflict = None
        identifier = original['message_id']
        if existing:
            if existing.get('legacy_sha256') == original['content_sha256']:
                return 'duplicate'
            first = existing.get('legacy_original', existing)
            conflict = dict(original_message_id=original['message_id'],
                first_message_id=existing['message_id'],
                same_visible_content=all(first.get(k) == original.get(k)
                    for k in ('speaker', 'text', 'conversation_id', 'timestamp')),
                changed_fields=sorted(k for k in set(first) | set(original)
                                      if first.get(k) != original.get(k)))
            identifier = 'native-version:' + bytes_sha256(canonical_json_bytes([
                str(self.source.resolve()), original['conversation_id'],
                original['message_id'], original['content_sha256']]))
            version = self.store.message_by_id(identifier)
            if version:
                if version.get('legacy_sha256') != original['content_sha256']:
                    raise ValueError('native version identity conflicts with stored record')
                self._conflict_record(version)
                return 'duplicate'
        with self.store.connection() as db:
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM messages').fetchone()[0]
            state = db.execute('SELECT next_round,pending FROM rounds WHERE conversation=?',
                               (original['conversation_id'],)).fetchone()
        record = normalize_raw(original, sequence)
        if conflict:
            record['message_id'] = identifier
            record['legacy_identity_conflict'] = conflict
            record['content_sha256'] = record_hash(record)
        next_round, pending = tuple(state) if state else (1, None)
        number = record['round_number']
        next_round = max(next_round, number + 1)
        if record['speaker'] == 'user' and number:
            pending = number
        if record['completes_round']:
            pending = None
        relative = 'raw/' + bytes_sha256(record['conversation_id'].encode()) + '.jsonl'
        target = safe_target(self.store.root, relative)
        atomic_write_json(self.store.journal, dict(record=record, path=relative,
            offset=target.stat().st_size if target.exists() else 0,
            next_round=next_round, pending_round=pending))
        self.store._recover()
        if conflict:
            self._conflict_record(record)
        return 'variant' if conflict else 'appended'

    def _file(self, relative):
        source = safe_target(self.source, relative)
        cursor_path = safe_target(self.store.root, self.relative + '/' + bytes_sha256(relative.encode())[:24] + '.json')
        saved = json.loads(cursor_path.read_text('utf-8')) if cursor_path.exists() else None
        if saved and (saved.get('source') != relative or saved.get('source_root') != str(self.source.resolve())):
            raise ValueError('native cursor source identity mismatch')
        if saved and saved['stamp'] == _stamp(source.stat()):
            return dict(appended=0, duplicates=0, variants=0, excluded=0, unchanged=True, pending=saved['pending'])
        offset = saved['offset'] if saved else 0
        appended = duplicates = variants = excluded = 0
        pending = False
        with source.open('rb') as handle, self.store.lock():
            self.store._recover()
            start_stat = os.fstat(handle.fileno())
            if saved and (list((start_stat.st_dev, start_stat.st_ino)) != saved['stamp'][:2]):
                raise ValueError('native source file identity changed')
            if start_stat.st_size < offset:
                raise ValueError('native source was truncated')
            digest = _prefix(handle, offset)
            if saved and digest.hexdigest() != saved['prefix_sha256']:
                raise ValueError('native source consumed prefix changed')
            # Read no later than this invocation's file-size snapshot.
            while handle.tell() < start_stat.st_size:
                beginning = handle.tell()
                line = handle.readline(start_stat.st_size - beginning)
                if not line.endswith(b'\n'):
                    pending = True
                    break
                block = [line]
                if line.rstrip(b'\r\n').removeprefix(b'\xef\xbb\xbf') == _MARKER:
                    for _ in range(3):
                        remaining = start_stat.st_size - handle.tell()
                        block.append(handle.readline(remaining) if remaining else b'')
                    if any(not item.endswith(b'\n') for item in block[1:]):
                        pending = True
                        break
                    if block[1].rstrip(b'\r\n') != b'```json' or block[3].rstrip(b'\r\n') != b'```':
                        raise ValueError('native raw record block framing changed')
                    original = json.loads(block[2])
                    status = self._append(original)
                    if status == 'excluded':
                        excluded += 1
                    elif status != 'duplicate':
                        appended += 1
                        variants += status == 'variant'
                    else:
                        duplicates += 1
                for item in block:
                    digest.update(item)
                offset = handle.tell()
            # An append while reading is harmless; mutation of consumed bytes is not.
            end_stat = os.fstat(handle.fileno())
            if _stamp(end_stat) != _stamp(start_stat):
                if _prefix(handle, offset).hexdigest() != digest.hexdigest():
                    raise ValueError('native source changed while importing')
            final_stat = source.stat()
            if (final_stat.st_dev, final_stat.st_ino) != (start_stat.st_dev, start_stat.st_ino):
                raise ValueError('native source replaced while importing')
            atomic_write_json(cursor_path, dict(source=relative, source_root=str(self.source.resolve()), offset=offset,
                prefix_sha256=digest.hexdigest(), stamp=_stamp(start_stat), pending=pending))
        return dict(appended=appended, duplicates=duplicates, variants=variants, excluded=excluded, unchanged=False, pending=pending)

    def run_once(self, progress=None):
        self._session_cache.clear()
        result = dict(appended=0, duplicates=0, variants=0, excluded=0, files_processed=0, files_unchanged=0, pending_file=None)
        with exclusive_lock(safe_target(self.store.root, self.relative + '/bridge.lock')):
            files = sorted(p.relative_to(self.source).as_posix() for p in self.source.rglob('*.md'))
            known = [json.loads(p.read_text('utf-8'))['source'] for p in self.cursor_root.glob('*.json')]
            if not set(known) <= set(files):
                raise ValueError('previously consumed native source file is missing')
            for relative in files:
                item = self._file(relative)
                for name in ('appended', 'duplicates', 'variants', 'excluded'):
                    result[name] += item[name]
                result['files_unchanged' if item['unchanged'] else 'files_processed'] += 1
                if progress:
                    progress(dict(result))  # Counts only, never raw content.
                if item['pending']:
                    result['pending_file'] = relative
                    break
        result.update({k:v for k,v in self.store.status().items() if k in {'total_messages', 'last_sequence'}})
        result['excluded_conversations'] = sorted(self._excluded)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--once', action='store_true', required=True)
    args = parser.parse_args()
    result = NativeArchiveBridge(args.root, args.source).run_once()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
