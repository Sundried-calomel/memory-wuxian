"""Exercise the vendored runtime in a separate module namespace, offline."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''import sys,unittest
sys.path.insert(0,sys.argv[1])
from test_summary_v2_runtime_engine import RuntimeEngineTest
from test_summary_v2_runtime_capacity import RuntimeCapacityTest
suite=unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeEngineTest)
suite.addTest(RuntimeCapacityTest('test_real_oversized_multilevel_continuation'))
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


class IsolatedSummaryV2EngineTest(unittest.TestCase):
    def test_runtime_claims_recovery_and_oversized_continuation(self):
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROGRAM,
                                 str(ROOT / 'vendor/summary_v2/tests')],
                                capture_output=True, text=True, encoding='utf-8', timeout=240)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
