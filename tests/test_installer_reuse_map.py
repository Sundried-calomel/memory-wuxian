from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_installer_reuse_map import ReuseMapError, validate_reuse_map


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InstallerReuseMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "docs/receipts").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        self.receipt = self.root / "docs/receipts/replan.json"
        self.receipt.write_text("{}\n", encoding="utf-8")
        self.exact = self.root / "scripts/exact.py"
        self.exact.write_text("exact\n", encoding="utf-8")
        self.correction = self.root / "scripts/correction.py"
        self.correction.write_text("incorrect\n", encoding="utf-8")
        self.composition = self.root / "scripts/composition.py"
        self.composition.write_text("old\n", encoding="utf-8")
        self.map = self.root / "reuse.json"
        self.document = {
            "schema_version": 1,
            "replan_receipt": {
                "path": "docs/receipts/replan.json",
                "sha256": digest(self.receipt),
            },
            "enforced_steps": ["S06"],
            "artifacts": [
                {
                    "path": "scripts/exact.py",
                    "disposition": "reuse_exact",
                    "baseline_sha256": digest(self.exact),
                },
                {
                    "path": "scripts/composition.py",
                    "disposition": "replace_composition",
                    "baseline_sha256": digest(self.composition),
                },
                {
                    "path": "scripts/correction.py",
                    "disposition": "reuse_with_correction",
                    "baseline_sha256": digest(self.correction),
                },
            ],
            "invalidated_receipts": ["S06", "S07"],
            "replacement_contract": {
                "path": "scripts/composition.py",
                "forbidden_classes": ["ComposedInstallMutation"],
                "required_mutation_classes": ["FirstMutation", "SecondMutation"],
                "required_methods": ["apply", "verify", "commit", "rollback"],
            },
            "required_invariants": ["fixture"],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_map(self) -> None:
        self.map.write_text(json.dumps(self.document), encoding="utf-8")

    def test_gate_requires_composition_change_and_preserves_exact_reuse(self) -> None:
        self.correction.write_text("corrected\n", encoding="utf-8")
        self.write_map()
        with self.assertRaisesRegex(ReuseMapError, "required bounded mutation"):
            validate_reuse_map(self.root, self.map, step="S06")
        self.composition.write_text(
            "class FirstMutation:\n"
            "    def apply(self): pass\n    def verify(self): pass\n"
            "    def commit(self): pass\n    def rollback(self): pass\n"
            "class SecondMutation:\n"
            "    def apply(self): pass\n    def verify(self): pass\n"
            "    def commit(self): pass\n    def rollback(self): pass\n",
            encoding="utf-8",
        )
        self.correction.write_text("corrected\n", encoding="utf-8")
        result = validate_reuse_map(self.root, self.map, step="S06")
        self.assertTrue(result["composition_changed"])
        self.assertEqual(result["exact_reuse_verified"], ["scripts/exact.py"])
        self.assertEqual(result["corrections_verified"], ["scripts/correction.py"])

    def test_gate_requires_declared_correction_to_change(self) -> None:
        self._write_valid_composition()
        self.write_map()
        with self.assertRaisesRegex(ReuseMapError, "correction was not applied"):
            validate_reuse_map(self.root, self.map, step="S06")

    def test_gate_fails_closed_on_exact_reuse_or_receipt_drift(self) -> None:
        self._write_valid_composition()
        self.correction.write_text("corrected\n", encoding="utf-8")
        self.write_map()
        self.exact.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ReuseMapError, "exact-reuse"):
            validate_reuse_map(self.root, self.map, step="S06")
        self.exact.write_text("exact\n", encoding="utf-8")
        self.receipt.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ReuseMapError, "replan receipt"):
            validate_reuse_map(self.root, self.map, step="S06")

    def test_gate_allows_completed_upstream_but_rejects_completed_downstream(self) -> None:
        self.document["enforced_steps"] = ["S06", "S07"]
        self._write_valid_composition()
        self.correction.write_text("corrected\n", encoding="utf-8")
        self.write_map()
        state = {"steps": {"S06": {"status": "completed"}, "S07": {"status": "in_progress"}}}
        validate_reuse_map(self.root, self.map, step="S07", state=state)
        state["steps"]["S07"]["status"] = "completed"
        with self.assertRaisesRegex(ReuseMapError, "downstream"):
            validate_reuse_map(self.root, self.map, step="S06", state=state)

    def _write_valid_composition(self) -> None:
        self.composition.write_text(
            "class FirstMutation:\n"
            "    def apply(self): pass\n    def verify(self): pass\n"
            "    def commit(self): pass\n    def rollback(self): pass\n"
            "class SecondMutation:\n"
            "    def apply(self): pass\n    def verify(self): pass\n"
            "    def commit(self): pass\n    def rollback(self): pass\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
