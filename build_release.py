"""Build a compatible update payload and a user-facing complete distribution."""
import hashlib
import json
import zipfile
import argparse
import platform
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from installer.package import CONTRACT, ENGINE_PROTOCOL
parser=argparse.ArgumentParser()
parser.add_argument('--platform',required=True)
parser.add_argument('--binary',required=True)
args=parser.parse_args()
if args.platform.startswith('macos'):
    args.platform='macos-'+('arm64' if platform.machine().lower() in {'arm64','aarch64'} else 'x64')
version = (root / 'VERSION').read_text('utf-8').strip()
core_files = json.loads((root / 'release-files.json').read_text('utf-8'))
native_name='bin/memory-wuxian-envelope'+('.exe' if args.platform.startswith('windows') else '')
files = ['README.md', 'README.zh-CN.md', 'README.ja.md', 'SKILL.md', 'LICENSE.txt', 'VERSION', 'RELEASE_NOTES.md', 'docs/PEER-SETUP.md', *core_files,native_name]
assert len(files) == len(set(files))
dist = root / 'dist'
dist.mkdir(exist_ok=True)
archive = dist / f'memory-wuxian-{version}-{args.platform}.zip'
manifest = {}
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    descriptor = json.dumps({'installer_protocol':ENGINE_PROTOCOL,'version':version,
                            'platform':args.platform,'compatibility':CONTRACT}, sort_keys=True).encode('utf-8')
    bundle.writestr('INSTALL.json', descriptor)
    manifest['INSTALL.json'] = hashlib.sha256(descriptor).hexdigest()
    for name in sorted(files):
        source = Path(args.binary).resolve() if name==native_name else root / name
        if source.is_symlink() or (name!=native_name and not source.resolve().is_relative_to(root)) or not source.is_file():
            raise ValueError('invalid release source')
        data = source.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        entry = zipfile.ZipInfo(name, date_time=(2026, 9, 28, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = (0o100755 if name==native_name else 0o100644) << 16
        bundle.writestr(entry, data)
    entry = zipfile.ZipInfo('MANIFEST.json', date_time=(2026, 9, 28, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    bundle.writestr(entry, json.dumps(manifest, indent=2).encode('utf-8'))
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert set(bundle.namelist()) == {*files, 'MANIFEST.json', 'INSTALL.json'}
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
(dist / (archive.name+'.sha256')).write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
print(json.dumps({'package': archive.name, 'files': len(files) + 2, 'sha256': checksum}))

# The updater keeps consuming the original payload name. The complete distribution
# adds the independently versioned engine without making it an update target.
installer_version=(root/'installer/VERSION').read_text('utf-8').strip()
installer_files=['cli.py','package.py','platform_runtime.py','transaction.py','updates.py','dashboard.py','README.md','VERSION']
complete=dist/f'memory-wuxian-{version}-{args.platform}-complete.zip'
with zipfile.ZipFile(archive) as payload, zipfile.ZipFile(complete,'w',zipfile.ZIP_DEFLATED) as bundle:
    for info in payload.infolist(): bundle.writestr(info,payload.read(info.filename))
    engine_hashes={}
    for name in installer_files:
        data=(root/'installer'/name).read_bytes()
        member='installer/'+name
        entry=zipfile.ZipInfo(member,date_time=(2026,9,30,0,0,0))
        entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16
        bundle.writestr(entry,data)
        engine_hashes[member]=hashlib.sha256(data).hexdigest()
    bundle.writestr('BUNDLE.json',json.dumps(dict(product_version=version,installer_version=installer_version,
        payload_sha256=checksum,installer_files=engine_hashes),sort_keys=True).encode('utf-8'))
complete_checksum=hashlib.sha256(complete.read_bytes()).hexdigest()
(dist/(complete.name+'.sha256')).write_text(f'{complete_checksum}  {complete.name}\n',encoding='utf-8')
print(json.dumps({'package':complete.name,'product_version':version,'installer_version':installer_version,'sha256':complete_checksum}))
