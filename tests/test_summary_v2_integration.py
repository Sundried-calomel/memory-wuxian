import hashlib
import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from memory_cli import MemoryStore, load_simple_yaml
from memory_summary_v2_links import SummaryV2Links, digest
from semantic_worker import run_job
from memory_jobs import MaintenanceQueue, semantic_eligibility_payload
import summary_v2_runtime as runtime
from summary_v2_restore import preview_restore, apply_restore


class SummaryV2IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '中文 日本語 ¥ 😀'
        self.root.mkdir()
        self.archive = self.root / 'archive'
        self.config_path = self.root / 'config.yaml'
        self.config_path.write_text('summary_v2:\n  enabled: true\n'
                                   'summaries:\n  level_1_trigger_rounds: 1\n  higher_level_trigger_count: 10\n'
                                   'backup:\n  enabled: false\n'
                                   f"ai_summary:\n  codex_cli_path_windows: '{sys.executable}'\n"
                                   f"  codex_cli_path: '{sys.executable}'\n", encoding='utf-8')
        with self.config_path.open('a', encoding='utf-8') as output:
            output.write(f"  worker_path: '{(ROOT / 'scripts/semantic_worker.py').as_posix()}'\n")
        self.config = load_simple_yaml(self.config_path)
        self.store = MemoryStore(self.archive, self.config)
        self.store.init()
        self.invocations = []
        original = runtime.call_engine
        def fixture_engine(store, request, *, config_path=None):
            if config_path is None:
                return original(store, request)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'request.json'
                path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8',
                                         str(ROOT / 'tests/summary_v2_fixture_process.py'), str(path),
                                         str(store.root), str(runtime.runtime_root(store)), str(config_path)],
                                        capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(0, result.returncode, result.stderr)
                value = json.loads(result.stdout)
                self.invocations.append(value['ai_invocations'])
                return value
        self.patch = patch.object(runtime, 'call_engine', side_effect=fixture_engine)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def append_round(self, number):
        for speaker in ('user', 'assistant'):
            self.store.append_message(speaker=speaker, text=f'摘要来源 第 {number} 轮 {speaker} 日本語 😀',
                                      timestamp=f'2026-09-06T01:{number:02d}:00+09:00',
                                      conversation_id='codex:fixture', message_id=f'{speaker}-{number}',
                                      reply_to=None, allow_secrets=False, complete_round=speaker == 'assistant')

    def execute(self, path, snapshot=None):
        return run_job(self.archive, self.config_path, path, False, create_backup=False,
                       source_snapshot=snapshot, defer_derived_updates=True)

    def test_l1_ingestion_keeps_v1_empty_and_recovers_coverage(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        snapshot = self.store.build_summary_source_snapshot()
        raw_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.store.raw_dir.rglob('*') if p.is_file()}
        self.assertTrue(raw_before)
        with patch.object(MemoryStore, 'read_all_raw', side_effect=AssertionError('Must reuse snapshot')):
            result = self.execute(path, snapshot)
        self.assertEqual('ingested', result['status'], result)
        self.assertFalse(path.exists())
        self.assertEqual([], self.store.summary_records())
        self.assertEqual(1, self.store.finalize_summary_batch([result])['summaries'])
        recovered = self.store.build_recovered_state()
        self.assertEqual(1, recovered['last_summarized_rounds']['codex:fixture'])
        self.assertEqual(2, recovered['next_summary_ids']['1'])
        self.assertEqual(raw_before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in raw_before})
        self.assertIsNone(self.store.make_summary_job())
        self.assertEqual(1, self.store.status()['summary_counts']['1'])
        self.assertEqual(1, self.store.status()['summary_v2_counts']['1'])
        legacy_same_alias = [{'summary_id': 'L1-000001', 'level': 1, 'conversation_id': 'codex:fixture'}]
        self.assertEqual(1, len(SummaryV2Links(self.store).effective_summary_records(legacy_same_alias)))
        from memory_federation import FederationManager
        self.assertFalse(any(r['artifact_type'] == 'summary' for r in FederationManager(self.store).local_artifacts().values()))

    def test_tenth_child_enqueues_one_parent_and_ingests(self):
        for number in range(1, 11):
            self.append_round(number)
            result = self.execute(self.store.make_summary_job())
            self.assertEqual('ingested', result['status'], result)
        from runtime_effect_gate import semantic_parent_debt
        self.assertEqual(1, len(semantic_parent_debt(self.store)))
        parent = self.store.make_summary_job()
        self.assertEqual([], semantic_parent_debt(self.store))
        job = json.loads(parent.read_text('utf-8'))
        self.assertEqual(2, job['summary_level'])
        self.assertEqual(10, len(job['source_summaries']))
        self.assertIsNone(self.store.make_summary_job())
        result = self.execute(parent)
        self.assertEqual('ingested', result['status'], result)
        records = SummaryV2Links(self.store).read_completions()
        self.assertEqual(11, len(records))
        self.assertEqual(10, len(records[-1]['source']['children']))
        self.assertEqual(10, self.store.status()['grouped_child_summaries'])

    def test_wrong_conversation_fails_before_engine(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        job = json.loads(path.read_text('utf-8'))
        job['conversation_id'] = 'codex:wrong'
        path.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'conversation changed'):
            self.execute(path)
        self.assertEqual([], self.invocations)

    def test_duplicate_raw_identity_is_rejected_before_model(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        job = json.loads(path.read_text('utf-8'))
        job['source_message_ids'].append(job['source_message_ids'][0])
        path.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            self.execute(path)
        self.assertEqual([], self.invocations)

    def test_isolated_inspection_does_not_import_core_into_mainline(self):
        # Exercise the production bootstrap, without model dispatch or archive writes.
        result = runtime.call_engine(self.store, {'children': []})
        self.assertEqual('verified', result['status'])
        self.assertNotIn('summary_v2_backfill', sys.modules)

    def test_completed_link_recovers_after_commit_tail_crash_without_engine(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        with patch.object(SummaryV2Links, 'finish_commit', side_effect=RuntimeError('synthetic crash')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
                self.execute(path)
        self.assertEqual([1], self.invocations)
        result = self.execute(path)
        self.assertTrue(result['recovered_commit'])
        self.assertEqual([1], self.invocations)
        self.assertFalse(path.exists())

    def test_yield_is_not_failed_and_restored_pause_requires_attention(self):
        import semantic_backfill
        self.append_round(1)
        path = self.store.make_summary_job()
        base = {'summary_format': 2, 'job_id': path.stem, 'ai_invocations': 0}
        for status, flags, expected in [
            ('yielded', {'reason': 'tick budget exhausted'}, 'catching-up'),
            ('deferred', {'reason': 'review restored checkpoint', 'reason_code': 'restored-checkpoint-review', 'requires_attention': True}, 'attention'),
        ]:
            with patch.object(semantic_backfill, 'dispatch_job', return_value={**base, 'status': status, **flags}):
                result = semantic_backfill.run_backfill(self.archive, self.config_path, 20, False)
            self.assertEqual(expected, result['status'], result)
            self.assertEqual(0, result['completed_jobs'])
            batch = json.loads((self.archive / 'maintenance/semantic-batch-state.json').read_text('utf-8'))
            if status == 'yielded':
                self.assertEqual(0, batch['failed_jobs'])
                self.assertEqual(1, result['yielded_jobs'])
            else:
                self.assertTrue(any(r.get('requires_attention') for r in result['skipped']))
    def test_archived_job_recovers_queue_completion_and_finalize(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        queue = MaintenanceQueue(self.archive)
        queued = queue.enqueue_semantic(semantic_eligibility_payload(path), max_attempts=4)
        self.execute(path)
        recovered = SummaryV2Links(self.store).reconcile_queue(queue)
        self.assertEqual(1, len(recovered))
        self.assertEqual('completed', next(j for j in queue.jobs() if j['job_id'] == queued['job_id'])['state'])
        self.assertEqual(1, self.store.finalize_summary_batch(recovered)['summaries'])
        self.assertEqual([], SummaryV2Links(self.store).reconcile_queue(queue))
        self.assertEqual([1], self.invocations)

    def test_disabled_v2_pending_cannot_reach_v1_invoker(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        self.config_path.write_text(self.config_path.read_text('utf-8').replace('enabled: true', 'enabled: false'), encoding='utf-8')
        for level in (1, 2):
            job = json.loads(path.read_text('utf-8'))
            job['summary_level'] = level
            path.write_text(json.dumps(job), encoding='utf-8')
            result = run_job(self.archive, self.config_path, path, False, create_backup=False,
                             invoker=lambda *a: self.fail('V1 invoker reached'))
            self.assertEqual('deferred', result['status'])
        self.assertEqual([], self.invocations)

    def test_valid_different_job_cannot_replace_inflight_source(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        self.append_round(2)
        snapshot = self.store.build_summary_source_snapshot()
        second_records = [r for r in snapshot['raw_by_id'].values() if r['round_number'] == 2]
        original = json.loads(path.read_text('utf-8'))
        replacement = self.store.build_level_1_job(self.store.load_state(), second_records, 2, 2,
                                                   'conversation:codex:fixture:rounds:2-2', 'codex:fixture')
        replacement.update(job_id=original['job_id'], target_summary_id=original['target_summary_id'], summary_format=2)
        fixture_engine = runtime.call_engine.side_effect
        def replace_after_execution(*args, **kwargs):
            result = fixture_engine(*args, **kwargs)
            path.write_text(json.dumps(replacement), encoding='utf-8')
            return result
        with patch.object(runtime, 'call_engine', side_effect=replace_after_execution):
            with self.assertRaisesRegex(ValueError, 'changed after dispatch'):
                self.execute(path, snapshot)
        self.assertEqual([], SummaryV2Links(self.store).read_completions())

    def test_v2_retrieve_and_capsule_use_typed_projection(self):
        self.append_round(1)
        self.execute(self.store.make_summary_job())
        text, metadata = self.store.retrieve('可追溯摘要')
        self.assertEqual(2, metadata['summary_format'])
        self.assertEqual('verified', metadata['verification'])
        self.assertIn('accepted_decision', text)
        self.assertIn('user-1', text)
        telemetry = {'conversation_id': 'codex:fixture', 'refresh_id': 'fixture', 'capsule_token_budget': 1000}
        with patch.object(self.store, 'context_refresh_telemetry', return_value=telemetry):
            capsule, meta = self.store.context_capsule()
        self.assertIn('Summary V2', capsule)
        self.assertEqual(['L1-000001'], meta['summary_ids'])
        self.assertLessEqual(len(capsule.encode('utf-8')), telemetry['capsule_token_budget'])
        self.assertIn('Recent Task State', capsule)

    def test_v2_hit_does_not_hide_new_unsummarized_raw_or_policy_search(self):
        self.append_round(1)
        self.execute(self.store.make_summary_job())
        for speaker in ('user', 'assistant'):
            self.store.append_message(
                speaker=speaker, text='可追溯摘要 alpha beta 后续修正必须先核验最新原文',
                timestamp='2026-09-06T01:02:00+09:00', conversation_id='codex:fixture',
                message_id='new-' + speaker, reply_to=None, allow_secrets=False,
                complete_round=speaker == 'assistant')
        for mode in ('historical', 'current-policy'):
            with self.subTest(mode=mode):
                text, metadata = self.store.retrieve('可追溯摘要 beta', mode=mode)
                self.assertEqual(2, metadata['summary_format'])
                self.assertIn('accepted_decision', text)
                self.assertIn('后续修正必须先核验最新原文', text)
                self.assertIn('new-user', [m['message_id'] for m in metadata['raw_matches']])
                if mode == 'current-policy':
                    self.assertIn('Policy Validity', text)
                    self.assertIn('newest verified raw matches', text)

    def test_backup_restore_retains_exact_v2_bytes_at_new_paths(self):
        self.append_round(1)
        self.execute(self.store.make_summary_job())
        self.store.config['backup'] = {'enabled': True, 'directory': str(self.root / 'backups')}
        snapshot = self.store.create_backup_snapshot('synthetic-v2-roundtrip')
        restored_archive = self.root / 'restored archive'
        shutil.copytree(snapshot, restored_archive)
        target = self.root / 'restored bundles'
        plan = preview_restore(snapshot, restored_archive, target)
        first = apply_restore(plan)
        self.assertEqual('restored', first['status'])
        self.assertEqual('no-change', apply_restore(plan)['status'])
        restored = MemoryStore(restored_archive, {'backup': {'enabled': False}})
        before = SummaryV2Links(self.store).read_completions()
        after = SummaryV2Links(restored).read_completions()
        self.assertEqual(before, after)
        self.assertEqual(SummaryV2Links(self.store).read_bundle(before[0]), SummaryV2Links(restored).read_bundle(after[0]))
        self.assertEqual(1, restored.build_recovered_state()['last_summarized_rounds']['codex:fixture'])

    def test_adoption_is_model_free_and_second_apply_is_noop(self):
        self.append_round(1)
        completed = self.execute(self.store.make_summary_job())
        # A separate archive fixture contains the same immutable source bytes but no links.
        adopter_root = self.root / 'adopter'
        adopter = MemoryStore(adopter_root, {**self.config, 'summary_v2': {
            'enabled': False, 'bundle_roots': {'legacy': str(runtime.runtime_root(self.store))}}})
        adopter.init()
        shutil.copytree(self.store.raw_dir, adopter.raw_dir, dirs_exist_ok=True)
        links = SummaryV2Links(adopter)
        before_calls = list(self.invocations)
        prepared = links.prepare_adoption([Path(completed['bundle'])], [], adopter.build_summary_source_snapshot())
        with patch.object(adopter, 'pending_jobs', return_value=[{'target_summary_id': prepared[0]['target_summary_id']} ]):
            with self.assertRaisesRegex(ValueError, 'pending summary alias'):
                links.apply_adoption(prepared)
        self.assertEqual([], links.read_completions())
        self.assertEqual(1, links.apply_adoption(prepared)['created'])
        files = {p: p.read_bytes() for p in adopter.root.rglob('*') if p.is_file()}
        self.assertEqual('no-change', links.apply_adoption(prepared)['status'])
        self.assertEqual(files, {p: p.read_bytes() for p in files})
        self.assertEqual(before_calls, self.invocations)
        self.assertEqual(1, adopter.build_recovered_state()['last_summarized_rounds']['codex:fixture'])

    def test_scheduling_snapshot_and_rebuild_do_not_read_historical_bundle_bodies(self):
        self.append_round(1)
        self.execute(self.store.make_summary_job())
        with patch.object(SummaryV2Links, 'read_bundle', side_effect=AssertionError('Historical body rescan')):
            snapshot = self.store.build_summary_source_snapshot()
            self.assertEqual(1, len(snapshot['summary_v2_by_alias']))
            self.assertIsNone(self.store.make_summary_job())
            self.assertEqual(1, self.store.build_recovered_state()['last_summarized_rounds']['codex:fixture'])

    def test_adoption_repairs_tail_after_last_link_and_rejects_changed_receipt(self):
        self.append_round(1)
        completed = self.execute(self.store.make_summary_job())
        adopter = MemoryStore(self.root / 'adopter-crash', {**self.config, 'summary_v2': {
            'enabled': False, 'bundle_roots': {'legacy': str(runtime.runtime_root(self.store))}}})
        adopter.init()
        shutil.copytree(self.store.raw_dir, adopter.raw_dir, dirs_exist_ok=True)
        links = SummaryV2Links(adopter)
        prepared = links.prepare_adoption([Path(completed['bundle'])], [], adopter.build_summary_source_snapshot())
        with patch.object(adopter, 'save_state', side_effect=RuntimeError('adoption tail crash')):
            with self.assertRaisesRegex(RuntimeError, 'adoption tail crash'):
                links.apply_adoption(prepared)
        self.assertEqual(1, len(links.read_completions()))
        self.assertEqual(0, links.apply_adoption(prepared)['created'])
        self.assertEqual(1, adopter.load_state()['last_summarized_rounds']['codex:fixture'])
        receipt = next((adopter.root / 'summary-v2/adoptions').glob('*.json'))
        receipt.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'receipt changed'):
            links.apply_adoption(prepared)
        self.assertEqual([1], self.invocations)

    def test_completed_queue_repairs_missing_derived_finalization(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        queue = MaintenanceQueue(self.archive)
        queue.enqueue_semantic(semantic_eligibility_payload(path), max_attempts=4)
        self.execute(path)
        links = SummaryV2Links(self.store)
        self.assertEqual(1, len(links.reconcile_queue(queue)))
        # Simulate exit after queue completion but before the batch finalizer.
        recovered = links.reconcile_queue(queue)
        self.assertEqual(1, len(recovered))
        self.store.finalize_summary_batch(recovered)
        self.assertEqual([], links.reconcile_queue(queue))
        self.assertEqual([1], self.invocations)

    def test_backup_covers_core_completion_before_mainline_commit_and_checks_claim(self):
        self.append_round(1)
        path = self.store.make_summary_job()
        with patch.object(SummaryV2Links, 'complete_job', side_effect=RuntimeError('before commit')):
            with self.assertRaisesRegex(RuntimeError, 'before commit'):
                self.execute(path)
        destination = self.root / 'checkpoint snapshot'
        shutil.copytree(self.archive, destination)
        links = SummaryV2Links(self.store)
        result = links.stage_backup(destination)
        self.assertEqual(1, result['objects'])
        self.assertEqual(1, result['checkpoints'])
        state_path = next((runtime.runtime_root(self.store) / 'backfill/rescue/node-state').glob('*/*.json'))
        state = json.loads(state_path.read_text('utf-8'))
        state['completed_receipt']['prompt_sha256'] = '0' * 64
        receipt = state['completed'].get('receipt', state['completed'].get('stage_receipt'))
        receipt['prompt_sha256'] = '0' * 64
        state_path.write_text(json.dumps(state), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'dispatch claim'):
            links.stage_backup(destination)
        self.assertEqual([1], self.invocations)

    def test_restore_rejects_extra_completions_and_unsafe_checkpoint_path(self):
        self.append_round(1)
        self.execute(self.store.make_summary_job())
        self.store.config['backup'] = {'enabled': True, 'directory': str(self.root / 'backups')}
        snapshot = self.store.create_backup_snapshot('restore-negative')
        restored = self.root / 'restore-negative'
        shutil.copytree(snapshot, restored)
        extra = restored / 'summary-v2/completions/L1-999999.json'
        extra.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'completion set'):
            preview_restore(snapshot, restored, self.root / 'new objects')
        # A different clean fixture tests an authenticated manifest with an unsafe path.
        clean = self.root / 'restore-clean'
        shutil.copytree(snapshot, clean)
        manifest_path = snapshot / 'summary-v2/backup-bundles.json'
        manifest = json.loads(manifest_path.read_text('utf-8'))
        manifest['checkpoints'] = [{'relative_path': '../escape', 'sha256': '0' * 64}]
        manifest['manifest_sha256'] = digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unsafe restored checkpoint'):
            preview_restore(snapshot, clean, self.root / 'new objects')

    def test_real_batch_reuses_snapshot_finalizes_once_and_drains_new_backup_debt(self):
        import semantic_backfill
        from semantic_dispatch import dispatch_job
        self.append_round(1)
        with self.config_path.open('a', encoding='utf-8') as output:
            output.write(f"backup:\n  enabled: true\n  directory: '{(self.root / 'batch backups').as_posix()}'\n")
        snapshot_builder = MemoryStore.build_summary_source_snapshot
        finalizer = MemoryStore.finalize_summary_batch
        backup_builder = MemoryStore.create_backup_snapshot
        def dispatch(*args, **kwargs):
            return dispatch_job(*args, **kwargs, availability_probe=lambda *_: (True, 'synthetic fixture'))
        with patch.object(MemoryStore, 'build_summary_source_snapshot', autospec=True, side_effect=snapshot_builder) as snapshots, \
             patch.object(MemoryStore, 'finalize_summary_batch', autospec=True, side_effect=finalizer) as finalizations, \
             patch.object(MemoryStore, 'create_backup_snapshot', autospec=True, side_effect=backup_builder) as backups, \
             patch.object(semantic_backfill, 'dispatch_job', side_effect=dispatch):
            result = semantic_backfill.run_backfill(self.archive, self.config_path, 20, False)
        self.assertEqual(1, result['completed_jobs'], result)
        self.assertEqual(1, snapshots.call_count)
        self.assertEqual(1, finalizations.call_count)
        self.assertEqual(1, backups.call_count)
        self.assertTrue(result['backup_debt_drained'], result)
        self.assertEqual([1], self.invocations)


if __name__ == '__main__':
    unittest.main()
