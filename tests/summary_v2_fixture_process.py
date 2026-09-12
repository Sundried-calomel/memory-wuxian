"""Test-only process boundary: real V2 lifecycle with a deterministic model fixture."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/summary_v2'
sys.path[:0] = [str(VENDOR / 'scripts'), str(VENDOR / 'tests')]
import summary_v2_backfill as engine
import summary_v2_worker as worker
from test_memory_summary_v2 import SummaryV2Test

request = json.loads(Path(sys.argv[1]).read_text('utf-8'))
fixture = SummaryV2Test()
original_run = subprocess.run
def version_only(command, *args, **kwargs):
    if list(command) != [str(Path(sys.executable).resolve()), '--version']:
        raise AssertionError('No model subprocess may run in this test')
    return original_run(command, *args, **kwargs)
def run_source(source, *args, **kwargs):
    def invoke(command, timeout, prompt):
        return fixture.candidate(source) if source['summary_level'] == 1 else fixture.parent_candidate(source)
    return worker.run_source(source, *args, invoker=invoke, **kwargs)
with patch.dict(os.environ, {'MEMORY_WUXIAN_CODEX': sys.executable}), \
     patch.object(subprocess, 'run', side_effect=version_only), \
     patch.object(engine, 'run_source', side_effect=run_source), \
     patch.object(engine, 'build_plan', side_effect=AssertionError('No historical planner')), \
     patch.object(engine.MemoryStore, 'read_all_raw', side_effect=AssertionError('No archive scan')):
    children = []
    for child in request.get('children', []):
        path = Path(child['path'])
        assert hashlib.sha256((path / 'summary.json').read_bytes()).hexdigest() == child['json_sha256']
        assert hashlib.sha256((path / 'summary.md').read_bytes()).hexdigest() == child['markdown_sha256']
        children.append(worker.load_sidecar(path))
    result = engine.run_closed_node(Path(sys.argv[2]), Path(sys.argv[3]), config_path=Path(sys.argv[4]),
                                    job=request['job'], children=children,
                                    tick_seconds=request.get('tick_seconds', 960))
    result = engine.describe_completed_node(result)
    print(json.dumps(result, ensure_ascii=False))
