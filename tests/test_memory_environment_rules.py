import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_environment import EnvironmentRegistry, revision_id_for
from memory_environment_rules import (
    EnvironmentRuleInstaller,
    RuleConflictError,
    RuleInstallationError,
)
from platform_lock import exclusive_lock


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def managed(block_id: str, body: str) -> str:
    return (
        f"<!-- memory-wuxian:managed-block:{block_id}:begin -->"
        f"{body}"
        f"<!-- memory-wuxian:managed-block:{block_id}:end -->"
    )


def authority_hashes(root: Path):
    result = {}
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and "environment" not in path.parts
            and path.name not in {"environment.lock", "environment-installer.lock"}
        ):
            result[str(path.relative_to(root))] = digest(path.read_bytes())
    return result


class FailingAfterReplaceInstaller(EnvironmentRuleInstaller):
    def _after_replace(self, target_path: Path) -> None:
        raise RuntimeError("simulated post-replace failure")


class CrashBeforeReplaceInstaller(EnvironmentRuleInstaller):
    def _before_replace(self, target_path: Path) -> None:
        raise SystemExit("simulated crash before replace")


class CrashAfterReplaceInstaller(EnvironmentRuleInstaller):
    def _after_replace(self, target_path: Path) -> None:
        raise SystemExit("simulated crash after replace")


class CrashAfterReceiptInstaller(EnvironmentRuleInstaller):
    def _complete_transaction(self, transaction_dir, *, status, receipt):
        raise SystemExit("simulated crash after receipt")


class CrashBeforeRollbackDeleteInstaller(EnvironmentRuleInstaller):
    def _delete_rollback_object(self, transaction_dir):
        raise SystemExit("simulated crash before rollback cleanup")


