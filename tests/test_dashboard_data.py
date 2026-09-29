import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from dashboard import status_payload
from runtime import MemoryRuntime
from storage import atomic_write_json


class DashboardChecks(unittest.TestCase):
    def test_levels_titles_lifecycle_and_incremental_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = MemoryRuntime(base / 'archive', model=lambda source: {
                'text': 'Synthetic decision', 'source_refs': source['allowed_refs']})
            runtime.store.append_message('user', 'hello', conversation_id='codex:a', message_id='u')
            runtime.store.append_message('assistant', 'yes', conversation_id='codex:a', message_id='a')
            summary = runtime.summary.generate('codex:a')
            runtime.summary.generate('codex:a', children=[summary])
            # Imported archives can retain globally numbered rounds.
            with runtime.store.connection() as db:
                db.execute('UPDATE rounds SET next_round=20001 WHERE conversation=?', ('codex:a',))
            (base / 'sessions').mkdir()
            with sqlite3.connect(base / 'state_1.sqlite') as db:
                db.execute('CREATE TABLE threads(id,title,cwd,archived)')
                db.execute('INSERT INTO threads VALUES(?,?,?,?)', ('a', 'Actual title', '/project', 1))
            config = dict(sessions_root=str(base / 'sessions'))
            result = status_payload(runtime, config)
            self.assertEqual(result['totals']['summary_counts'], {'1': 1, '2': 1})
            self.assertEqual(result['totals']['archived_conversations'], 1)
            self.assertEqual(result['active_conversations'], [])
            self.assertEqual(result['conversations'][0]['title'], 'Actual title')
            self.assertEqual(result['conversations'][0]['project'], '/project')
            self.assertEqual(result['conversations'][0]['completed_rounds'], 1)
            self.assertIsNone(result['totals']['reported_total_tokens'])
            self.assertGreater(result['totals']['storage_bytes'], 0)
            runtime.store.append_message('user', 'next', conversation_id='codex:a', message_id='u2')
            runtime._dashboard_data.built_at = 0
            result = status_payload(runtime, config)
            self.assertEqual(result['totals']['messages'], 3)
            self.assertEqual(result['totals']['characters'], 12)

    def test_running_mode_and_errors_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = MemoryRuntime(Path(directory) / 'archive')
            atomic_write_json(runtime.store.root / 'live-status.json', dict(status='running', pid=123,
                attempt_at='2026-09-29T03:00:00+00:00', phase='summaries',
                collection={'errors': {'source': 'historical gap'}, 'pending_files': 2},
                summary_errors={'c': 'model unavailable'}, summary_progress={'remaining': 4}))
            with patch('dashboard.process_observation', return_value={'process_running': True}):
                result = status_payload(runtime, dict(sessions_root=directory, interval_seconds=60))
            self.assertEqual(result['collector']['mode'], 'active')
            self.assertEqual(result['health'], 'attention')
            self.assertEqual(result['debt_status']['debts']['coverage_debt']['quarantined'], 1)
            self.assertEqual(result['debt_status']['debts']['semantic_debt']['retry'], 1)

    def test_ledger_copy_not_double_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = MemoryRuntime(base / 'archive')
            runtime.store.append_message('user', 'hello', conversation_id='codex:a', message_id='u')
            ledger = dict(session_id='a', segment_id='s', measurement='codex-reported-model-usage',
                reported_usage={'total_tokens': 123}, updated_at='2026-01-01T00:00:00+00:00')
            for root in (base / 'old', runtime.store.root):
                atomic_write_json(root / 'imports/codex/token-usage/a.json', ledger)
            result = status_payload(runtime, dict(legacy_archive_root=str(base / 'old')))
            self.assertEqual(result['totals']['reported_total_tokens'], 123)
            self.assertTrue(result['token_usage']['historical'])


if __name__ == '__main__':
    unittest.main()
