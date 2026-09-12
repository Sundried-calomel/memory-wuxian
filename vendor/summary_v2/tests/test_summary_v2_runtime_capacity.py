"""Real oversized route and multilevel compaction, with fixture model calls only."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import test_summary_v2_runtime_engine as support

engine, worker = support.engine, support.worker


class RuntimeCapacityTest(support.RuntimeEngineTest):
    # Inherit fixture setup only; the ordinary small tests are selected separately.
    def test_real_oversized_multilevel_continuation(self):
        narrative = '合成容量样本段' * 500
        text = '采用并行 summary-v2 侧车且不修改 summary-v1。\n' + '\n'.join(
            f'容量段落 {i}: {narrative}' for i in range(8))
        base = self.fixture.job()['source_records'][0]
        records = [{**base, 'sequence': i + 1, 'message_id': f'capacity-{i + 1:03d}',
                    'speaker': 'user' if i == 0 else 'assistant', 'round_number': 1,
                    'text': text, 'completes_round': i == 27} for i in range(28)]
        job = {**self.job(), 'job_id': 'job-real-capacity-28', 'target_summary_id': 'L1-900028',
               'source_records': records, 'source_message_ids': [r['message_id'] for r in records],
               'source_sha256': support._source_sha256(records)}
        formal = engine.build_level_1_source(job)
        self.assertGreater(engine.compile_prompt(formal)['prompt_utf8_bytes'], engine.REDUCE_PROMPT_LIMIT)
        self.assertEqual('l1-map-reduce-dag', engine._decide_route(
            {'level': 1, 'summary_id': job['target_summary_id']}, formal)['route'])
        self.assertEqual(28, len(engine._chunk_job(job)))
        calls, call_lock = [], threading.Lock()
        def fixture_source(source, *args, **kwargs):
            context = kwargs['invocation_context']
            def invoke(command, timeout, prompt):
                self.assertLessEqual(timeout, 900)
                self.assertLessEqual(len(prompt.encode('utf-8')), engine.REDUCE_PROMPT_LIMIT)
                self.assertLessEqual(len(source['source_refs']), 96)
                with call_lock:
                    calls.append((source['job_id'], context['stage']))
                candidate = self.fixture.candidate(source)
                candidate['scenes'] = [{'local_id': f'capacity_scene_{i}', 'title': f'容量段落 {i}',
                                        'summary': narrative, 'source_refs': list(source['source_refs'])}
                                       for i in range(8)]
                return candidate
            return worker.run_source(source, *args, invoker=invoke, **kwargs)
        previous, saved, results = set(), {}, []
        with patch.object(engine, 'run_source', side_effect=fixture_source):
            for _ in range(32):
                before = len(calls)
                result = self.run_node(job)
                results.append(result)
                self.assertIn(result['status'], {'yielded', 'completed'}, result)
                state = json.loads(Path(result['state_path']).read_text('utf-8'))
                dispatched = set(state['dispatches'])
                new = dispatched - previous
                self.assertLessEqual(len(calls) - before, 3)
                self.assertEqual(len(calls) - before, len(new))
                waves = {'maps' if k.startswith('map/') else k.rsplit('-map-', 1)[0] for k in new}
                self.assertLessEqual(len(waves), 1)
                current = {f'{group}/{key}': value for group in ('maps', 'reductions') for key, value in state[group].items()}
                for key, value in saved.items():
                    self.assertEqual(value, current[key])
                previous, saved = dispatched, current
                if result['status'] == 'completed':
                    break
            else:
                self.fail('Node did not complete in 32 bounded ticks')
            self.assertEqual(28, len(state['maps']))
            self.assertGreaterEqual(len({key.rsplit('-map-', 1)[0] for key in state['reductions']}), 2)
            self.assertLessEqual(len(calls), 56)
            replay = self.run_node(job)
            self.assertEqual('completed', replay['status'], replay)
            self.assertEqual(0, replay['ai_invocations'])
            # A completed reduction cannot be regenerated to repair a missing receipt.
            path = Path(replay['state_path'])
            state = json.loads(path.read_text('utf-8'))
            key = next(iter(state['reductions']))
            previous_state = path.read_bytes()
            del state['dispatches']['reduction/' + key]
            engine.atomic_write_canonical_json(path, state)
            claim_only = self.run_node(job)
            self.assertEqual('blocked', claim_only['status'])
            self.assertEqual(0, claim_only['ai_invocations'])
            path.write_bytes(previous_state)
            state = json.loads(previous_state)
            del state['reductions'][key]
            del state['dispatches']['reduction/' + key]
            engine.atomic_write_canonical_json(path, state)
            broken = self.run_node(job)
            self.assertEqual('blocked', broken['status'])
            self.assertEqual(0, broken['ai_invocations'])
        self.assertEqual(len(calls), len({job_id for job_id, _ in calls}))
        final = worker.load_sidecar(Path(result['bundle']))
        self.assertEqual(job['source_message_ids'], final['source']['raw_message_ids'])
        self.assertEqual(0, final['coverage']['silent_loss_count'])


if __name__ == '__main__':
    unittest.main()
