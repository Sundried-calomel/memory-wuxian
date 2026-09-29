"""Bounded transaction tests using synthetic packages and an isolated target."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'installer'))
from package import CONTRACT, digest, host_platform, read_package
from transaction import Installer, atomic, write_json


class Offline:
    def snapshot(self, root): return {'offline':True}
    def pause(self, root, state): pass
    def resume(self, root, state): pass
    def close(self): pass
    def load_check(self, root):
        if (root/'core/live.py').read_bytes() == b'broken': raise RuntimeError('entry fails')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.installer = Installer(self.base/'product')
        self.platform = Offline()

    def package(self, version='2.20.2', **changes):
        binary = 'bin/memory-wuxian-envelope' + ('.exe' if os.name=='nt' else '')
        files = {'VERSION':version.encode(), 'SKILL.md':b'skill', 'core/live.py':b'live',
                 'core/runtime.py':b'runtime', 'core/collector.py':b'collector', binary:b'binary'}
        files.update(changes)
        files['INSTALL.json'] = json.dumps({'installer_protocol':1,'version':version,
            'platform':host_platform(),'compatibility':CONTRACT}).encode()
        path = self.base/(version+'.zip')
        with zipfile.ZipFile(path,'w') as bundle:
            for name, content in files.items(): bundle.writestr(name,content)
            bundle.writestr('MANIFEST.json',json.dumps({n:digest(v) for n,v in files.items()}))
        return read_package(path,digest(path.read_bytes()))

    def test_three_versions_changed_only_and_rollback(self):
        first = self.package(**{'core/obsolete.py':b'old'})
        self.installer.apply(first,self.platform)
        root = self.installer.root
        unchanged = root/'SKILL.md'
        stamp = unchanged.stat().st_mtime_ns
        local = root/'core/live-config.json'; local.write_bytes(b'local configuration')
        history = root/'archive'; history.mkdir(); (history/'raw').write_bytes(b'history')
        second = self.package('2.20.3', **{'core/live.py':b'new'})
        result = self.installer.apply(second,self.platform)
        self.assertEqual(set(result['changed']),{'VERSION','core/live.py','core/obsolete.py'})
        third = self.package('2.20.4', **{'core/live.py':b'newer'})
        self.installer.apply(third,self.platform)
        self.assertEqual(unchanged.stat().st_mtime_ns,stamp)
        self.assertEqual(local.read_bytes(),b'local configuration')
        self.assertEqual((history/'raw').read_bytes(),b'history')
        self.installer.recover(self.platform,rollback=True)
        self.assertEqual((root/'core/live.py').read_bytes(),b'new')
        self.assertEqual(self.installer.read_state()['version'],'2.20.3')

    def test_failed_load_restores(self):
        self.installer.apply(self.package(),self.platform)
        with self.assertRaises(RuntimeError):
            self.installer.apply(self.package('2.20.3', **{'core/live.py':b'broken'}),self.platform)
        self.assertEqual((self.installer.root/'core/live.py').read_bytes(),b'live')
        self.assertFalse(self.installer.journal.exists())

    def test_interrupted_write_recovers(self):
        self.installer.apply(self.package(),self.platform)
        new = self.package('2.20.3')
        before, after, entries = self.installer.plan(new)
        record = dict(root=str(self.installer.root),before=before,after=after,entries=entries,services={'offline':True})
        write_json(self.installer.journal,record)
        atomic(self.installer.root/'VERSION',b'2.20.3')
        self.installer.recover(self.platform)
        self.assertEqual((self.installer.root/'VERSION').read_bytes(),b'2.20.2')

    def test_adoption_and_local_conflicts(self):
        package = self.package()
        for name, content in package['files'].items(): atomic(self.installer.root/name,content,package['modes'][name])
        self.installer.adopt(package)
        (self.installer.root/'core/live.py').write_bytes(b'local edit')
        with self.assertRaisesRegex(ValueError,'local program edit'): self.installer.plan(self.package('2.20.3'))
        (self.installer.root/'core/live.py').write_bytes(b'live')
        (self.installer.root/'core/new.py').write_bytes(b'user file')
        with self.assertRaisesRegex(ValueError,'unmanaged file collision'):
            self.installer.plan(self.package('2.20.3', **{'core/new.py':b'package file'}))

    def test_package_rejects_unmanaged_paths_and_bad_digest(self):
        with self.assertRaises(ValueError): self.package(**{'core/live-config.json':b'overwrite'})
        package = self.package()
        with self.assertRaises(ValueError): read_package(self.base/'2.20.2.zip','0'*64)

    def test_pause_failure_resumes_without_changing_files(self):
        self.installer.apply(self.package(),self.platform)
        class Busy(Offline):
            resumed = False
            def pause(self, root, state): raise TimeoutError('busy')
            def resume(self, root, state): self.resumed = True
        busy = Busy()
        with self.assertRaises(TimeoutError): self.installer.apply(self.package('2.20.3'),busy)
        self.assertTrue(busy.resumed)
        self.assertEqual(self.installer.read_state()['version'],'2.20.2')

    def test_contract_rejects_schema_change(self):
        self.package()
        path = self.base/'2.20.2.zip'
        with zipfile.ZipFile(path) as bundle: files={n:bundle.read(n) for n in bundle.namelist()}
        descriptor=json.loads(files['INSTALL.json']); descriptor['compatibility']['archive']=999
        files['INSTALL.json']=json.dumps(descriptor).encode()
        files['MANIFEST.json']=json.dumps({n:digest(v) for n,v in files.items() if n!='MANIFEST.json'}).encode()
        with zipfile.ZipFile(path,'w') as bundle:
            for n,v in files.items(): bundle.writestr(n,v)
        with self.assertRaisesRegex(ValueError,'compatibility'): read_package(path,digest(path.read_bytes()))


if __name__=='__main__': unittest.main()