class EnvironmentRuleInstallerTest(unittest.TestCase):
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
        self.root = self.base / "global"
        self.root.mkdir()
        self.target = self.root / "AGENTS.md"
        self.created_at = "2026-07-28T12:00:00+00:00"

    def tearDown(self):
        self.temporary.cleanup()

    def artifact(
        self,
        artifact_id="global-rule:codex-agents",
        *,
        object_class="global-rule",
        scope="global",
        project_id=None,
    ):
        return {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "object_class": object_class,
            "scope": scope,
            "project_id": project_id,
            "display_name": artifact_id,
            "created_at": self.created_at,
        }

    def revision(
        self,
        artifact_id: str,
        content: str,
        *,
        version: int,
        base_revision_id=None,
    ):
        content_hash = digest(content.encode("utf-8"))
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
            "runtime_requirements": {},
            "provenance": {"source": "test"},
            "lifecycle_state": "discovered",
            "created_at": self.created_at,
        }
        value["revision_id"] = revision_id_for(value)
        return value

    def register_pair(
        self,
        *,
        artifact=None,
        base_content: str,
        remote_content: str,
        projects=None,
    ):
        artifact = artifact or self.artifact()
        base_revision = self.revision(
            artifact["artifact_id"], base_content, version=1
        )
        base_manifest = {
            "schema_version": 1,
            "artifacts": [
                {
                    "artifact": artifact,
                    "revision": base_revision,
                    "content": base_content,
                }
            ],
            "projects": projects or [],
        }
        self.registry.register(base_manifest, apply=True)
        remote_revision = self.revision(
            artifact["artifact_id"],
            remote_content,
            version=2,
            base_revision_id=base_revision["revision_id"],
        )
        remote_manifest = {
            "schema_version": 1,
            "artifacts": [
                {
                    "artifact": artifact,
                    "revision": remote_revision,
                    "content": remote_content,
                }
            ],
            "projects": [],
        }
        self.registry.register(remote_manifest, apply=True)
        return base_revision, remote_revision

    def global_binding(
        self,
        base_revision,
        *,
        binding_id="global:codex-agents",
        root=None,
        relative_path="AGENTS.md",
        classification="canonical",
        install_strategy="managed-block",
        managed_block_id="shared-rules",
        owner="memory-wuxian",
    ):
        value = {
            "binding_id": binding_id,
            "scope": "global",
            "root": str(root or self.root),
            "relative_path": relative_path,
            "classification": classification,
            "install_strategy": install_strategy,
            "owner": owner,
            "base_revision_id": base_revision["revision_id"],
            "base_content_sha256": base_revision["content_sha256"],
        }
        if managed_block_id is not None:
            value["managed_block_id"] = managed_block_id
        return value

    def installer(self, binding, installer_class=EnvironmentRuleInstaller):
        return installer_class(
            self.registry,
            target_node_id="mac-mini-lab",
            registered_bindings={binding["binding_id"]: binding},
        )

    def transaction_directories(self):
        root = self.registry.root / "transactions" / "rule-installs"
        return sorted(path for path in root.glob("rule-*") if path.is_dir())

    @staticmethod
    def reseal_transaction(transaction):
        value = dict(transaction)
        value.pop("metadata_sha256", None)
        transaction["metadata_sha256"] = digest(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def project(
        self,
        root: Path,
        *,
        active=True,
        classification="canonical",
        strategy="managed-block",
        relative_path="PROJECT_AGENTS.md",
    ):
        return {
            "schema_version": 1,
            "project_id": "orf1-library",
            "display_name": "ORF1 library",
            "local_root": str(root),
            "active": active,
            "rule_bindings": [
                {
                    "binding_id": "project-agents",
                    "relative_path": relative_path,
                    "classification": classification,
                    "install_strategy": strategy,
                    "managed_block_id": "project-shared",
                }
            ],
            "skill_bindings": [],
        }

    def project_binding(self, base_revision, **updates):
        value = {
            "binding_id": "project:orf1-library:project-agents",
            "scope": "project",
            "project_id": "orf1-library",
            "project_binding_id": "project-agents",
            "owner": "memory-wuxian",
            "base_revision_id": base_revision["revision_id"],
            "base_content_sha256": base_revision["content_sha256"],
        }
        value.update(updates)
        return value

    def test_managed_block_preview_apply_preserves_external_bytes_and_mode(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(f"prefix\r\n{base}\r\nsuffix Ω\n", encoding="utf-8")
        os.chmod(self.target, 0o640)
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        original = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        installer = self.installer(binding)

        environment_before = sorted(self.registry.root.rglob("*.json"))
        preview = installer.install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
        )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(sorted(self.registry.root.rglob("*.json")), environment_before)

        result = installer.install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        updated = self.target.read_bytes()
        begin = managed("shared-rules", "").encode().split(b"end -->")[0]
        self.assertTrue(updated.startswith(b"prefix\r\n"))
        self.assertTrue(updated.endswith("suffix Ω\n".encode("utf-8")))
        self.assertIn(b"\nnew\n", updated)
        self.assertNotIn(b"\nold\n", updated)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), original_mode)
        self.assertEqual(result["receipt"]["result"], "installed")
        self.assertEqual(result["receipt"]["final_installed_sha256"], digest(updated))
        transaction_dir = self.transaction_directories()[0]
        self.assertEqual(
            json.loads(
                (transaction_dir / "transaction.json").read_text(encoding="utf-8")
            )["status"],
            "installed",
        )
        self.assertFalse((transaction_dir / "rollback.bin").exists())
        self.assertTrue(begin)

    def test_three_way_no_change_and_conflict_decisions(self):
        base = managed("shared-rules", "\nbase\n")
        remote = managed("shared-rules", "\nremote\n")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        installer = self.installer(binding)

        self.target.write_text(f"before\n{remote}\nafter\n", encoding="utf-8")
        no_change = installer.install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        self.assertEqual(no_change["status"], "no-change")
        self.assertEqual(
            list(self.registry.root.joinpath("receipts").glob("*.json")), []
        )
        self.assertEqual(self.transaction_directories(), [])

        self.target.write_text(
            f"before\n{managed('shared-rules', chr(10) + 'local' + chr(10))}\nafter\n",
            encoding="utf-8",
        )
        before = self.target.read_bytes()
        with self.assertRaises(RuleConflictError) as caught:
            installer.install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(caught.exception.receipt["result"], "failed")
        self.assertFalse(caught.exception.receipt["rollback"]["attempted"])

    def test_remote_equal_base_is_no_change_without_receipt(self):
        base = managed("shared-rules", "\nbase\n")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=base
        )
        self.target.write_text(
            f"outside\n{managed('shared-rules', chr(10) + 'local' + chr(10))}\n",
            encoding="utf-8",
        )
        binding = self.global_binding(base_revision)
        result = self.installer(binding).install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        self.assertEqual(result["status"], "no-change")
        self.assertEqual(result["reason"], "remote equals base")
        self.assertEqual(
            list(self.registry.root.joinpath("receipts").glob("*.json")), []
        )

    def test_rollback_restores_exact_bytes_and_mode(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(f"prefix\n{base}\nsuffix\n", encoding="utf-8")
        os.chmod(self.target, 0o604)
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        before = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        installer = self.installer(binding, FailingAfterReplaceInstaller)
        with self.assertRaises(RuleInstallationError) as caught:
            installer.install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), original_mode)
        self.assertEqual(caught.exception.receipt["result"], "rolled-back")
        self.assertTrue(caught.exception.receipt["rollback"]["succeeded"])

    def test_system_exit_before_replace_recovers_prepared_as_aborted(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(f"prefix\n{base}\nsuffix\n", encoding="utf-8")
        before = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashBeforeReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        transaction = json.loads(
            (transaction_dir / "transaction.json").read_text(encoding="utf-8")
        )
        self.assertEqual(transaction["status"], "prepared")
        rollback_path = transaction_dir / "rollback.bin"
        self.assertTrue(rollback_path.is_file())
        self.assertEqual(digest(rollback_path.read_bytes()), transaction["original_sha256"])
        self.assertEqual(
            transaction["target_canonical_path"],
            str(self.target.resolve(strict=True)),
        )
        self.assertEqual(transaction["artifact_id"], "global-rule:codex-agents")
        self.assertEqual(transaction["revision_id"], remote_revision["revision_id"])
        self.assertEqual(transaction["target_binding"], binding["binding_id"])
        self.assertEqual(
            transaction["candidate_sha256"],
            digest(f"prefix\n{remote}\nsuffix\n".encode("utf-8")),
        )
        self.assertEqual(self.target.read_bytes(), before)

        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["aborted"], 1)
        terminal = json.loads(
            (transaction_dir / "transaction.json").read_text(encoding="utf-8")
        )
        self.assertEqual(terminal["status"], "aborted")
        self.assertFalse((transaction_dir / "rollback.bin").exists())
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.registry.root.joinpath("receipts").glob("*.json")), [])

    def test_prepared_marker_with_candidate_target_is_recovered_as_replaced(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        before = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashBeforeReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        self.assertEqual(
            json.loads(
                (transaction_dir / "transaction.json").read_text(encoding="utf-8")
            )["status"],
            "prepared",
        )

        # Simulate a process dying after os.replace but before persisting the
        # replaced marker.
        self.target.write_text(remote, encoding="utf-8")
        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["rolled_back"], 1)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(
            json.loads(
                (transaction_dir / "transaction.json").read_text(encoding="utf-8")
            )["status"],
            "rolled-back",
        )
        self.assertFalse((transaction_dir / "rollback.bin").exists())

    def test_system_exit_after_replace_is_rolled_back_by_new_instance(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(f"prefix\n{base}\nsuffix\n", encoding="utf-8")
        os.chmod(self.target, 0o640)
        before = self.target.read_bytes()
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        self.assertIn(b"new", self.target.read_bytes())
        self.assertEqual(
            json.loads(
                (transaction_dir / "transaction.json").read_text(encoding="utf-8")
            )["status"],
            "replaced",
        )
        self.assertTrue((transaction_dir / "rollback.bin").is_file())

        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["rolled_back"], 1)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), original_mode)
        terminal = json.loads(
            (transaction_dir / "transaction.json").read_text(encoding="utf-8")
        )
        self.assertEqual(terminal["status"], "rolled-back")
        self.assertFalse((transaction_dir / "rollback.bin").exists())
        receipts = list(self.registry.root.joinpath("receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            json.loads(receipts[0].read_text(encoding="utf-8"))["result"],
            "rolled-back",
        )

    def test_installed_receipt_is_commit_point_across_crash(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReceiptInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        self.assertIn(b"new", self.target.read_bytes())
        self.assertTrue((transaction_dir / "rollback.bin").exists())
        receipts = list(self.registry.root.joinpath("receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            json.loads(receipts[0].read_text(encoding="utf-8"))["result"],
            "installed",
        )

        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["installed"], 1)
        self.assertIn(b"new", self.target.read_bytes())
        terminal = json.loads(
            (transaction_dir / "transaction.json").read_text(encoding="utf-8")
        )
        self.assertEqual(terminal["status"], "installed")
        self.assertFalse((transaction_dir / "rollback.bin").exists())

    def test_terminal_recovery_removes_committed_rollback_object(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashBeforeRollbackDeleteInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        terminal = json.loads(
            (transaction_dir / "transaction.json").read_text(encoding="utf-8")
        )
        self.assertEqual(terminal["status"], "installed")
        self.assertTrue((transaction_dir / "rollback.bin").exists())

        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["installed"], 0)
        self.assertIn(b"new", self.target.read_bytes())
        self.assertFalse((transaction_dir / "rollback.bin").exists())

    def test_rolled_back_receipt_is_reused_after_recovery_crash(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        before = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReceiptInstaller).recover_pending()
        self.assertEqual(self.target.read_bytes(), before)
        receipts = list(self.registry.root.joinpath("receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertEqual(
            json.loads(receipts[0].read_text(encoding="utf-8"))["result"],
            "rolled-back",
        )
        self.assertTrue((transaction_dir / "rollback.bin").exists())

        recovered = self.installer(binding).recover_pending()
        self.assertEqual(recovered["rolled_back"], 1)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(len(list(self.registry.root.joinpath("receipts").glob("*.json"))), 1)
        self.assertEqual(
            json.loads(
                (transaction_dir / "transaction.json").read_text(encoding="utf-8")
            )["status"],
            "rolled-back",
        )
        self.assertFalse((transaction_dir / "rollback.bin").exists())

    def test_recovery_rejects_tampered_transaction_and_changed_base(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        transaction_path = transaction_dir / "transaction.json"
        transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        transaction["candidate_sha256"] = "f" * 64
        transaction_path.write_text(json.dumps(transaction), encoding="utf-8")
        changed_target = self.target.read_bytes()
        with self.assertRaisesRegex(
            (ValueError, RuleInstallationError), "metadata hash"
        ):
            self.installer(binding).recover_pending()
        self.assertEqual(self.target.read_bytes(), changed_target)
        self.assertTrue((transaction_dir / "rollback.bin").exists())

        # Restore the original sealed record, then change the trusted binding's
        # recorded base. Recovery must still refuse to trust the transaction.
        transaction["candidate_sha256"] = digest(changed_target)
        self.reseal_transaction(transaction)
        transaction_path.write_text(json.dumps(transaction), encoding="utf-8")
        changed_binding = dict(binding)
        changed_binding["base_content_sha256"] = "e" * 64
        with self.assertRaisesRegex(
            RuleInstallationError, "binding or recorded base changed"
        ):
            self.installer(changed_binding).recover_pending()
        self.assertEqual(self.target.read_bytes(), changed_target)
        self.assertTrue((transaction_dir / "rollback.bin").exists())

    def test_recovery_never_uses_transaction_absolute_path(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        victim = self.base / "must-not-touch.md"
        victim.write_text("victim\n", encoding="utf-8")
        victim_before = victim.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        transaction_path = transaction_dir / "transaction.json"
        transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        transaction["target_canonical_path"] = str(victim.resolve(strict=True))
        transaction["target_path_sha256"] = digest(
            transaction["target_canonical_path"].encode("utf-8")
        )
        self.reseal_transaction(transaction)
        transaction_path.write_text(json.dumps(transaction), encoding="utf-8")
        target_before = self.target.read_bytes()

        with self.assertRaisesRegex(
            RuleInstallationError, "target binding changed"
        ):
            self.installer(binding).recover_pending()
        self.assertEqual(victim.read_bytes(), victim_before)
        self.assertEqual(self.target.read_bytes(), target_before)
        self.assertTrue((transaction_dir / "rollback.bin").exists())

    def test_recovery_rejects_tampered_rollback_object(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        rollback_path = transaction_dir / "rollback.bin"
        rollback_path.write_bytes(b"tampered")
        target_before = self.target.read_bytes()
        with self.assertRaisesRegex(ValueError, "rollback object hash mismatch"):
            self.installer(binding).recover_pending()
        self.assertEqual(self.target.read_bytes(), target_before)
        self.assertEqual(rollback_path.read_bytes(), b"tampered")

    def test_recovery_fails_closed_when_registry_advances(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        newer = managed("shared-rules", "\nnewer\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        with self.assertRaises(SystemExit):
            self.installer(binding, CrashAfterReplaceInstaller).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        transaction_dir = self.transaction_directories()[0]
        changed_target = self.target.read_bytes()
        newer_revision = self.revision(
            "global-rule:codex-agents",
            newer,
            version=3,
            base_revision_id=remote_revision["revision_id"],
        )
        self.registry.register(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "artifact": self.artifact(),
                        "revision": newer_revision,
                        "content": newer,
                    }
                ],
                "projects": [],
            },
            apply=True,
        )
        with self.assertRaisesRegex(
            RuleInstallationError, "registry current revision changed"
        ):
            self.installer(binding).recover_pending()
        self.assertEqual(self.target.read_bytes(), changed_target)
        self.assertTrue((transaction_dir / "rollback.bin").exists())

    def test_marker_base_utf8_symlink_and_traversal_fail_closed(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )

        cases = [
            ("duplicate marker", f"{base}{base}".encode("utf-8"), {}),
            ("non utf8", b"\xff\xfe", {}),
            ("no base", base.encode("utf-8"), {"base_revision_id": None}),
            ("traversal", base.encode("utf-8"), {"relative_path": "../AGENTS.md"}),
        ]
        for label, target_bytes, updates in cases:
            with self.subTest(label=label):
                self.target.unlink(missing_ok=True)
                self.target.write_bytes(target_bytes)
                binding = self.global_binding(base_revision)
                binding.update(updates)
                before = self.target.read_bytes()
                with self.assertRaises(RuleInstallationError):
                    self.installer(binding).install(
                        artifact_id="global-rule:codex-agents",
                        revision_id=remote_revision["revision_id"],
                        target_binding=binding["binding_id"],
                        apply=True,
                    )
                self.assertEqual(self.target.read_bytes(), before)

        self.target.unlink()
        real_target = self.root / "real.md"
        real_target.write_text(base, encoding="utf-8")
        self.target.symlink_to(real_target)
        binding = self.global_binding(base_revision)
        with self.assertRaises(RuleInstallationError):
            self.installer(binding).install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=binding["binding_id"],
                apply=True,
            )
        self.assertEqual(real_target.read_text(encoding="utf-8"), base)

    def test_whole_file_requires_canonical_memory_wuxian_owner(self):
        base = "old whole file\n"
        remote = "new whole file\n"
        self.target.write_text(base, encoding="utf-8")
        os.chmod(self.target, 0o640)
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(
            base_revision,
            install_strategy="whole-file",
            managed_block_id=None,
        )
        installed = self.installer(binding).install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        self.assertEqual(installed["status"], "installed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), remote)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), original_mode)

        for classification, owner in [
            ("canonical", "another-owner"),
            ("project-local", "memory-wuxian"),
            ("generated", "memory-wuxian"),
            ("excluded", "memory-wuxian"),
        ]:
            with self.subTest(classification=classification, owner=owner):
                os.chmod(self.target, 0o644)
                self.target.write_text(base, encoding="utf-8")
                binding = self.global_binding(
                    base_revision,
                    classification=classification,
                    install_strategy="whole-file",
                    managed_block_id=None,
                    owner=owner,
                )
                with self.assertRaises(RuleInstallationError):
                    self.installer(binding).install(
                        artifact_id="global-rule:codex-agents",
                        revision_id=remote_revision["revision_id"],
                        target_binding=binding["binding_id"],
                        apply=True,
                    )
                self.assertEqual(self.target.read_text(encoding="utf-8"), base)

    def test_project_binding_requires_registered_active_project(self):
        project_root = self.base / "project"
        project_target = project_root / "nested" / "PROJECT_AGENTS.md"
        project_target.parent.mkdir(parents=True)
        base = managed("project-shared", "\nold\n")
        remote = managed("project-shared", "\nnew\n")
        project_target.write_text(f"outside\n{base}\n", encoding="utf-8")
        artifact = self.artifact(
            "project-rule:orf1-agents",
            object_class="project-rule",
            scope="project",
            project_id="orf1-library",
        )
        base_revision, remote_revision = self.register_pair(
            artifact=artifact,
            base_content=base,
            remote_content=remote,
            projects=[
                self.project(
                    project_root,
                    relative_path=r"nested\PROJECT_AGENTS.md",
                )
            ],
        )
        binding = self.project_binding(base_revision)
        result = self.installer(binding).install(
            artifact_id=artifact["artifact_id"],
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        self.assertEqual(result["status"], "installed")
        self.assertIn("new", project_target.read_text(encoding="utf-8"))

    def test_install_accepts_only_registered_binding_identity(self):
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        installer = self.installer(binding)
        before = self.target.read_bytes()
        with self.assertRaises(RuleInstallationError):
            installer.install(
                artifact_id="global-rule:codex-agents",
                revision_id=remote_revision["revision_id"],
                target_binding=str(self.target),
                apply=True,
            )
        self.assertEqual(self.target.read_bytes(), before)

    def test_project_unbound_or_inactive_is_rejected(self):
        for active, include_project in [(False, True), (True, False)]:
            with self.subTest(active=active, include_project=include_project):
                temporary = tempfile.TemporaryDirectory()
                try:
                    archive = Path(temporary.name) / "memory"
                    archive.mkdir()
                    registry = EnvironmentRegistry(archive)
                    project_root = Path(temporary.name) / "project"
                    project_root.mkdir()
                    (project_root / "PROJECT_AGENTS.md").write_text(
                        managed("project-shared", "\nold\n"), encoding="utf-8"
                    )
                    artifact = self.artifact(
                        "project-rule:orf1-agents",
                        object_class="project-rule",
                        scope="project",
                        project_id="orf1-library",
                    )
                    original_registry = self.registry
                    self.registry = registry
                    project_record = self.project(project_root, active=active)
                    if not include_project:
                        project_record["rule_bindings"] = []
                    base_revision, remote_revision = self.register_pair(
                        artifact=artifact,
                        base_content=managed("project-shared", "\nold\n"),
                        remote_content=managed("project-shared", "\nnew\n"),
                        projects=[project_record],
                    )
                    binding = self.project_binding(base_revision)
                    installer = EnvironmentRuleInstaller(
                        registry,
                        target_node_id="mac-mini-lab",
                        registered_bindings={binding["binding_id"]: binding},
                    )
                    with self.assertRaises(RuleInstallationError):
                        installer.install(
                            artifact_id=artifact["artifact_id"],
                            revision_id=remote_revision["revision_id"],
                            target_binding=binding["binding_id"],
                            apply=True,
                        )
                    self.registry = original_registry
                finally:
                    temporary.cleanup()

    def test_receipt_matches_schema_and_1x_authority_is_unchanged(self):
        before = authority_hashes(self.archive)
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        result = self.installer(binding).install(
            artifact_id="global-rule:codex-agents",
            revision_id=remote_revision["revision_id"],
            target_binding=binding["binding_id"],
            apply=True,
        )
        schema = json.loads(
            (SKILL_ROOT / "schemas" / "environment-receipt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        receipt = result["receipt"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(receipt))
        self.assertEqual(receipt["schema_version"], schema["properties"]["schema_version"]["const"])
        for field in ("receipt_id", "revision_id", "content_sha256"):
            self.assertRegex(receipt[field], schema["properties"][field]["pattern"])
        for field in ("previous_installed_sha256", "final_installed_sha256"):
            if receipt[field] is not None:
                self.assertRegex(receipt[field], schema["properties"][field]["pattern"])
        self.assertIn(receipt["result"], schema["properties"]["result"]["enum"])
        self.assertIsInstance(receipt["rehearsal"], dict)
        self.assertIsInstance(receipt["rollback"], dict)
        self.assertEqual(authority_hashes(self.archive), before)
        self.assertFalse((self.archive / ".locks" / "archive.lock").exists())
        installer_lock = self.archive / ".locks" / "environment-installer.lock"
        self.assertTrue(installer_lock.is_file())
        with exclusive_lock(installer_lock):
            self.assertTrue(installer_lock.is_file())

    def test_permission_change_candidate_is_rejected_without_target_change(self):
        if os.name == "nt":
            self.skipTest("Windows exposes only its read-only chmod subset")
        base = managed("shared-rules", "\nold\n")
        remote = managed("shared-rules", "\nnew\n")
        self.target.write_text(base, encoding="utf-8")
        os.chmod(self.target, 0o640)
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        before = self.target.read_bytes()
        base_revision, remote_revision = self.register_pair(
            base_content=base, remote_content=remote
        )
        binding = self.global_binding(base_revision)
        installer = self.installer(binding)
        original_write_candidate = installer._write_candidate

        def expanded_candidate(target, payload, mode):
            candidate = original_write_candidate(target, payload, mode)
            os.chmod(candidate, mode | 0o111)
            return candidate

        with patch.object(installer, "_write_candidate", side_effect=expanded_candidate):
            with self.assertRaises(RuleInstallationError):
                installer.install(
                    artifact_id="global-rule:codex-agents",
                    revision_id=remote_revision["revision_id"],
                    target_binding=binding["binding_id"],
                    apply=True,
                )
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), original_mode)


if __name__ == "__main__":
    unittest.main()
