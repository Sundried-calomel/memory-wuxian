import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from memory_cli import MemoryStore, RAW_MARKER
from memory_identity import build_resolution, raw_digest, verified_resolution, projected_records
from memory_jobs import MaintenanceQueue, semantic_eligibility_payload
import summary_v2_identity_repair as repair


class HistoricalIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / '中文 日本語 ¥ 😀'
        self.base.mkdir()
        self.config_path = self.base / 'config.yaml'
        self.config_path.write_text('summary_v2:\n  enabled: true\nsummaries:\n  level_1_trigger_rounds: 1\nbackup:\n  enabled: false\nai_summary:\n  timeout_seconds: 900\n', encoding='utf-8')
        from memory_cli import load_simple_yaml
        self.store = MemoryStore(self.base / 'archive', load_simple_yaml(self.config_path))
        self.store.init()
        for line, speaker in enumerate(('user', 'assistant'), 1):
            self.store.append_message(speaker, '历史来源 ' + speaker, '2026-09-06T00:00:00+00:00',
                'codex:identity-fixture', speaker, None, False,
                source={'kind': 'codex-rollout-jsonl', 'session_id': 'fixture', 'path': '/source/会話.jsonl', 'line': line, 'phase': 'final_answer' if line == 2 else ''},
                complete_round=speaker == 'assistant')
        records = self.store.read_all_raw()
        self.raw_path = self.store.root / records[0]['_path']
        for record in records:
            item = {k: v for k, v in record.items() if k != '_path'}
            item['sequence'] += 2
            item['round_number'] = 2
            item['content_sha256'] = raw_digest(item)
            self.append_record(item)
        records = self.store.read_all_raw()
        state = self.store.load_state()
        job = self.store.build_level_1_job(state, records, 1, 2, 'original-duplicate-source', 'codex:identity-fixture')
        self.job_path = self.store.persist_job(state, job)
        self.old_bytes = self.job_path.read_bytes()
        self.raw_bytes = self.raw_path.read_bytes()
        self.queue = MaintenanceQueue(self.store.root)
        owner = self.queue.enqueue_semantic(semantic_eligibility_payload(self.job_path), max_attempts=4)
        owner.pop('created')
        owner.update(state='quarantined', attempts=4, last_error='duplicate source identity')
        self.queue._write(owner)
        self.owner = owner

    def append_record(self, record):
        with self.raw_path.open('a', encoding='utf-8', newline='') as stream:
            stream.write('\n' + RAW_MARKER + '\n```json\n' + json.dumps(record, ensure_ascii=False) + '\n```\n')

    def test_replacement_preserves_history_and_retires_quarantine_without_completion(self):
        plan = repair.preview(self.store, self.job_path)
        self.assertEqual(4, plan['physical_source_records'])
        self.assertEqual(2, plan['selected_source_records'])
        result = repair.apply(self.store, plan, plan['plan_sha256'])
        self.assertEqual('applied', result['status'])
        self.assertEqual(self.old_bytes, Path(result['retained_original']).read_bytes())
        self.assertEqual(self.raw_bytes, self.raw_path.read_bytes())
        self.assertEqual([], self.store.summary_records())
        self.assertEqual(0, self.queue.status()['quarantined'])
        self.assertEqual(0, self.queue.status()['counts']['completed'])
        active = self.store.pending_jobs()
        self.assertEqual([plan['replacement']['job_id']], [j['job_id'] for j in active])
        before = self.store.state_path.read_bytes()
        self.assertEqual('already-applied', repair.apply(self.store, plan, plan['plan_sha256'])['status'])
        self.assertEqual(before, self.store.state_path.read_bytes())
        self.assertEqual(self.raw_bytes, self.raw_path.read_bytes())

    def test_repair_recovers_after_original_retired_before_replacement_published(self):
        plan = repair.preview(self.store, self.job_path)
        write = repair.immutable_json
        def interrupted(path, value):
            if path.name == plan['replacement']['job_id'] + '.json':
                raise OSError('injected publication failure')
            return write(path, value)
        with patch.object(repair, 'immutable_json', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'publication'):
                repair.apply(self.store, plan, plan['plan_sha256'])
        self.assertEqual([], self.store.pending_jobs())
        self.assertEqual('applied', repair.apply(self.store, plan, plan['plan_sha256'])['status'])
        self.assertEqual(1, len(self.store.pending_jobs()))
        self.assertEqual(self.raw_bytes, self.raw_path.read_bytes())

    def test_non_equivalent_source_is_rejected_before_any_write(self):
        item = copy.deepcopy(self.store.read_all_raw()[-1])
        item.pop('_path')
        item['text'] = 'conflicting evidence'
        item['sequence'] = 5
        item['content_sha256'] = raw_digest(item)
        self.append_record(item)
        with self.assertRaisesRegex(ValueError, 'Conflicting message identity'):
            repair.preview(self.store, self.job_path)
        self.assertFalse((self.store.root / 'source-identity').exists())
        self.assertEqual(self.old_bytes, self.job_path.read_bytes())

    def test_occupied_replacement_id_fails_before_retiring_any_evidence(self):
        plan = repair.preview(self.store, self.job_path)
        occupied = self.store.pending_dir / (plan['replacement']['job_id'] + '.json')
        occupied.write_text('{"other":true}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'occupied'):
            repair.apply(self.store, plan, plan['plan_sha256'])
        self.assertEqual(self.old_bytes, self.job_path.read_bytes())
        self.assertEqual(1, self.queue.status()['quarantined'])
        self.assertFalse((self.store.root / 'source-identity').exists())

    def test_original_declared_ids_cannot_silently_disappear(self):
        original = json.loads(self.old_bytes)
        for ids in (original['source_message_ids'] + ['missing'], original['source_message_ids'][1:], list(reversed(original['source_message_ids']))):
            with self.subTest(ids=ids):
                changed = {**original, 'source_message_ids': ids}
                self.job_path.write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'message IDs disagree'):
                    repair.preview(self.store, self.job_path)

    def test_repaired_job_cannot_dispatch_after_resolution_is_lost(self):
        plan = repair.preview(self.store, self.job_path)
        result = repair.apply(self.store, plan, plan['plan_sha256'])
        (self.store.root / 'source-identity/resolution.json').unlink()
        import summary_v2_runtime as runtime
        with patch.object(runtime, 'call_engine') as engine:
            with self.assertRaises(FileNotFoundError):
                runtime.execute_job(self.store, self.config_path, Path(result['replacement_job']), create_backup=False)
        engine.assert_not_called()

    def test_repeat_apply_rejects_corrupted_commit_evidence(self):
        plan = repair.preview(self.store, self.job_path)
        repair.apply(self.store, plan, plan['plan_sha256'])
        committed = self.store.root / 'source-identity/repairs' / plan['plan_sha256'] / 'committed.json'
        committed.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            repair.apply(self.store, plan, plan['plan_sha256'])

    def test_published_replacement_cannot_run_before_repair_commits(self):
        plan = repair.preview(self.store, self.job_path)
        write = repair.immutable_json
        def interrupted(path, value):
            if path.name == 'committed.json':
                raise OSError('injected commit failure')
            return write(path, value)
        with patch.object(repair, 'immutable_json', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'commit failure'):
                repair.apply(self.store, plan, plan['plan_sha256'])
        import summary_v2_runtime as runtime
        target = self.store.pending_dir / (plan['replacement']['job_id'] + '.json')
        with patch.object(runtime, 'call_engine') as engine:
            result = runtime.execute_job(self.store, self.config_path, target, create_backup=False)
            self.assertEqual('identity-repair-incomplete', result['reason_code'])
            self.assertEqual('deferred', result['status'])
        engine.assert_not_called()
        import datetime as dt
        import semantic_dispatch as dispatch
        from memory_jobs import parse_iso
        moment = [dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)]
        queue = MaintenanceQueue(self.store.root, clock=lambda: moment[0])
        config = {**self.store.config, 'ai_summary': {**self.store.config['ai_summary'],
                  'worker_path': str(ROOT / 'scripts/semantic_worker.py')}}
        with patch.object(dispatch, 'MaintenanceQueue', return_value=queue), patch.object(dispatch, 'load_simple_yaml', return_value=config), patch.object(runtime, 'call_engine') as engine:
            for _ in range(4):
                result = dispatch.dispatch_job(self.store.root, self.config_path, target, create_backup=False, check_availability=False)
                self.assertEqual('identity-repair-incomplete', result['reason_code'])
                self.assertEqual(0, result['ai_invocations'])
                queued = queue._read(queue._path(result['maintenance_job_id']))
                self.assertEqual('semantic-ready', queued['state'])
                self.assertEqual(0, queued['attempts'])
                self.assertIsNone(queued['lease_owner'])
                moment[0] = parse_iso(queued['available_at']) + dt.timedelta(seconds=1)
            engine.assert_not_called()
        self.assertEqual('applied', repair.apply(self.store, plan, plan['plan_sha256'])['status'])
        from memory_identity import verify_job_binding
        verify_job_binding(self.store, plan['replacement'], self.store.read_all_raw())

    def test_resolution_requires_the_exact_approved_plan(self):
        plan = repair.preview(self.store, self.job_path)
        repair.apply(self.store, plan, plan['plan_sha256'])
        path = self.store.root / 'source-identity/repairs' / plan['plan_sha256'] / 'plan.json'
        path.write_text('{"replacement":{}}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'approved plan'):
            verified_resolution(self.store, self.store.read_all_raw())
        self.assertIn('duplicate raw message IDs', self.store.audit()['integrity_issues'])

    def test_retained_original_remains_required_after_commit(self):
        plan = repair.preview(self.store, self.job_path)
        result = repair.apply(self.store, plan, plan['plan_sha256'])
        Path(result['retained_original']).unlink()
        import summary_v2_runtime as runtime
        with patch.object(runtime, 'call_engine') as engine:
            with self.assertRaises(FileNotFoundError):
                runtime.execute_job(self.store, self.config_path, Path(result['replacement_job']), create_backup=False)
        engine.assert_not_called()

    def test_known_collision_members_and_activation_are_verified(self):
        plan = repair.preview(self.store, self.job_path)
        repair.apply(self.store, plan, plan['plan_sha256'])
        self.assertIsNotNone(verified_resolution(self.store, self.store.read_all_raw()))
        projected = projected_records(self.store, self.store.read_all_raw())
        self.assertEqual([3, 4], [r['sequence'] for r in projected])
        self.append_record({k: v for k, v in projected[-1].items() if k != '_path'})
        with self.assertRaisesRegex(ValueError, 'unreviewed collision'):
            verified_resolution(self.store, self.store.read_all_raw())

    def test_distinct_sequence_collision_is_retained_and_true_gap_is_separate(self):
        records = self.store.read_all_raw()
        item = copy.deepcopy(records[-1])
        item.pop('_path')
        item['message_id'] = 'different-id'
        item['round_number'] = 0
        item['content_sha256'] = raw_digest(item)
        self.append_record(item)
        audit = self.store.audit()
        self.assertIn('duplicate raw message sequences', audit['integrity_issues'])
        self.assertNotIn('raw message sequence gap', audit['integrity_issues'])
        plan = repair.preview(self.store, self.job_path)
        repair.apply(self.store, plan, plan['plan_sha256'])
        self.assertEqual(5, len(self.store.read_all_raw()))
        self.assertEqual(3, len(projected_records(self.store, self.store.read_all_raw())))
        self.assertNotIn('duplicate raw message sequences', self.store.audit()['integrity_issues'])
        item['sequence'] = 6
        item['message_id'] = 'future-id'
        item['content_sha256'] = raw_digest(item)
        self.append_record(item)
        self.assertIn('raw message sequence gap', self.store.audit()['integrity_issues'])

    def test_plain_append_does_not_invalidate_reviewed_historical_groups(self):
        plan = repair.preview(self.store, self.job_path)
        repair.apply(self.store, plan, plan['plan_sha256'])
        item = copy.deepcopy(self.store.read_all_raw()[-1])
        item.pop('_path')
        item.update(sequence=5, message_id='new-id', round_number=0)
        item['content_sha256'] = raw_digest(item)
        self.append_record(item)
        self.assertIsNotNone(verified_resolution(self.store, self.store.read_all_raw()))

    def test_replacement_runs_through_existing_isolated_core_and_typed_ingestion(self):
        plan = repair.preview(self.store, self.job_path)
        result = repair.apply(self.store, plan, plan['plan_sha256'])
        import summary_v2_runtime as runtime
        def fixture_engine(store, request, *, config_path=None):
            request_path = self.base / 'request.json'
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            proc = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', str(ROOT / 'tests/summary_v2_fixture_process.py'),
                str(request_path), str(store.root), str(runtime.runtime_root(store)), str(config_path)],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(0, proc.returncode, proc.stderr)
            return json.loads(proc.stdout)
        from semantic_worker import run_job
        with patch.object(runtime, 'call_engine', side_effect=fixture_engine):
            completed = run_job(self.store.root, self.config_path, Path(result['replacement_job']), False, create_backup=False, defer_derived_updates=True)
        self.assertEqual('ingested', completed['status'])
        self.assertEqual([], self.store.summary_records())
        self.assertEqual(self.raw_bytes, self.raw_path.read_bytes())
        self.assertEqual('already-applied', repair.apply(self.store, plan, plan['plan_sha256'])['status'])
        completion = self.store.root / 'summary-v2/completions' / (plan['replacement']['target_summary_id'] + '.json')
        valid_completion = completion.read_bytes()
        completion.write_text(json.dumps({'job_sha256': plan['replacement_job_sha256']}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'completion identity'):
            repair.apply(self.store, plan, plan['plan_sha256'])
        completion.write_bytes(valid_completion)
        (self.store.archive_dir / (plan['replacement']['job_id'] + '-ingested.json')).unlink()
        with self.assertRaises(FileNotFoundError):
            repair.apply(self.store, plan, plan['plan_sha256'])


if __name__ == '__main__':
    unittest.main()
