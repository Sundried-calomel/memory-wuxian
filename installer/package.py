"""Trusted package boundary. No candidate-selected commands or migrations."""
import hashlib
import json
import platform
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

ENGINE_PROTOCOL = 1
CONTRACT = {'archive': 1, 'configuration': 1, 'sync': 'core-v1', 'layout': 'compact-core-v1'}
MAX_PACKAGE = 64 * 1024 * 1024
ROOT_FILES = {'README.md', 'README.zh-CN.md', 'README.ja.md', 'SKILL.md', 'LICENSE.txt', 'VERSION', 'RELEASE_NOTES.md', 'docs/PEER-SETUP.md', 'scripts/memory_dashboard.py'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def managed_name(name):
    if not isinstance(name, str) or '\\' in name or ':' in name:
        raise ValueError('invalid package path')
    path = PurePosixPath(name)
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}
    if any(p.split('.')[0].upper() in reserved for p in path.parts):
        raise ValueError('reserved package path')
    if path.is_absolute() or path.as_posix() != name or any(p in {'.', '..'} or p.endswith(('.', ' ')) for p in path.parts):
        raise ValueError('invalid package path')
    allowed = name in ROOT_FILES or name in {'core/dashboard.html', 'core/environment-selection.json',
                'bin/memory-wuxian-envelope', 'bin/memory-wuxian-envelope.exe'}
    allowed |= bool(re.fullmatch(r'core/[a-z][a-z0-9_]*\.py', name))
    if not allowed:
        raise ValueError('package path outside managed program files: ' + name)
    return name


def host_platform():
    machine = platform.machine().lower()
    arch = 'arm64' if machine in {'arm64', 'aarch64'} else 'x64' if machine in {'amd64', 'x86_64'} else machine
    os_name = {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}.get(platform.system())
    return f'{os_name}-{arch}'


def read_package(path, expected_sha256, *, expected_platform=None):
    """Expected digest must come from a trusted release channel, not this ZIP."""
    path = Path(path)
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256 or ''):
        raise ValueError('explicit trusted package SHA256 required')
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_PACKAGE + 1)
    if len(raw) > MAX_PACKAGE or digest(raw) != expected_sha256.lower():
        raise ValueError('package size or trusted digest mismatch')
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) > 512 or len({n.casefold() for n in names}) != len(names):
            raise ValueError('duplicate or excessive package entries')
        if sum(item.file_size for item in infos) > MAX_PACKAGE:
            raise ValueError('expanded package too large')
        for item in infos:
            mode = item.external_attr >> 16
            if item.is_dir() or item.flag_bits & 1 or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise ValueError('only ordinary unencrypted files are allowed')
            if item.filename not in {'MANIFEST.json', 'INSTALL.json'}:
                managed_name(item.filename)
        manifest = parse(archive.read('MANIFEST.json'))
        if not isinstance(manifest, dict) or set(manifest) != set(names) - {'MANIFEST.json'}:
            raise ValueError('manifest must cover the exact package')
        data = {}
        for name, expected in manifest.items():
            content = archive.read(name)
            if digest(content) != expected:
                raise ValueError('package member digest mismatch: ' + name)
            data[name] = content
        version = data['VERSION'].decode('utf-8').strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('invalid product version')
        selected_platform = expected_platform or host_platform()
        if 'INSTALL.json' in data:
            descriptor = parse(data.pop('INSTALL.json'))
        else:
            # One explicit adapter for the already published compact-core package.
            if version not in {'2.20.1', '2.20.3'} or path.name != f'memory-wuxian-{version}-{selected_platform}.zip':
                raise ValueError('package has no supported installation contract')
            descriptor = {'installer_protocol': 1, 'version': version, 'platform': selected_platform,
                          'compatibility': CONTRACT}
        if descriptor != {'installer_protocol': ENGINE_PROTOCOL, 'version': version,
                          'platform': selected_platform, 'compatibility': CONTRACT}:
            raise ValueError('unsupported platform or compatibility contract; explicit migration required')
        binary = 'bin/memory-wuxian-envelope' + ('.exe' if selected_platform.startswith('windows-') else '')
        if not {'core/live.py', 'core/collector.py', 'core/runtime.py', 'SKILL.md', binary} <= set(data):
            raise ValueError('required runtime files missing')
        modes = {name: (0o755 if name == binary else 0o644) for name in data}
        return {'version': version, 'platform': selected_platform, 'compatibility': CONTRACT,
                'package_sha256': digest(raw), 'files': data, 'modes': modes}
