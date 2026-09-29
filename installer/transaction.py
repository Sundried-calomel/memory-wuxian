"""One changed-file transaction and one retained rollback; no archive migration."""
import base64
import contextlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from package import CONTRACT, digest, managed_name, parse


def target_path(root, relative):
    managed_name(relative)
    path = root / relative
    for item in (root, *path.relative_to(root).parents):
        candidate = item if item.is_absolute() else root / item
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise ValueError('linked installation path')
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('target is not an ordinary file')
    return path


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if os.name != 'nt':
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def write_json(path, value):
    atomic(path, json.dumps(value, sort_keys=True).encode('utf-8'))


@contextlib.contextmanager
def lock(path, timeout=30):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0: handle.write(b'0'); handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline: raise TimeoutError('runtime busy; retry later')
                time.sleep(.1)
        try: yield
        finally:
            handle.seek(0)
            if os.name == 'nt': msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Installer:
    def __init__(self, target):
        self.root = Path(target).absolute()
        for path in (self.root, *self.root.parents):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('installation root must not traverse links')
        self.control = self.root.parent / ('.' + self.root.name + '.installer')
        if self.control.is_symlink() or (hasattr(self.control, 'is_junction') and self.control.is_junction()):
            raise ValueError('linked control directory')
        self.state = self.control / 'installation.json'
        self.journal = self.control / 'pending.json'
        self.previous = self.control / 'rollback.json'

    def read_state(self):
        if not self.state.exists(): return None
        state = parse(self.state.read_bytes())
        if state.get('root') != str(self.root) or state.get('compatibility') != CONTRACT:
            raise ValueError('installation identity or schema mismatch')
        for name in state['files']: managed_name(name)
        return state

    def plan(self, package):
        state = self.read_state()
        if state and tuple(map(int, package['version'].split('.'))) < tuple(map(int, state['version'].split('.'))):
            raise ValueError('use explicit rollback instead of an implicit downgrade')
        old_files = state['files'] if state else {}
        if not state and self.root.exists() and any(self.root.iterdir()):
            raise ValueError('unmanaged existing installation; explicit adoption/migration required')
        changed = []
        for name in sorted(set(old_files) | set(package['files'])):
            path = target_path(self.root, name)
            old = path.read_bytes() if path.exists() else None
            new = package['files'].get(name)
            old_mode = stat.S_IMODE(path.stat().st_mode) if old is not None else None
            new_mode = package['modes'].get(name)
            if old is not None and name not in old_files:
                raise ValueError('unmanaged file collision: ' + name)
            if old is not None and name in old_files and digest(old) != old_files[name]['sha256']:
                if old != new: raise ValueError('local program edit: ' + name)
            if old == new and (os.name == 'nt' or old_mode == new_mode): continue
            changed.append({'name': name, 'old': base64.b64encode(old).decode() if old is not None else None,
                            'old_sha256': digest(old) if old is not None else None,
                            'old_mode': old_mode, 'new_sha256': digest(new) if new is not None else None,
                            'new_mode': new_mode})
        next_state = {'root': str(self.root), 'version': package['version'], 'compatibility': CONTRACT,
                      'package_sha256': package['package_sha256'], 'platform': package['platform'],
                      'files': {n: {'sha256': digest(v), 'mode': package['modes'][n]} for n,v in package['files'].items()}}
        return state, next_state, changed

    def adopt(self, package):
        """Record ownership only after comparison with a trusted baseline package."""
        with lock(self.control / 'lock'):
            if self.state.exists() or self.journal.exists():
                raise ValueError('installation already managed or awaiting recovery')
            for name, content in package['files'].items():
                path = target_path(self.root, name)
                if not path.is_file() or path.read_bytes() != content:
                    raise ValueError('baseline differs: ' + name)
            state = {'root': str(self.root), 'version': package['version'], 'compatibility': CONTRACT,
                     'package_sha256': package['package_sha256'], 'platform': package['platform'],
                     'files': {n: {'sha256': digest(v), 'mode': package['modes'][n]} for n,v in package['files'].items()}}
            write_json(self.state, state)
            return {'status':'adopted', 'version':state['version']}

    def apply(self, package, platform):
        with lock(self.control / 'lock'):
            if self.journal.exists(): raise RuntimeError('unfinished update; run recover')
            before, after, entries = self.plan(package)
            if before == after and not entries: return {'status':'unchanged','changed':[]}
            snapshot = platform.snapshot(self.root)
            record = {'root':str(self.root),'before':before,'after':after,'entries':entries,'services':snapshot}
            write_json(self.journal, record)
            paused = False
            try:
                platform.pause(self.root, snapshot)
                paused = True
                # Only recheck affected targets at the replacement boundary.
                self.check_targets(record)
                for entry in entries:
                    path = target_path(self.root, entry['name'])
                    content = package['files'].get(entry['name'])
                    if content is None: path.unlink(missing_ok=True)
                    else: atomic(path, content, entry['new_mode'])
                platform.load_check(self.root)
                write_json(self.state, after)
                platform.resume(self.root, snapshot)
                os.replace(self.journal, self.previous)
                return {'status':'applied','version':after['version'],'changed':[e['name'] for e in entries]}
            except BaseException:
                if paused:
                    platform.pause(self.root, snapshot)
                    self.restore(record)
                platform.resume(self.root, snapshot)
                self.journal.unlink()
                raise
            finally: platform.close()

    def check_targets(self, record, recovery=False):
        if record.get('root') != str(self.root): raise ValueError('wrong transaction root')
        for entry in record['entries']:
            path = target_path(self.root, entry['name'])
            old = base64.b64decode(entry['old'], validate=True) if entry['old'] is not None else None
            if (digest(old) if old is not None else None) != entry['old_sha256']:
                raise ValueError('damaged rollback bytes: ' + entry['name'])
            allowed = {digest(old) if old is not None else None}
            if recovery: allowed.add(entry['new_sha256'])
            actual = digest(path.read_bytes()) if path.exists() else None
            if actual not in allowed: raise ValueError('file changed outside transaction: ' + entry['name'])

    def restore(self, record):
        self.check_targets(record, recovery=True)
        for entry in reversed(record['entries']):
            path = target_path(self.root, entry['name'])
            if entry['old'] is None: path.unlink(missing_ok=True)
            else: atomic(path, base64.b64decode(entry['old']), entry['old_mode'])
        if record['before'] is None: self.state.unlink(missing_ok=True)
        else: write_json(self.state, record['before'])

    def recover(self, platform, *, rollback=False):
        with lock(self.control/'lock'):
            path = self.previous if rollback else self.journal
            if rollback and self.journal.exists(): raise RuntimeError('recover interrupted update first')
            if not path.exists(): return {'status':'clean'}
            record = parse(path.read_bytes())
            if rollback and self.read_state() != record['after']:
                raise ValueError('rollback does not match installed state')
            self.check_targets(record, recovery=True)
            try:
                platform.pause(self.root, record['services'])
                self.restore(record)
                if record['before'] is not None: platform.load_check(self.root)
                platform.resume(self.root, record['services'])
                path.unlink()
                return {'status':'rolled-back' if rollback else 'recovered'}
            finally: platform.close()
