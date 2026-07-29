import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment import EnvironmentRegistry, revision_id_for
from memory_environment_skills import (
    EnvironmentSkillInstaller,
    INSTALLER_LOCK_NAME,
    SkillInstallationError,
    skill_package_contract_bytes,
)
from platform_lock import exclusive_lock


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def authority_hashes(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] == "environment":
            continue
        if relative.parts[:1] == (".locks",):
            continue
        result[relative.as_posix()] = sha256(path.read_bytes())
    return result


class FailingInstaller(EnvironmentSkillInstaller):
    def _after_switch(self, target):
        raise RuntimeError("injected post-switch failure")


class CrashingInstaller(EnvironmentSkillInstaller):
    def _after_switch(self, target):
        raise SystemExit("simulated process termination")


class RollbackBoundaryCrashingInstaller(EnvironmentSkillInstaller):
    def _after_rollback_saved(self, target):
        raise SystemExit("simulated termination before transaction marker")


class FinishFailingInstaller(EnvironmentSkillInstaller):
    @staticmethod
    def _finish_transaction(path, status):
        raise OSError("simulated transaction-finalization failure")


class EnvironmentSkillInstallerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "memory"
        self.archive.mkdir()
        (self.archive / "state.json").write_text('{"format_version":1}\n')
        (self.archive / "raw").mkdir()
        (self.archive / "raw" / "messages.jsonl").write_text('{"id":"m1"}\n')
        (self.archive / "summaries").mkdir()
        (self.archive / "summaries" / "L1.json").write_text('{"id":"L1"}\n')
        (self.archive / "imports" / "codex" / "token-usage").mkdir(parents=True)
        (self.archive / "imports" / "codex" / "token-usage" / "x.json").write_text(
            '{"tokens":1}\n'
        )
        self.registry = EnvironmentRegistry(self.archive)
        self.global_root = self.base / "global"
        self.global_root.mkdir()
        self.project_root = self.base / "project"
        self.project_root.mkdir()
        self.created_at = "2026-07-28T09:00:00+00:00"
        self.artifact_id = "global-skill:demo-skill"
        self.revision = self.register_revision()

    def tearDown(self):
        self.temporary.cleanup()

    def artifact(self, artifact_id=None, scope="global", project_id=None):
        skill_id = (artifact_id or self.artifact_id).split(":", 1)[-1]
        return {
            "schema_version": 1,
            "artifact_id": artifact_id or self.artifact_id,
            "object_class": f"{scope}-skill",
            "scope": scope,
            "project_id": project_id,
            "display_name": skill_id,
            "created_at": self.created_at,
        }

    def revision_value(
        self,
        artifact_id=None,
        *,
        version=1,
        base_revision_id=None,
        content=None,
    ):
        artifact_id = artifact_id or self.artifact_id
        content = content or f"registered package metadata {artifact_id} v{version}\n"
        digest = sha256(content.encode())
        value = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact_id,
            "origin_node_id": "mac-test-node",
            "version": version,
            "base_revision_id": base_revision_id,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.10"},
            "provenance": {"source": "test"},
            "lifecycle_state": "staged",
            "created_at": self.created_at,
        }
        value["revision_id"] = revision_id_for(value)
        return value, content

    def project(self, enabled=True, active=True):
        return {
            "schema_version": 1,
            "project_id": "project-one",
            "display_name": "Project One",
            "local_root": str(self.project_root),
            "active": active,
            "rule_bindings": [],
            "skill_bindings": [
                {
                    "skill_id": "project-skill",
                    "enabled": enabled,
                    "pinned_version": None,
                }
            ],
        }

    def register_revision(
        self,
        *,
        artifact=None,
        version=1,
        base_revision_id=None,
        projects=None,
        contract_manifest=None,
    ):
        artifact = artifact or self.artifact()
        if contract_manifest is None:
            contract_manifest = self.manifest(
                "rev:" + "0" * 64,
                skill_id=artifact["artifact_id"].split(":", 1)[-1],
                scope=artifact["scope"],
                project_id=artifact["project_id"],
            )
        content = skill_package_contract_bytes(contract_manifest).decode("utf-8")
        revision, content = self.revision_value(
            artifact["artifact_id"],
            version=version,
            base_revision_id=base_revision_id,
            content=content,
        )
        self.registry.register(
            {
                "schema_version": 1,
                "artifacts": [
                    {"artifact": artifact, "revision": revision, "content": content}
                ],
                "projects": projects or [],
            },
            apply=True,
        )
        return revision

    def installer(self, cls=EnvironmentSkillInstaller, **updates):
        options = {
            "target_node_id": "mac-test-node",
            "platform": "windows" if os.name == "nt" else "macos",
            "runtime_versions": {"python": "3.12.4"},
            "global_skill_bindings": {
                "global-demo": {
                    "skill_id": "demo-skill",
                    "root": str(self.global_root),
                    "relative_path": ".codex/skills/demo-skill",
                    "enabled": True,
                }
            },
        }
        options.update(updates)
        return cls(self.registry, **options)

    def files(self, skill_id="demo-skill", marker="v1"):
        return {
            "SKILL.md": (
                f"---\nname: {skill_id}\n"
                f"description: Test Skill {marker}\n---\n\n# {marker}\n"
            ).encode(),
            "agents/openai.yaml": (
                "interface:\n"
                '  display_name: "Demo Skill"\n'
                '  short_description: "Test package"\n'
                f'  default_prompt: "Use ${skill_id} for this task."\n'
            ).encode(),
            "scripts/check.py": f"VALUE = {marker!r}\n".encode(),
        }

    def manifest(
        self,
        revision_id,
        *,
        skill_id="demo-skill",
        scope="global",
        project_id=None,
        files=None,
        platforms=None,
        runtime=None,
        network=None,
        persistent=None,
        version="1.0.0",
    ):
        files = files or self.files(skill_id)
        return {
            "schema_version": 1,
            "skill_id": skill_id,
            "version": version,
            "scope": scope,
            "project_id": project_id,
            "source_revision": revision_id,
            "files": [
                {
                    "path": path,
                    "size": len(payload),
                    "sha256": sha256(payload),
                    "executable": path.startswith("scripts/"),
                }
                for path, payload in sorted(files.items())
            ],
            "supported_platforms": platforms or ["macos", "windows"],
            "runtime_requirements": runtime or {"python": ">=3.10"},
            "network_access": network or {"enabled": False, "destinations": []},
            "persistent_components": persistent or [],
            "checks": [
                {"type": "utf8", "path": "SKILL.md"},
                {"type": "python-compile", "path": "scripts/check.py"},
            ],
            "rollback": {"strategy": "one-verified-version"},
        }

    def package(
        self,
        name,
        manifest,
        files=None,
        *,
        extra=None,
        symlink=None,
        duplicate=None,
    ):
        path = self.base / name
        files = files or {
            item["path"]: self.files(manifest["skill_id"])[item["path"]]
            for item in manifest["files"]
        }
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "skill-package-manifest.json",
                    json.dumps(manifest, sort_keys=True),
                )
                for relative, payload in files.items():
                    archive.writestr(relative, payload)
                if extra is not None:
                    archive.writestr(extra[0], extra[1])
                if symlink is not None:
                    info = zipfile.ZipInfo(symlink)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, "../outside")
                if duplicate is not None:
                    archive.writestr(duplicate[0], duplicate[1])
        return path

    def test_preview_apply_and_persistent_platform_lock(self):
        before = authority_hashes(self.archive)
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("demo.zip", manifest)
        installer = self.installer()
        preview = installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
        )
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(Path(preview["target_path"]).exists())
        self.assertFalse(installer.receipts_dir.exists())
        result = installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        self.assertEqual(result["status"], "installed")
        self.assertTrue((Path(result["target_path"]) / "SKILL.md").is_file())
        self.assertEqual(result["receipt"]["result"], "installed")
        self.assertEqual(authority_hashes(self.archive), before)
        self.assertTrue((self.archive / ".locks" / INSTALLER_LOCK_NAME).exists())
        with exclusive_lock(self.archive / ".locks" / INSTALLER_LOCK_NAME):
            pass

    def test_zip_slip_absolute_and_symlink_entries_are_rejected(self):
        manifest = self.manifest(self.revision["revision_id"])
        for bad_name in ("../escape", "/absolute", "C:/windows"):
            package = self.package(
                f"{sha256(bad_name.encode())}.zip",
                manifest,
                extra=(bad_name, b"x"),
            )
            with self.assertRaisesRegex(ValueError, "path|relative|traversal"):
                self.installer().install(
                    package_path=package,
                    artifact_id=self.artifact_id,
                    revision_id=self.revision["revision_id"],
                    target_binding="global-demo",
                )
        package = self.package(
            "symlink.zip", manifest, symlink="scripts/link.py"
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_undeclared_duplicate_and_case_collisions_are_rejected(self):
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("undeclared.zip", manifest, extra=("secret.txt", b"x"))
        with self.assertRaisesRegex(ValueError, "undeclared"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )
        package = self.package(
            "duplicate.zip", manifest, duplicate=("SKILL.md", b"other")
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )
        package = self.package(
            "case.zip", manifest, extra=("skill.md", b"other")
        )
        with self.assertRaisesRegex(ValueError, "case-insensitive"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_hash_and_skill_metadata_mismatch_are_rejected(self):
        manifest = self.manifest(self.revision["revision_id"])
        files = self.files()
        files["scripts/check.py"] = b"changed = True\n"
        package = self.package("hash.zip", manifest, files)
        with self.assertRaisesRegex(ValueError, "size mismatch|hash mismatch"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )
        bad_files = self.files()
        bad_files["SKILL.md"] = (
            "---\nname: another-skill\ndescription: Wrong\n---\n"
        ).encode()
        bad_manifest = self.manifest(
            self.revision["revision_id"], files=bad_files
        )
        package = self.package("metadata.zip", bad_manifest, bad_files)
        with self.assertRaisesRegex(ValueError, "frontmatter name"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_package_contract_must_match_registered_revision(self):
        files = self.files(marker="substituted")
        manifest = self.manifest(
            self.revision["revision_id"],
            files=files,
            version="9.9.9",
        )
        package = self.package("substituted.zip", manifest, files)
        with self.assertRaisesRegex(ValueError, "contract does not match"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_platform_runtime_and_unsafe_check_are_rejected(self):
        manifest = self.manifest(
            self.revision["revision_id"], platforms=["windows"]
        )
        package = self.package("platform.zip", manifest)
        with self.assertRaisesRegex(ValueError, "does not support platform"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )
        manifest = self.manifest(
            self.revision["revision_id"], runtime={"python": ">=9.0"}
        )
        package = self.package("runtime.zip", manifest)
        with self.assertRaisesRegex(ValueError, "not satisfied"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )
        manifest = self.manifest(self.revision["revision_id"])
        manifest["checks"] = [{"type": "shell", "path": "SKILL.md"}]
        package = self.package("shell.zip", manifest)
        with self.assertRaisesRegex(ValueError, "whitelist"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_permission_expansion_is_rejected_on_update(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("first.zip", first_manifest)
        self.installer().install(
            package_path=first_package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        files = self.files(marker="v2")
        contract = self.manifest(
            "rev:" + "0" * 64,
            files=files,
            version="2.0.0",
            network={"enabled": True, "destinations": ["example.com"]},
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        second_manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package(
            "expanded.zip", second_manifest, files
        )
        with self.assertRaisesRegex(ValueError, "expand network"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
            )

    def test_persistent_component_expansion_is_rejected(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("persistent-base.zip", first_manifest)
        self.installer().install(
            package_path=first_package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        files = self.files(marker="persistent-v2")
        contract = self.manifest(
            "rev:" + "0" * 64,
            files=files,
            version="2.0.0",
            persistent=[{"component_id": "agent", "type": "launch-agent"}],
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        second_manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package("persistent-expanded.zip", second_manifest, files)
        with self.assertRaisesRegex(ValueError, "expand persistent"):
            self.installer().install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
            )

    def test_project_binding_must_be_registered_active_enabled_and_matching(self):
        artifact_id = "project-skill:project-skill"
        artifact = self.artifact(
            artifact_id, scope="project", project_id="project-one"
        )
        revision = self.register_revision(
            artifact=artifact, projects=[self.project()]
        )
        files = self.files("project-skill")
        manifest = self.manifest(
            revision["revision_id"],
            skill_id="project-skill",
            scope="project",
            project_id="project-one",
            files=files,
        )
        package = self.package("project.zip", manifest, files)
        result = self.installer().install(
            package_path=package,
            artifact_id=artifact_id,
            revision_id=revision["revision_id"],
            target_binding="project:project-one:project-skill",
        )
        self.assertEqual(result["status"], "preview")
        with self.assertRaisesRegex(ValueError, "does not match project_id"):
            self.installer().install(
                package_path=package,
                artifact_id=artifact_id,
                revision_id=revision["revision_id"],
                target_binding="project:wrong:project-skill",
            )

    def test_unregistered_global_and_inactive_project_bindings_fail_closed(self):
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("unregistered-global.zip", manifest)
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.installer(global_skill_bindings={}).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

        artifact_id = "project-skill:project-skill"
        artifact = self.artifact(
            artifact_id, scope="project", project_id="project-one"
        )
        revision = self.register_revision(
            artifact=artifact, projects=[self.project(active=False)]
        )
        files = self.files("project-skill")
        project_manifest = self.manifest(
            revision["revision_id"],
            skill_id="project-skill",
            scope="project",
            project_id="project-one",
            files=files,
        )
        project_package = self.package(
            "inactive-project.zip", project_manifest, files
        )
        with self.assertRaisesRegex(ValueError, "active local project"):
            self.installer().install(
                package_path=project_package,
                artifact_id=artifact_id,
                revision_id=revision["revision_id"],
                target_binding="project:project-one:project-skill",
            )

    def test_pinned_version_is_enforced(self):
        manifest = self.manifest(self.revision["revision_id"], version="1.0.0")
        package = self.package("pinned.zip", manifest)
        bindings = {
            "global-demo": {
                "skill_id": "demo-skill",
                "root": str(self.global_root),
                "relative_path": ".codex/skills/demo-skill",
                "enabled": True,
                "pinned_version": "2.0.0",
            }
        }
        with self.assertRaisesRegex(ValueError, "pinned"):
            self.installer(global_skill_bindings=bindings).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
            )

    def test_post_switch_failure_restores_previous_and_writes_rollback_receipt(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("base.zip", first_manifest)
        target = Path(
            self.installer().install(
                package_path=first_package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
                apply=True,
            )["target_path"]
        )
        original = (target / "SKILL.md").read_bytes()
        files = self.files(marker="v2")
        contract = self.manifest(
            "rev:" + "0" * 64, files=files, version="2.0.0"
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package("failing.zip", manifest, files)
        with self.assertRaises(SkillInstallationError) as raised:
            self.installer(FailingInstaller).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
                apply=True,
            )
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.assertEqual(raised.exception.receipt["result"], "rolled-back")
        self.assertTrue(raised.exception.receipt["rollback"]["succeeded"])
        rollback = self.installer()._rollback_path("global-demo")
        self.assertTrue((rollback / "SKILL.md").is_file())

    def test_process_crash_is_recovered_from_persistent_transaction(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("crash-base.zip", first_manifest)
        target = Path(
            self.installer().install(
                package_path=first_package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
                apply=True,
            )["target_path"]
        )
        original = (target / "SKILL.md").read_bytes()
        files = self.files(marker="crash-v2")
        contract = self.manifest(
            "rev:" + "0" * 64, files=files, version="2.0.0"
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package("crash.zip", manifest, files)
        with self.assertRaises(SystemExit):
            self.installer(CrashingInstaller).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
                apply=True,
            )
        self.assertNotEqual((target / "SKILL.md").read_bytes(), original)
        pending = [
            path
            for path in self.installer().transactions_dir.glob("*.json")
            if json.loads(path.read_text())["status"] == "prepared"
        ]
        self.assertEqual(len(pending), 1)
        with exclusive_lock(self.installer().lock_path):
            recovered = self.installer().recover_transactions()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["result"], "rolled-back")
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.assertEqual(json.loads(pending[0].read_text())["status"], "rolled-back")

    def test_crash_after_rollback_save_precedes_transaction_and_target_mutation(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("boundary-base.zip", first_manifest)
        target = Path(
            self.installer().install(
                package_path=first_package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
                apply=True,
            )["target_path"]
        )
        original_hash = self.installer()._actual_tree_hash(target)
        files = self.files(marker="boundary-v2")
        contract = self.manifest(
            "rev:" + "0" * 64, files=files, version="2.0.0"
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package("boundary-v2.zip", manifest, files)
        with self.assertRaises(SystemExit):
            self.installer(RollbackBoundaryCrashingInstaller).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
                apply=True,
            )
        self.assertEqual(self.installer()._actual_tree_hash(target), original_hash)
        self.assertTrue(
            self.installer()._rollback_path("global-demo").is_dir()
        )
        self.assertFalse(
            any(
                json.loads(path.read_text())["status"] == "prepared"
                for path in self.installer().transactions_dir.glob("*.json")
            )
        )

    def test_crash_recovery_uses_persistent_rollback_when_displaced_is_missing(self):
        first_manifest = self.manifest(self.revision["revision_id"])
        first_package = self.package("rollback-base.zip", first_manifest)
        target = Path(
            self.installer().install(
                package_path=first_package,
                artifact_id=self.artifact_id,
                revision_id=self.revision["revision_id"],
                target_binding="global-demo",
                apply=True,
            )["target_path"]
        )
        original = (target / "SKILL.md").read_bytes()
        files = self.files(marker="rollback-v2")
        contract = self.manifest(
            "rev:" + "0" * 64, files=files, version="2.0.0"
        )
        second = self.register_revision(
            version=2,
            base_revision_id=self.revision["revision_id"],
            contract_manifest=contract,
        )
        manifest = {**contract, "source_revision": second["revision_id"]}
        package = self.package("rollback-crash.zip", manifest, files)
        with self.assertRaises(SystemExit):
            self.installer(CrashingInstaller).install(
                package_path=package,
                artifact_id=self.artifact_id,
                revision_id=second["revision_id"],
                target_binding="global-demo",
                apply=True,
            )
        pending = next(
            path
            for path in self.installer().transactions_dir.glob("*.json")
            if json.loads(path.read_text())["status"] == "prepared"
        )
        transaction = json.loads(pending.read_text())
        displaced = Path(transaction["displaced_path"])
        self.assertTrue(displaced.is_dir())
        for path in sorted(displaced.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        displaced.rmdir()
        rollback = Path(transaction["rollback_path"])
        self.assertTrue((rollback / "SKILL.md").is_file())
        with exclusive_lock(self.installer().lock_path):
            recovered = self.installer().recover_transactions()
        self.assertEqual(recovered[0]["result"], "rolled-back")
        self.assertEqual((target / "SKILL.md").read_bytes(), original)

    def test_installed_receipt_is_commit_point_for_transaction_recovery(self):
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("commit-point.zip", manifest)
        installer = self.installer(FinishFailingInstaller)
        result = installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["transaction_status"], "recovery-required")
        target = Path(result["target_path"])
        installed = (target / "SKILL.md").read_bytes()
        normal = self.installer()
        with exclusive_lock(normal.lock_path):
            recovered = normal.recover_transactions()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["receipt_id"], result["receipt"]["receipt_id"])
        self.assertEqual((target / "SKILL.md").read_bytes(), installed)
        transaction = next(normal.transactions_dir.glob("*.json"))
        self.assertEqual(json.loads(transaction.read_text())["status"], "installed")

    def test_no_change_creates_no_object_backup_or_receipt(self):
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("same.zip", manifest)
        installer = self.installer()
        installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        receipt_files = sorted(installer.receipts_dir.glob("*"))
        rollback_files = (
            sorted(installer.rollback_root.rglob("*"))
            if installer.rollback_root.exists()
            else []
        )
        registry_objects = sorted(self.registry.objects_dir.rglob("*"))
        result = installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )
        self.assertEqual(result["status"], "no-change")
        self.assertEqual(sorted(installer.receipts_dir.glob("*")), receipt_files)
        self.assertEqual(
            sorted(installer.rollback_root.rglob("*"))
            if installer.rollback_root.exists()
            else [],
            rollback_files,
        )
        self.assertEqual(sorted(self.registry.objects_dir.rglob("*")), registry_objects)

    def test_receipt_has_exact_schema_fields_and_windows_mode_is_predictable(self):
        manifest = self.manifest(self.revision["revision_id"])
        package = self.package("receipt.zip", manifest)
        installer = self.installer(
            platform="windows", runtime_versions={"python": "3.12.4"}
        )
        receipt = installer.install(
            package_path=package,
            artifact_id=self.artifact_id,
            revision_id=self.revision["revision_id"],
            target_binding="global-demo",
            apply=True,
        )["receipt"]
        self.assertEqual(
            set(receipt),
            {
                "schema_version", "receipt_id", "artifact_id", "revision_id",
                "content_sha256", "target_node_id", "target_binding",
                "previous_installed_sha256", "final_installed_sha256",
                "rehearsal", "result", "rollback", "created_at",
            },
        )
        installer._validate_receipt(receipt)
        script = Path(receipt["target_binding"] == "global-demo" and self.global_root)
        script = script / ".codex" / "skills" / "demo-skill" / "scripts" / "check.py"
        self.assertTrue(script.is_file())


if __name__ == "__main__":
    unittest.main()
