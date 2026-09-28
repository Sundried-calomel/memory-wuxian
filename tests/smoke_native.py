"""Small local assembly check: direct capture -> encrypted peer import -> ACK."""
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'core'))
from archive import ArchiveStore
from collector import DirectCollector
from core_sync import CoreSyncService
from live import run_tick


def main():
    binary = Path(os.environ['MW_ENVELOPE_BINARY']).resolve()
    with tempfile.TemporaryDirectory(prefix='smoke-', dir=BASE) as tmp:
        work = Path(tmp)
        sessions = work / 'sessions'
        sessions.mkdir()
        stamp = '2026-09-28T12:00:00+00:00'
        def event(kind, payload, **extra):
            return json.dumps(dict(type=kind, payload=payload, timestamp=stamp, **extra), ensure_ascii=False).encode() + b'\n'
        source = sessions / 'rollout-local.jsonl'
        source.write_bytes(event('session_meta', {'id': 'session-a', 'source': 'vscode', 'thread_source':'user'}, ordinal=0) +
            event('event_msg', {'type': 'item_completed', 'turn_id': 'turn-1', 'item':
                {'id': 'msg-1', 'type': 'UserMessage', 'content': [{'type': 'Text', 'text': '保留原文'}]}}) +
            event('event_msg', {'type': 'item_completed', 'turn_id': 'turn-1', 'item':
                {'id': 'msg-2', 'type': 'AgentMessage', 'phase': 'commentary', 'content': [{'type': 'Text', 'text': '处理中'}]}}) +
            event('response_item', {'type': 'function_call_output', 'output': 'DO NOT CAPTURE'}) +
            event('event_msg', {'type': 'item_completed', 'turn_id': 'turn-1', 'item':
                {'id': 'msg-3', 'type': 'AgentMessage', 'phase': 'final_answer', 'content': [{'type': 'Text', 'text': '完成'}]}}))
        (sessions / 'rollout-child.jsonl').write_bytes(event('session_meta', {'id':'child', 'source':{'subagent':{}}}) +
            event('event_msg', {'type':'user_message', 'message':'DO NOT CAPTURE'}))
        legacy = sessions / 'rollout-legacy.jsonl'
        legacy.write_bytes(event('session_meta', {'id':'legacy', 'source':'vscode'}) +
            event('event_msg', {'type':'user_message', 'message':'旧格式'}) +
            event('event_msg', {'type':'agent_message', 'phase':'final_answer', 'message':'旧格式回答'}))
        for stream,text in [('one','slice one'),('two','slice two')]:
            (sessions/f'rollout-slices_{stream}.jsonl').write_bytes(
                event('session_meta',{'id':'slices','source':'vscode','thread_source':'user'})+
                event('event_msg',{'type':'user_message','message':text}))
        config = work / 'config.json'
        config.write_text(json.dumps({'root':str(work/'a'), 'sessions_root':str(sessions), 'auto_summary':False}))
        assert run_tick(config)['status'] == 'completed'
        left = ArchiveStore(work / 'a')
        collector = DirectCollector(left, sessions)
        records = list(left.records())
        assert len(records) == 7 and not any('DO NOT' in r['text'] for r in records)
        assert sum(r['completes_round'] for r in records) == 2
        assert collector.run_once()['appended'] == 0
        # Replaying a lost checkpoint must not duplicate successfully committed messages.
        for checkpoint in (left.root/'collectors').glob('*.json'):
            checkpoint.unlink()
        assert collector.run_once()['appended'] == 0
        tail = event('event_msg', {'type':'user_message', 'message':'partial then complete'})
        with source.open('ab') as handle:
            handle.write(tail[:-1])
        assert collector.run_once()['appended'] == 0
        with source.open('ab') as handle:
            handle.write(b'\n')
        assert collector.run_once()['appended'] == 1
        right = ArchiveStore(work / 'b')
        def identity(node):
            path = work / (node + '.json')
            result = subprocess.run([str(binary), 'init-identity', '--path', str(path), '--node-id', node],
                                    capture_output=True, text=True, check=True)
            return path, json.loads(result.stdout)
        a_path, a = identity('node-a')
        b_path, b = identity('node-b')
        def transport(store, path, me, peer):
            return CoreSyncService(store, exchange_root=work/'exchange', binary=binary, identity=path,
                local_node_id=me['node_id'], peer_id=peer['node_id'],
                peer_encryption_public_key=peer['encryption_public_key'],
                peer_signing_public_key=peer['signing_public_key'])
        sender, receiver = transport(left,a_path,a,b), transport(right,b_path,b,a)
        sent = sender.sync_once()
        received = receiver.sync_once()
        acknowledged = sender.sync_once()
        assert sent['last_run']['sent_batches'] == 1
        assert received['last_run']['received_batches'] == 1
        assert acknowledged['last_run']['acknowledged_batches'] == 1
        assert receiver.peer_index.state('node-a')['last_wire_sequence'] == 8
        sealed = sender._seal(b'boundary check', target='node-b')
        assert receiver._open(sealed, expected_origin='node-a', expected_target='node-b') == b'boundary check'
        tampered = sealed[:-1] + bytes([sealed[-1] ^ 1])
        try:
            receiver._open(tampered, expected_origin='node-a', expected_target='node-b')
        except ValueError:
            pass
        else:
            raise AssertionError('tampered envelope accepted')
        result = {'status':'passed', 'captured_messages':8, 'rounds':2,
                  'replay_duplicate_messages':0, 'peer_imported_messages':8,
                  'acknowledged_batches':1, 'tampered_envelope':'rejected',
                  'scope':'isolated local synthetic nodes; no live cutover'}
        (BASE/'smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
