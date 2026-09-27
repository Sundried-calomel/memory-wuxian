"""Package only versioned public core files; no device files or native installers."""
import hashlib
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
version = (root / 'VERSION').read_text('utf-8').strip()
core_files = json.loads((root / 'release-files.json').read_text('utf-8'))
files = ['README.md', 'SKILL.md', 'LICENSE.txt', 'VERSION', 'RELEASE_NOTES.md', 'docs/PEER-SETUP.md', *core_files]
assert len(files) == len(set(files))
dist = root / 'dist'
dist.mkdir(exist_ok=True)
archive = dist / f'memory-wuxian-{version}.zip'
manifest = {}
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for name in sorted(files):
        source = root / name
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('invalid release source')
        data = source.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        entry = zipfile.ZipInfo(name, date_time=(2026, 9, 28, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        bundle.writestr(entry, data)
    entry = zipfile.ZipInfo('MANIFEST.json', date_time=(2026, 9, 28, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    bundle.writestr(entry, json.dumps(manifest, indent=2).encode('utf-8'))
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert set(bundle.namelist()) == {*files, 'MANIFEST.json'}
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
(dist / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
print(json.dumps({'package': archive.name, 'files': len(files) + 1, 'sha256': checksum}))
