import base64
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from environment import EnvironmentService, reconcile
from storage import bytes_sha256
from environment_sync import validate_files, render_agents


class SyncTests(unittest.TestCase):
    def test_remote_only(self):
        self.assertEqual(reconcile(b'a', b'b', bytes_sha256(b'a')), b'b')

    def test_local_only(self):
        self.assertEqual(reconcile(b'b', b'a', bytes_sha256(b'a')), b'b')

    def test_identical(self):
        self.assertEqual(reconcile(b'b', b'b', bytes_sha256(b'a')), b'b')

    def test_new_remote(self):
        self.assertEqual(reconcile(None, b'a', None), b'a')

    def test_local_delete_preserved(self):
        self.assertIsNone(reconcile(None, b'a', bytes_sha256(b'a')))

    def test_no_base_conflict(self):
        with self.assertRaises(RuntimeError):
            reconcile(b'b', b'c', bytes_sha256(b'a'))

    def test_merge(self):
        base = b'one\n2\n3\n4\n5\n6\nseven\n'
        local = base.replace(b'one', b'local')
        remote = base.replace(b'seven', b'remote')
        self.assertEqual(reconcile(local, remote, bytes_sha256(base), base64.b64encode(base)),
                         local.replace(b'seven', b'remote'))

    def test_overlap(self):
        with self.assertRaises(RuntimeError):
            reconcile(b'local\n', b'remote\n', bytes_sha256(b'base\n'), base64.b64encode(b'base\n'))

    def test_bad_base(self):
        with self.assertRaises(RuntimeError):
            reconcile(b'b', b'c', bytes_sha256(b'a'), base64.b64encode(b'wrong'))

    def test_cycles(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'AGENTS.md'
            p.write_bytes(b'a')
            svc = EnvironmentService(root)
            svc.bind('rules', p)
            svc.apply('rules', {'': b'b'})
            self.assertEqual(p.read_bytes(), b'b')
            svc.apply('rules', {'': b'c'})
            self.assertEqual(p.read_bytes(), b'c')
            p.write_bytes(b'local')
            svc.apply('rules', {'': b'c'})
            self.assertEqual(p.read_bytes(), b'local')
            svc.apply('rules', {'': b'local'})
            svc.apply('rules', {'': b'next'})
            self.assertEqual(p.read_bytes(), b'next')

    def test_conflict_atomic(self):
        with tempfile.TemporaryDirectory() as root:
            tree = Path(root) / 'skill'
            tree.mkdir()
            (tree/'SKILL.md').write_bytes(b'entry')
            (tree/'x.py').write_bytes(b'base\n')
            svc = EnvironmentService(root)
            svc.bind('skill', tree, 'skill-tree')
            (tree/'x.py').write_bytes(b'local\n')
            with self.assertRaises(RuntimeError):
                svc.apply('skill', {'SKILL.md': b'changed', 'x.py': b'remote\n'}, dependency_check=lambda x: True)
            self.assertEqual((tree/'SKILL.md').read_bytes(), b'entry')
            self.assertEqual((tree/'x.py').read_bytes(), b'local\n')

    def test_template(self):
        files = {'AGENTS.md': b'Use {{MW_PATH:project}}', 'path-keys.json': b'{"format":"memory-wuxian-path-refs-v1","keys":["project"]}'}
        validate_files('global-codex-agents', files)
        with self.assertRaises(ValueError):
            render_agents(files, {})
        self.assertEqual(render_agents(files, {'path_bindings':{'project':'D:/project'}}), b'Use D:/project')

    def test_two_devices(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            paths = [Path(p)/'AGENTS.md' for p in (left,right)]
            services = [EnvironmentService(p) for p in (left,right)]
            for p,s in zip(paths,services):
                p.write_bytes(b'base')
                s.bind('rules',p)
            for index, text in [(0,b'left change'),(1,b'right change'),(0,b'left again')]:
                paths[index].write_bytes(text)
                services[1-index].apply('rules', {'':text})
                services[index].apply('rules', {'':paths[1-index].read_bytes()})
                self.assertEqual(paths[0].read_bytes(),paths[1].read_bytes())


if __name__ == '__main__':
    unittest.main()
