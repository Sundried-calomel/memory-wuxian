import json
from pathlib import Path
import tempfile
import unittest

from memory_cli import MemoryStore
from source_reconcile import plan, apply, align


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

    def test_missing_history_is_not_treated_as_complete(self):
        self.write(self.events[:-1])
        with self.assertRaisesRegex(ValueError, 'prefix differs'):
            plan(self.root, self.source, self.config)

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
