from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import install_governance_ai as scheduler


class GovernanceAISchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.python = root / "runtime" / "python"
        self.skill = root / "Memory Wuxian"
        self.archive = root / "Archive"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_macos_plist_is_bounded_and_independent(self) -> None:
        payload = scheduler.macos_plist(self.python, self.skill, self.archive)
        encoded = plistlib.dumps(payload)
        decoded = plistlib.loads(encoded)

        self.assertEqual(decoded["StartInterval"], 300)
        self.assertFalse(decoded["KeepAlive"])
        self.assertEqual(decoded["ProcessType"], "Background")
        command = decoded["ProgramArguments"]
        self.assertEqual(command[0], str(self.python))
        self.assertIn("environment-governance-ai-tick", command)
        self.assertIn("--maximum-batches", command)
        self.assertEqual(command[command.index("--maximum-batches") + 1], "1")
        self.assertNotIn("cloud-sync", command)

    def test_windows_task_is_least_privilege_and_bounded(self) -> None:
        root = ET.fromstring(
            scheduler.windows_xml(self.python, self.skill, self.archive)
        )
        namespace = {"t": scheduler.TASK_XML_NAMESPACE}

        self.assertEqual(root.findtext(".//t:Interval", namespaces=namespace), "PT5M")
        self.assertEqual(
            root.findtext(".//t:RunLevel", namespaces=namespace),
            "LeastPrivilege",
        )
        self.assertEqual(
            root.findtext(".//t:ExecutionTimeLimit", namespaces=namespace),
            "PT20M",
        )
        arguments = root.findtext(".//t:Arguments", namespaces=namespace) or ""
        self.assertIn("environment-governance-ai-tick", arguments)
        self.assertIn("--maximum-batches 1", arguments)
        self.assertNotIn("cloud-sync", arguments)


if __name__ == "__main__":
    unittest.main()
