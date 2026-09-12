"""Bounded execution with real projector/persistence and an explicit model fixture."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import summary_v2_backfill as engine
import summary_v2_worker as worker
import test_memory_summary_v2 as fixtures
from memory_atoms import _source_sha256


class RuntimeEngineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / '日本語 中文 😀'
        self.archive = self.root / 'archive'
        self.archive.mkdir(parents=True)
        self.output = self.root / 'output'
        self.config = self.root / 'config.yaml'
        self.config.write_text(f"ai_summary:\n  codex_cli_path_windows: '{sys.executable}'\n"
                               f"  codex_cli_path: '{sys.executable}'\n", encoding='utf-8')
        self.fixture = fixtures.SummaryV2Test()
        self.calls = []
        self.invalid = False
        self.timeout = False
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'MEMORY_WUXIAN_CODEX': sys.executable}).start()
        original_run = subprocess.run
        def version_only(command, *args, **kwargs):
            self.assertEqual([str(Path(sys.executable).resolve()), '--version'], list(command))
            return original_run(command, *args, **kwargs)
        patch.object(subprocess, 'run', side_effect=version_only).start()
        patch.object(engine, 'build_plan', side_effect=AssertionError('No historical planner')).start()
        patch.object(engine.MemoryStore, 'read_all_raw', side_effect=AssertionError('No archive scan')).start()
        def invoke_source(source, *args, **kwargs):
            def invoke(command, timeout, prompt):
                self.calls.append(source['parallel_summary_id'])
                if self.timeout:
                    raise worker.CodexInvocationError('one-shot summary-v2 model call timed out after 900s',
                        {'classification': 'infra-timeout', 'candidate_exists': False,
                         'candidate_sha256': None, 'elapsed_seconds': 900.1, 'returncode': None})
                if self.invalid:
                    return {'invalid': True}
                return (self.fixture.candidate(source) if source['summary_level'] == 1
                        else self.fixture.parent_candidate(source))
            return worker.run_source(source, *args, invoker=invoke, **kwargs)
        patch.object(engine, 'run_source', side_effect=invoke_source).start()

    def job(self, offset=0):
        return {**self.fixture.job(offset), 'target_summary_id': f'L1-{offset + 1:06d}'}

    def run_node(self, job, **kwargs):
        return engine.run_closed_node(self.archive, self.output, config_path=self.config,
                                      job=job, **kwargs)

    def test_direct_completion_replay_verifies_without_model(self):
        job = self.job()
        first = self.run_node(job)
        self.assertEqual('completed', first['status'], first)
        second = self.run_node(job)
        self.assertEqual('completed', second['status'], second)
        self.assertEqual(first['bundle'], second['bundle'])
        self.assertEqual(0, second['ai_invocations'])
        self.assertEqual(1, len(self.calls))

    def test_deadline_yields_before_claim_and_resumes(self):
        job = self.job()
        original_budget = engine.TickBudget
        def exhausted(**kwargs):
            budget = original_budget(**kwargs)
            budget.deadline = budget.clock()
            return budget
        with patch.object(engine, 'TickBudget', side_effect=exhausted):
            result = self.run_node(job)
        self.assertEqual('yielded', result['status'], result)
        state = json.loads(Path(result['state_path']).read_text('utf-8'))
        self.assertEqual({}, state['dispatches'])
        self.assertEqual(0, state['content_attempts'])
        self.assertEqual('completed', self.run_node(job)['status'])

    def test_impossible_configured_tick_is_rejected(self):
        with self.assertRaisesRegex(engine.SummaryV2Error, 'call timeout plus'):
            self.run_node(self.job(), tick_seconds=300)
        self.assertEqual([], self.calls)

    def test_configured_single_call_limit_is_respected(self):
        with self.config.open('a', encoding='utf-8') as output:
            output.write('  maximum_parallel_model_calls: 1\n')
        chunks = [self.job(i * 3) for i in range(2)]
        records = [record for chunk in chunks for record in chunk['source_records']]
        job = {**chunks[0], 'source_records': records,
               'source_message_ids': [record['message_id'] for record in records],
               'source_sha256': _source_sha256(records)}
        with patch.object(engine, '_decide_route', return_value={'route': 'l1-map-reduce-dag'}), \
             patch.object(engine, '_chunk_job', return_value=chunks):
            results = [self.run_node(job) for _ in range(3)]
        self.assertEqual(['yielded', 'yielded', 'completed'], [r['status'] for r in results])
        self.assertEqual([1, 1, 1], [r['ai_invocations'] for r in results])

    def test_content_failure_is_terminal_and_does_not_dispatch_again(self):
        self.invalid = True
        first = self.run_node(self.job())
        self.assertEqual('content-failed-terminal', first['node_state'], first)
        second = self.run_node(self.job())
        self.assertEqual('blocked', second['status'])
        self.assertEqual(0, second['ai_invocations'])
        self.assertEqual(1, len(self.calls))

    def test_ambiguous_claim_does_not_dispatch_again(self):
        job = self.job()
        with patch.object(engine, 'run_source', side_effect=OSError('lost model process')):
            first = self.run_node(job)
        self.assertEqual('infra-blocked', first['node_state'])
        second = self.run_node(job)
        self.assertEqual('blocked', second['status'])
        self.assertEqual(0, len(self.calls))

    def timeout_node(self, job, **kwargs):
        self.timeout = True
        first = self.run_node(job, **kwargs)
        self.timeout = False
        self.assertEqual('infra-blocked', first['node_state'], first)
        path = Path(first['state_path'])
        return path, path.read_bytes()

    def test_timeout_routes_to_existing_rescue_in_bounded_waves(self):
        chunks = [self.job(i * 3) for i in range(3)]
        records = [record for chunk in chunks for record in chunk['source_records']]
        job = {**chunks[0], 'source_records': records,
               'source_message_ids': [record['message_id'] for record in records],
               'source_sha256': _source_sha256(records)}
        path, before = self.timeout_node(job)
        with patch.object(engine, '_chunk_job', return_value=chunks):
            maps = self.run_node(job)
            self.assertEqual(('yielded', 3), (maps['status'], maps['ai_invocations']), maps)
            final = self.run_node(job)
            self.assertEqual(('completed', 1), (final['status'], final['ai_invocations']), final)
            replay = self.run_node(job)
            self.assertEqual(('completed', 0), (replay['status'], replay['ai_invocations']), replay)
        self.assertEqual(before, path.read_bytes())
        state = json.loads(Path(final['state_path']).read_text('utf-8'))
        self.assertEqual(engine.MAP_RESCUE_REVISION, state['revision'])
        self.assertEqual(engine.file_sha256(path), state['runtime_recovery']['state_sha256'])
        self.assertEqual(5, len(self.calls))  # failed direct + three maps + final
        engine.describe_completed_node(final)

    def test_timeout_evidence_drift_is_not_a_new_model_attempt(self):
        job = self.job()
        path, before = self.timeout_node(job)
        state = json.loads(before)
        for key in ('source_sha256', 'config_sha256', 'job_sha256'):
            with self.subTest(key=key):
                broken = json.loads(before)
                broken['runtime_request'][key] = '0' * 64
                engine.atomic_write_canonical_json(path, broken)
                with self.assertRaisesRegex(engine.SummaryV2Error, 'predecessor request'):
                    self.run_node(job)
        path.write_bytes(before)
        diagnostic_path = engine._rescue_artifact_root(self.output, 'normal', engine.RUNNER_REVISION,
                                                      job['target_summary_id']) / 'diagnostic.json'
        diagnostic_before = diagnostic_path.read_bytes()
        diagnostic = json.loads(diagnostic_before)
        diagnostic['stage_binding']['prompt_sha256'] = '0' * 64
        engine.atomic_write_canonical_json(diagnostic_path, diagnostic)
        with self.assertRaisesRegex(engine.SummaryV2Error, 'claim or diagnostic'):
            self.run_node(job)
        diagnostic_path.write_bytes(diagnostic_before)
        state['binding']['codex_sha256'] = '0' * 64
        engine.atomic_write_canonical_json(path, state)
        with self.assertRaisesRegex(engine.SummaryV2Error, 'runtime or diagnostic'):
            self.run_node(job)
        self.assertEqual(1, len(self.calls))

    def test_timeout_with_candidate_remains_blocked(self):
        job = self.job()
        self.timeout_node(job)
        diagnostic_path = engine._rescue_artifact_root(self.output, 'normal', engine.RUNNER_REVISION,
                                                      job['target_summary_id']) / 'diagnostic.json'
        diagnostic = json.loads(diagnostic_path.read_text('utf-8'))
        diagnostic.update(candidate_exists=True, candidate_sha256='a' * 64)
        engine.atomic_write_canonical_json(diagnostic_path, diagnostic)
        second = self.run_node(job)
        self.assertEqual(('blocked', 0), (second['status'], second['ai_invocations']))
        self.assertEqual(1, len(self.calls))

    def test_rescue_rejects_changed_predecessor_between_ticks(self):
        job = self.job()
        path, before = self.timeout_node(job)
        first = self.run_node(job)
        self.assertEqual('yielded', first['status'], first)
        state = json.loads(before)
        state['unexpected_mutation'] = True
        engine.atomic_write_canonical_json(path, state)
        count = len(self.calls)
        with self.assertRaisesRegex(engine.SummaryV2Error, 'predecessor evidence changed'):
            self.run_node(job)
        self.assertEqual(count, len(self.calls))

    def test_missing_predecessor_never_recreates_the_direct_claim(self):
        job = self.job()
        path, _ = self.timeout_node(job)
        self.assertEqual('yielded', self.run_node(job)['status'])
        diagnostic_path = engine._rescue_artifact_root(self.output, 'normal', engine.RUNNER_REVISION,
                                                      job['target_summary_id']) / 'diagnostic.json'
        path.unlink()
        count = len(self.calls)
        with self.assertRaisesRegex(engine.SummaryV2Error, 'state is missing'):
            self.run_node(job)
        diagnostic_path.unlink()
        with self.assertRaisesRegex(engine.SummaryV2Error, 'intact timeout predecessor'):
            self.run_node(job)
        self.assertFalse(path.exists())
        self.assertEqual(count, len(self.calls))

    def test_candidate04_timeout_uses_new_rescue_without_rebinding_old_state(self):
        job = self.job()
        path, before = self.timeout_node(job)
        diagnostic_path = engine._rescue_artifact_root(self.output, 'normal', engine.RUNNER_REVISION,
                                                      job['target_summary_id']) / 'diagnostic.json'
        state = json.loads(before)
        diagnostic = json.loads(diagnostic_path.read_text('utf-8'))
        predecessor = '1ab62310e8473039e1207fb0534d91d68c56434ee1596e0aaf3edd1f8f1a8f21'
        state['binding']['runner_sha256'] = predecessor
        state['dispatches']['final/direct']['runner_sha256'] = predecessor
        diagnostic['stage_binding']['runner_sha256'] = predecessor
        engine.atomic_write_canonical_json(path, state)
        engine.atomic_write_canonical_json(diagnostic_path, diagnostic)
        frozen = path.read_bytes(), diagnostic_path.read_bytes()
        self.assertEqual('yielded', self.run_node(job)['status'])
        self.assertEqual('completed', self.run_node(job)['status'])
        self.assertEqual(frozen, (path.read_bytes(), diagnostic_path.read_bytes()))

    def test_rescue_timeout_is_terminal_for_the_rescue_revision(self):
        job = self.job()
        self.timeout_node(job)
        self.timeout = True
        rescue = self.run_node(job)
        self.assertEqual('infra-blocked', rescue['node_state'], rescue)
        count = len(self.calls)
        self.timeout = False
        replay = self.run_node(job)
        self.assertEqual(('blocked', 0), (replay['status'], replay['ai_invocations']))
        self.assertEqual(count, len(self.calls))

    def test_parent_timeout_uses_existing_parent_rescue_revision(self):
        children = [worker.load_sidecar(Path(self.run_node(self.job(i * 3))['bundle'])) for i in range(2)]
        job = {'job_id': 'job-parent-timeout', 'target_summary_id': 'L2-000001',
               'summary_level': 2, 'conversation_id': children[0]['conversation_id'],
               'source_summaries': [c['parallel_summary_id'] for c in children]}
        path, before = self.timeout_node(job, children=children)
        final = self.run_node(job, children=children)
        self.assertEqual(('completed', 1), (final['status'], final['ai_invocations']), final)
        self.assertEqual(engine.PARENT_RESCUE_REVISION, json.loads(Path(final['state_path']).read_text('utf-8'))['revision'])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(0, self.run_node(job, children=children)['ai_invocations'])

    def test_parent_timeout_with_four_children_uses_bounded_maps_then_final(self):
        children = [worker.load_sidecar(Path(self.run_node(self.job(i * 3))['bundle'])) for i in range(4)]
        job = {'job_id': 'job-parent-maps-timeout', 'target_summary_id': 'L2-000001',
               'summary_level': 2, 'conversation_id': children[0]['conversation_id'],
               'source_summaries': [c['parallel_summary_id'] for c in children]}
        path, before = self.timeout_node(job, children=children)
        results = []
        for _ in range(5):
            result = self.run_node(job, children=children)
            results.append(result)
            self.assertLessEqual(result['ai_invocations'], 3)
            self.assertIn(result['status'], {'yielded', 'completed'}, result)
            if result['status'] == 'completed':
                break
        self.assertEqual('completed', results[-1]['status'], results)
        self.assertGreaterEqual(len(results), 2)
        state = json.loads(Path(result['state_path']).read_text('utf-8'))
        self.assertTrue(state['maps'])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(0, self.run_node(job, children=children)['ai_invocations'])

    def test_corrupt_completed_receipt_fails_closed(self):
        job = self.job()
        first = self.run_node(job)
        path = Path(first['state_path'])
        state = json.loads(path.read_text('utf-8'))
        state['completed_receipt']['source_sha256'] = '0' * 64
        engine.atomic_write_canonical_json(path, state)
        second = self.run_node(job)
        self.assertEqual('blocked', second['status'], second)
        self.assertEqual(1, len(self.calls))

    def test_changed_markdown_cannot_be_rebound_after_core_completion(self):
        result = self.run_node(self.job())
        (Path(result['bundle']) / 'summary.md').write_text('changed readable Markdown', encoding='utf-8')
        with self.assertRaisesRegex(engine.SummaryV2Error, 'canonical projection'):
            engine.describe_completed_node(result)
        self.assertEqual(1, len(self.calls))

    def test_five_maps_take_two_map_ticks_then_final(self):
        chunks = [self.job(i * 3) for i in range(5)]
        records = [record for chunk in chunks for record in chunk['source_records']]
        job = {**chunks[0], 'source_records': records,
               'source_message_ids': [record['message_id'] for record in records],
               'source_sha256': _source_sha256(records)}
        route = {'route': 'l1-map-reduce-dag'}
        with patch.object(engine, '_decide_route', return_value=route), \
             patch.object(engine, '_chunk_job', return_value=chunks):
            results = [self.run_node(job) for _ in range(3)]
            self.assertEqual(['yielded', 'yielded', 'completed'], [r['status'] for r in results], results)
            self.assertEqual([3, 2, 1], [r['ai_invocations'] for r in results])
            replay = self.run_node(job)
            self.assertEqual('completed', replay['status'], replay)
            self.assertEqual(0, replay['ai_invocations'])
            path = Path(replay['state_path'])
            state = json.loads(path.read_text('utf-8'))
            original_state = path.read_bytes()
            del state['dispatches']['map/map-001']
            engine.atomic_write_canonical_json(path, state)
            claim_only = self.run_node(job)
            self.assertEqual('blocked', claim_only['status'], claim_only)
            self.assertEqual(0, claim_only['ai_invocations'])
            path.write_bytes(original_state)
            state = json.loads(original_state)
            del state['maps']['map-001']
            del state['dispatches']['map/map-001']
            engine.atomic_write_canonical_json(path, state)
            broken = self.run_node(job)
            self.assertEqual('blocked', broken['status'], broken)
            self.assertEqual(0, broken['ai_invocations'])
        self.assertEqual(6, len(self.calls))

    def test_real_failure_wins_over_yield_and_siblings_drain(self):
        barrier = threading.Barrier(3)
        saved = []
        def work(item):
            barrier.wait(timeout=5)
            if item == 0:
                raise engine.BudgetYield('fixture')
            if item == 1:
                raise engine.ContentStageFailure('invalid', {'model_called': True})
            saved.append(item)
            return item
        budget = engine.TickBudget()
        token = engine._runtime_tick.set(budget)
        try:
            with self.assertRaises(engine.ContentStageFailure):
                list(engine._bounded_parallel_results([0, 1, 2, 3], work))
        finally:
            engine._runtime_tick.reset(token)
        self.assertEqual([2], saved)

    def test_runtime_uses_one_config_snapshot(self):
        job = self.job()
        original = engine._execute_model_node
        def changed_on_disk(*args, **kwargs):
            self.config.write_text('ai_summary:\n  timeout_seconds: 1800\n', encoding='utf-8')
            return original(*args, **kwargs)
        with patch.object(engine, '_execute_model_node', side_effect=changed_on_disk):
            result = self.run_node(job)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(1, len(self.calls))


if __name__ == '__main__':
    unittest.main()
