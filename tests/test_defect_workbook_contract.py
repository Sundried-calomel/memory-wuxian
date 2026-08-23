from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "docs" / "retrospectives" / "memory-wuxian-defect-workbook.md"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_text_sha256(path: Path) -> str:
    canonical = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DefectWorkbookContractTests(unittest.TestCase):
    def test_workbook_keeps_required_recurrence_families(self) -> None:
        text = WORKBOOK.read_text(encoding="utf-8")
        expected = {f"MW-R{number:02d}" for number in range(1, 13)}
        found = set(re.findall(r"MW-R\d{2}", text))
        self.assertTrue(expected.issubset(found))

    def test_incident_ids_are_unique(self) -> None:
        text = WORKBOOK.read_text(encoding="utf-8")
        ids = re.findall(r"^### (MW-(?:WIN|MAC)-\d{3})", text, flags=re.MULTILINE)
        self.assertGreaterEqual(len(ids), 13)
        self.assertEqual(len(ids), len(set(ids)))

    def test_workbook_preserves_evidence_and_prevention_fields(self) -> None:
        text = WORKBOOK.read_text(encoding="utf-8")
        for required in (
            "## 证据覆盖",
            "## 复发矩阵",
            "## Windows 事故账",
            "## macOS 事故账",
            "## 当前未闭合证据与运行债务",
            "## 每次缺陷更新流程",
            "逃逸边界",
            "真实效果检查",
        ):
            self.assertIn(required, text)

    def test_agent_rules_route_future_changes_through_workbook(self) -> None:
        rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(
            "docs/retrospectives/memory-wuxian-defect-workbook.md", rules
        )
        self.assertIn("adjacent-entry regression", rules)
        self.assertIn("installed or production-sized effect check", rules)

    def test_future_release_contracts_bind_defect_gate_receipts(self) -> None:
        for path in sorted((ROOT / "docs" / "work-contracts").glob("v*.json")):
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\.json", path.name)
            if not match or tuple(map(int, match.groups())) <= (2, 12, 3):
                continue
            contract = json.loads(path.read_text(encoding="utf-8"))
            gate = contract.get("defect_workbook")
            self.assertIsInstance(gate, dict, path.name)
            self.assertTrue(gate.get("applicable_families"), path.name)
            is_repair = contract.get("classification") == "repair"
            is_hotfix = "hotfix" in contract.get("candidate_id", "").lower()
            if is_repair or is_hotfix:
                self.assertTrue(gate.get("original_triggers"), path.name)
                self.assertIsInstance(gate.get("project_workbook_updated"), bool, path.name)
            if is_hotfix:
                self.assertTrue(gate["project_workbook_updated"], path.name)
            for field in ("preflight", "completion"):
                if field == "completion" and gate.get("completion_sha256") == "PENDING":
                    self.assertNotIn(
                        contract.get("lifecycle_state"),
                        {"complete", "completed", "installed", "published", "released"},
                        path.name,
                    )
                    continue
                receipt = ROOT / gate[f"{field}_receipt"]
                self.assertTrue(receipt.is_file(), f"{path.name}: missing {receipt}")
                version = tuple(map(int, match.groups()))
                actual = (
                    file_sha256(receipt)
                    if version >= (2, 20, 0)
                    else legacy_text_sha256(receipt)
                )
                self.assertEqual(actual, gate[f"{field}_sha256"], path.name)


if __name__ == "__main__":
    unittest.main()
