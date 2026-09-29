"""Independent installer CLI: install/update, adopt, recover, rollback."""
import argparse
import json
from pathlib import Path
import sys

# Also supports the isolated embedded Python distribution.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from package import read_package
from transaction import Installer
from platform_runtime import PlatformRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply','adopt','recover','rollback'])
    parser.add_argument('--target', required=True)
    parser.add_argument('--package')
    parser.add_argument('--sha256', help='digest from the trusted release channel')
    parser.add_argument('--offline', action='store_true', help='explicitly assert all target clients/services are stopped')
    parser.add_argument('--python', help='Python used to check the installed entry point')
    args = parser.parse_args()
    installer = Installer(args.target)
    platform = PlatformRuntime(offline=args.offline, python=args.python)
    if args.action in {'apply','adopt'}:
        if not args.package or not args.sha256: parser.error('--package and --sha256 required')
        package = read_package(args.package, args.sha256)
        result = installer.adopt(package) if args.action=='adopt' else installer.apply(package, platform)
    else:
        result = installer.recover(platform, rollback=args.action=='rollback')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
