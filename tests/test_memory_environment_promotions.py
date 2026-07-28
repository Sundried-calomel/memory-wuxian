import copy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment_promotions import PromotionStore, REQUIRED_VALIDATIONS


class PromotionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name) / "memory"
        self.archive.mkdir()
        self.store = PromotionStore(self.archive)

    def tearDown(self):
        self.temporary.cleanup()

    def record(self, state="discovered"):
        return {
            "schema_version": 1,
            "promotion_id": "promotion-orf1-shared-gate",
            "source_project_id": "orf1-library",
            "source_skill_id": "orf1-gate",
            "source_capability": "generic-long-run-gate",
            "classification": "extension",
            "proposed_global_owner": "global-long-run-gate",
            "interface_contract": {"input": "task contract"},
            "retained_project_adapter": {"owner": "orf1-gate"},
            "provenance": {"source_revision": "rev:" + "a" * 64},
            "validation_matrix": [
                {"name": "candidate-evidence", "status": "pass", "evidence": "review.md"}
            ],
            "review_state": state,
            "approval": {
                "required": True,
                "approved": False,
                "approved_at": None,
                "evidence": None,
            },
        }

    @staticmethod
    def passing_matrix():
        return [
            {"name": name, "status": "pass", "evidence": f"{name}.txt"}
            for name in sorted(REQUIRED_VALIDATIONS)
        ]

    def advance_to_validating(self):
        discovered = self.record()
        self.store.propose(discovered, apply=True)
        pending = copy.deepcopy(discovered)
        pending["review_state"] = "pending-review"
        self.store.transition(pending["promotion_id"], pending, apply=True)
        validating = copy.deepcopy(pending)
        validating["review_state"] = "validating"
        self.store.transition(validating["promotion_id"], validating, apply=True)
        return validating

    def test_preview_has_no_side_effects(self):
        result = self.store.propose(self.record())
        self.assertEqual(result["status"], "preview")
        self.assertFalse((self.archive / "environment").exists())

    def test_records_are_immutable_chained_events(self):
        validating = self.advance_to_validating()
        history = self.store.history(validating["promotion_id"])
        self.assertEqual([1, 2, 3], [item["event_sequence"] for item in history])
        self.assertIsNone(history[0]["previous_event_sha256"])
        self.assertEqual(
            history[0]["event_sha256"], history[1]["previous_event_sha256"]
        )
        self.assertEqual("validating", self.store.current(validating["promotion_id"])["review_state"])
        self.assertEqual("valid", self.store.validate()["status"])

    def test_cannot_skip_review_or_auto_approve(self):
        discovered = self.record()
        self.store.propose(discovered, apply=True)
        accepted = copy.deepcopy(discovered)
        accepted["review_state"] = "accepted"
        accepted["approval"]["approved"] = True
        accepted["approval"]["approved_at"] = "2026-07-28T12:00:00+00:00"
        accepted["approval"]["evidence"] = "user-review.md"
        accepted["validation_matrix"] = self.passing_matrix()
        with self.assertRaisesRegex(ValueError, "invalid review transition"):
            self.store.transition(accepted["promotion_id"], accepted)

    def test_promotable_requires_complete_passing_matrix(self):
        validating = self.advance_to_validating()
        promotable = copy.deepcopy(validating)
        promotable["review_state"] = "promotable"
        with self.assertRaisesRegex(ValueError, "validation missing"):
            self.store.transition(promotable["promotion_id"], promotable)
        promotable["validation_matrix"] = self.passing_matrix()
        result = self.store.transition(promotable["promotion_id"], promotable, apply=True)
        self.assertEqual(result["status"], "recorded")

    def test_acceptance_requires_explicit_evidence(self):
        validating = self.advance_to_validating()
        promotable = copy.deepcopy(validating)
        promotable["review_state"] = "promotable"
        promotable["validation_matrix"] = self.passing_matrix()
        self.store.transition(promotable["promotion_id"], promotable, apply=True)
        accepted = copy.deepcopy(promotable)
        accepted["review_state"] = "accepted"
        with self.assertRaisesRegex(ValueError, "explicit approval"):
            self.store.transition(accepted["promotion_id"], accepted)
        accepted["approval"].update(
            {
                "approved": True,
                "approved_at": "2026-07-28T12:00:00+00:00",
                "evidence": "explicit-user-approval.md",
            }
        )
        self.store.transition(accepted["promotion_id"], accepted, apply=True)
        self.assertEqual(
            "accepted", self.store.current(accepted["promotion_id"])["review_state"]
        )

    def test_source_identity_cannot_change(self):
        discovered = self.record()
        self.store.propose(discovered, apply=True)
        pending = copy.deepcopy(discovered)
        pending["review_state"] = "pending-review"
        pending["source_project_id"] = "different-project"
        with self.assertRaisesRegex(ValueError, "source_project_id cannot change"):
            self.store.transition(discovered["promotion_id"], pending)

    def test_terminal_states_cannot_be_reopened(self):
        discovered = self.record()
        discovered["classification"] = "project-only"
        self.store.propose(discovered, apply=True)
        terminal = copy.deepcopy(discovered)
        terminal["review_state"] = "project-only"
        self.store.transition(terminal["promotion_id"], terminal, apply=True)
        reopened = copy.deepcopy(terminal)
        reopened["review_state"] = "pending-review"
        with self.assertRaisesRegex(ValueError, "invalid review transition"):
            self.store.transition(reopened["promotion_id"], reopened)


if __name__ == "__main__":
    unittest.main()
