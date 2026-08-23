import tomllib
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_documentation_version,
    assert_readme_tokens,
    assert_source_tokens,
    project_version,
)


ROOT = Path(__file__).resolve().parents[1]


class V220ReleaseContractTests(unittest.TestCase):
    def test_exact_release_identity_and_documentation_are_synchronized(self):
        version = project_version(ROOT)
        self.assertEqual(version, "2.20.0")
        cargo = tomllib.loads(
            (ROOT / "native-collector/Cargo.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(cargo["package"]["version"], version)
        assert_documentation_version(self, ROOT, version)
        assert_readme_tokens(
            self,
            ROOT,
            (
                "2.20.0",
                "windows_installer_transaction.py",
                "CollectorGenerationMutation",
                "commit-intent",
                "rollback-verified",
                "archive pointer",
            ),
        )

    def test_production_composition_declares_seven_bounded_mutations(self):
        assert_source_tokens(
            self,
            ROOT,
            "scripts/install_windows_transaction.py",
            present=(
                "CollectorGenerationMutation",
                "ConfigurationMigrationMutation",
                "ArchiveInitializationMutation",
                "FederationNodeInitializationMutation",
                "MaintenanceRegistrationMutation",
                "AutoUpdateRegistrationMutation",
                "DashboardShortcutMutation",
            ),
            absent=("ComposedInstallMutation",),
        )

    def test_manifest_and_state_machine_preserve_archive_authority(self):
        assert_source_tokens(
            self,
            ROOT,
            "scripts/windows_install_manifest.py",
            present=(
                '"archive"',
                '"auto-update"',
                '"collector"',
                '"config"',
                '"shortcut"',
            ),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/windows_installer_transaction.py",
            present=("commit-intent", "rollback-verified", "rollback-incomplete"),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/install_windows_transaction.py",
            present=(
                "archive_pointer",
                '"raw_archive_targeted": False',
                '"archive_pointer_targeted": False',
                '"archive_pointer_status": pointer_status',
            ),
            absent=("raw_records_path",),
        )


if __name__ == "__main__":
    unittest.main()
