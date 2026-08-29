from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "windows" / "install.ps1"


@unittest.skipUnless(os.name == "nt", "Windows PowerShell bootstrap contract")
class WindowsInnoBootstrapTests(unittest.TestCase):
    def run_until_sessions_gate(self, *, existing_skill: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = root / "user"
            skill = user / ".codex" / "skills" / "memory-wuxian"
            candidate = root / "candidate"
            (candidate / "bin").mkdir(parents=True)
            (candidate / "SKILL.md").write_text("candidate\n", encoding="utf-8")
            (candidate / "config.yaml").write_text("archive_root: test\n", encoding="utf-8")
            (candidate / "bin" / "memory-wuxian-collector.exe").write_bytes(b"collector")
            skill.mkdir(parents=True)
            if existing_skill:
                (skill / "SKILL.md").write_text("installed\n", encoding="utf-8")
                (skill / "config.yaml").write_text("installed: true\n", encoding="utf-8")
                (skill / "do-not-copy.txt").write_text("foreign\n", encoding="utf-8")
            metadata = {
                "unins000.exe": b"inno-executable",
                "unins000.dat": b"inno-data",
            }
            for name, payload in metadata.items():
                (skill / name).write_bytes(payload)

            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(INSTALLER),
                    "-SkillRoot",
                    str(skill),
                    "-CandidateRoot",
                    str(candidate),
                    "-SourceEntrypoint",
                    "inno",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Codex sessions root was not found", completed.stderr)
            for name, payload in metadata.items():
                self.assertEqual((candidate / name).read_bytes(), payload)
                self.assertEqual((skill / name).read_bytes(), payload)
            self.assertFalse((candidate / "do-not-copy.txt").exists())

    def test_clean_inno_placeholder_is_admitted_and_metadata_is_preserved(self) -> None:
        self.run_until_sessions_gate(existing_skill=False)

    def test_existing_install_preserves_only_inno_metadata(self) -> None:
        self.run_until_sessions_gate(existing_skill=True)


if __name__ == "__main__":
    unittest.main()
