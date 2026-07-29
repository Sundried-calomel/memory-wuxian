import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment import EnvironmentRegistry, revision_id_for
from memory_environment_bindings import EnvironmentBindingRegistry
from memory_environment_rules import EnvironmentRuleInstaller
from memory_environment_skills import EnvironmentSkillInstaller


def authority_hashes(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] == "environment":
            continue
        if relative.parts[:1] == (".locks",):
            continue
        hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


class EnvironmentBindingRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
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
        self.rules_root = self.base / "rules"
        self.skills_root = self.base / "skills"
        self.project_root = self.base / "project"
        self.rules_root.mkdir()
        self.skills_root.mkdir()
        self.project_root.mkdir()
        self.registry = EnvironmentRegistry(self.archive)
        self.bindings = EnvironmentBindingRegistry(
            self.registry, node_id="mac-mini-lab", platform_name=self.native_platform()
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def native_platform():
        if os.name == "nt":
            return "windows"
        return "macos" if sys.platform == "darwin" else "linux"

    def artifact(self, artifact_id="global-rule:codex-agents"):
        return {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Codex rules",
            "created_at": "2026-07-28T12:00:00+00:00",
        }

    def revision(self, artifact_id, content):
        digest = hashlib.sha256(content.encode()).hexdigest()
        value = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact_id,
            "origin_node_id": "mac-mini-lab",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {},
            "provenance": {"source": "test"},
            "lifecycle_state": "discovered",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        value["revision_id"] = revision_id_for(value)
        return value

    def register_project_authority(self, *, local_root=None, project_id="orf1-library"):
        content = "project rules\n"
        artifact_id = f"project-rule:{project_id}"
        manifest = {
            "schema_version": 1,
            "artifacts": [
                {
                    "artifact": {
                        **self.artifact(artifact_id),
                        "object_class": "project-rule",
                        "scope": "project",
                        "project_id": project_id,
                    },
                    "revision": self.revision(artifact_id, content),
                    "content": content,
                }
            ],
            "projects": [
                {
                    "schema_version": 1,
                    "project_id": project_id,
                    "display_name": project_id,
                    "local_root": str(local_root or self.project_root),
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
                        {
                            "skill_id": "orf1-gate",
                            "enabled": True,
                            "pinned_version": None,
                        }
                    ],
                }
            ],
        }
        self.registry.register(manifest, apply=True)

    def register_roots(self):
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        self.bindings.register_root(
            root_id="codex-skills",
            role="global-skills",
            owner="user",
            root=self.skills_root,
            apply=True,
        )

    def rule_binding(self, **updates):
        value = {
            "binding_id": "codex-agents",
            "scope": "global",
            "owner": "user",
            "root_id": "codex-rules",
            "relative_path": "AGENTS.md",
            "classification": "canonical",
            "install_strategy": "managed-block",
            "managed_block_id": "memory-wuxian",
            "platform": self.native_platform(),
            "installed_revision_id": None,
            "installed_content_sha256": None,
            "base_revision_id": None,
            "base_content_sha256": None,
        }
        value.update(updates)
        return value

    def skill_binding(self, **updates):
        value = {
            "binding_id": "memory-wuxian-global",
            "scope": "global",
            "owner": "user",
            "skill_id": "memory-wuxian",
            "target_root_id": "codex-skills",
            "platform": self.native_platform(),
            "installed_revision_id": None,
            "installed_content_sha256": None,
            "base_revision_id": None,
            "base_content_sha256": None,
        }
        value.update(updates)
        return value

    def project_rule_binding(self, **updates):
        value = {
            "binding_id": "orf1-library.project-agents",
            "scope": "project",
            "owner": "project:orf1-library",
            "project_id": "orf1-library",
            "project_binding_id": "project-agents",
            "platform": self.native_platform(),
            "installed_revision_id": None,
            "installed_content_sha256": None,
            "base_revision_id": None,
            "base_content_sha256": None,
        }
        value.update(updates)
        return value

    def test_preview_apply_idempotency_and_persistent_os_lock(self):
        before = authority_hashes(self.archive)
        preview = self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
        )
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(self.bindings.path.exists())

        applied = self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        self.assertEqual(applied["status"], "registered")
        self.assertTrue(self.bindings.lock_path.exists())
        repeated = self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        self.assertEqual(repeated["status"], "no-change")
        self.assertEqual(authority_hashes(self.archive), before)

    def test_discovery_requires_registered_explicit_root_and_is_non_recursive(self):
        (self.rules_root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        nested = self.rules_root / "nested"
        nested.mkdir()
        (nested / "PROJECT_AGENTS.md").write_text("# Hidden\n", encoding="utf-8")
        (self.rules_root / "notes.md").write_text("# Notes\n", encoding="utf-8")
        (self.rules_root / "binary.bin").write_bytes(b"\x00\x01")

        with self.assertRaisesRegex(ValueError, "not explicitly registered"):
            self.bindings.discover(role="global-rules", root=self.rules_root)
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        registry_before = self.bindings.path.read_bytes()
        modified_before = self.bindings.path.stat().st_mtime_ns
        result = self.bindings.scan(role="global-rules", root=self.rules_root)
        self.assertEqual([item["name"] for item in result["proposed"]], ["AGENTS.md"])
        self.assertEqual([item["name"] for item in result["ambiguous"]], ["notes.md"])
        excluded = {item["name"]: item["reason"] for item in result["excluded"]}
        self.assertEqual(excluded["nested"], "non-recursive")
        self.assertEqual(excluded["binary.bin"], "unsupported-extension")
        self.assertNotIn("PROJECT_AGENTS.md", json.dumps(result))
        self.assertEqual(self.bindings.path.read_bytes(), registry_before)
        self.assertEqual(self.bindings.path.stat().st_mtime_ns, modified_before)

    def test_skill_discovery_scans_only_direct_children_with_skill_file(self):
        direct = self.skills_root / "memory-wuxian"
        direct.mkdir()
        (direct / "SKILL.md").write_text(
            "---\nname: memory-wuxian\ndescription: Test\n---\nsecret body\n",
            encoding="utf-8",
        )
        with (direct / "SKILL.md").open("ab") as handle:
            handle.write(b"\xff\xfe")
        nested = direct / "nested-skill"
        nested.mkdir()
        (nested / "SKILL.md").write_text(
            "---\nname: nested-skill\n---\n", encoding="utf-8"
        )
        missing = self.skills_root / "missing"
        missing.mkdir()
        self.bindings.register_root(
            root_id="codex-skills",
            role="global-skills",
            owner="user",
            root=self.skills_root,
            apply=True,
        )
        result = self.bindings.discover(role="global-skills", root=self.skills_root)
        self.assertEqual(len(result["proposed"]), 1)
        self.assertEqual(result["proposed"][0]["skill_id"], "memory-wuxian")
        self.assertNotIn("secret body", json.dumps(result))
        self.assertNotIn("nested-skill", json.dumps(result))
        self.assertEqual(result["excluded"][0]["reason"], "missing-regular-SKILL.md")

    def test_symlink_root_and_escaping_target_are_rejected(self):
        link = self.base / "rules-link"
        link.symlink_to(self.rules_root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink root"):
            self.bindings.register_root(
                root_id="linked-rules",
                role="global-rules",
                owner="user",
                root=link,
                apply=True,
            )
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        with self.assertRaisesRegex(ValueError, "path traversal"):
            self.bindings.register_rule_binding(
                self.rule_binding(relative_path="../outside.md")
            )
        outside = self.base / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        (self.rules_root / "AGENTS.md").symlink_to(outside)
        self.bindings.register_rule_binding(self.rule_binding(), apply=True)
        with self.assertRaisesRegex(ValueError, "symlink target|escapes"):
            self.bindings.get_rule_bindings()

    def test_project_root_and_bindings_have_one_authoritative_source(self):
        (self.project_root / "PROJECT_AGENTS.md").write_text(
            "# Project\n", encoding="utf-8"
        )
        self.register_project_authority()
        preview = self.bindings.register_project(project_id="orf1-library")
        self.assertEqual(preview["status"], "preview")
        self.bindings.register_project(project_id="orf1-library", apply=True)
        self.bindings.register_project_rule_binding(
            self.project_rule_binding(), apply=True
        )
        state = json.loads(self.bindings.path.read_text(encoding="utf-8"))
        self.assertEqual(state["projects"], [{"project_id": "orf1-library"}])
        self.assertNotIn(str(self.project_root), json.dumps(state))

        result = self.bindings.discover(
            role="project", root=self.project_root, project_id="orf1-library"
        )
        self.assertEqual(result["proposed"][0]["binding_id"], "project-agents")
        mappings = self.bindings.get_rule_bindings()
        self.assertEqual(mappings[0]["project_id"], "orf1-library")
        self.assertEqual(
            mappings[0]["target_path"],
            str(self.project_root / "PROJECT_AGENTS.md"),
        )
        self.assertEqual(mappings[0]["project_binding_id"], "project-agents")

    def test_unknown_project_and_wrong_project_root_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown project"):
            self.bindings.register_project(project_id="missing-project")
        self.register_project_authority()
        self.bindings.register_project(project_id="orf1-library", apply=True)
        other = self.base / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "must match"):
            self.bindings.discover(
                role="project", root=other, project_id="orf1-library"
            )

    def test_rule_and_skill_bindings_return_verified_registered_mappings(self):
        self.register_roots()
        (self.rules_root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        self.bindings.register_rule_binding(self.rule_binding(), apply=True)
        self.bindings.register_skill_binding(self.skill_binding(), apply=True)
        persisted = json.loads(self.bindings.path.read_text(encoding="utf-8"))
        self.assertEqual(
            persisted["rule_bindings"][0]["root"], str(self.rules_root)
        )
        self.assertEqual(
            persisted["skill_bindings"][0]["target_root"], str(self.skills_root)
        )
        rules = self.bindings.get_rule_bindings()
        skills = self.bindings.get_skill_bindings()
        self.assertEqual(rules[0]["target_path"], str(self.rules_root / "AGENTS.md"))
        self.assertEqual(
            skills[0]["target_path"], str(self.skills_root / "memory-wuxian")
        )
        rule_installer = EnvironmentRuleInstaller.from_binding_registry(
            self.registry,
            target_node_id="mac-mini-lab",
            binding_registry=self.bindings,
        )
        self.assertEqual(
            set(rule_installer.registered_bindings), {"codex-agents"}
        )
        skill_installer = EnvironmentSkillInstaller.from_binding_registry(
            self.registry,
            target_node_id="mac-mini-lab",
            platform=self.native_platform(),
            runtime_versions={"python": "3.12.0"},
            binding_registry=self.bindings,
        )
        self.assertEqual(
            set(skill_installer.global_skill_bindings),
            {"memory-wuxian-global"},
        )

        state = json.loads(self.bindings.path.read_text(encoding="utf-8"))
        state["rule_bindings"][0]["root_id"] = "unregistered"
        self.bindings.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown rule root"):
            self.bindings.get_rule_bindings()

    def test_binding_cannot_supply_an_arbitrary_target_root(self):
        self.register_roots()
        with self.assertRaisesRegex(ValueError, "does not match registered root"):
            self.bindings.register_rule_binding(
                {**self.rule_binding(), "root": str(self.project_root)}
            )
        with self.assertRaisesRegex(ValueError, "does not match registered root"):
            self.bindings.register_skill_binding(
                {**self.skill_binding(), "target_root": str(self.project_root)}
            )

    def test_updates_require_exact_content_base_not_timestamp(self):
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        current = json.loads(self.bindings.path.read_text(encoding="utf-8"))["roots"][0]
        current_hash = hashlib.sha256(
            json.dumps(
                current, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "base conflict"):
            self.bindings.register_root(
                root_id="codex-rules",
                role="global-rules",
                owner="new-owner",
                root=self.rules_root,
                apply=True,
            )
        updated = self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="new-owner",
            root=self.rules_root,
            base_binding_sha256=current_hash,
            apply=True,
        )
        self.assertEqual(updated["status"], "registered")

    def test_duplicate_ids_and_closed_schema_are_rejected(self):
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        state = json.loads(self.bindings.path.read_text(encoding="utf-8"))
        state["unexpected"] = True
        self.bindings.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.bindings.status()

        state.pop("unexpected")
        state["roots"].append(dict(state["roots"][0]))
        self.bindings.path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate root ID"):
            self.bindings.status()

    def test_transaction_recovery_and_lock_file_persistence(self):
        with patch.object(
            self.bindings, "_before_commit", side_effect=RuntimeError("interrupted")
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.bindings.register_root(
                    root_id="codex-rules",
                    role="global-rules",
                    owner="user",
                    root=self.rules_root,
                    apply=True,
                )
        self.assertEqual(len(list(self.bindings.transactions_dir.glob("*.json"))), 1)
        self.assertEqual(self.bindings.recover_transactions(), 1)
        self.assertFalse(self.bindings.path.exists())
        self.assertTrue(self.bindings.lock_path.exists())

    def test_transaction_recovery_rejects_unrelated_registry_change(self):
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        current = json.loads(self.bindings.path.read_text(encoding="utf-8"))
        base_hash = hashlib.sha256(
            json.dumps(
                current["roots"][0],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with patch.object(
            self.bindings, "_before_commit", side_effect=RuntimeError("interrupted")
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.bindings.register_root(
                    root_id="codex-rules",
                    role="global-rules",
                    owner="changed",
                    root=self.rules_root,
                    base_binding_sha256=base_hash,
                    apply=True,
                )
        self.bindings.path.write_text('{"external":"change"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.bindings.recover_transactions()

    def test_windows_paths_are_local_bindings_not_artifact_identity(self):
        windows = EnvironmentBindingRegistry(
            self.registry, node_id="windows-lab", platform_name="windows"
        )
        artifact_id = self.artifact()["artifact_id"]
        if os.name == "nt":
            first_root = self.rules_root
            second_root = self.base / "other-rules"
            second_root.mkdir()
            skills_root = self.skills_root
        else:
            first_root = Path(r"C:\Users\User\.codex")
            second_root = Path(r"D:\Codex")
            skills_root = Path(r"C:\Users\User\.codex\skills")
        preview_a = windows.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=first_root,
        )
        preview_b = windows.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=second_root,
        )
        self.assertEqual(preview_a["root_id"], preview_b["root_id"])
        self.assertEqual(artifact_id, self.artifact()["artifact_id"])
        windows.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=first_root,
            apply=True,
        )
        windows.register_root(
            root_id="codex-skills",
            role="global-skills",
            owner="user",
            root=skills_root,
            apply=True,
        )
        windows.register_skill_binding(
            {
                **self.skill_binding(),
                "platform": "windows",
            },
            apply=True,
        )
        persisted = json.loads(windows.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["roots"][0]["root"], r"C:\Users\User\.codex")
        self.assertEqual(
            persisted["skill_bindings"][0]["target_root"],
            r"C:\Users\User\.codex\skills",
        )
        with self.assertRaisesRegex(ValueError, "inactive"):
            windows.get_skill_bindings()

    def test_schema_file_is_closed_and_names_node_path(self):
        schema = json.loads(
            (ROOT / "schemas" / "environment-binding.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(
            schema["$defs"]["globalRuleBinding"]["additionalProperties"]
        )
        self.assertFalse(
            schema["$defs"]["projectRuleBinding"]["additionalProperties"]
        )
        self.assertFalse(
            schema["$defs"]["skillBinding"]["additionalProperties"]
        )
        self.bindings.register_root(
            root_id="codex-rules",
            role="global-rules",
            owner="user",
            root=self.rules_root,
            apply=True,
        )
        self.assertEqual(
            self.bindings.path,
            self.archive / "environment" / "bindings" / "mac-mini-lab.json",
        )

    def test_binding_registry_and_transaction_symlinks_fail_closed(self):
        outside = self.base / "outside-bindings.json"
        outside.write_text("{}\n", encoding="utf-8")
        self.bindings.bindings_dir.mkdir(parents=True)
        self.bindings.path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "registry must not be a symlink"):
            self.bindings.status()


if __name__ == "__main__":
    unittest.main()
