import sys
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_documentation_version,
    assert_minimum_project_version,
    assert_readme_tokens,
    assert_source_tokens,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import build_parser
from memory_cli_contract import command_spec
from memory_project_attachments import (
    CHUNK_BYTES,
    MAX_FILE_BYTES,
    MAX_GENERATION_BYTES,
    STREAM_ID,
)


class V215ReleaseContractTests(unittest.TestCase):
    def test_version_and_public_contract_are_synchronized(self):
        version = assert_minimum_project_version(self, ROOT, (2, 15, 0))
        assert_documentation_version(self, ROOT, version)
        assert_readme_tokens(
            self,
            ROOT,
            (
                "project-attachment-v1",
                "project-attachment-sync",
                "4 MiB",
                "256 MiB",
                "1 GiB",
            ),
        )

    def test_attachment_limits_and_commands_match_contract(self):
        self.assertEqual(STREAM_ID, "project-attachment-v1")
        self.assertEqual(CHUNK_BYTES, 4 * 1024 * 1024)
        self.assertEqual(MAX_FILE_BYTES, 256 * 1024 * 1024)
        self.assertEqual(MAX_GENERATION_BYTES, 1024 * 1024 * 1024)
        choices = set()
        for action in build_parser()._actions:
            if hasattr(action, "choices") and action.choices:
                choices.update(action.choices)
        for command in (
            "project-attachment-build",
            "project-attachment-owner-register",
            "project-attachment-owner-refresh",
            "project-attachment-owner-status",
            "project-attachment-status",
            "project-attachment-sync",
            "project-attachment-reconstruct",
        ):
            self.assertIn(command, choices)

    def test_native_envelope_declares_stream_bound_attachment_kinds(self):
        assert_source_tokens(
            self,
            ROOT,
            "native-collector/src/bin/memory-wuxian-envelope.rs",
            present=(
                '"project-attachment-v1-bundle"',
                '"project-attachment-v1-ack"',
                "project_attachment_kind_round_trips_and_is_stream_bound",
            ),
        )

    def test_reconstruction_completion_is_receipt_bound(self):
        source = (ROOT / "scripts" / "memory_project_attachments.py").read_text(encoding="utf-8")
        receipt_write = source.index("atomic_write_json(receipt_path, receipt)")
        incomplete_return = source.index('return {"status": "incomplete"')
        conflict_return = source.index('return {"status": "conflict"')
        self.assertLess(incomplete_return, receipt_write)
        self.assertLess(conflict_return, receipt_write)
        self.assertIn("verified_reconstructions", source)

    def test_attachment_sync_uses_the_federation_coordination_lock(self):
        self.assertEqual(
            "federation", command_spec("project-attachment-sync").lock_policy
        )
        for command in (
            "project-attachment-build",
            "project-attachment-owner-register",
            "project-attachment-owner-refresh",
            "project-attachment-reconstruct",
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    "project-attachment-command", command_spec(command).lock_policy
                )

    def test_dashboard_names_each_attachment_lifecycle_stage(self):
        assert_source_tokens(
            self,
            ROOT,
            "dashboard/index.html",
            present=(
                "local_manifest_creation",
                "encrypted_publication",
                "peer_acknowledgement",
                "verified_reconstruction",
                "本地清单",
                "Encrypted publication",
                "検証済み復元",
            ),
        )


if __name__ == "__main__":
    unittest.main()
