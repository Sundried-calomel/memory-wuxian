import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "memory_cli.py"
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment import revision_id_for


class EnvironmentCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "memory"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            f'memory:\n  root_directory: "{self.archive}"\n',
            encoding="utf-8",
        )
        self.run_cli("init")
        self.manifest_path = self.base / "environment-manifest.json"
        self.manifest_path.write_text(
            json.dumps(self.manifest(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def invoke_cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--root",
                str(self.archive),
                "--config",
                str(self.config),
                *arguments,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def run_cli(self, *arguments):
        completed = self.invoke_cli(*arguments)
        if completed.returncode != 0:
            self.fail(
                f"Command failed: {completed.args}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return json.loads(completed.stdout)

    @staticmethod
    def manifest():
        content = "Shared rule\n"
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        artifact = {
            "schema_version": 1,
            "artifact_id": "global-rule:codex-agents",
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Codex shared rules",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact["artifact_id"],
            "origin_node_id": "mac-mini-lab",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": content_sha256,
            "object_path": (
                f"objects/sha256/{content_sha256[:2]}/{content_sha256[2:]}"
            ),
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.12"},
            "provenance": {"source": "explicit-manifest"},
            "lifecycle_state": "discovered",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        return {
            "schema_version": 1,
            "artifacts": [
                {"artifact": artifact, "revision": revision, "content": content}
            ],
            "projects": [],
        }

    def test_preview_is_default_and_does_not_initialize_environment(self):
        result = self.run_cli(
            "environment-register",
            "--manifest",
            str(self.manifest_path),
        )
        self.assertEqual(result["status"], "preview")
        self.assertFalse((self.archive / "environment").exists())

    def test_apply_list_show_validate_and_status(self):
        initialized = self.run_cli("environment-init")
        self.assertEqual(initialized["status"], "initialized")
        applied = self.run_cli(
            "environment-register",
            "--manifest",
            str(self.manifest_path),
            "--apply",
        )
        self.assertEqual(applied["status"], "registered")
        listed = self.run_cli(
            "environment-list",
            "--object-class",
            "global-rule",
        )
        self.assertEqual(
            listed["artifacts"][0]["artifact_id"],
            "global-rule:codex-agents",
        )
        shown = self.run_cli(
            "environment-show",
            "--artifact-id",
            "global-rule:codex-agents",
        )
        self.assertEqual(shown["revision"]["version"], 1)
        self.assertEqual(self.run_cli("environment-validate")["status"], "valid")
        self.assertEqual(self.run_cli("environment-status")["artifacts"], 1)

    def test_scan_requires_an_explicit_source(self):
        completed = self.invoke_cli("environment-scan")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Provide --manifest or --scan-root", completed.stderr)

    def test_environment_commands_do_not_use_archive_lock(self):
        locks = self.archive / ".locks"
        locks.mkdir(exist_ok=True)
        (locks / "archive.lock").write_text("collector\n", encoding="utf-8")
        result = self.run_cli("environment-init")
        self.assertEqual(result["status"], "initialized")
        self.assertTrue((locks / "archive.lock").exists())
        self.assertTrue((locks / "environment.lock").exists())


if __name__ == "__main__":
    unittest.main()
