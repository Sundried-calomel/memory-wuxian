"""Choose release checks from the previous release diff; no duplicate CI gate."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
ALL = sorted(p.stem for p in (ROOT / 'tests').glob('test_*.py'))
SHARED = {'archive.py', 'storage.py', 'runtime.py', 'install.py'}


def select(paths, full=False):
    tests = set(ALL if full else [])
    native = full
    package = full
    for path in paths:
        name = Path(path).name
        if path.startswith('tests/test_') and path.endswith('.py'):
            if Path(path).stem in ALL:
                tests.add(Path(path).stem)
        if path.startswith(('installer/',)) or path in {'build_release.py', 'release-files.json'}:
            tests.update({'test_installer', 'test_updates'})
            package = True
        if path.startswith('.github/workflows/') or path == 'scripts/release_checks.py':
            tests.add('test_release_plan')
            package = True
        if path.startswith('native-envelope/'):
            native = True
        if path == 'tests/smoke_native.py':
            native = True
        if path == 'tests/smoke_complete_bundle.py':
            package = True
        if not path.startswith('core/'):
            continue
        if name in SHARED:
            tests.update(ALL)
            native = package = True
        elif name in {'environment.py', 'environment_sync.py', 'environment-selection.json'}:
            tests.update({'test_environment_autosync', 'test_sync_waiting_mcp'})
        elif name == 'mcp_server.py':
            tests.add('test_sync_waiting_mcp')
        elif name in {'core_sync.py', 'peer_bridge.py', 'exchange.py', 'legacy_crypto.py', 'legacy_protocol.py'}:
            tests.update({'test_operational_repairs', 'test_release'})
            native = True
        elif name in {'dashboard.py', 'dashboard.html', 'dashboard_data.py', 'window.py'}:
            tests.add('test_dashboard_data')
            if name in {'dashboard.py', 'window.py'}:
                package = True
        elif name in {'collector.py', 'live.py', 'summary.py', 'token_usage.py'}:
            tests.add('test_operational_repairs')
        else:
            tests.add('test_release')
    return {'tests': sorted(tests), 'native': native, 'package': package}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--run')
    args = parser.parse_args()
    if args.run is not None:
        names = json.loads(args.run)
        if not isinstance(names, list) or any(name not in ALL for name in names):
            raise ValueError('unknown test selection')
        if not names:
            print('No behavioral tests selected for this documentation-only diff.')
            return
        sys.path.insert(0, str(ROOT / 'tests'))
        suite = unittest.defaultTestLoader.loadTestsFromNames(names)
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        return
    base = args.base
    if not base and not args.full:
        base = subprocess.check_output(['git', 'describe', '--tags', '--match', 'v[0-9]*',
                                       '--abbrev=0', 'HEAD^'], cwd=ROOT, text=True).strip()
    paths = [] if args.full else subprocess.check_output(
        ['git', 'diff', '--name-only', '--no-renames', base, 'HEAD'], cwd=ROOT, text=True).splitlines()
    plan = select(paths, args.full)
    print(json.dumps({'base': base, 'changed': paths, **plan}))
    if args.output:
        with open(args.output, 'a', encoding='utf-8') as stream:
            for key, value in plan.items():
                stream.write(key + '=' + json.dumps(value) + '\n')


if __name__ == '__main__':
    main()
