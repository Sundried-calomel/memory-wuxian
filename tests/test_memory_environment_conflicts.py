import copy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment_conflicts import EnvironmentConflictStore


class EnvironmentConflictStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name) / "memory"
        self.archive.mkdir()
        self.store = EnvironmentConflictStore(self.archive)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def request(**updates):
        value = {
            "artifact_id": "global-rule:codex-agents",
            "object_class": "global-rule",
            "base_revision_id": "rev:" + "1" * 64,
            "local_revision_id": "rev:" + "2" * 64,
            "remote_revision_id": "rev:" + "3" * 64,
            "base_content_sha256": "a" * 64,
            "local_content_sha256": "b" * 64,
            "remote_content_sha256": "c" * 64,
            "local_changed_blocks": ["local"],
            "remote_changed_blocks": ["remote"],
            "local_deleted": False,
            "remote_deleted": False,
            "unregistered_local_change": False,
            "project_identity_ambiguous": False,
            "platform_incompatible": False,
            "permission_expansion": False,
            "network_expansion": False,
        }
        value.update(updates)
        return value

    def test_one_sided_identical_and_disjoint_changes_auto_converge(self):
        identical = self.store.assess(
            self.request(remote_content_sha256="b" * 64)
        )
        self.assertEqual(identical["decision"], "no-change")
        remote = self.store.assess(
            self.request(local_content_sha256="a" * 64)
        )
        self.assertEqual(remote["decision"], "take-remote")
        local = self.store.assess(
            self.request(remote_content_sha256="a" * 64)
        )
        self.assertEqual(local["decision"], "keep-local")
        disjoint = self.store.assess(self.request())
        self.assertEqual(disjoint["decision"], "merge-managed-blocks")
        self.assertFalse((self.archive / "environment").exists())

    def test_same_block_and_skill_divergence_are_queued(self):
        same = self.store.assess(
            self.request(
                local_changed_blocks=["shared"],
                remote_changed_blocks=["shared"],
            ),
            apply=True,
        )
        self.assertEqual(same["status"], "queued")
        self.assertEqual(
            same["conflict"]["conflict_kind"], "same-managed-block"
        )
        skill = self.store.assess(
            self.request(
                artifact_id="global-skill:demo",
                object_class="global-skill",
                local_changed_blocks=[],
                remote_changed_blocks=[],
            )
        )
        self.assertEqual(skill["conflict"]["conflict_kind"], "divergent-skill-code")

    def test_security_and_identity_flags_always_require_review(self):
        for field, expected in (
            ("permission_expansion", "permission-expansion"),
            ("network_expansion", "network-expansion"),
            ("project_identity_ambiguous", "project-identity-ambiguity"),
            ("platform_incompatible", "platform-incompatible"),
            ("unregistered_local_change", "unregistered-local-change"),
        ):
            with self.subTest(field=field):
                result = self.store.assess(self.request(**{field: True}))
                self.assertEqual(result["conflict"]["conflict_kind"], expected)

    def test_delete_modify_and_missing_base_fail_closed(self):
        delete_modify = self.store.assess(
            self.request(
                local_content_sha256=None,
                local_deleted=True,
                local_changed_blocks=[],
            )
        )
        self.assertEqual(delete_modify["conflict"]["conflict_kind"], "delete-modify")
        missing_base = self.store.assess(
            self.request(
                base_revision_id=None,
                base_content_sha256=None,
                local_changed_blocks=[],
                remote_changed_blocks=[],
            )
        )
        self.assertEqual(missing_base["conflict"]["conflict_kind"], "base-mismatch")

    def test_resolution_is_explicit_and_append_only(self):
        queued = self.store.assess(
            self.request(
                local_changed_blocks=["shared"],
                remote_changed_blocks=["shared"],
            ),
            apply=True,
        )
        conflict_id = queued["conflict"]["conflict_id"]
        preview = self.store.resolve(
            conflict_id,
            action="manual-merge",
            evidence="review-42.md",
            reviewer="user",
        )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(len(self.store.history(conflict_id)), 1)
        resolved = self.store.resolve(
            conflict_id,
            action="manual-merge",
            evidence="review-42.md",
            reviewer="user",
            apply=True,
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(len(self.store.history(conflict_id)), 2)
        self.assertEqual(self.store.current(conflict_id)["status"], "resolved")
        with self.assertRaisesRegex(ValueError, "not pending"):
            self.store.resolve(
                conflict_id,
                action="take-local",
                evidence="later.md",
                reviewer="user",
            )

    def test_modification_time_is_not_an_input(self):
        request = self.request()
        request["mtime"] = 123
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.store.assess(request)


if __name__ == "__main__":
    unittest.main()
