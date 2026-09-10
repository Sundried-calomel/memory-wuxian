import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from memory_cli import MemoryStore
from source_reconcile import plan, apply, align, inventory_alignment


def candidate_collector():
    suffix = '.exe' if os.name == 'nt' else ''
    default = Path(__file__).resolve().parents[1] / 'native-collector' / 'target' / 'debug' / ('memory-wuxian-collector' + suffix)
    return os.environ.get('MEMORY_WUXIAN_TEST_COLLECTOR', str(default))


class SourceReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'memory'
        self.source = self.base / 'rollout-2026-09-08T01-00-00-019fb8f2-9a67-7b03-9474-6f92cd6b21a7.jsonl'
        self.config = {'safety': {'redact_secrets': True}, 'backup': {'enabled': False}}
        self.events = [
            {'type': 'session_meta', 'payload': {'id': '019fb8f2-9a67-7b03-9474-6f92cd6b21a7', 'source': 'cli'}},
            {'timestamp': '2026-09-08T01:00:00Z', 'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'question'}},
            {'timestamp': '2026-09-08T01:00:01Z', 'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer', 'message': 'answer'}},
        ]
        self.write(self.events)
        self.store = MemoryStore(self.root, self.config)
        self.store.init()
        self.store.sync_codex_file(self.source)
        self.raw_before = {p: p.read_bytes() for p in self.root.joinpath('raw').rglob('*.md')}

    def write(self, events):
        self.source.write_text(''.join(json.dumps(e) + '\n' for e in events))

    def test_exact_prefix_relocation_preserves_raw_and_is_stale_guarded(self):
        self.write([self.events[0], {'type': 'ignored', 'payload': {}}, *self.events[1:],
                    {'timestamp': '2026-09-08T01:00:02Z', 'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'next'}}])
        proposal = plan(self.root, self.source, self.config)
        self.assertEqual(proposal['verified_records'], 2)
        self.assertEqual(proposal['pending_source_records'], 1)
        self.assertEqual(proposal['new_cursor']['message_last_line'], 4)
        self.assertEqual(apply(self.root, proposal)['status'], 'applied')
        self.assertEqual(self.raw_before, {p: p.read_bytes() for p in self.raw_before})
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            apply(self.root, proposal)

    def test_changed_visible_history_is_not_silently_replayed(self):
        self.events[1]['payload']['message'] = 'different question'
        self.write(self.events)
        with self.assertRaisesRegex(ValueError, 'prefix differs'):
            plan(self.root, self.source, self.config)

    def test_relocation_then_installed_native_capture_does_not_replay_legacy_prefix(self):
        ident = '019fb8f2-9a67-7b03-9474-6f92cd6b21a7'
        # Archive the same messages first, then emulate a source representation
        # change and a legacy cursor lacking the completed-item marker.
        cursor_path = self.root / 'imports/codex' / (ident + '.json')
        cursor = json.loads(cursor_path.read_text())
        cursor.pop('visible_message_format_version', None)
        cursor_path.write_text(json.dumps(cursor))
        def completed(text, role, stamp):
            item = {'type': role, 'content': [{'type': 'text', 'text': text}]}
            if role == 'AgentMessage':
                item['phase'] = 'final_answer'
            return {'timestamp': stamp, 'type': 'event_msg',
                    'payload': {'type': 'item_completed', 'item': item}}
        events = [self.events[0], {'type': 'ignored', 'payload': {}},
                  completed('question', 'UserMessage', '2026-09-08T01:00:00Z'),
                  completed('answer', 'AgentMessage', '2026-09-08T01:00:01Z'),
                  completed('next', 'UserMessage', '2026-09-08T01:00:02Z')]
        self.source.write_text(''.join(json.dumps(e, separators=(',', ':')) + '\n' for e in events))
        proposal = plan(self.root, self.source, self.config)
        apply(self.root, proposal)
        config = self.base / 'native-config.yaml'
        config.write_text('backup:\n  enabled: false\nautomation:\n  enabled: false\n')
        command = [candidate_collector(),
                   '--archive-root', str(self.root), '--config', str(config),
                   '--sessions-root', str(self.base), '--session-file', str(self.source), '--once']
        for _ in range(2):
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            records = self.store.read_raw_file(self.store.conversation_transcript_path('codex:' + ident))
            self.assertEqual([r['text'] for r in records], ['question', 'answer', 'next'])
            self.assertEqual(len({r['message_id'] for r in records}), 3)

    def test_missing_history_is_not_treated_as_complete(self):
        self.write(self.events[:-1])
        with self.assertRaisesRegex(ValueError, 'prefix differs'):
            plan(self.root, self.source, self.config)

    def test_completed_file_change_matches_legacy_and_native(self):
        changes = {'x.py': {'type': 'update', 'unified_diff': '-old\n+new\n'}}
        legacy = {'type': 'patch_apply_end', 'success': True, 'changes': changes}
        modern = {'type': 'item_completed', 'item': {'type': 'FileChange', 'status': 'completed', 'changes': changes}}
        expected = MemoryStore.summarize_file_change(legacy)
        self.assertEqual(MemoryStore.summarize_file_change(modern), expected)
        for status in ['failed', 'in_progress', None]:
            bad = {'type': 'item_completed', 'item': dict(modern['item'], status=status)}
            self.assertIsNone(MemoryStore.summarize_file_change(bad))
        self.assertIsNone(MemoryStore.summarize_file_change({'type': 'item_completed', 'item': []}))
        event = {'timestamp': '2026-09-08T01:00:02Z', 'type': 'event_msg', 'payload': modern}
        failed = {'timestamp': '2026-09-08T01:00:03Z', 'type': 'event_msg',
                  'payload': {'type': 'item_completed', 'item': dict(modern['item'], status='failed')}}
        malformed = {'timestamp': '2026-09-08T01:00:04Z', 'type': 'event_msg',
                     'payload': {'type': 'item_completed', 'item': []}}
        invalid_events = []
        for changes in [{'x.py': None}, {'x.py': {'unified_diff': 123}}, {'x.py': {'content': []}}, {'x.py': {'type': False}}]:
            invalid_payload = {'type': 'item_completed', 'item': dict(modern['item'], changes=changes)}
            self.assertIsNone(MemoryStore.summarize_file_change(invalid_payload))
            invalid_events.append({'timestamp': '2026-09-08T01:00:05Z', 'type': 'event_msg', 'payload': invalid_payload})
        self.write([*self.events, event, failed, malformed, *invalid_events])
        config = self.base / 'native-config.yaml'
        config.write_text('backup:\n  enabled: false\nautomation:\n  enabled: false\n')
        command = [candidate_collector(), '--archive-root', str(self.root),
                   '--config', str(config), '--sessions-root', str(self.base),
                   '--session-file', str(self.source), '--once']
        for _ in range(2):
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            records = self.store.read_raw_file(self.store.conversation_transcript_path('codex:019fb8f2-9a67-7b03-9474-6f92cd6b21a7'))
            self.assertEqual([r['text'] for r in records], ['question', 'answer', expected])
        proposal = plan(self.root, self.source, self.config)
        self.assertEqual(proposal['verified_records'], 3)
        self.assertEqual(proposal['pending_source_records'], 0)

    def test_inventory_preserves_occurrences_and_refuses_user_gaps(self):
        def record(text, line, speaker='tool'):
            return {'speaker': speaker, 'timestamp': '2026-09-08T01:00:00Z',
                    'source': {'phase': 'tool_activity' if speaker == 'tool' else 'user', 'line': line},
                    'text': text, 'message_id': str(line)}
        archived = [record('a', 1), record('a', 2), record('retained', 3)]
        current = [record('a', 10), record('gap', 11), record('a', 12)]
        anchor, pairs, gaps, retained = inventory_alignment(archived, current)
        self.assertEqual(anchor, 12)
        self.assertEqual([old['message_id'] for _, old in pairs], ['1', '2'])
        self.assertEqual([r['text'] for r in gaps], ['gap'])
        self.assertEqual([r['message_id'] for r in retained], ['3'])
        with self.assertRaisesRegex(ValueError, 'non-tool historical'):
            inventory_alignment(archived, [current[0], record('new user', 11, 'user'), current[-1]])
        with self.assertRaisesRegex(ValueError, 'No exact'):
            inventory_alignment(archived, [record('unknown', 10)])

    def test_history_plan_does_not_apply_unresolved_wal(self):
        wal = self.root / 'imports/codex/capture-wal.jsonl'
        wal.write_text(json.dumps({'transaction_id': 'test', 'phase': 'prepared',
                                  'intent': {'source_path': str(self.source.resolve())}}) + '\n')
        proposal = plan(self.root, self.source, self.config, reconcile_history=True)
        self.assertTrue(proposal['pending_wal_for_verified_coverage'])
        with self.assertRaisesRegex(ValueError, 'reviewed transactional recovery adapter'):
            apply(self.root, proposal)
        self.assertEqual(self.raw_before, {p: p.read_bytes() for p in self.raw_before})

    def test_prepared_wal_blocks_cursor_relocation(self):
        wal = self.root / 'imports/codex/capture-wal.jsonl'
        wal.write_text(json.dumps({'transaction_id': 'test', 'phase': 'prepared',
                                  'intent': {'source_path': str(self.source.resolve())}}) + '\n')
        with self.assertRaisesRegex(ValueError, 'Unresolved WAL'):
            plan(self.root, self.source, self.config)

    def test_retained_suffix_requires_unique_complete_anchor(self):
        def record(text, line):
            return {'speaker': 'user', 'timestamp': '2026-09-08T01:00:00Z',
                    'source': {'phase': 'user', 'line': line}, 'text': text}
        archived = [record(text, i) for i, text in enumerate(['old', 'a', 'b'], 1)]
        retained = [record(text, i) for i, text in enumerate(['a', 'b', 'new'], 8)]
        self.assertEqual(align(archived, retained), 9)
        with self.assertRaisesRegex(ValueError, 'unique complete suffix'):
            align(archived, [record('b', 8)])
        repeated = [record(text, i) for i, text in enumerate(['a', 'b', 'a', 'b'], 1)]
        with self.assertRaisesRegex(ValueError, 'unique complete suffix'):
            align(repeated, repeated)


if __name__ == '__main__':
    unittest.main()
