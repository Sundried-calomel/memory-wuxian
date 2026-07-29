import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_federation import FederationManager
from memory_governance_ai import GovernanceAIQueue
from memory_environment import revision_id_for
from memory_environment_evolution import ProductEvolutionStore


class GovernanceAIQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "memory"
        self.store = MemoryStore(self.root, load_simple_yaml(ROOT / "config.yaml"))
        self.store.init()
        FederationManager(self.store).init_node(
            "Test node", requested_node_id="node-local"
        )
        self.queue = GovernanceAIQueue(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def item(
        number: int,
        *,
        kind: str = "lesson-extraction",
        owner: str = "owner-a",
        product: str = "product-a",
        priority: str = "normal",
        created_at: str | None = None,
    ):
        content = json.dumps({"event": number}, sort_keys=True)
        return {
            "schema_version": 1,
            "item_id": f"item-{number:03d}",
            "task_kind": kind,
            "origin_node_id": "node-local",
            "owner_id": owner,
            "product_ids": [product],
            "source_revision": f"revision-{number}",
            "priority": priority,
            "urgent_reason": "active data-loss risk" if priority == "urgent" else None,
            "evidence": [
                {
                    "evidence_id": f"evidence-{number}",
                    "kind": "receipt",
                    "reference": f"receipt-{number}.json",
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "content": content,
                }
            ],
            "created_at": created_at
            or datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def result(batch):
        refs = [
            entry["evidence_id"]
            for item in batch["items"]
            for entry in item["evidence"]
        ]
        return {
            "schema_version": 1,
            "batch_id": batch["batch_id"],
            "task_kind": batch["task_kind"],
            "source_item_ids": batch["item_ids"],
            "facts": [{"statement": "Observed change", "evidence_refs": refs}],
            "interpretations": [],
            "recommendations": [],
            "classifications": [],
            "product_evolution_records": [],
            "governance_proposals": [],
            "no_change": False,
            "human_review_required": True,
        }

    def configure(self, **changes):
        return self.queue.configure({"enabled": True, **changes}, apply=True)

    def enqueue(self, *items):
        for item in items:
            self.queue.enqueue(item, apply=True)

    def test_disabled_check_never_invokes_ai(self):
        self.enqueue(self.item(1, priority="urgent"))
        calls = []
        result = self.queue.tick(
            run_ai=True,
            worker=lambda batch: calls.append(batch) or self.result(batch),
        )
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(calls, [])

    def test_same_product_micro_batch_runs_at_three_items(self):
        self.configure()
        self.enqueue(self.item(1), self.item(2), self.item(3))
        due = self.queue.due_batches()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["due_reason"], "count")
        self.assertEqual(due[0]["item_ids"], ["item-001", "item-002", "item-003"])
        result = self.queue.tick(run_ai=True, worker=self.result)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["ai_invoked"])
        self.assertEqual(self.queue.status()["counts"]["pending"], 0)
        self.assertEqual(self.queue.status()["counts"]["draft_results"], 1)

    def test_unrelated_products_are_not_combined(self):
        self.configure()
        self.enqueue(
            self.item(1, product="product-a"),
            self.item(2, product="product-a"),
            self.item(3, product="product-b"),
        )
        self.assertEqual(self.queue.due_batches(), [])

    def test_age_and_urgent_triggers_do_not_wait_for_count(self):
        self.configure()
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        self.enqueue(self.item(1, created_at=old))
        due = self.queue.due_batches()
        self.assertEqual(due[0]["due_reason"], "age")
        with tempfile.TemporaryDirectory():
            pass
        other_root = Path(self.temporary.name) / "second"
        second_store = MemoryStore(other_root, load_simple_yaml(ROOT / "config.yaml"))
        second_store.init()
        FederationManager(second_store).init_node(
            "Second", requested_node_id="node-second"
        )
        second = GovernanceAIQueue(second_store)
        second.configure({"enabled": True}, apply=True)
        urgent = self.item(2, priority="urgent")
        urgent["origin_node_id"] = "node-second"
        second.enqueue(urgent, apply=True)
        self.assertEqual(second.due_batches()[0]["due_reason"], "urgent")

    def test_global_review_runs_only_on_configured_coordinator(self):
        self.configure(coordinator_node_id="node-other")
        self.enqueue(
            self.item(1, kind="governance-classification", priority="urgent")
        )
        self.assertEqual(self.queue.due_batches(), [])
        self.queue.configure({"coordinator_node_id": "node-local"}, apply=True)
        self.assertEqual(len(self.queue.due_batches()), 1)

    def test_preview_configuration_and_enqueue_are_side_effect_free(self):
        policy = self.queue.configure({"enabled": True})
        self.assertEqual(policy["status"], "preview")
        self.assertFalse(self.queue.policy()["enabled"])
        preview = self.queue.enqueue(self.item(1))
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.queue.pending_items(), [])

    def test_tampered_evidence_and_urgent_without_reason_fail_closed(self):
        changed = self.item(1)
        changed["evidence"][0]["content"] = "tampered"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.queue.enqueue(changed)
        urgent = self.item(2, priority="urgent")
        urgent["urgent_reason"] = None
        with self.assertRaisesRegex(ValueError, "urgent_reason"):
            self.queue.enqueue(urgent)

    def test_failed_batch_retries_then_isolated_as_failed(self):
        self.configure(maximum_failed_retries=2)
        self.enqueue(self.item(1, priority="urgent"))

        def fail(_batch):
            raise RuntimeError("synthetic failure")

        first = self.queue.tick(run_ai=True, worker=fail)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(self.queue.status()["counts"]["pending"], 1)
        second = self.queue.tick(run_ai=True, worker=fail)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(self.queue.status()["counts"]["pending"], 0)
        self.assertEqual(self.queue.status()["counts"]["failed_items"], 1)

    def test_result_cannot_claim_automatic_acceptance(self):
        self.configure()
        self.enqueue(self.item(1, priority="urgent"))

        def invalid(batch):
            value = self.result(batch)
            value["human_review_required"] = False
            return value

        result = self.queue.tick(run_ai=True, worker=invalid)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.queue.status()["counts"]["draft_results"], 0)

    def test_discovery_creates_tasks_from_registered_revision_and_is_idempotent(self):
        content = "Registered owner\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        artifact = {
            "schema_version": 1,
            "artifact_id": "global-skill:demo",
            "object_class": "global-skill",
            "scope": "global",
            "project_id": None,
            "display_name": "Demo",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact["artifact_id"],
            "origin_node_id": "node-local",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos"],
            "runtime_requirements": {"python": ">=3.10"},
            "provenance": {"source": "test"},
            "lifecycle_state": "staged",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        revision["revision_id"] = revision_id_for(revision)
        manifest = {
            "schema_version": 1,
            "artifacts": [
                {"artifact": artifact, "revision": revision, "content": content}
            ],
            "projects": [],
        }
        self.queue.registry.register(manifest, apply=True)
        first = self.queue.discover(apply=True)
        second = self.queue.discover(apply=True)
        self.assertEqual(first["result_counts"]["queued"], 1)
        self.assertEqual(second["result_counts"]["no-change"], 1)
        self.assertEqual(self.queue.pending_items()[0]["task_kind"], "evolution-synthesis")

    def test_discovery_creates_lesson_task_from_product_evolution(self):
        record = {
            "schema_version": 1,
            "record_id": "evolution-one",
            "origin_node_id": "node-local",
            "product_id": "demo-product",
            "owner_id": "demo-owner",
            "source_revision": "revision-one",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        ProductEvolutionStore(self.store).record(record, apply=True)
        result = self.queue.discover(apply=True)
        self.assertEqual(result["result_counts"]["queued"], 1)
        self.assertEqual(self.queue.pending_items()[0]["task_kind"], "lesson-extraction")


if __name__ == "__main__":
    unittest.main()
