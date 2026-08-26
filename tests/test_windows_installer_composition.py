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
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install_windows_transaction import (
    ArchiveInitializationMutation,
    CollectorGenerationMutation,
    ConfigurationMigrationMutation,
    DashboardShortcutMutation,
    FederationNodeInitializationMutation,
    MaintenanceRegistrationMutation,
    ProductInstallAdapter,
    WindowsInstallResourceNamespace,
    build_manifest,
    default_installer_resource_root,
)
import install_windows_transaction as composition
from windows_installer_transaction import TransactionToken


class WindowsInstallerCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate = self.root / "candidate"
        self.target = self.root / "target"
        self.runtime = self.root / "runtime"
        self.sessions = self.root / "sessions"
        self.archive = self.root / "archive"
        self.candidate.mkdir()
        self.target.mkdir()
        self.sessions.mkdir()
        (self.runtime / "python").mkdir(parents=True)
        shutil.copy2(sys.executable, self.runtime / "python/python.exe")
        (self.runtime / "runtime-lock.json").write_text("{}\n", encoding="utf-8")
        (self.runtime / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
        (self.root / "codex.exe").write_bytes(b"codex-fixture")
        (self.candidate / "pyproject.toml").write_text('[project]\nversion = "2.20.0"\n', encoding="utf-8")
        (self.candidate / "SKILL.md").write_text("# fixture\n", encoding="utf-8")
        (self.candidate / "bin").mkdir()
        (self.candidate / "bin/memory-wuxian-dashboard-launcher.exe").write_bytes(b"expected-launcher")
        (self.target / "pyproject.toml").write_text('[project]\nversion = "2.15.0"\n', encoding="utf-8")
        self.original_config = b"archive:\n  backup_keep: 7\ncloud:\n  enabled: true\n"
        (self.target / "config.yaml").write_bytes(self.original_config)
        (self.target / "config.defaults.yaml").write_text(
            "archive:\n  backup_keep: 3\n  incremental: true\ncloud:\n  enabled: false\ndashboard:\n  language: zh-CN\n",
            encoding="utf-8",
        )
        self.pointer = self.root / "active-root.txt"
        self.pointer.write_text(str(self.archive), encoding="utf-8")
        args = argparse.Namespace(
            operation="install", source_entrypoint="manual", candidate_root=str(self.candidate),
            skill_root=str(self.target), archive_root=str(self.archive), archive_pointer=str(self.pointer),
            sessions_root=str(self.sessions), python_executable=str(self.runtime / "python/python.exe"),
            runtime_bundle_root=str(self.runtime), runtime_bundle_id="a" * 64,
            codex_cli=str(self.root / "codex.exe"), journal_path=str(self.root / "journal.json"),
            manifest_output=str(self.root / "manifest.json"), failure_point=None,
        )
        self.manifest = build_manifest(args)
        self.token = TransactionToken("transaction", "secret")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_registry_drives_config_migration_and_exact_rollback(self) -> None:
        mutation = ConfigurationMigrationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/config"
        )
        mutation.prepare(self.token)
        evidence = mutation.apply(self.token)
        self.assertEqual(
            evidence["steps"],
            [
                "windows-2.15.0-to-2.18.0",
                "windows-2.18.0-to-2.19.0",
                "windows-2.19.0-to-2.19.1",
                "windows-2.19.1-to-2.20.0",
            ],
        )
        self.assertTrue(mutation.verify(self.token)["config_sha256"])
        mutation.rollback(self.token)
        self.assertEqual((self.target / "config.yaml").read_bytes(), self.original_config)
        self.assertFalse((self.target / "config-migration-receipt.json").exists())

    def test_config_partial_apply_uses_precomputed_hashes_for_rollback(self) -> None:
        mutation = ConfigurationMigrationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/config-partial"
        )
        mutation.prepare(self.token)
        real_atomic = composition.atomic_replace_bytes

        def fail_receipt(path, payload, **kwargs):
            if Path(path).name == "config-migration-receipt.json":
                raise OSError("injected receipt write failure")
            return real_atomic(path, payload, **kwargs)

        with mock.patch.object(composition, "atomic_replace_bytes", side_effect=fail_receipt):
            with self.assertRaisesRegex(OSError, "injected"):
                mutation.apply(self.token)
        mutation.rollback(self.token)
        mutation.rollback_verify(self.token)
        self.assertEqual((self.target / "config.yaml").read_bytes(), self.original_config)

    def test_archive_initialization_is_bounded_and_preserves_raw_and_pointer(self) -> None:
        raw = self.archive / "raw/2026/08/record.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("authoritative raw record\n", encoding="utf-8")
        raw_before = raw.read_bytes()
        pointer_before = self.pointer.read_bytes()
        mutation = ArchiveInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/archive"
        )
        mutation.prepare(self.token)
        mutation.apply(self.token)
        mutation.verify(self.token)
        self.assertEqual(raw.read_bytes(), raw_before)
        self.assertEqual(self.pointer.read_bytes(), pointer_before)
        mutation.rollback(self.token)
        self.assertEqual(raw.read_bytes(), raw_before)
        self.assertEqual(self.pointer.read_bytes(), pointer_before)

    def test_archive_initialization_clean_apply_does_not_require_target_config(self) -> None:
        (self.candidate / "config.yaml").write_bytes(self.original_config)
        (self.target / "config.yaml").unlink()
        mutation = ArchiveInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/archive-clean"
        )
        mutation.prepare(self.token)
        mutation.apply(self.token)
        mutation.verify(self.token)
        self.assertFalse((self.target / "config.yaml").exists())

    def test_archive_verify_delegates_clean_pointer_creation_to_collector(self) -> None:
        self.pointer.unlink()
        mutation = ArchiveInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/archive-pointer-delegation"
        )
        mutation.prepare(self.token)
        mutation.apply(self.token)
        self.pointer.write_text(str(self.archive) + "\n", encoding="utf-8")
        evidence = mutation.verify(self.token)
        self.assertEqual(
            evidence["archive_pointer_status"],
            "delegated-to-installed-capture-generation",
        )

    def test_archive_rollback_retains_runtime_writes_without_targeting_raw(self) -> None:
        mutation = ArchiveInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/archive-runtime-write"
        )
        mutation.prepare(self.token)
        mutation.apply(self.token)
        runtime_file = next(path for path in mutation.files if path.is_file())
        runtime_file.write_bytes(runtime_file.read_bytes() + b"runtime-update\n")
        raw = self.archive / "raw/2026/08/new-record.md"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"append-only runtime record\n")

        evidence = mutation.rollback(self.token)
        verified = mutation.rollback_verify(self.token)

        self.assertTrue(runtime_file.is_file())
        self.assertEqual(raw.read_bytes(), b"append-only runtime record\n")
        self.assertIn(str(runtime_file), evidence["retained_runtime_files"])
        self.assertIn(str(runtime_file), verified["retained_runtime_files"])
        self.assertFalse(evidence["raw_archive_targeted"])

    def test_prepare_evidence_is_serializable_and_has_no_raw_backup(self) -> None:
        mutation = ArchiveInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/archive"
        )
        evidence = mutation.prepare(self.token)
        payload = json.dumps(evidence, ensure_ascii=False)
        self.assertFalse(any("raw" in Path(item["path"]).parts for item in evidence["bounded_files"]))
        self.assertFalse(evidence["archive_pointer_targeted"])

    def test_federation_node_clean_install_is_reversible(self) -> None:
        mutation = FederationNodeInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/federation-clean"
        )
        prepared = mutation.prepare(self.token)
        self.assertFalse(prepared["existing_node_preserved"])
        self.assertFalse((self.archive / "federation").exists())
        mutation.apply(self.token)
        verified = mutation.verify(self.token)
        self.assertEqual(verified["display_name"], mutation.display_name)
        node = self.archive / "federation/node.json"
        self.assertTrue(node.is_file())
        node_document = json.loads(node.read_text(encoding="utf-8"))
        self.assertEqual(
            Path(node_document["replica_root"]).resolve(),
            (self.archive.parent / "archive-federation-cache").resolve(),
        )
        self.assertNotIn("mwf-probe-", node_document["replica_root"])
        mutation.rollback(self.token)
        mutation.rollback_verify(self.token)
        self.assertFalse(node.exists())

    def test_federation_node_repeat_install_preserves_exact_identity(self) -> None:
        node = self.archive / "federation/node.json"
        node.parent.mkdir(parents=True)
        original = b'{"created_at":"before","display_name":"existing","format_version":1,"node_id":"mw-existing-node","protocol_version":2,"replica_root":"fixture"}\n'
        node.write_bytes(original)
        mutation = FederationNodeInitializationMutation(
            self.manifest, self.token, backup_root=self.root / "backups/federation-existing"
        )
        prepared = mutation.prepare(self.token)
        self.assertTrue(prepared["existing_node_preserved"])
        mutation.apply(self.token)
        mutation.verify(self.token)
        self.assertEqual(node.read_bytes(), original)
        mutation.rollback(self.token)
        mutation.rollback_verify(self.token)
        self.assertEqual(node.read_bytes(), original)

    def test_federation_rollback_retains_runtime_modified_created_file(self) -> None:
        mutation = FederationNodeInitializationMutation(
            self.manifest,
            self.token,
            backup_root=self.root / "backups/federation-runtime-write",
        )
        mutation.prepare(self.token)
        mutation.apply(self.token)
        node = self.archive / "federation/node.json"
        node.write_bytes(node.read_bytes() + b"\n")

        evidence = mutation.rollback(self.token)
        verified = mutation.rollback_verify(self.token)

        self.assertTrue(node.is_file())
        self.assertIn(
            str(node.resolve()),
            {str(Path(item).resolve()) for item in evidence["retained_files"]},
        )
        self.assertIn(
            str(node.resolve()),
            {str(Path(item).resolve()) for item in verified["retained_files"]},
        )

    def test_federation_prepare_survives_a_deep_transaction_backup_root(self) -> None:
        backup_root = self.root / ("deep-a-" + "a" * 90) / ("deep-b-" + "b" * 90) / "federation-node"
        mutation = FederationNodeInitializationMutation(
            self.manifest,
            self.token,
            backup_root=backup_root,
        )

        evidence = mutation.prepare(self.token)

        self.assertFalse(backup_root.exists())
        self.assertFalse((self.archive / "federation").exists())
        self.assertTrue(all("expected_base64" in item for item in evidence["bounded_files"]))
        json.dumps(evidence, ensure_ascii=False)

    def test_windows_resource_staging_uses_a_short_durable_root(self) -> None:
        program_data = self.root / "ProgramData"
        with mock.patch.dict(composition.os.environ, {"PROGRAMDATA": str(program_data)}):
            production = default_installer_resource_root()
            rehearsal = default_installer_resource_root(rehearsal=True)

        self.assertEqual(
            production.resolve(),
            (program_data / "MemoryWuxian/installer-resources").resolve(),
        )
        self.assertEqual(
            rehearsal.resolve(),
            (program_data / "MemoryWuxianRehearsal/installer-resources").resolve(),
        )
        self.assertNotEqual(production, rehearsal)
        production_source = (ROOT / "scripts/install_windows_transaction.py").read_text(encoding="utf-8")
        rehearsal_source = (ROOT / "scripts/run_windows_installer_rehearsal.py").read_text(encoding="utf-8")
        self.assertIn("resource_root=default_installer_resource_root(),", production_source)
        self.assertIn("resource_root=default_installer_resource_root(rehearsal=True),", rehearsal_source)

    def test_product_composition_is_closed_and_has_seven_bounded_resources(self) -> None:
        adapter = ProductInstallAdapter(
            codex_cli=self.root / "codex.exe", resource_root=self.root / "resources"
        )
        mutations = adapter.build(self.manifest, self.token)
        self.assertEqual(
            [type(item).__name__ for item in mutations],
            [
                "ArchiveInitializationMutation",
                "FederationNodeInitializationMutation",
                "CollectorGenerationMutation",
                "ConfigurationMigrationMutation",
                "MaintenanceRegistrationMutation",
                "AutoUpdateRegistrationMutation",
                "DashboardShortcutMutation",
            ],
        )
        self.assertEqual(len({item.resource_id for item in mutations}), 7)
        self.assertEqual(
            [item.resource_id for item in mutations],
            [
                "archive-scaffold",
                "local-federation-node",
                "installed-capture-generation",
                "configuration-overlay",
                "maintenance-scheduler",
                "auto-update-scheduler",
                "dashboard-launcher",
            ],
        )
        self.assertEqual(mutations[2].owned_tasks, ("MemoryWuxianCodexSync",))
        self.assertEqual(mutations[4].owned_tasks, ("MemoryWuxianMaintenance",))
        self.assertEqual(mutations[5].owned_tasks, ("MemoryWuxianAutoUpdate",))
        self.assertEqual(mutations[6].shortcut_name, "Memory无限状态台.lnk")
        source = (ROOT / "scripts/install_windows_transaction.py").read_text(encoding="utf-8")
        self.assertNotIn("install_auto_update.py", source)
        self.assertNotIn("CurrentVersion\\\\Run", source)
        with self.assertRaisesRegex(RuntimeError, "exact full component set"):
            adapter.build(replace(self.manifest, requested_components=("collector",)), self.token)

    def test_rehearsal_namespace_changes_only_declared_external_resources(self) -> None:
        desktop = (self.root / "rehearsal-desktop").resolve()
        namespace = WindowsInstallResourceNamespace(
            collector_task_name="MemoryWuxianRehearsal-collector",
            collector_run_value="MemoryWuxianRehearsal-collector",
            maintenance_task_name="MemoryWuxianRehearsal-maintenance",
            auto_update_task_name="MemoryWuxianRehearsal-update",
            auto_update_run_value="MemoryWuxianRehearsal-update",
            dashboard_desktop=desktop,
            dashboard_shortcut_name="MemoryWuxianRehearsal.lnk",
        )
        mutations = ProductInstallAdapter(
            codex_cli=self.root / "codex.exe",
            resource_root=self.root / "resources-rehearsal",
            resources=namespace,
        ).build(self.manifest, self.token)
        collector, maintenance, updater, dashboard = (
            mutations[2], mutations[4], mutations[5], mutations[6]
        )
        self.assertEqual(collector.owned_tasks, (namespace.collector_task_name,))
        self.assertEqual(maintenance.owned_tasks, (namespace.maintenance_task_name,))
        self.assertEqual(updater.owned_tasks, (namespace.auto_update_task_name,))
        self.assertEqual(dashboard.desktop, desktop)
        self.assertEqual(dashboard.shortcut_name, namespace.dashboard_shortcut_name)
        source = (ROOT / "scripts/install_windows_transaction.py").read_text(encoding="utf-8")
        self.assertIn('"-ShortcutName", self.shortcut_name', source)

    def test_machine_resource_contract_matches_composition(self) -> None:
        contract = json.loads(
            (ROOT / "docs/install-transaction/resource-interface-contract.json").read_text(encoding="utf-8")
        )
        mutations = ProductInstallAdapter(
            codex_cli=self.root / "codex.exe",
            resource_root=self.root / "resources-contract",
        ).build(self.manifest, self.token)
        self.assertEqual(contract["transaction_order"], [item.resource_id for item in mutations])
        declared = {item["id"]: item for item in contract["transaction_resources"]}
        self.assertEqual(set(declared), {item.resource_id for item in mutations})
        for mutation in mutations:
            resource = declared[mutation.resource_id]
            self.assertEqual(resource["owner"].rsplit(":", 1)[-1], type(mutation).__name__)

    def test_collector_mutation_passes_exact_outer_journal_to_cli(self) -> None:
        task_name = "MemoryWuxianRehearsal-collector"
        run_value = "MemoryWuxianRehearsal-collector"
        mutation = CollectorGenerationMutation(
            self.manifest,
            self.token,
            codex_cli=self.root / "codex.exe",
            resource_root=self.root / "resources/transaction",
            task_name=task_name,
            run_value=run_value,
        )
        evidence = mutation.prepare(self.token)
        journal = Path(evidence["collector_journal"])
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("{}\n", encoding="utf-8")
        observed: list[str] = []

        def completed(command):
            observed.extend(command)
            return __import__("subprocess").CompletedProcess(command, 0, stdout=f"install-journal:{journal}\n", stderr="")

        with mock.patch.object(composition, "_run", side_effect=completed):
            result = mutation.apply(self.token)
        index = observed.index("--journal-path")
        self.assertEqual(Path(observed[index + 1]).resolve(), journal.resolve())
        self.assertEqual(observed[observed.index("--task-name") + 1], task_name)
        self.assertEqual(observed[observed.index("--run-value") + 1], run_value)
        self.assertEqual(
            Path(observed[observed.index("--active-root-pointer") + 1]).resolve(),
            self.pointer.resolve(),
        )
        self.assertIn("--skip-maintenance", observed)
        self.assertEqual(Path(result["collector_journal"]).resolve(), journal.resolve())

    def test_collector_projection_excludes_external_runtime_before_child_install(self) -> None:
        runtime_file = self.candidate / "runtime/windows/python/Lib/site-packages/pythonnet/runtime/deep.dll"
        runtime_file.parent.mkdir(parents=True)
        runtime_file.write_bytes(b"external-runtime")
        retained = self.candidate / "scripts/retained.py"
        retained.parent.mkdir(parents=True)
        retained.write_text("VALUE = 1\n", encoding="utf-8")
        mutation = CollectorGenerationMutation(
            self.manifest,
            self.token,
            codex_cli=self.root / "codex.exe",
            resource_root=self.root / "resources/projection",
        )

        evidence = mutation.prepare(self.token)
        projection = Path(evidence["skill_candidate"])
        self.assertEqual(evidence["excluded_top_level"], ["runtime"])
        self.assertFalse((projection / "runtime").exists())
        self.assertEqual((projection / "scripts/retained.py").read_bytes(), retained.read_bytes())
        self.assertEqual(composition._tree_sha256(projection), evidence["skill_candidate_sha256"])

        journal = Path(evidence["collector_journal"])
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("{}\n", encoding="utf-8")
        observed: list[str] = []

        def completed(command):
            observed.extend(command)
            return __import__("subprocess").CompletedProcess(
                command, 0, stdout=f"install-journal:{journal}\n", stderr=""
            )

        with mock.patch.object(composition, "_run", side_effect=completed):
            mutation.apply(self.token)
        candidate_index = observed.index("--candidate-root")
        self.assertEqual(Path(observed[candidate_index + 1]).resolve(), projection.resolve())
        self.assertNotEqual(projection.resolve(), self.candidate.resolve())

    def test_maintenance_task_has_independent_exact_rollback(self) -> None:
        mutation = MaintenanceRegistrationMutation(
            self.manifest,
            self.token,
            backup_root=self.root / "backups/maintenance",
        )
        previous = b"previous-maintenance-task"
        expected = b"expected-maintenance-task"
        state = {"xml": previous}

        def register(_name, payload, **_kwargs):
            state["xml"] = payload

        def uninstall(_name, **_kwargs):
            state["xml"] = None

        with (
            mock.patch.object(composition, "query_windows_task_xml", side_effect=lambda *_args, **_kwargs: state["xml"]),
            mock.patch.object(composition, "maintenance_windows_xml", return_value=expected),
            mock.patch.object(composition, "register_windows_task", side_effect=register),
            mock.patch.object(composition, "uninstall_windows_task", side_effect=uninstall),
            mock.patch.object(composition, "inspect_windows_task_xml", side_effect=lambda payload: {"payload": payload}),
            mock.patch.object(composition, "windows_task_xml_equivalent", side_effect=lambda actual, wanted: actual == wanted),
        ):
            evidence = mutation.prepare(self.token)
            self.assertEqual(Path(evidence["backup"]).read_bytes(), previous)
            mutation.apply(self.token)
            mutation.verify(self.token)
            self.assertEqual(state["xml"], expected)
            mutation.rollback(self.token)
            mutation.rollback_verify(self.token)
            self.assertEqual(state["xml"], previous)

    def test_dashboard_prepare_defers_real_launch_effect_to_s12(self) -> None:
        mutation = DashboardShortcutMutation(
            self.manifest, self.token, backup_root=self.root / "backups/shortcut"
        )
        desktop = self.root / "Desktop"
        desktop.mkdir()
        completed = __import__("subprocess").CompletedProcess(
            ["powershell"], 0, stdout=str(desktop) + "\n", stderr=""
        )
        with mock.patch.object(composition, "_run", return_value=completed):
            evidence = mutation.prepare(self.token)
        self.assertEqual(evidence["real_launch_effect_gate"], "S12-installed-runtime-effect-receipt")
        self.assertNotIn("real_launch_effect_passed", evidence)

    def test_dashboard_launcher_hash_drift_blocks_effect_verification(self) -> None:
        mutation = DashboardShortcutMutation(
            self.manifest, self.token, backup_root=self.root / "backups/shortcut-hash"
        )
        shortcut = self.root / "Desktop/Memory无限状态台.lnk"
        shortcut.parent.mkdir()
        shortcut.write_bytes(b"shortcut")
        launcher = self.target / "bin/memory-wuxian-dashboard-launcher.exe"
        launcher.parent.mkdir(exist_ok=True)
        launcher.write_bytes(b"wrong-launcher")
        icon = self.target / "assets/memory-wuxian.ico"
        icon.parent.mkdir()
        icon.write_bytes(b"icon")
        mutation.shortcut = shortcut
        mutation.expected_launcher_sha256 = hashlib.sha256(b"expected-launcher").hexdigest()
        mutation.expected_icon_sha256 = hashlib.sha256(b"icon").hexdigest()
        mutation.expected_launcher_config = {
            "schema_version": 1,
            "python_executable": str(self.manifest.runtime_bundle.python_executable),
            "archive_root": str(self.archive),
        }
        mutation.launcher_config.parent.mkdir(parents=True, exist_ok=True)
        mutation.launcher_config.write_text(json.dumps(mutation.expected_launcher_config), encoding="utf-8")
        observed = {
            "exists": True,
            "target": str(launcher),
            "working_directory": str(self.target),
            "arguments": "",
            "icon": str(icon) + ",0",
            "target_exists": True,
        }
        completed = __import__("subprocess").CompletedProcess(
            ["powershell"], 0, stdout=json.dumps(observed), stderr=""
        )
        with mock.patch.object(composition, "_run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "executable hash"):
                mutation.verify(self.token)


if __name__ == "__main__":
    unittest.main()
