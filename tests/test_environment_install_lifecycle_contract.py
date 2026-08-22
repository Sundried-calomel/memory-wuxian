from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_environment_rules as rule_module  # noqa: E402
import memory_environment_skills as skill_module  # noqa: E402
from memory_environment_rules import EnvironmentRuleInstaller  # noqa: E402
from memory_environment_skills import EnvironmentSkillInstaller  # noqa: E402
from platform_lock import exclusive_lock  # noqa: E402
from tests import test_memory_environment_rules as rule_support  # noqa: E402
from tests import test_memory_environment_skills as skill_support  # noqa: E402


FIXED_TIME = "2026-08-19T14:30:00+00:00"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def transaction_metadata_sha256(value) -> str:
    payload = dict(value)
    payload.pop("metadata_sha256", None)
    return sha256(canonical_json_bytes(payload))


class FixedUUIDs:
    def __init__(self, start: int):
        self.next_value = start
        self.issued: list[UUID] = []

    def __call__(self) -> UUID:
        value = UUID(int=self.next_value)
        self.next_value += 1
        self.issued.append(value)
        return value


@contextmanager
def fixed_lifecycle(module, start: int):
    sequence = FixedUUIDs(start)
    with (
        mock.patch.object(module, "_now_iso", return_value=FIXED_TIME),
        mock.patch.object(module.uuid, "uuid4", side_effect=sequence),
    ):
        yield sequence


class TracedRuleInstaller(EnvironmentRuleInstaller):
    trace: list[str] = []

    def _create_transaction(self, **kwargs):
        result = super()._create_transaction(**kwargs)
        self.trace.append("transaction-prepared")
        return result

    def _write_candidate(self, target, payload, mode):
        result = super()._write_candidate(target, payload, mode)
        self.trace.append("candidate-written")
        return result

    def _validate_candidate(self, candidate, **kwargs):
        result = super()._validate_candidate(candidate, **kwargs)
        self.trace.append("candidate-validated")
        return result

    def _before_replace(self, target_path):
        self.trace.append("before-replace")
        return super()._before_replace(target_path)

    def _set_transaction_status(self, transaction_dir, status):
        result = super()._set_transaction_status(transaction_dir, status)
        self.trace.append(f"transaction-{status}")
        return result

    def _after_replace(self, target_path):
        self.trace.append("after-replace")
        return super()._after_replace(target_path)

    def _persist_receipt(self, receipt):
        result = super()._persist_receipt(receipt)
        self.trace.append(f"receipt-{receipt['result']}")
        return result

    def _complete_transaction(self, transaction_dir, *, status, receipt):
        result = super()._complete_transaction(
            transaction_dir, status=status, receipt=receipt
        )
        self.trace.append(f"transaction-{status}")
        return result

    def _delete_rollback_object(self, transaction_dir):
        result = super()._delete_rollback_object(transaction_dir)
        self.trace.append("rollback-deleted")
        return result


class CrashRuleAfterReplace(TracedRuleInstaller):
    def _after_replace(self, target_path):
        self.trace.append("crash-after-replace")
        raise SystemExit("injected crash after rule replacement")


class TracedSkillInstaller(EnvironmentSkillInstaller):
    trace: list[str] = []

    def _save_verified_rollback(self, target, target_binding, previous_hash):
        result = super()._save_verified_rollback(
            target, target_binding, previous_hash
        )
        self.trace.append("rollback-saved")
        return result

    def _after_rollback_saved(self, target):
        self.trace.append("after-rollback-saved")
        return super()._after_rollback_saved(target)

    def _write_transaction(self, transaction_id, prepared, **kwargs):
        result = super()._write_transaction(transaction_id, prepared, **kwargs)
        self.trace.append("transaction-prepared")
        return result

    def _after_switch(self, target):
        self.trace.append("after-switch")
        return super()._after_switch(target)

    def _persist_receipt(self, receipt):
        result = super()._persist_receipt(receipt)
        self.trace.append(f"receipt-{receipt['result']}")
        return result

    def _finish_transaction(self, path, status):
        result = super()._finish_transaction(path, status)
        self.trace.append(f"transaction-{status}")
        return result


