import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_environment import (
    EnvironmentRegistry,
    _strict_keys,
    canonical_bytes,
    revision_id_for,
    sha256_bytes,
)


def make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            raise OSError(completed.stderr or completed.stdout)
    else:
        link.symlink_to(target, target_is_directory=True)


def authority_hashes(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "environment" not in path.parts and path.name != "environment.lock":
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


class EnvironmentRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "memory"
        self.archive.mkdir()
        (self.archive / "state.json").write_text(
            '{"format_version":1,"total_messages":2}\n', encoding="utf-8"
        )
        (self.archive / "raw").mkdir()
        (self.archive / "raw" / "messages.jsonl").write_text(
            '{"message_id":"m1"}\n', encoding="utf-8"
        )
        (self.archive / "summaries").mkdir()
        (self.archive / "summaries" / "L1.json").write_text(
            '{"summary_id":"L1"}\n', encoding="utf-8"
        )
        (self.archive / "imports" / "codex" / "token-usage").mkdir(parents=True)
        (self.archive / "imports" / "codex" / "token-usage" / "s.json").write_text(
            '{"total_tokens":10}\n', encoding="utf-8"
        )
        self.registry = EnvironmentRegistry(self.archive)
        self.created_at = "2026-07-28T12:00:00+00:00"

    def tearDown(self):
        self.temporary.cleanup()

    def test_canonical_bytes_hash_and_strict_error_order_are_exact(self):
        payload = {"路径": "日本語 ￥ 😀", "flag": True}
        expected = '{"flag":true,"路径":"日本語 ￥ 😀"}'.encode("utf-8")
        self.assertEqual(canonical_bytes(payload), expected)
        self.assertEqual(
            sha256_bytes(expected),
            "74de3bc3fe72f0caffbbac8afaa0ca44ccefee50397b9ca6532dab2e44a8738c",
        )
        with self.assertRaisesRegex(
            ValueError,
            r"^fixture: unknown fields: \['extra'\]$",
        ):
            _strict_keys({"extra": 1}, {"required"}, {"required"}, "fixture")

    def test_read_registry_revalidates_authority_path(self):
        self.registry.init()
        registry_path = self.registry.registry_path
        original = sys.modules["memory_environment"].is_link_like

        with patch(
            "memory_environment.is_link_like",
            side_effect=lambda path: Path(path) == registry_path or original(path),
        ):
            with self.assertRaisesRegex(ValueError, "symlink path"):
                self.registry.status()

    def artifact(self, artifact_id="global-rule:codex-agents", **updates):
        value = {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Codex shared rules",
            "created_at": self.created_at,
        }
        value.update(updates)
        return value

    def revision(
        self,
        artifact_id="global-rule:codex-agents",
        content="Shared rule\n",
        *,
        version=1,
        base_revision_id=None,
        **updates,
    ):
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        value = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact_id,
            "origin_node_id": "mac-mini-lab",
            "version": version,
            "base_revision_id": base_revision_id,
            "content_sha256": content_hash,
            "object_path": f"objects/sha256/{content_hash[:2]}/{content_hash[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.12"},
            "provenance": {"source": "explicit-manifest"},
            "lifecycle_state": "discovered",
            "created_at": self.created_at,
        }
        value.update(updates)
        value["revision_id"] = revision_id_for(value)
        return value

    def project(self, local_root="/Users/test/ORF1 library", **updates):
        value = {
            "schema_version": 1,
            "project_id": "orf1-library",
            "display_name": "ORF1 library",
            "local_root": local_root,
            "active": True,
            "rule_bindings": [
                {
                    "binding_id": "project-agents",
                    "relative_path": "PROJECT_AGENTS.md",
                    "classification": "canonical",
                    "install_strategy": "managed-block",
                    "managed_block_id": "shared-rules",
                }
            ],
            "skill_bindings": [
                {"skill_id": "orf1-gate", "enabled": True, "pinned_version": None}
            ],
        }
        value.update(updates)
        return value

    def manifest(self, *, artifact=None, revision=None, content="Shared rule\n", projects=None):
        artifact = artifact or self.artifact()
        revision = revision or self.revision(artifact["artifact_id"], content)
        return {
            "schema_version": 1,
            "artifacts": [
                {"artifact": artifact, "revision": revision, "content": content}
            ],
            "projects": projects or [],
        }

    def test_schema_fields_are_persisted_exactly(self):
        self.registry.register(self.manifest(), apply=True)
        shown = self.registry.show("global-rule:codex-agents")
        self.assertEqual(set(shown["artifact"]), {
            "schema_version", "artifact_id", "object_class", "scope",
            "project_id", "display_name", "created_at",
        })
        self.assertEqual(set(shown["revision"]), {
            "schema_version", "revision_id", "artifact_id", "origin_node_id",
            "version", "base_revision_id", "content_sha256", "object_path",
            "supported_platforms", "runtime_requirements", "provenance",
            "lifecycle_state", "created_at",
        })
        self.assertRegex(shown["revision"]["revision_id"], r"^rev:[0-9a-f]{64}$")
        self.assertEqual(self.registry.validate()["status"], "valid")

    def test_init_preview_and_apply_preserve_1x_authority(self):
        before = authority_hashes(self.archive)
        self.registry.init()
        self.assertTrue(self.registry.receipts_dir.is_dir())
        self.assertEqual(self.registry.register(self.manifest())["status"], "preview")
        self.registry.register(self.manifest(), apply=True)
        self.assertEqual(authority_hashes(self.archive), before)

    def test_repeated_registration_is_no_change(self):
        self.registry.register(self.manifest(), apply=True)
        before = self.registry.registry_path.read_bytes()
        result = self.registry.register(self.manifest(), apply=True)
        self.assertEqual(result["status"], "no-change")
        self.assertEqual(self.registry.registry_path.read_bytes(), before)

    def test_update_requires_current_base_and_next_version(self):
        self.registry.register(self.manifest(), apply=True)
        current = self.registry.show("global-rule:codex-agents")["revision"]["revision_id"]
        stale = self.revision(
            content="Changed\n", version=2, base_revision_id="rev:" + "1" * 64
        )
        with self.assertRaisesRegex(ValueError, "stale base_revision_id"):
            self.registry.register(self.manifest(revision=stale, content="Changed\n"))
        valid = self.revision(content="Changed\n", version=2, base_revision_id=current)
        result = self.registry.register(
            self.manifest(revision=valid, content="Changed\n"), apply=True
        )
        self.assertEqual(result["status"], "registered")

    def test_create_requires_null_base_and_version_one(self):
        bad = self.revision(version=2, base_revision_id="rev:" + "2" * 64)
        with self.assertRaisesRegex(ValueError, "create requires base_revision_id=null"):
            self.registry.register(self.manifest(revision=bad))

    def test_duplicate_artifact_and_project_ids_fail_closed(self):
        manifest = self.manifest(projects=[self.project(), self.project()])
        manifest["artifacts"].append(dict(manifest["artifacts"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate artifact_id"):
            self.registry.register(manifest)
        manifest = self.manifest(projects=[self.project(), self.project()])
        with self.assertRaisesRegex(ValueError, "duplicate project_id"):
            self.registry.register(manifest)

    def test_project_schema_and_device_local_root_do_not_affect_artifact_identity(self):
        artifact = self.artifact(
            artifact_id="project-skill:orf1-gate",
            object_class="project-skill",
            scope="project",
            project_id="orf1-library",
        )
        revision = self.revision("project-skill:orf1-gate")
        mac = self.manifest(
            artifact=artifact, revision=revision, projects=[self.project()]
        )
        windows = self.manifest(
            artifact=artifact,
            revision=revision,
            projects=[self.project(local_root="D:\\Research\\ORF1 library")],
        )
        self.assertEqual(
            self.registry.register(mac)["artifacts"][0]["artifact"]["artifact_id"],
            self.registry.register(windows)["artifacts"][0]["artifact"]["artifact_id"],
        )
        self.registry.register(mac, apply=True)
        project = self.registry.projects()[0]
        self.assertEqual(project["rule_bindings"][0]["classification"], "canonical")
        self.assertTrue(project["skill_bindings"][0]["enabled"])

    def test_path_traversal_and_symlink_escape_are_rejected(self):
        revision = self.revision()
        revision["object_path"] = "objects/sha256/aa/../../outside"
        revision["revision_id"] = revision_id_for(revision)
        with self.assertRaisesRegex(ValueError, "path traversal"):
            self.registry.register(self.manifest(revision=revision))

        self.registry.init()
        outside = self.base / "outside"
        outside.mkdir()
        content = "symlink content"
        digest = hashlib.sha256(content.encode()).hexdigest()
        link = self.registry.root / "objects" / "sha256" / digest[:2]
        make_directory_link(link, outside)
        revision = self.revision(content=content)
        with self.assertRaisesRegex(ValueError, "symlink|escapes"):
            self.registry.register(
                self.manifest(revision=revision, content=content), apply=True
            )

    def test_registry_event_path_and_revision_hash_are_verified(self):
        self.registry.register(self.manifest(), apply=True)
        registry = json.loads(self.registry.registry_path.read_text())
        registry["events"][0]["revision_path"] = "../outside.json"
        self.registry.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "path traversal"):
            self.registry.validate()

        self.registry = EnvironmentRegistry(self.archive)
        self.registry.registry_path.unlink()
        self.registry.init()
        self.registry.register(self.manifest(), apply=True)
        registry = json.loads(self.registry.registry_path.read_text())
        registry["events"][0]["revision_path"] = (
            "revisions/" + "0" * 64 + "/" + "1" * 64 + ".json"
        )
        self.registry.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not canonical"):
            self.registry.validate()

        self.registry.registry_path.unlink()
        self.registry.init()
        self.registry.register(self.manifest(), apply=True)
        shown = self.registry.show("global-rule:codex-agents")
        revision_path = self.registry.root / shown["revision"]["object_path"]
        revision_path.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.registry.validate()

    def test_project_and_artifact_are_atomically_invisible_on_failure(self):
        artifact = self.artifact(
            artifact_id="project-rule:orf1-agents",
            object_class="project-rule",
            scope="project",
            project_id="orf1-library",
        )
        revision = self.revision("project-rule:orf1-agents")
        manifest = self.manifest(
            artifact=artifact, revision=revision, projects=[self.project()]
        )
        self.registry.init()
        with patch.object(
            self.registry, "_before_registry_commit", side_effect=RuntimeError("simulated")
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.registry.register(manifest, apply=True)
        self.assertEqual(self.registry.list(), [])
        self.assertEqual(self.registry.projects(), [])
        self.assertEqual(self.registry.status()["pending_transactions"], 1)
        self.assertEqual(self.registry.init()["recovered"], 1)

    def test_recovery_rebuilds_derived_views_after_authoritative_commit(self):
        with patch.object(
            self.registry, "_rebuild_derived", side_effect=RuntimeError("simulated")
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.registry.register(self.manifest(), apply=True)
        self.assertEqual(len(self.registry.list()), 1)
        self.assertEqual(self.registry.status()["pending_transactions"], 1)
        self.assertEqual(self.registry.init()["recovered"], 1)
        derived = json.loads(
            (self.registry.manifests_dir / "global-rules.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [item["artifact_id"] for item in derived["artifacts"]],
            ["global-rule:codex-agents"],
        )
        self.assertEqual(self.registry.status()["pending_transactions"], 0)

    def test_archive_lock_is_independent(self):
        locks = self.archive / ".locks"
        locks.mkdir()
        (locks / "archive.lock").write_text("collector\n", encoding="utf-8")
        self.registry.register(self.manifest(), apply=True)
        self.assertTrue((locks / "archive.lock").exists())
        self.assertTrue((locks / "environment.lock").exists())
        self.assertEqual(
            self.registry.register(self.manifest(), apply=True)["status"],
            "no-change",
        )

    def test_unknown_schema_and_explicit_nonrecursive_scan(self):
        manifest = self.manifest()
        manifest["artifacts"][0]["artifact"]["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "unsupported schema_version"):
            self.registry.register(manifest)

        explicit = self.base / "explicit"
        explicit.mkdir()
        (explicit / "environment-manifest.json").write_text(
            json.dumps(self.manifest()), encoding="utf-8"
        )
        nested = explicit / "nested"
        nested.mkdir()
        (nested / "ignored.json").write_text("invalid", encoding="utf-8")
        result = self.registry.scan(roots=[explicit])
        self.assertEqual(len(result["manifests"]), 1)


if __name__ == "__main__":
    unittest.main()
