"""Preview or apply exact, model-free links to existing Summary V2 bundles."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from memory_cli import MemoryStore, load_simple_yaml
from memory_summary_v2_links import SummaryV2Links, digest
from platform_transaction import atomic_write_canonical_json


def existing_inventory(root: Path) -> tuple[list[Path], list[Path]]:
    formal = sorted(path.parent for path in (root / 'memory-wuxian-summary-v2').glob('level-*/summary-v2-*/summary.json'))
    if not formal:
        raise ValueError('No formal Summary V2 bundles found under the explicit binding root')
    paths = {}
    for path in root.rglob('summary.json'):
        if path.parent.name.startswith('summary-v2-'):
            if path.parent.name in paths and paths[path.parent.name] != path.parent:
                raise ValueError('Multiple paths claim the same V2 bundle ID')
            paths[path.parent.name] = path.parent
    reached = set()
    pending = list(formal)
    while pending:
        path = pending.pop()
        if path in reached:
            continue
        reached.add(path)
        sidecar = json.loads((path / 'summary.json').read_text('utf-8'))
        for child in [*sidecar['source']['source_manifest'].get('children', []), *sidecar.get('internal_routes', [])]:
            pending.append(paths[child['summary_v2_id']])
    return formal, sorted(reached - set(formal))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--binding', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    store = MemoryStore(Path(args.root), load_simple_yaml(Path(args.config)))
    links = SummaryV2Links(store)
    binding_root = links.bindings()[args.binding]
    formal, internal = existing_inventory(binding_root)
    snapshot = store.build_summary_source_snapshot()
    prepared = links.prepare_adoption(formal, internal, snapshot)
    plan = {'format': 'memory-wuxian-summary-v2-adoption-plan-v1', 'binding_id': args.binding,
            'binding_root': str(binding_root), 'archive_root': str(store.root.resolve()),
            'completions': prepared}
    plan['plan_sha256'] = digest(plan)
    path = Path(args.plan)
    if args.apply:
        reviewed = json.loads(path.read_text('utf-8'))
        if reviewed != plan:
            raise ValueError('Adoption preview changed; review the new plan before applying')
        result = links.apply_adoption(prepared)
    else:
        if path.exists() and json.loads(path.read_text('utf-8')) != plan:
            raise ValueError('Use a new plan path; never overwrite a reviewed adoption plan')
        atomic_write_canonical_json(path, plan)
        result = {'status': 'preview', 'roots': len(formal), 'internal_bundles': len(internal)}
    print(json.dumps({**result, 'plan_sha256': plan['plan_sha256'], 'plan': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