class CrashSkillAfterSwitch(TracedSkillInstaller):
    def _after_switch(self, target):
        self.trace.append("crash-after-switch")
        raise SystemExit("injected crash after Skill directory switch")


class EnvironmentInstallLifecycleContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        TracedRuleInstaller.trace = []
        CrashRuleAfterReplace.trace = TracedRuleInstaller.trace
        TracedSkillInstaller.trace = []
        CrashSkillAfterSwitch.trace = TracedSkillInstaller.trace

    def rule_harness(self):
        harness = rule_support.EnvironmentRuleInstallerTest(methodName="runTest")
        harness.setUp()
        self.addCleanup(harness.tearDown)
        harness.target = harness.root / "规则 日本語 ¥ 🧪.md"
        return harness

    def skill_harness(self):
        harness = skill_support.EnvironmentSkillInstallerTest(methodName="runTest")
        harness.setUp()
        self.addCleanup(harness.tearDown)
        return harness

    def rule_case(self, harness):
        base_block = rule_support.managed("shared-rules", "\r\n旧规则 ¥\r\n")
        remote_block = rule_support.managed("shared-rules", "\n新规则 日本語 🧪\n")
        original = f"前置\r\n{base_block}\r\n后置 Ω\n".encode("utf-8")
        candidate = f"前置\r\n{remote_block}\r\n后置 Ω\n".encode("utf-8")
        harness.target.write_bytes(original)
        os.chmod(harness.target, 0o640)
        original_mode = stat.S_IMODE(harness.target.stat().st_mode)
        base_revision, remote_revision = harness.register_pair(
            base_content=base_block,
            remote_content=remote_block,
        )
        binding = harness.global_binding(
            base_revision,
            relative_path=harness.target.name,
        )
        return {
            "base_block": base_block,
            "remote_block": remote_block,
            "original": original,
            "candidate": candidate,
            "original_mode": original_mode,
            "base_revision": base_revision,
            "remote_revision": remote_revision,
            "binding": binding,
        }

    def expected_rule_rehearsal(self, case, transaction_id=None):
        value = {
            "decision": "update",
            "reason": "local equals base and remote changed",
            "strategy": "managed-block",
            "classification": "canonical",
            "owner": "memory-wuxian",
            "base_revision_id": case["base_revision"]["revision_id"],
            "base_content_sha256": case["base_revision"]["content_sha256"],
            "local_view_sha256": sha256(
                case["base_block"].encode("utf-8").replace(b"\r\n", b"\n")
            ),
            "remote_view_sha256": sha256(
                case["remote_block"].encode("utf-8").replace(b"\r\n", b"\n")
            ),
            "candidate_sha256": sha256(case["candidate"]),
            "outside_bytes_unchanged": True,
            "mode_preserved": True,
        }
        if transaction_id is not None:
            value["transaction_id"] = transaction_id
        return value

    def expected_rule_transaction(
        self,
        harness,
        case,
        transaction_id,
        *,
        status,
        receipt=None,
    ):
        target_canonical = str(harness.target.resolve(strict=True))
        value = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "artifact_id": "global-rule:codex-agents",
            "revision_id": case["remote_revision"]["revision_id"],
            "target_binding": case["binding"]["binding_id"],
            "target_node_id": "mac-mini-lab",
            "target_canonical_path": target_canonical,
            "target_path_sha256": sha256(target_canonical.encode("utf-8")),
            "binding_sha256": sha256(canonical_json_bytes(case["binding"])),
            "base_revision_id": case["base_revision"]["revision_id"],
            "base_content_sha256": case["base_revision"]["content_sha256"],
            "content_sha256": case["remote_revision"]["content_sha256"],
            "candidate_sha256": sha256(case["candidate"]),
            "original_sha256": sha256(case["original"]),
            "original_mode": case["original_mode"],
            "strategy": "managed-block",
            "managed_block_id": "shared-rules",
            "rollback_object": "rollback.bin",
            "status": status,
            "receipt_id": None if receipt is None else receipt["receipt_id"],
            "receipt_sha256": None,
            "created_at": FIXED_TIME,
            "updated_at": FIXED_TIME,
            "finished_at": FIXED_TIME if status in {"installed", "rolled-back"} else None,
        }
        if receipt is not None:
            value["receipt_sha256"] = sha256(pretty_json_bytes(receipt))
        value["metadata_sha256"] = transaction_metadata_sha256(value)
        return value

    def expected_rule_receipt(
        self,
        case,
        transaction_id,
        receipt_id,
        *,
        result,
        recovery=False,
    ):
        rehearsal = self.expected_rule_rehearsal(case, transaction_id)
        if recovery:
            rehearsal = {
                "transaction_id": transaction_id,
                "recovery": True,
                "strategy": "managed-block",
                "binding_sha256": sha256(canonical_json_bytes(case["binding"])),
                "error": "incomplete replaced transaction recovered",
            }
        rollback = (
            {
                "attempted": True,
                "succeeded": True,
                "recovered_after_crash": True,
            }
            if recovery
            else {"attempted": False, "succeeded": False}
        )
        return {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "artifact_id": "global-rule:codex-agents",
            "revision_id": case["remote_revision"]["revision_id"],
            "content_sha256": case["remote_revision"]["content_sha256"],
            "target_node_id": "mac-mini-lab",
            "target_binding": case["binding"]["binding_id"],
            "previous_installed_sha256": sha256(case["original"]),
            "final_installed_sha256": sha256(
                case["original"] if recovery else case["candidate"]
            ),
            "rehearsal": rehearsal,
            "result": result,
            "rollback": rollback,
            "created_at": FIXED_TIME,
        }

    def test_rule_install_freezes_public_result_journal_receipt_mode_and_phases(self):
        harness = self.rule_harness()
        case = self.rule_case(harness)
        installer = harness.installer(case["binding"], TracedRuleInstaller)

        with fixed_lifecycle(rule_module, 0x100) as ids:
            preview = installer.install(
                artifact_id="global-rule:codex-agents",
                revision_id=case["remote_revision"]["revision_id"],
                target_binding=case["binding"]["binding_id"],
            )
            result = installer.install(
                artifact_id="global-rule:codex-agents",
                revision_id=case["remote_revision"]["revision_id"],
                target_binding=case["binding"]["binding_id"],
                apply=True,
            )

        transaction_id = f"rule-{ids.issued[0].hex}"
        receipt_id = f"rule-{ids.issued[1].hex}"
        expected_rehearsal = self.expected_rule_rehearsal(case)
        canonical_target = str(harness.target.resolve(strict=True))
        self.assertEqual(
            preview,
            {
                "status": "preview",
                "artifact_id": "global-rule:codex-agents",
                "revision_id": case["remote_revision"]["revision_id"],
                "target_binding": case["binding"]["binding_id"],
                "target_path": canonical_target,
                "decision": "update",
                "previous_installed_sha256": sha256(case["original"]),
                "candidate_sha256": sha256(case["candidate"]),
                "rehearsal": expected_rehearsal,
            },
        )
        receipt = self.expected_rule_receipt(
            case, transaction_id, receipt_id, result="installed"
        )
        self.assertEqual(
            result,
            {
                "status": "installed",
                "target_path": canonical_target,
                "receipt": receipt,
            },
        )
        transaction_dir = harness.transaction_directories()[0]
        expected_transaction = self.expected_rule_transaction(
            harness,
            case,
            transaction_id,
            status="installed",
            receipt=receipt,
        )
        self.assertEqual(
            (transaction_dir / "transaction.json").read_bytes(),
            pretty_json_bytes(expected_transaction),
        )
        receipt_path = harness.registry.receipts_dir / f"{receipt_id}.json"
        self.assertEqual(receipt_path.read_bytes(), pretty_json_bytes(receipt))
        self.assertEqual(harness.target.read_bytes(), case["candidate"])
        self.assertEqual(
            stat.S_IMODE(harness.target.stat().st_mode), case["original_mode"]
        )
        self.assertFalse((transaction_dir / "rollback.bin").exists())
        self.assertEqual(
            TracedRuleInstaller.trace,
            [
                "transaction-prepared",
                "candidate-written",
                "candidate-validated",
                "before-replace",
                "transaction-replaced",
                "after-replace",
                "receipt-installed",
                "transaction-installed",
                "rollback-deleted",
            ],
        )

    def test_rule_incomplete_replaced_transaction_recovers_exact_rollback_bytes(self):
        harness = self.rule_harness()
        case = self.rule_case(harness)
        installer = harness.installer(case["binding"], CrashRuleAfterReplace)
        with fixed_lifecycle(rule_module, 0x200) as crash_ids:
            with self.assertRaisesRegex(SystemExit, "injected crash"):
                installer.install(
                    artifact_id="global-rule:codex-agents",
                    revision_id=case["remote_revision"]["revision_id"],
                    target_binding=case["binding"]["binding_id"],
                    apply=True,
                )

        transaction_id = f"rule-{crash_ids.issued[0].hex}"
        transaction_dir = harness.transaction_directories()[0]
        rollback_path = transaction_dir / "rollback.bin"
        expected_replaced = self.expected_rule_transaction(
            harness, case, transaction_id, status="replaced"
        )
        self.assertEqual(
            (transaction_dir / "transaction.json").read_bytes(),
            pretty_json_bytes(expected_replaced),
        )
        self.assertEqual(rollback_path.read_bytes(), case["original"])
        expected_rollback_mode = 0o666 if os.name == "nt" else 0o600
        self.assertEqual(
            stat.S_IMODE(rollback_path.stat().st_mode), expected_rollback_mode
        )
        self.assertEqual(harness.target.read_bytes(), case["candidate"])
        self.assertEqual(
            CrashRuleAfterReplace.trace,
            [
                "transaction-prepared",
                "candidate-written",
                "candidate-validated",
                "before-replace",
                "transaction-replaced",
                "crash-after-replace",
            ],
        )

        normal = harness.installer(case["binding"])
        with fixed_lifecycle(rule_module, 0x210) as recovery_ids:
            recovered = normal.recover_pending()
        receipt_id = f"rule-{recovery_ids.issued[0].hex}"
        receipt = self.expected_rule_receipt(
            case,
            transaction_id,
            receipt_id,
            result="rolled-back",
            recovery=True,
        )
        self.assertEqual(
            recovered,
            {"status": "recovered", "installed": 0, "rolled_back": 1, "aborted": 0},
        )
        self.assertEqual(harness.target.read_bytes(), case["original"])
        self.assertEqual(
            stat.S_IMODE(harness.target.stat().st_mode), case["original_mode"]
        )
        self.assertFalse(rollback_path.exists())
        self.assertEqual(
            (transaction_dir / "transaction.json").read_bytes(),
            pretty_json_bytes(
                self.expected_rule_transaction(
                    harness,
                    case,
                    transaction_id,
                    status="rolled-back",
                    receipt=receipt,
                )
            ),
        )
        receipt_path = harness.registry.receipts_dir / f"{receipt_id}.json"
        self.assertEqual(receipt_path.read_bytes(), pretty_json_bytes(receipt))

    def skill_files(self, harness, marker):
        files = harness.files(marker=marker)
        files["notes/日本語-¥-🧪.txt"] = f"内容 {marker} ¥ 🧪\n".encode("utf-8")
        return files

    def install_skill_revision(
        self,
        harness,
        *,
        marker,
        version,
        base_revision_id=None,
        installer_class=EnvironmentSkillInstaller,
        uuid_start,
    ):
        files = self.skill_files(harness, marker)
        contract = harness.manifest(
            "rev:" + "0" * 64,
            files=files,
            version=version,
        )
        revision = harness.register_revision(
            version=int(version.split(".", 1)[0]),
            base_revision_id=base_revision_id,
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": revision["revision_id"]}
        package = harness.package(f"{marker}.zip", manifest, files)
        installer = harness.installer(installer_class)
        with fixed_lifecycle(skill_module, uuid_start) as ids:
            preview = installer.install(
                package_path=package,
                artifact_id=harness.artifact_id,
                revision_id=revision["revision_id"],
                target_binding="global-demo",
            )
            result = installer.install(
                package_path=package,
                artifact_id=harness.artifact_id,
                revision_id=revision["revision_id"],
                target_binding="global-demo",
                apply=True,
            )
        return {
            "files": files,
            "revision": revision,
            "manifest": manifest,
            "package": package,
            "installer": installer,
            "preview": preview,
            "result": result,
            "ids": ids,
        }

    def assert_skill_tree(self, root, files):
        actual = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(actual, files)
        for relative, payload in files.items():
            path = root.joinpath(*relative.split("/"))
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(stat.S_ISLNK(path.lstat().st_mode))
            expected_mode = (
                0o666
                if os.name == "nt"
                else (0o755 if relative.startswith("scripts/") else 0o644)
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode)

    @staticmethod
    def expected_skill_tree_hash(files, manifest, platform):
        executable = {
            item["path"]: bool(item["executable"])
            for item in manifest["files"]
        }
        records = [
            {
                "path": relative,
                "sha256": sha256(payload),
                "size": len(payload),
                "executable": (
                    False if platform == "windows" else executable[relative]
                ),
            }
            for relative, payload in sorted(files.items())
        ]
        return sha256(canonical_json_bytes(records))

    def test_skill_install_freezes_public_result_journal_receipt_tree_and_phases(self):
        harness = self.skill_harness()
        installed = self.install_skill_revision(
            harness,
            marker="初版-¥-🧪",
            version="2.0.0",
            base_revision_id=harness.revision["revision_id"],
            uuid_start=0x300,
            installer_class=TracedSkillInstaller,
        )
        ids = installed["ids"].issued
        transaction_id = f"skill-{ids[0].hex}"
        receipt_id = f"skill-{ids[3].hex}"
        target = Path(installed["result"]["target_path"])
        installer = installed["installer"]
        package_sha = sha256(installed["package"].read_bytes())
        actual_hash = installer._actual_tree_hash(target)
        logical_hash = installer._logical_tree_hash(installed["manifest"])
        rehearsal = {
            "package_sha256": package_sha,
            "manifest_sha256": sha256(canonical_json_bytes(installed["manifest"])),
            "declared_files": len(installed["manifest"]["files"]),
            "checks": ["utf8", "python-compile"],
            "platform": installer.platform,
            "runtime_requirements": {"python": ">=3.10"},
            "network_access": {"enabled": False, "destinations": []},
            "persistent_components": [],
            "codex_discovery": "validated",
        }
        self.assertEqual(
            installed["preview"],
            {
                "status": "preview",
                "artifact_id": harness.artifact_id,
                "revision_id": installed["revision"]["revision_id"],
                "target_binding": "global-demo",
                "target_path": str(target),
                "decision": "install",
                "package_sha256": package_sha,
                "previous_installed_sha256": None,
                "final_installed_sha256": logical_hash,
                "rehearsal": rehearsal,
            },
        )
        receipt = {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "artifact_id": harness.artifact_id,
            "revision_id": installed["revision"]["revision_id"],
            "content_sha256": package_sha,
            "target_node_id": "mac-test-node",
            "target_binding": "global-demo",
            "previous_installed_sha256": None,
            "final_installed_sha256": actual_hash,
            "rehearsal": {**rehearsal, "transaction_id": transaction_id},
            "result": "installed",
            "rollback": {"available": False, "attempted": False, "succeeded": False},
            "created_at": FIXED_TIME,
        }
        self.assertEqual(
            installed["result"],
            {"status": "installed", "target_path": str(target), "receipt": receipt},
        )
        transaction_path = installer.transactions_dir / f"{transaction_id}.json"
        expected_transaction = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "artifact_id": harness.artifact_id,
            "revision_id": installed["revision"]["revision_id"],
            "target_binding": "global-demo",
            "target_path": str(target),
            "staging_path": str(installer.staging_root / transaction_id),
            "sibling_candidate_path": str(target.parent / f".{target.name}.mw-candidate-{ids[1].hex}"),
            "displaced_path": str(target.parent / f".{target.name}.mw-previous-{ids[2].hex}"),
            "rollback_path": str(installer._rollback_path("global-demo")),
            "had_previous": False,
            "previous_installed_sha256": None,
            "expected_final_sha256": actual_hash,
            "package_sha256": package_sha,
            "rehearsal": {**rehearsal, "transaction_id": transaction_id},
            "status": "installed",
            "created_at": FIXED_TIME,
            "finished_at": FIXED_TIME,
        }
        self.assertEqual(transaction_path.read_bytes(), pretty_json_bytes(expected_transaction))
        self.assertEqual(
            (installer.receipts_dir / f"{receipt_id}.json").read_bytes(),
            pretty_json_bytes(receipt),
        )
        self.assert_skill_tree(target, installed["files"])
        self.assertFalse(Path(expected_transaction["staging_path"]).exists())
        self.assertFalse(Path(expected_transaction["sibling_candidate_path"]).exists())
        self.assertFalse(Path(expected_transaction["displaced_path"]).exists())
        self.assertFalse(Path(expected_transaction["rollback_path"]).exists())
        self.assertEqual(
            TracedSkillInstaller.trace,
            ["transaction-prepared", "after-switch", "receipt-installed", "transaction-installed"],
        )

    def test_skill_incomplete_switch_keeps_separate_paths_and_recovers_old_tree(self):
        harness = self.skill_harness()
        initial = self.install_skill_revision(
            harness,
            marker="基线-¥",
            version="2.0.0",
            base_revision_id=harness.revision["revision_id"],
            uuid_start=0x400,
        )
        target = Path(initial["result"]["target_path"])
        initial_hash = initial["installer"]._actual_tree_hash(target)
        update_files = self.skill_files(harness, "更新-日本語-🧪")
        contract = harness.manifest(
            "rev:" + "0" * 64,
            files=update_files,
            version="3.0.0",
        )
        revision = harness.register_revision(
            version=3,
            base_revision_id=initial["revision"]["revision_id"],
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": revision["revision_id"]}
        package = harness.package("更新-日本語-🧪.zip", manifest, update_files)
        crashing = harness.installer(CrashSkillAfterSwitch)
        with fixed_lifecycle(skill_module, 0x500) as crash_ids:
            with self.assertRaisesRegex(SystemExit, "injected crash"):
                crashing.install(
                    package_path=package,
                    artifact_id=harness.artifact_id,
                    revision_id=revision["revision_id"],
                    target_binding="global-demo",
                    apply=True,
                )

        ids = crash_ids.issued
        transaction_id = f"skill-{ids[0].hex}"
        transaction_path = crashing.transactions_dir / f"{transaction_id}.json"
        package_sha = sha256(package.read_bytes())
        final_hash = self.expected_skill_tree_hash(
            update_files, manifest, crashing.platform
        )
        rehearsal = {
            "package_sha256": package_sha,
            "manifest_sha256": sha256(canonical_json_bytes(manifest)),
            "declared_files": len(manifest["files"]),
            "checks": ["utf8", "python-compile"],
            "platform": crashing.platform,
            "runtime_requirements": {"python": ">=3.10"},
            "network_access": {"enabled": False, "destinations": []},
            "persistent_components": [],
            "codex_discovery": "validated",
            "transaction_id": transaction_id,
        }
        expected_transaction = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "artifact_id": harness.artifact_id,
            "revision_id": revision["revision_id"],
            "target_binding": "global-demo",
            "target_path": str(target),
            "staging_path": str(crashing.staging_root / transaction_id),
            "sibling_candidate_path": str(
                target.parent / f".{target.name}.mw-candidate-{ids[1].hex}"
            ),
            "displaced_path": str(
                target.parent / f".{target.name}.mw-previous-{ids[2].hex}"
            ),
            "rollback_path": str(crashing._rollback_path("global-demo")),
            "had_previous": True,
            "previous_installed_sha256": initial_hash,
            "expected_final_sha256": final_hash,
            "package_sha256": package_sha,
            "rehearsal": rehearsal,
            "status": "prepared",
            "created_at": FIXED_TIME,
        }
        self.assertEqual(
            transaction_path.read_bytes(), pretty_json_bytes(expected_transaction)
        )
        self.assertEqual(
            CrashSkillAfterSwitch.trace,
            ["rollback-saved", "after-rollback-saved", "transaction-prepared", "crash-after-switch"],
        )
        staging = Path(expected_transaction["staging_path"])
        sibling = Path(expected_transaction["sibling_candidate_path"])
        displaced = Path(expected_transaction["displaced_path"])
        rollback = Path(expected_transaction["rollback_path"])
        self.assertFalse(staging.exists())
        self.assertFalse(sibling.exists())
        self.assertTrue(displaced.is_dir())
        self.assertTrue(rollback.is_dir())
        self.assert_skill_tree(target, update_files)
        self.assert_skill_tree(displaced, initial["files"])
        self.assert_skill_tree(rollback, initial["files"])
        self.assertEqual(crashing._actual_tree_hash(displaced), initial_hash)
        self.assertEqual(crashing._actual_tree_hash(rollback), initial_hash)

        normal = harness.installer()
        with fixed_lifecycle(skill_module, 0x510) as recovery_ids:
            with exclusive_lock(normal.lock_path):
                recovered = normal.recover_transactions()
        receipt_id = f"skill-{recovery_ids.issued[0].hex}"
        receipt = {
            "schema_version": 1,
            "receipt_id": receipt_id,
            "artifact_id": harness.artifact_id,
            "revision_id": revision["revision_id"],
            "content_sha256": package_sha,
            "target_node_id": "mac-test-node",
            "target_binding": "global-demo",
            "previous_installed_sha256": initial_hash,
            "final_installed_sha256": initial_hash,
            "rehearsal": {**rehearsal, "recovery": "startup-crash-recovery"},
            "result": "rolled-back",
            "rollback": {"available": True, "attempted": True, "succeeded": True},
            "created_at": FIXED_TIME,
        }
        self.assertEqual(recovered, [receipt])
        self.assertEqual(
            (normal.receipts_dir / f"{receipt_id}.json").read_bytes(),
            pretty_json_bytes(receipt),
        )
        recovered_transaction = {
            **expected_transaction,
            "status": "rolled-back",
            "finished_at": FIXED_TIME,
            "recovery_receipt_id": receipt_id,
        }
        self.assertEqual(
            transaction_path.read_bytes(), pretty_json_bytes(recovered_transaction)
        )
        self.assert_skill_tree(target, initial["files"])
        self.assertFalse(displaced.exists())
        self.assertTrue(rollback.is_dir())


if __name__ == "__main__":
    unittest.main()
