from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_transaction_diagnostic.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=False)
    assert completed.returncode == expect, completed.stdout + completed.stderr
    return completed


class InstallTransactionDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.installer = self.root / "Memory Wuxian 日本語 Setup.exe"
        self.installer.write_bytes(b"frozen-installer")
        self.guard = self.root / "protected"
        self.guard.mkdir()
        (self.guard / "state.txt").write_text("unchanged\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_hash_mismatch_fails_before_bundle_creation(self) -> None:
        output = self.root / "diagnostic"
        result = run(
            "prepare-sandbox", "--installer", str(self.installer),
            "--expected-sha256", "0" * 64, "--output-root", str(output),
            "--guard-path", str(self.guard), expect=2,
        )
        self.assertIn("does not match", result.stderr)
        self.assertFalse(output.exists())

    def test_output_root_must_not_overlap_protected_path(self) -> None:
        result = run(
            "prepare-sandbox", "--installer", str(self.installer),
            "--expected-sha256", sha256(self.installer),
            "--output-root", str(self.guard / "diagnostic"),
            "--guard-path", str(self.guard), expect=2,
        )
        self.assertIn("overlaps protected path", result.stderr)

    def test_prepared_bundle_is_hash_bound_and_leaves_guard_unchanged(self) -> None:
        output = self.root / "diagnostic"
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT), "prepare-sandbox",
                "--installer", str(self.installer),
                "--expected-sha256", sha256(self.installer),
                "--output-root", str(output), "--guard-path", str(self.guard),
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertIn(result.returncode, {0, 3}, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "prepared" if result.returncode == 0 else "backend-unavailable")
        self.assertTrue(receipt["guard_snapshot_unchanged"])
        config = (output / "MemoryWuxianInstallerDiagnostic.wsb").read_text(encoding="utf-8")
        self.assertIn("<ReadOnly>true</ReadOnly>", config)
        self.assertIn("<Networking>Disable</Networking>", config)
        copied = output / "input" / self.installer.name
        self.assertEqual(sha256(copied), sha256(self.installer))
        self.assertEqual((self.guard / "state.txt").read_text(encoding="utf-8"), "unchanged\n")


if __name__ == "__main__":
    unittest.main()
