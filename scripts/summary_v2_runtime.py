"""Control Plane bridge to the isolated, manifest-bound Summary V2 engine."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from platform_process import no_window_kwargs

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'summary_v2'
MANIFEST = VENDOR / 'runtime-manifest.json'

# -I excludes both ambient PYTHONPATH and the mainline scripts directory.
BOOTSTRAP = '''import sys,json,hashlib
from pathlib import Path
root=Path(sys.argv.pop(1)).resolve()
manifest=json.loads((root/'runtime-manifest.json').read_text(encoding='utf-8'))
expected={item['path']:item['sha256'] for item in manifest['files']}
actual={p.relative_to(root).as_posix() for p in (root/'scripts').glob('*.py')}
assert actual=={p for p in expected if p.startswith('scripts/')}, 'engine file inventory changed'
for relative,digest in expected.items():
    assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==digest, 'engine artifact changed: '+relative
sys.path.insert(0,str(root/'scripts'))
from summary_v2_backfill import main
for module in tuple(sys.modules.values()):
    path=getattr(module,'__file__',None)
    if path and Path(path).name in {Path(p).name for p in actual}:
        assert Path(path).resolve().parent==root/'scripts', 'mixed engine module origin'
raise SystemExit(main())
'''


def enabled(config: dict[str, Any]) -> bool:
    return config.get('summary_v2', {}).get('enabled', False) is True


def runtime_root(store) -> Path:
    restored_path = store.root / 'summary-v2/restored-bindings.json'
    restored = json.loads(restored_path.read_text('utf-8')) if restored_path.exists() else {}
    configured = store.config.get('summary_v2', {}).get('runtime_root')
    configured = restored.get('roots', {}).get('runtime', configured)
    path = Path(configured).expanduser() if configured else store.root.with_name(store.root.name + '-summary-v2-runtime')
    path = path.resolve()
    if path == store.root.resolve() or path.is_relative_to(store.root.resolve()):
        raise ValueError('Summary V2 runtime root must be outside the raw archive')
    return path


def artifact_identity() -> str:
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def call_engine(store, request: dict[str, Any], *, config_path: Path | None = None) -> dict[str, Any]:
    """Use the only admitted core CLI; inspect mode cannot dispatch a model."""
    operation = 'runtime-node' if config_path is not None else 'inspect-bundles'
    with tempfile.TemporaryDirectory(prefix='memory-wuxian-v2-request-') as temporary:
        request_path = Path(temporary) / 'request.json'
        request_path.write_text(json.dumps(request, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        command = [sys.executable, '-I', '-B', '-X', 'utf8', '-c', BOOTSTRAP, str(VENDOR),
                   '--archive-root', str(store.root.resolve()), '--output-root', str(runtime_root(store)),
                   operation, '--request', str(request_path)]
        if config_path is not None:
            command.extend(['--config', str(config_path.resolve())])
        # The engine bounds admission and drains claimed stages; killing this parent is not a pause.
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                check=False, **no_window_kwargs())
        if result.returncode:
            raise RuntimeError('Summary V2 isolated engine failed: ' + result.stderr[-3000:])
        return json.loads(result.stdout)


def execute_job(store, config_path: Path, job_path: Path, *, source_snapshot=None,
                defer_derived_updates=False, create_backup=True, dry_run=False) -> dict[str, Any]:
    from memory_summary_v2_links import SummaryV2Links
    links = SummaryV2Links(store)
    job = json.loads(job_path.read_text(encoding='utf-8'))
    restored_path = store.root / 'summary-v2/restored-bindings.json'
    if restored_path.exists() and job['job_id'] in json.loads(restored_path.read_text('utf-8')).get('paused_jobs', []):
        return {'status': 'deferred', 'summary_format': 2, 'job_id': job['job_id'], 'ai_invocations': 0,
                'reason_code': 'restored-checkpoint-review', 'requires_attention': True,
                'reason': 'Restored pending V2 execution requires checkpoint review before resuming'}
    snapshot = source_snapshot if source_snapshot is not None else store.build_summary_source_snapshot()
    from memory_identity import IdentityRepairIncomplete
    try:
        children = links.verify_source(job, snapshot)
    except IdentityRepairIncomplete as error:
        return {'status': 'deferred', 'summary_format': 2, 'job_id': job['job_id'],
                'ai_invocations': 0, 'reason_code': 'identity-repair-incomplete', 'reason': str(error)}
    if dry_run:
        return {'status': 'dry-run', 'summary_format': 2, 'job_id': job['job_id'], 'ai_invocations': 0}
    seconds = store.config.get('summary_v2', {}).get('tick_seconds', 960)
    timeout = store.config.get('ai_summary', {}).get('timeout_seconds', 900)
    if not 0 < timeout <= 900 or not timeout + 45 <= seconds <= 1200:
        raise ValueError('V2 tick budget must include its model timeout plus 45 seconds, within 1200 seconds')
    recovered = links.recover_committed_job(job_path, snapshot)
    if recovered is not None:
        return recovered
    links.bind_job_route(job)
    result = call_engine(store, {'job': job, 'children': children,
                                'tick_seconds': seconds},
                         config_path=config_path)
    result['summary_format'] = 2
    if result['status'] == 'blocked':
        result['requires_attention'] = True
        result['reason_code'] = result.get('node_state', 'v2-engine-blocked')
    if result['status'] != 'completed':
        return result
    completed = links.complete_job(job_path, result, source_snapshot=snapshot,
                                   defer_derived_updates=defer_derived_updates, dispatched_job=job)
    if create_backup:
        backup = store.create_backup_snapshot('summary-v2-ingested', {'job_id': job['job_id']})
        completed['backup'] = str(backup) if backup else None
    return {**completed, 'ai_invocations': result['ai_invocations']}
