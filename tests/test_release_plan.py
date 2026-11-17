import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_checks', ROOT/'scripts/release_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class ReleasePlan(unittest.TestCase):
    def test_docs_do_not_trigger_regressions(self):
        self.assertEqual(checks.select(['README.md','VERSION'])['tests'], [])

    def test_mcp_patch_is_incremental(self):
        plan = checks.select(['core/mcp_server.py'])
        self.assertEqual(plan['tests'], ['test_sync_waiting_mcp'])
        self.assertFalse(plan['native'])
        self.assertFalse(plan['package'])

    def test_environment_patch(self):
        self.assertEqual(checks.select(['core/environment.py'])['tests'],
                         ['test_environment_autosync','test_sync_waiting_mcp'])

    def test_packaging_keeps_installer_checks(self):
        plan = checks.select(['build_release.py'])
        self.assertEqual(plan['tests'], ['test_installer','test_updates'])
        self.assertTrue(plan['package'])

    def test_shared_changes_and_explicit_full(self):
        self.assertEqual(checks.select(['core/storage.py'])['tests'], checks.ALL)
        self.assertEqual(checks.select([],True)['tests'], checks.ALL)

    def test_no_duplicate_tests(self):
        plan=checks.select(['core/mcp_server.py','core/environment.py','tests/test_sync_waiting_mcp.py'])
        self.assertEqual(len(plan['tests']),len(set(plan['tests'])))

    def test_release_has_no_sidecar_checksum_assets(self):
        workflow=(ROOT/'.github/workflows/release-core.yml').read_text('utf-8')
        builder=(ROOT/'build_release.py').read_text('utf-8')
        self.assertNotIn('SHA256SUMS',workflow)
        self.assertNotIn("+'.sha256'",builder)
        self.assertIn("selected['sha256']",(ROOT/'installer/updates.py').read_text('utf-8'))
