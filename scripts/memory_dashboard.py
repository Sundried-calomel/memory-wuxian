"""Route the existing macOS application launcher to the compact dashboard."""
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--config')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    core = Path(__file__).resolve().parents[1] / 'core'
    sys.path.insert(0, str(core))
    import dashboard
    sys.argv = [str(core/'dashboard.py'), '--root', args.root,
                '--config', str(core/'live-config.json'), '--port', str(args.port)]
    return dashboard.main()


if __name__ == '__main__':
    raise SystemExit(main())
