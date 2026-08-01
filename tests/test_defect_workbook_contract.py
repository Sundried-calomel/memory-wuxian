from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "docs" / "retrospectives" / "memory-wuxian-defect-workbook.md"


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


if __name__ == "__main__":
    unittest.main()
