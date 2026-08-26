from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install_windows_transaction import build_manifest
from platform_transaction import atomic_write_canonical_json
from windows_install_manifest import InstallManifestError, validate_manifest
from windows_installer_transaction import (
    InstallerExit,
    TransactionToken,
    WindowsInstallerTransaction,
)


class FakeMutation:
    def __init__(
        self,
        *,
        fail_apply: bool = False,
        fail_verify: bool = False,
        fail_rollback: bool = False,
        fail_commit: bool = False,
    ) -> None:
        self.name = "fixture"
        self.resource_id = "fixture-resource"
        self.compensation = "restore fixture"
        self.owned_paths = ("fixture",)
        self.owned_tasks = ()
        self.owned_registry_values = ()
        self.forbidden_paths = ()
        self.prepare_evidence = {"snapshot": "fixture-before"}
        self.fail_apply = fail_apply
        self.fail_verify = fail_verify
        self.fail_rollback = fail_rollback
        self.fail_commit = fail_commit
        self.calls: list[str] = []
        self.tokens: list[TransactionToken] = []

    def prepare(self, token: TransactionToken) -> dict[str, str]:
        self.tokens.append(token)
        return self.prepare_evidence

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, str]) -> None:
        self.tokens.append(token)
        self.prepare_evidence = evidence

    def discard_prepare(self, token: TransactionToken) -> None:
        self.tokens.append(token)

    def rollback_verify(self, token: TransactionToken) -> dict[str, str]:
        self.tokens.append(token)
        return {"rollback_verify": "ok"}

    def apply(self, token: TransactionToken) -> dict[str, str]:
        self.calls.append("apply")
        self.tokens.append(token)
        if self.fail_apply:
            raise RuntimeError("apply failed")
        return {"apply": "ok"}

    def verify(self, token: TransactionToken) -> dict[str, str]:
        self.calls.append("verify")
        self.tokens.append(token)
        if self.fail_verify:
            raise RuntimeError("verify failed")
        return {"verify": "ok"}

    def commit(self, token: TransactionToken) -> dict[str, str]:
        self.calls.append("commit")
        self.tokens.append(token)
        if self.fail_commit:
            raise RuntimeError("commit failed")
        return {"commit": "ok"}

    def rollback(self, token: TransactionToken) -> dict[str, str]:
        self.calls.append("rollback")
        self.tokens.append(token)
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        return {"rollback": "ok"}


class NamedMutation(FakeMutation):
    def __init__(self, name: str, calls: list[str]) -> None:
        super().__init__()
        self.name = name
        self.resource_id = f"resource-{name}"
        self.shared_calls = calls

    def apply(self, token: TransactionToken) -> dict[str, str]:
        self.shared_calls.append(f"apply:{self.name}")
        return super().apply(token)

    def verify(self, token: TransactionToken) -> dict[str, str]:
        self.shared_calls.append(f"verify:{self.name}")
        return super().verify(token)

    def commit(self, token: TransactionToken) -> dict[str, str]:
        self.shared_calls.append(f"commit:{self.name}")
        return super().commit(token)


class FakeAdapter:
    def __init__(self, mutation: FakeMutation) -> None:
        self.mutation = mutation

    def build(self, _manifest, _token):
        return [self.mutation]


class SequenceAdapter:
    def __init__(self, mutations) -> None:
        self.mutations = mutations

    def build(self, _manifest, _token):
        return self.mutations


class WindowsInstallerTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / "pyproject.toml").write_text('[project]\nversion = "2.20.0"\n', encoding="utf-8")
        (self.candidate / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.runtime = self.root / "runtime"
        (self.runtime / "python").mkdir(parents=True)
        shutil.copy2(sys.executable, self.runtime / "python" / "python.exe")
        (self.runtime / "runtime-lock.json").write_text("{}\n", encoding="utf-8")
        (self.runtime / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
        (self.root / "codex.exe").write_bytes(b"codex-fixture")
        self.runtime_id = "a" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, source: str = "manual") -> argparse.Namespace:
        return argparse.Namespace(
            operation="install",
            source_entrypoint=source,
            candidate_root=str(self.candidate),
            skill_root=str(self.root / "skill"),
            archive_root=str(self.root / "archive"),
            archive_pointer=str(self.root / "active-root.txt"),
            sessions_root=str(self.sessions),
            python_executable=str(self.runtime / "python" / "python.exe"),
            runtime_bundle_root=str(self.runtime),
            runtime_bundle_id=self.runtime_id,
            codex_cli=str(self.root / "codex.exe"),
            journal_path=str(self.root / "journal.json"),
            manifest_output=str(self.root / "manifest.json"),
            failure_point=None,
        )

    def test_closed_manifest_rejects_unknown_fields(self) -> None:
        document = build_manifest(self.args()).to_document()
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(
            Path(document["codex_cli"]["path"]).resolve(),
            (self.root / "codex.exe").resolve(),
        )
        self.assertEqual(
            document["requested_components"],
            ["archive", "auto-update", "collector", "config", "federation-node", "maintenance", "shortcut"],
        )
        self.assertNotIn("failure_point", document)
        document["command"] = "powershell.exe"
        with self.assertRaises(InstallManifestError):
            validate_manifest(document)

    def test_controller_commits_in_order_with_one_token(self) -> None:
        mutation = FakeMutation()
        journal = self.root / "journal.json"
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(build_manifest(self.args()))
        self.assertEqual(result.exit_code, InstallerExit.SUCCESS)
        self.assertEqual(mutation.calls, ["apply", "verify", "commit"])
        self.assertEqual(len({item.secret for item in mutation.tokens}), 1)
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["phase"], "committed")
        self.assertEqual(
            json.loads(journal.read_text(encoding="utf-8"))["mutations"][0]["prepare_evidence"],
            {"snapshot": "fixture-before"},
        )
        entry = json.loads(journal.read_text(encoding="utf-8"))["mutations"][0]
        self.assertEqual(entry["owned_paths"], ["fixture"])
        self.assertEqual(entry["owned_tasks"], [])

    def test_partial_apply_and_verify_failures_roll_back(self) -> None:
        cases = (
            (FakeMutation(fail_apply=True), InstallerExit.APPLY_FAILED_ROLLED_BACK),
            (FakeMutation(fail_verify=True), InstallerExit.EFFECT_VERIFICATION_FAILED),
        )
        for mutation, expected_exit in cases:
            journal = self.root / f"journal-{id(mutation)}.json"
            result = WindowsInstallerTransaction(
                journal_path=journal, adapters=[FakeAdapter(mutation)]
            ).execute(build_manifest(self.args()))
            self.assertEqual(result.exit_code, expected_exit)
            self.assertEqual(mutation.calls[-1], "rollback")
            self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["phase"], "rolled-back")
            self.assertEqual(
                json.loads(journal.read_text(encoding="utf-8"))["mutations"][0]["status"],
                "rollback-verified",
            )

    def test_dependent_resources_commit_before_foundational_generation(self) -> None:
        calls: list[str] = []
        mutations = [NamedMutation("generation", calls), NamedMutation("config", calls)]
        result = WindowsInstallerTransaction(
            journal_path=self.root / "commit-order.json",
            adapters=[SequenceAdapter(mutations)],
        ).execute(build_manifest(self.args()))
        self.assertEqual(result.exit_code, InstallerExit.SUCCESS)
        self.assertEqual(
            [item for item in calls if item.startswith("commit:")],
            ["commit:config", "commit:generation"],
        )

    def test_each_of_seven_resource_boundaries_rolls_back_every_prior_apply(self) -> None:
        names = [
            "archive-initialization",
            "federation-node-initialization",
            "collector-generation",
            "configuration-migration",
            "maintenance-registration",
            "auto-update-registration",
            "dashboard-shortcut",
        ]
        for completed_index, name in enumerate(names):
            calls: list[str] = []
            mutations = [NamedMutation(item, calls) for item in names]
            failure_point = (
                f"before-apply:{names[completed_index + 1]}"
                if completed_index + 1 < len(names)
                else f"before-verify:{names[0]}"
            )
            result = WindowsInstallerTransaction(
                journal_path=self.root / f"seven-resource-{completed_index}.json",
                adapters=[SequenceAdapter(mutations)],
            ).execute(build_manifest(self.args()), failure_point=failure_point)
            self.assertEqual(result.phase, "rolled-back", name)
            for index, mutation in enumerate(mutations):
                self.assertEqual("rollback" in mutation.calls, index <= completed_index, name)

    def test_rollback_incomplete_and_commit_failure_have_distinct_exact_codes(self) -> None:
        cases = (
            (FakeMutation(fail_apply=True, fail_rollback=True), InstallerExit.APPLY_FAILED_ROLLBACK_INCOMPLETE, "rollback-incomplete"),
            (FakeMutation(fail_commit=True), InstallerExit.COMMIT_FAILED, "rolled-back"),
        )
        for mutation, expected_code, expected_phase in cases:
            journal = self.root / f"journal-exit-{int(expected_code)}.json"
            result = WindowsInstallerTransaction(
                journal_path=journal, adapters=[FakeAdapter(mutation)]
            ).execute(build_manifest(self.args()))
            document = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(result.exit_code, expected_code)
            self.assertEqual(result.phase, expected_phase)
            self.assertEqual(document["phase"], expected_phase)

    def unfinished_journal(self, manifest, *, phase: str, status: str) -> dict:
        manifest_document = manifest.to_document()
        manifest_sha256 = hashlib.sha256(
            json.dumps(manifest_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": 1,
            "transaction_id": "recoverable-transaction",
            "transaction_token": {"secret": "recoverable-secret"},
            "phase": phase,
            "source_entrypoint": manifest.source_entrypoint,
            "operation": manifest.operation,
            "package": {"version": manifest.package.version, "sha256": manifest.package.sha256},
            "manifest": manifest_document,
            "manifest_sha256": manifest_sha256,
            "mutations": [{
                "name": "fixture", "resource_id": "fixture-resource",
                "compensation": "restore fixture", "prepare_evidence": {"snapshot": "fixture-before"},
                "owned_paths": ["fixture"], "owned_tasks": [],
                "owned_registry_values": [], "forbidden_paths": [],
                "status": status, "apply_evidence": None, "verify_evidence": None,
                "commit_evidence": None, "rollback_evidence": None, "rollback_verify_evidence": None,
            }],
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "error": None,
        }

    def test_unfinished_apply_journal_is_recovered_not_overwritten(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "recover-apply.json"
        atomic_write_canonical_json(journal, self.unfinished_journal(manifest, phase="applying", status="applying"))
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(manifest)
        self.assertEqual(result.transaction_id, "recoverable-transaction")
        self.assertEqual(result.phase, "rolled-back")
        self.assertEqual(mutation.calls, ["rollback"])

    def test_partial_prepare_journal_resumes_same_transaction(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "recover-prepare.json"
        document = self.unfinished_journal(manifest, phase="preparing", status="pending")
        document["mutations"][0]["prepare_evidence"] = None
        atomic_write_canonical_json(journal, document)
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(manifest)
        self.assertEqual(result.exit_code, InstallerExit.SUCCESS)
        self.assertEqual(result.transaction_id, "recoverable-transaction")
        self.assertEqual(mutation.calls, ["apply", "verify", "commit"])

    def test_rollback_incomplete_blocks_new_transaction(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "rollback-incomplete.json"
        atomic_write_canonical_json(
            journal,
            self.unfinished_journal(manifest, phase="rollback-incomplete", status="rollback-failed"),
        )
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(manifest)
        self.assertEqual(result.exit_code, InstallerExit.APPLY_FAILED_ROLLBACK_INCOMPLETE)
        self.assertEqual(result.transaction_id, "recoverable-transaction")
        self.assertEqual(mutation.calls, [])

    def test_recovery_rejects_any_full_manifest_drift(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "manifest-drift-recovery.json"
        atomic_write_canonical_json(
            journal,
            self.unfinished_journal(manifest, phase="applying", status="applying"),
        )
        drifted = replace(manifest, archive_root=self.root / "different-archive")
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(drifted)
        self.assertEqual(result.exit_code, InstallerExit.INVALID_MANIFEST)
        self.assertEqual(result.phase, "recovery-request-mismatch")
        self.assertEqual(mutation.calls, [])
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["phase"], "applying")

    def test_commit_intent_recovery_completes_idempotent_commit(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "recover-commit.json"
        atomic_write_canonical_json(journal, self.unfinished_journal(manifest, phase="committing", status="commit-intent"))
        result = WindowsInstallerTransaction(
            journal_path=journal, adapters=[FakeAdapter(mutation)]
        ).execute(manifest)
        self.assertEqual(result.exit_code, InstallerExit.SUCCESS)
        self.assertEqual(result.transaction_id, "recoverable-transaction")
        self.assertEqual(mutation.calls, ["commit"])
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["phase"], "committed")

    def test_identity_drift_returns_exact_manifest_code_before_prepare(self) -> None:
        manifest = build_manifest(self.args())
        mutation = FakeMutation()
        journal = self.root / "identity-drift.json"

        def reject(_manifest) -> None:
            raise RuntimeError("candidate drift")

        result = WindowsInstallerTransaction(
            journal_path=journal,
            adapters=[FakeAdapter(mutation)],
            identity_validator=reject,
        ).execute(manifest)
        self.assertEqual(result.exit_code, InstallerExit.INVALID_MANIFEST)
        self.assertEqual(result.phase, "invalid-manifest")
        self.assertEqual(mutation.calls, [])
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["phase"], "invalid-manifest")

    def test_all_entry_sources_build_the_same_install_contract(self) -> None:
        documents = [build_manifest(self.args(source)).to_document() for source in ("inno", "manual", "auto-update")]
        normalized = []
        for document in documents:
            document = dict(document)
            document.pop("source_entrypoint")
            normalized.append(document)
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(normalized[1], normalized[2])

    def test_outer_entrypoints_route_to_the_single_controller(self) -> None:
        powershell = (ROOT / "packaging/windows/install.ps1").read_text(encoding="utf-8").lower()
        inno = (ROOT / "packaging/windows/MemoryWuxian.iss").read_text(encoding="utf-8").lower()
        updater = (ROOT / "scripts/auto_update.py").read_text(encoding="utf-8").lower()
        self.assertIn("install_windows_transaction.py", powershell)
        self.assertIn("windows_installer_broker.py", powershell)
        self.assertIn("--prepare-only", powershell)
        self.assertIn("--launch-manifest", powershell)
        self.assertIn("--controller", powershell)
        self.assertNotIn("install_codex_autosync_windows.py", powershell)
        self.assertIn("sourceentrypoint", inno)
        self.assertIn("getcustomsetupexitcode", inno)
        self.assertIn("transactionexitcode := resultcode", inno)
        self.assertNotIn("[run]", inno)
        self.assertIn("/sourceentrypoint=auto-update", updater)
        self.assertIn("exit $transactionexit", powershell)
        self.assertNotIn("write-error", powershell)


if __name__ == "__main__":
    unittest.main()
