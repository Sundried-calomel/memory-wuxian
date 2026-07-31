from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_release_rehearsal.py"
sys.path.insert(0, str(ROOT / "scripts"))
from run_release_rehearsal import source_identity


class RehearsalEvidenceReuseTests(unittest.TestCase):
    def test_reuses_full_suite_evidence_for_focused_unittest_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "unittest.log"
            evidence.write_text(
                f"SOURCE_CONTENT_SHA256={source_identity()[2]}\n"
                "test_mw29_signature_001_metadata_authenticity_fails_closed (tests.test_update_governance.UpdateGovernanceTests.test_mw29_signature_001_metadata_authenticity_fails_closed) ... ok\n"
                "test_mw210_01_capture_is_deterministic_and_deduplicated (tests.test_memory_environment_profiles.EnvironmentProfileTests.test_mw210_01_capture_is_deterministic_and_deduplicated) ... ok\n"
                "Ran 353 tests in 12.345s\n\nOK (skipped=7)\n",
                encoding="utf-8",
            )
            output = root / "rehearsal"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output",
                    str(output),
                    "--exclude-baseline",
                    "--reuse-unittest-evidence",
                    str(evidence),
                    "--scenario-shard-index",
                    "1",
                    "--scenario-shard-count",
                    "31",
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            reused = [
                item
                for item in report["scenarios"]
                if "reused_evidence_sha256" in item
            ]
            self.assertGreaterEqual(len(reused), 1)
            references = [output / item["evidence"] for item in reused]
            self.assertEqual(len(references), len(set(references)))
            for item, reference in zip(reused, references):
                self.assertIn(
                    item["reused_evidence_sha256"],
                    reference.read_text(encoding="utf-8"),
                )

    def test_missing_reused_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output",
                    str(Path(temporary) / "rehearsal"),
                    "--exclude-baseline",
                    "--reuse-unittest-evidence",
                    str(Path(temporary) / "missing.log"),
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("Reusable unittest evidence is missing", completed.stderr)

    def test_failed_unittest_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "unittest.log"
            evidence.write_text(
                "Ran 353 tests in 12.345s\n\nFAILED (failures=1)\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output",
                    str(root / "rehearsal"),
                    "--exclude-baseline",
                    "--reuse-unittest-evidence",
                    str(evidence),
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn(
                "Reusable unittest evidence has no final OK result",
                completed.stderr,
            )

    def test_required_signature_skip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "unittest.log"
            evidence.write_text(
                f"SOURCE_CONTENT_SHA256={source_identity()[2]}\n"
                "test_mw29_signature_001_metadata_authenticity_fails_closed (tests.test_update_governance.UpdateGovernanceTests.test_mw29_signature_001_metadata_authenticity_fails_closed) ... skipped 'missing'\n"
                "Ran 1 test in 0.001s\n\nOK (skipped=1)\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(root / "rehearsal"), "--exclude-baseline", "--reuse-unittest-evidence", str(evidence)],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("does not prove the mandatory update-signature case", completed.stderr)


if __name__ == "__main__":
    unittest.main()
