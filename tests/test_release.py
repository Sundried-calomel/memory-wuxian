import gzip
import importlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
from runtime import MemoryRuntime
from backup import BackupService
from environment import EnvironmentService
from dashboard import status_payload, make_server
from core_sync import _wire_payload, _GZIP_MAGIC, MAX_PAGE_BYTES


class ReleaseChecks(unittest.TestCase):
    def test_dashboard_loopback_needs_no_reverse_dns(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=MemoryRuntime(Path(directory)/'archive')
            with patch('socket.getfqdn',side_effect=AssertionError('unexpected hostname lookup')):
                with make_server(runtime,port=0) as server:
                    self.assertEqual(server.server_name,'localhost')

    def test_entry_imports(self):
        for path in (ROOT / 'core').glob('*.py'):
            importlib.import_module(path.stem)

    def test_archive_search_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = MemoryRuntime(root / 'archive')
            for _ in range(2):
                runtime.store.append_message('user', 'portable archive decision', conversation_id='c', message_id='u')
            self.assertEqual(runtime.store.status()['total_messages'], 1)
            self.assertTrue(runtime.query.query('decision')['results'])
            snapshot = runtime.backup.create(root / 'backups', keep=1)
            BackupService.restore(snapshot['path'], root / 'restored')
            restored = MemoryRuntime(root / 'restored')
            self.assertEqual(restored.store.message_by_id('u')['text'], 'portable archive decision')
            status = status_payload(restored)
            self.assertEqual(len(status['conversations']), 1)
            self.assertIsInstance(status['active_conversations'], list)

    def test_local_edit_blocks_environment_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'AGENTS.md'
            target.write_text('original', encoding='utf-8')
            service = EnvironmentService(root)
            service.bind('rule', 'AGENTS.md')
            target.write_text('local edit', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                service.apply('rule', {'': b'remote edit'})
            self.assertEqual(target.read_text('utf-8'), 'local edit')

    def test_large_payload_is_lossless(self):
        data = b'x' * (MAX_PAGE_BYTES + 100)
        wire = _wire_payload(data, MAX_PAGE_BYTES)
        self.assertLess(len(wire), MAX_PAGE_BYTES)
        self.assertTrue(wire.startswith(_GZIP_MAGIC))
        self.assertEqual(gzip.decompress(wire[len(_GZIP_MAGIC):]), data)


if __name__ == '__main__':
    unittest.main()
