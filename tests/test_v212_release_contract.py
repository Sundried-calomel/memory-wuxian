import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V212ReleaseContractTest(unittest.TestCase):
    def test_release_contract_and_capability_receipt_are_bound(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.12.0")
        contract = json.loads((ROOT / "docs/work-contracts/v2.12.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["validation_profile"], "full")
        self.assertIn("Raw messages", contract["invariants"][0])
        receipt = json.loads((ROOT / "docs/capability-admission/v2.12.0/receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "allowed")
        self.assertEqual(receipt["candidate_id"], "federated-daily-metrics")

    def test_protocol_and_dashboard_contract_are_explicit(self):
        federation = (ROOT / "scripts/memory_federation.py").read_text(encoding="utf-8")
        self.assertIn("PROTOCOL_VERSION = 2", federation)
        self.assertIn("MINIMUM_PROTOCOL_VERSION = 1", federation)
        self.assertIn('"token-usage"', federation)
        dashboard = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
        dashboard_backend = (ROOT / "scripts/memory_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("FORMAT_VERSION = 4", dashboard_backend)
        for token in (
            'data-daily-mode="messages"',
            'data-daily-mode="reported_tokens"',
            "renderDailyDrilldown",
            "complete_token_coverage",
            "全设备",
            "All devices",
            "全デバイス",
        ):
            self.assertIn(token, dashboard)

    def test_daily_metrics_are_derived_and_documented_in_all_locales(self):
        self.assertTrue((ROOT / "scripts/daily_metrics.py").is_file())
        for readme in ("README.md", "README.zh-CN.md", "README.ja.md"):
            text = (ROOT / readme).read_text(encoding="utf-8")
            for token in (
                "daily_metrics.py",
                "trusted synchronized devices",
                "Codex-reported",
                "Asia/Tokyo",
                "protocol v2",
            ):
                self.assertIn(token, text, f"{readme}: {token}")


if __name__ == "__main__":
    unittest.main()
