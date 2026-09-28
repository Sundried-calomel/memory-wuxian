"""Direct Codex input -> ArchiveStore. One bounded pass per maintenance tick."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from archive import ArchiveStore, tool_description, source_identity
from storage import atomic_write_json, bytes_sha256, exclusive_lock

MAX_LINE = 16 * 1024 * 1024
MAX_BATCH = 500


def session_identity(payload):
    identity = payload.get('id') or payload.get('session_id')
    if not isinstance(identity, str) or not identity:
        raise ValueError('missing session identity')
    source, thread = payload.get('source'), payload.get('thread_source')
    internal = (isinstance(source, dict) and 'subagent' in source) or source == 'exec'
    allowed = (not internal and thread in (None, 'user') and isinstance(source, str)
               and source in ('cli', 'vscode', 'app', 'desktop'))
    return identity, not allowed


def visible(event, session, line_number, layout, stream=None):
    """Adapt only supported visible events; never capture reasoning/tool output."""
    outer, payload = event.get('type'), event.get('payload')
    if not isinstance(payload, dict):
        return None
    kind = payload.get('type')
    item_id = None
    if outer == 'event_msg' and kind == 'item_completed':
        item = payload.get('item', {})
        kind = item.get('type')
        if kind in ('UserMessage', 'AgentMessage'):
            turn, identifier = payload.get('turn_id'), item.get('id')
            if not isinstance(turn, str) or not isinstance(identifier, str):
                raise ValueError('completed message lacks stable identity')
            item_id = hashlib.sha256(f'{session}\0{turn}\0{identifier}'.encode()).hexdigest()
            parts = item.get('content')
            if not isinstance(parts, list):
                raise ValueError('invalid completed message content')
            texts = []
            for part in parts:
                if part.get('type') in ('Text', 'text'):
                    if not isinstance(part.get('text'), str):
                        raise ValueError('invalid message text')
                    texts.append(part['text'])
                elif part.get('type') not in ('image', 'local_image', 'Image', 'LocalImage'):
                    raise ValueError('unsupported completed message content')
            payload = {'type': 'user_message' if kind == 'UserMessage' else 'agent_message',
                       'message': ''.join(texts), 'phase': item.get('phase')}
        elif kind == 'FileChange':
            payload = {'type': 'patch_apply_end', 'success': item.get('status') == 'completed',
                       'changes': item.get('changes')}
        else:
            return None
        kind = payload['type']
    complete = False
    if outer == 'event_msg' and kind == 'user_message':
        speaker, phase, text = 'user', 'user', payload.get('message')
    elif outer == 'event_msg' and kind == 'agent_message':
        phase = payload.get('phase')
        if phase not in ('commentary', 'final_answer'):
            raise ValueError('unsupported assistant message phase')
        speaker, text, complete = 'assistant', payload.get('message'), phase == 'final_answer'
    elif outer == 'response_item' and kind in ('function_call', 'custom_tool_call', 'local_shell_call', 'web_search_call'):
        speaker, phase = 'tool', 'tool_activity'
        payload = dict(payload)
        payload.setdefault('name', kind)
        payload.setdefault('input', payload.get('command', ''))
        text = tool_description(payload)
    elif outer == 'event_msg' and kind == 'patch_apply_end' and payload.get('success') is True:
        changes = payload.get('changes')
        if not isinstance(changes, dict):
            raise ValueError('invalid file change map')
        speaker, phase = 'tool', 'file_change'
        text = '\n'.join(f"File: {path} [{change.get('type', 'update')}]" +
                         (f" -> {change['move_path']}" if change.get('move_path') else '')
                         for path, change in sorted(changes.items()))
    else:
        return None
    if not isinstance(text, str):
        raise ValueError('visible message text missing')
    if not text:
        return None
    stamp = event.get('timestamp')
    if not isinstance(stamp, str):
        raise ValueError('visible message timestamp missing')
    dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    suffix = {'user': 'u', 'assistant': 'a', 'tool': 't'}[speaker]
    fragment = f'item-{item_id}' if item_id else (f'layout2-{line_number:08}' if layout == 2 else f'{line_number:08}')
    if not item_id and stream:
        fragment = 'stream-' + bytes_sha256(stream.encode())[:16] + '-' + fragment
    return dict(speaker=speaker, text=text, timestamp=stamp, conversation_id='codex:' + session,
                message_id=f'codex-{session}-{fragment}-{suffix}', complete_round=complete,
                source={'kind': 'codex-session', 'session_id': session, 'line': line_number, 'phase': phase})


class DirectCollector:
    def __init__(self, store, sessions):
        self.store = store
        self.sessions = Path(sessions).resolve(strict=True)
        if not self.sessions.is_dir():
            raise ValueError('sessions must be a directory')

    def collect(self, path):
        path = Path(path).resolve(strict=True)
        path.relative_to(self.sessions)
        key = bytes_sha256(str(path).encode())
        checkpoint = self.store.root / 'collectors' / (key + '.json')
        with exclusive_lock(checkpoint.with_suffix('.lock')):
            cursor = json.loads(checkpoint.read_text('utf-8')) if checkpoint.exists() else {'offset': 0, 'line': 0}
            count = 0
            with path.open('rb') as handle:
                first = handle.readline(MAX_LINE + 1)
                if not first.endswith(b'\n'):
                    if len(first) > MAX_LINE:
                        raise ValueError('session metadata exceeds size limit')
                    return {'appended': 0, 'waiting': True}
                metadata = json.loads(first)
                if metadata.get('type') != 'session_meta':
                    raise ValueError('session metadata must precede messages')
                session, excluded = session_identity(metadata['payload'])
                if excluded:
                    return {'appended': 0, 'excluded': True}
                if cursor.get('session', session) != session:
                    raise ValueError('session identity changed')
                cursor['session'] = session
                layout = 2 if metadata.get('ordinal') == 0 else 1
                offset = cursor['offset']
                if os.fstat(handle.fileno()).st_size < offset:
                    raise ValueError('source truncated; explicit recovery required')
                if offset:
                    handle.seek(max(0, offset - 256))
                    if bytes_sha256(handle.read(min(offset, 256))) != cursor['anchor']:
                        raise ValueError('source changed at cursor; explicit recovery required')
                handle.seek(offset)
                for _ in range(MAX_BATCH):
                    start = handle.tell()
                    raw = handle.readline(MAX_LINE + 1)
                    if len(raw) > MAX_LINE:
                        raise ValueError('source line exceeds size limit')
                    if not raw or not raw.endswith(b'\n'):
                        handle.seek(start)
                        break
                    event = json.loads(raw)
                    number = cursor['line'] + 1
                    if event.get('type') == 'session_meta' and number != 1:
                        raise ValueError('unexpected session metadata inside stream')
                    message = visible(event, session, number, layout, path.stem if '_' in path.stem else None)
                    if message:
                        message['source']['path'] = str(path)
                        existing = self.store.message_by_id(message['message_id'])
                        if existing and existing.get('legacy_sha256'):
                            if any(existing[k] != message[k] for k in ('speaker','text','conversation_id')):
                                raise ValueError('legacy message identity differs; explicit migration required')
                            if dt.datetime.fromisoformat(existing['timestamp']) != dt.datetime.fromisoformat(message['timestamp'].replace('Z','+00:00')):
                                raise ValueError('legacy message timestamp differs')
                            result = {'status':'duplicate'}
                        else:
                            try:
                                result = self.store.append_message(**message)
                            except ValueError as error:
                                if existing:
                                    fields=[k for k in ('speaker','text','conversation_id','timestamp','complete_round') if existing.get(k)!=message.get(k)]
                                    if source_identity(existing.get('source'))!=source_identity(message['source']):fields.append('source')
                                    raise ValueError('message conflict in '+','.join(fields)) from error
                                raise
                        count += result['status'] == 'appended'
                    cursor.update(offset=handle.tell(), line=number)
                offset = cursor['offset']
                handle.seek(max(0, offset - 256))
                cursor['anchor'] = bytes_sha256(handle.read(min(offset, 256)))
            atomic_write_json(checkpoint, cursor)
            return {'appended': count, 'offset': offset}

    def run_once(self):
        result = {'appended': 0, 'files': 0, 'errors': {}}
        for folder, directories, files in os.walk(self.sessions, followlinks=False):
            directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()]
            for name in sorted(files):
                if not name.startswith('rollout-') or not name.endswith('.jsonl'):
                    continue
                path = Path(folder) / name
                if path.is_symlink():
                    continue
                try:
                    item = self.collect(path)
                    result['appended'] += item['appended']
                    result['files'] += 1
                except (ValueError, OSError, KeyError, TypeError) as error:
                    result['errors'][str(path)] = str(error)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--sessions', required=True)
    args = parser.parse_args()
    print(json.dumps(DirectCollector(ArchiveStore(args.root), args.sessions).run_once(), ensure_ascii=False))
