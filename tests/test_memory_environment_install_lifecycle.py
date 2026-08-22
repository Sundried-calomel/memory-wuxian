from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from memory_environment_install_lifecycle import InstallLifecycleCoordinator  # noqa: E402


class FakeAdapter:
    def __init__(self, decision: str):
        self.decision = decision
        self.trace: list[str] = []

    def recover(self) -> None:
        self.trace.append("recover")

    def prepare(self) -> dict[str, str]:
        self.trace.append("prepare")
        return {"decision": self.decision}

    def no_change_result(self, prepared: dict[str, str]):
        self.trace.append("no-change")
        if prepared["decision"] == "no-change":
            return {"status": "no-change"}
        return None

    def preview_result(self, prepared: dict[str, str]):
        self.trace.append("preview")
        return {"status": "preview", **prepared}

    def apply_prepared(self, prepared: dict[str, str]):
        self.trace.append("apply")
        return {"status": "installed", **prepared}


class InstallLifecycleCoordinatorTests(unittest.TestCase):
    def test_no_change_stops_before_preview_and_apply(self):
        adapter = FakeAdapter("no-change")

        result = InstallLifecycleCoordinator().run(adapter, apply=True)

        self.assertEqual(result, {"status": "no-change"})
        self.assertEqual(adapter.trace, ["recover", "prepare", "no-change"])

    def test_preview_stops_before_apply(self):
        adapter = FakeAdapter("update")

        result = InstallLifecycleCoordinator().run(adapter, apply=False)

        self.assertEqual(result, {"status": "preview", "decision": "update"})
        self.assertEqual(
            adapter.trace,
            ["recover", "prepare", "no-change", "preview"],
        )

    def test_apply_runs_only_after_recovery_and_prepare(self):
        adapter = FakeAdapter("update")

        result = InstallLifecycleCoordinator().run(adapter, apply=True)

        self.assertEqual(result, {"status": "installed", "decision": "update"})
        self.assertEqual(
            adapter.trace,
            ["recover", "prepare", "no-change", "apply"],
        )


if __name__ == "__main__":
    unittest.main()
