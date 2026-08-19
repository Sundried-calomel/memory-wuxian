import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_atoms import _source_sha256  # noqa: E402
from memory_cli import MemoryStore  # noqa: E402
from memory_summary_v2 import build_level_1_source, project  # noqa: E402
from platform_transaction import atomic_write_canonical_json  # noqa: E402
from summary_v2_backfill import (  # noqa: E402
    EXECUTION_CONTRACT_FORMAT,
    MAP_RESCUE_REVISION,
    PARENT_RESCUE_REVISION,
    SummaryV2Error,
    _begin_rescue_attempt,
    _exclusive_runner_lock,
    _execution_fingerprint,
    _chunk_job,
    _load_rescue_attempt_state,
    _refresh_plan,
    _rescue_quarantine_is_eligible,
    _rescue_artifact_root,
    _rescue_state_path,
    _run_rescue_map_chunk,
    _select_single_attempt_candidates,
    _write_rescue_state,
    build_plan,
    run_batch,
    run_parent_rescue,
)


def summary_markdown(summary_id, level, conversation_id, source_sha, message_ids, children=()):
    source_messages = "\n".join(f'  - "{value}"' for value in message_ids)
    source_summaries = "\n".join(f'  - "{value}"' for value in children)
    child_block = f"source_summaries: \n{source_summaries}\n" if children else ""
    message_block = f"source_message_ids: \n{source_messages}\n" if message_ids else ""
    return (
        "---\n"
        f"summary_id: {summary_id}\n"
        f"summary_level: {level}\n"
        f"conversation_id: \"{conversation_id}\"\n"
        "created_at: \"2026-08-13T00:00:00+00:00\"\n"
        f"source_sha256: \"{source_sha}\"\n"
        f"{message_block}{child_block}"
        "format_version: 1\n"
        "---\n\n"
        f"# Level-{level} Summary {summary_id}\n\n"
        "## Topics\n\n- topic\n\n"
        "## Established Conclusions\n\n- conclusion\n\n"
        "## Open Questions\n\n- None recorded.\n\n"
        "## Concepts\n\n- concept\n\n"
        "## Policy Events\n\n- None recorded.\n\n"
        "## Source References\n"
    )


class SummaryV2BackfillTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "archive"
        self.output = self.base / "summary-v2"
        self.store = MemoryStore(self.archive, {})
        self.store.init()
        self.conversation = "codex:fixture-conversation"
        for index in range(1, 5):
            self.store.append_message(
                speaker="user" if index % 2 else "assistant",
                text=f"消息 {index} / 日本語 / ￥ / emoji 😀",
                timestamp=f"2026-08-13T00:00:0{index}+00:00",
                conversation_id=self.conversation,
                message_id=f"fixture-{index}",
                reply_to=None,
                allow_secrets=False,
                complete_round=index % 2 == 0,
            )
        raw = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in self.store.read_all_raw()
        ]
        by_id = {item["message_id"]: item for item in raw}
        self.first_ids = ["fixture-1", "fixture-2"]
        self.second_ids = ["fixture-3", "fixture-4"]
        level1 = self.archive / "summaries" / "level-1"
        level1.mkdir(parents=True, exist_ok=True)
        (level1 / "L1-000001.md").write_text(
            summary_markdown(
                "L1-000001",
                1,
                self.conversation,
                _source_sha256([by_id[value] for value in self.first_ids]),
                self.first_ids,
            ),
            encoding="utf-8",
        )
        (level1 / "L1-000002.md").write_text(
            summary_markdown(
                "L1-000002",
                1,
                self.conversation,
                _source_sha256([by_id[value] for value in self.second_ids]),
                self.second_ids,
            ),
            encoding="utf-8",
        )
        level2 = self.archive / "summaries" / "level-2"
        level2.mkdir(parents=True, exist_ok=True)
        (level2 / "L2-000001.md").write_text(
            summary_markdown(
                "L2-000001",
                2,
                self.conversation,
                "0" * 64,
                [],
                ["L1-000001", "L1-000002"],
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_reconstructs_verified_l1_and_waits_for_parent(self):
        before = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        calls = 0
        original = MemoryStore.read_all_raw

        def counted(store):
            nonlocal calls
            calls += 1
            return original(store)

        with patch.object(MemoryStore, "read_all_raw", counted):
            plan = build_plan(self.archive, self.output)
        self.assertEqual(1, calls)
        self.assertEqual(4, plan["raw_message_count"])
        self.assertEqual(3, plan["summary_v1_count"])
        self.assertEqual(2, plan["counts"]["level_1_ready"])
        self.assertEqual(1, plan["counts"]["level_2_waiting_for_children"])
        self.assertFalse(plan["quarantine"])
        self.assertTrue((self.output / "backfill" / "jobs" / "level-1" / "L1-000001.json").is_file())
        after = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        repeated = build_plan(self.archive, self.output)
        self.assertEqual(plan["counts"], repeated["counts"])

    def test_conflicting_existing_sidecars_quarantine_only_their_summary(self):
        plan = build_plan(self.archive, self.output)
        task = next(item for item in plan["tasks"] if item["summary_id"] == "L1-000001")
        job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
        source = build_level_1_source(job)

        def candidate(text):
            refs = list(source["source_refs"])
            return {
                "format_version": 2,
                "job_id": source["job_id"],
                "summary_level": 1,
                "source_sha256": source["source_sha256"],
                "overview": [{"local_id": "o1", "text": text, "source_refs": refs}],
                "scenes": [{"local_id": "s1", "title": "多语言记录", "summary": text, "source_refs": refs}],
                "atoms": [
                    {
                        "local_id": "a1",
                        "atom_type": "work_fact",
                        "statement": text,
                        "epistemic_status": "explicit_fact",
                        "scope": "fixture",
                        "source_refs": refs,
                    }
                ],
                "relations": [],
                "retrieval_anchors": [],
                "omissions": [],
            }

        roots = [self.base / "candidate-a", self.base / "candidate-b"]
        for root, text in zip(roots, ["候选甲", "候选乙"]):
            sidecar = project(source, candidate(text))
            target = root / sidecar["summary_v2_id"] / "summary.json"
            target.parent.mkdir(parents=True)
            atomic_write_canonical_json(target, sidecar)
        conflicted = build_plan(self.archive, self.base / "fresh-output", roots)
        by_id = {task["summary_id"]: task for task in conflicted["tasks"]}
        self.assertEqual("quarantined", by_id["L1-000001"]["status"])
        self.assertEqual("ready", by_id["L1-000002"]["status"])
        reasons = {item["summary_id"]: item["reason"] for item in conflicted["quarantine"]}
        self.assertEqual("conflicting-existing-sidecars", reasons["L1-000001"])
        refreshed_without_external_roots = _refresh_plan(conflicted, [])
        reasons = {
            item["summary_id"]: item["reason"]
            for item in refreshed_without_external_roots["quarantine"]
        }
        self.assertEqual("conflicting-existing-sidecars", reasons["L1-000001"])

    def test_source_hash_drift_is_quarantined(self):
        path = self.archive / "summaries" / "level-1" / "L1-000001.md"
        text = path.read_text(encoding="utf-8").replace(
            "source_sha256: \"", "source_sha256: \"f", 1
        )
        path.write_text(text, encoding="utf-8")
        plan = build_plan(self.archive, self.output)
        quarantined = {item["summary_id"]: item["reason"] for item in plan["quarantine"]}
        self.assertIn("L1-000001", quarantined)
        self.assertIn("source-validation", quarantined["L1-000001"])

    def test_output_inside_archive_is_rejected(self):
        with self.assertRaisesRegex(SummaryV2Error, "outside the archive"):
            build_plan(self.archive, self.archive / "derived")

    def test_unreadable_sidecar_fails_closed_without_writing_plan(self):
        sidecar = self.output / "existing" / "summary.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("{}", encoding="utf-8")
        with patch(
            "summary_v2_backfill.load_sidecar",
            side_effect=PermissionError("denied"),
        ):
            with self.assertRaisesRegex(SummaryV2Error, "cannot read or validate"):
                build_plan(self.archive, self.output)
        self.assertFalse((self.output / "backfill" / "plan.json").exists())

    def test_execution_identity_drift_stops_before_plan_mutation(self):
        contract = _execution_fingerprint()
        contract["format"] = EXECUTION_CONTRACT_FORMAT
        contract["windows_user"] = contract["windows_user"] + "-different"
        path = self.output / "backfill" / "execution-contract.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(contract), encoding="utf-8")
        config = self.base / "config.yaml"
        config.write_text("ai_summary: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(SummaryV2Error, "refusing runner fallback"):
            run_batch(self.archive, self.output, config, maximum_jobs=1)
        self.assertFalse((self.output / "backfill" / "plan.json").exists())

    def test_rescue_chunks_are_an_exact_ordered_partition(self):
        build_plan(self.archive, self.output)
        task = next(item for item in json.loads(
            (self.output / "backfill" / "plan.json").read_text(encoding="utf-8")
        )["tasks"] if item.get("job"))
        job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
        chunks = _chunk_job(job, target_bytes=1)
        recovered = [
            message_id
            for chunk in chunks
            for message_id in chunk["source_message_ids"]
        ]
        self.assertEqual(job["source_message_ids"], recovered)
        self.assertEqual(len(recovered), len(set(recovered)))

    def test_runner_lock_rejects_a_second_writer_and_cleans_up(self):
        lock_path = self.output / "backfill" / ".runner-lock"
        with _exclusive_runner_lock(self.output, "first"):
            self.assertTrue(lock_path.is_dir())
            with self.assertRaisesRegex(SummaryV2Error, "another summary-v2 runner"):
                with _exclusive_runner_lock(self.output, "second"):
                    self.fail("second runner unexpectedly acquired the lock")
        self.assertFalse(lock_path.exists())

    def test_rescue_state_merge_preserves_maps_from_disk(self):
        path = self.output / "backfill" / "rescue" / "state.json"
        base = {
            "revision": "fixture-v1",
            "summary_id": "L1-fixture",
            "maps": {"map-001": {"source_sha256": "one"}},
            "reductions": {"stage-001-map-001": {"source_sha256": "reduce-one"}},
        }
        _write_rescue_state(path, base)
        stale = {
            "revision": "fixture-v1",
            "summary_id": "L1-fixture",
            "maps": {"map-002": {"source_sha256": "two"}},
            "reductions": {"stage-001-map-002": {"source_sha256": "reduce-two"}},
        }
        _write_rescue_state(path, stale)
        self.assertEqual({"map-001", "map-002"}, set(stale["maps"]))
        self.assertEqual(
            {"stage-001-map-001", "stage-001-map-002"},
            set(stale["reductions"]),
        )

    def test_failed_rescue_revision_schedules_zero_followup_work(self):
        before = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        task = {"summary_id": "L1-fixture"}
        for family, revision in (
            ("map", MAP_RESCUE_REVISION),
            ("parent", PARENT_RESCUE_REVISION),
        ):
            path = _rescue_state_path(
                self.output,
                family,
                revision,
                task["summary_id"],
            )
            state = _load_rescue_attempt_state(path, revision, task["summary_id"])
            _begin_rescue_attempt(path, state)
            state["attempt_status"] = "failed"
            state["last_error"] = "fixture validation failure"
            _write_rescue_state(path, state)
            with patch("summary_v2_backfill.run_source") as model_call:
                selected, deferred = _select_single_attempt_candidates(
                    [task],
                    self.output,
                    family,
                    revision,
                    1,
                )
            self.assertEqual([], selected)
            self.assertEqual([task["summary_id"]], deferred)
            model_call.assert_not_called()
        after = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_in_progress_rescue_can_resume_but_new_revision_is_independent(self):
        task = {"summary_id": "L1-resume"}
        path = _rescue_state_path(
            self.output,
            "map",
            MAP_RESCUE_REVISION,
            task["summary_id"],
        )
        state = _load_rescue_attempt_state(
            path,
            MAP_RESCUE_REVISION,
            task["summary_id"],
        )
        _begin_rescue_attempt(path, state)
        state["maps"]["map-001"] = {"source_sha256": "partial"}
        _write_rescue_state(path, state)
        selected, deferred = _select_single_attempt_candidates(
            [task],
            self.output,
            "map",
            MAP_RESCUE_REVISION,
            1,
        )
        self.assertEqual([task], selected)
        self.assertEqual([], deferred)

        next_revision = MAP_RESCUE_REVISION + ".next"
        selected, deferred = _select_single_attempt_candidates(
            [task],
            self.output,
            "map",
            next_revision,
            1,
        )
        self.assertEqual([task], selected)
        self.assertEqual([], deferred)
        self.assertFalse(
            _rescue_state_path(
                self.output,
                "map",
                next_revision,
                task["summary_id"],
            ).exists()
        )

    def test_rejected_candidate_is_evidence_only_and_artifacts_are_revision_scoped(self):
        source = {"job_id": "fixture"}
        rejected = self.output / "old-rejected.json"
        rejected.parent.mkdir(parents=True, exist_ok=True)
        rejected.write_text('{"stale":true}', encoding="utf-8")
        with patch("summary_v2_backfill.run_source", return_value={"status": "created"}) as call:
            _run_rescue_map_chunk(
                source,
                self.output / "maps",
                self.archive,
                self.base / "config.yaml",
                rejected,
            )
        self.assertNotIn("candidate", call.call_args.kwargs)
        self.assertEqual(rejected, call.call_args.kwargs["rejected_candidate_path"])
        old_root = _rescue_artifact_root(
            self.output, "map", MAP_RESCUE_REVISION, "L1-fixture"
        )
        new_root = _rescue_artifact_root(
            self.output, "map", MAP_RESCUE_REVISION + ".next", "L1-fixture"
        )
        self.assertNotEqual(old_root, new_root)
        self.assertFalse(str(new_root).startswith(str(old_root)))

    def test_rescue_selection_reports_terminal_nodes_beyond_batch_limit(self):
        tasks = [
            {"summary_id": "L1-ready"},
            {"summary_id": "L1-terminal"},
        ]
        path = _rescue_state_path(
            self.output,
            "map",
            MAP_RESCUE_REVISION,
            "L1-terminal",
        )
        state = _load_rescue_attempt_state(path, MAP_RESCUE_REVISION, "L1-terminal")
        _begin_rescue_attempt(path, state)
        state["attempt_status"] = "failed"
        _write_rescue_state(path, state)

        selected, deferred = _select_single_attempt_candidates(
            tasks,
            self.output,
            "map",
            MAP_RESCUE_REVISION,
            1,
        )
        self.assertEqual([tasks[0]], selected)
        self.assertEqual(["L1-terminal"], deferred)

    def test_old_normal_failure_routes_to_new_rescue_campaign_once(self):
        plan = build_plan(self.archive, self.output)
        failure = self.output / "backfill" / "failures" / "L1-000001.json"
        failure.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_canonical_json(
            failure,
            {
                "summary_id": "L1-000001",
                "runner_revision": "older-normal-revision",
                "attempts": 1,
                "attempt_status": "content-failed-terminal",
                "last_error": "fixture content failure",
            },
        )
        refreshed = _refresh_plan(plan, [])
        task = next(item for item in refreshed["tasks"] if item["summary_id"] == "L1-000001")
        self.assertEqual("quarantined", task["status"])
        self.assertEqual("pending", task["campaign_status"])
        self.assertFalse(task["eligible"])

    def test_infra_blocked_state_is_terminal_without_consuming_content_retry(self):
        task = {"summary_id": "L1-infra"}
        path = _rescue_state_path(
            self.output, "map", MAP_RESCUE_REVISION, task["summary_id"]
        )
        state = _load_rescue_attempt_state(path, MAP_RESCUE_REVISION, task["summary_id"])
        _begin_rescue_attempt(path, state)
        state["attempt_status"] = "infra-blocked"
        state["attempts"] = 0
        _write_rescue_state(path, state)
        selected, deferred = _select_single_attempt_candidates(
            [task], self.output, "map", MAP_RESCUE_REVISION, 1
        )
        self.assertEqual([], selected)
        self.assertEqual(["L1-infra"], deferred)
        self.assertEqual(0, state["attempts"])

        next_revision = MAP_RESCUE_REVISION + ".schema-fixed"
        selected, deferred = _select_single_attempt_candidates(
            [task], self.output, "map", next_revision, 1
        )
        self.assertEqual([task], selected)
        self.assertEqual([], deferred)
        self.assertFalse(
            _rescue_state_path(
                self.output, "map", next_revision, task["summary_id"]
            ).exists()
        )

    def test_new_revision_candidate_pool_reincludes_infrastructure_failures(self):
        self.assertTrue(_rescue_quarantine_is_eligible("infra-blocked"))
        self.assertTrue(_rescue_quarantine_is_eligible("model-failure-limit"))
        self.assertFalse(_rescue_quarantine_is_eligible("conflicting-existing-sidecars"))
        self.assertFalse(_rescue_quarantine_is_eligible(None))

    def test_parent_rescue_derives_reasons_before_candidate_selection(self):
        plan = build_plan(self.archive, self.output)
        plan["tasks"] = [
            {"summary_id": "L2-content", "status": "quarantined", "level": 2},
            {"summary_id": "L2-infra", "status": "quarantined", "level": 2},
            {"summary_id": "L2-conflict", "status": "quarantined", "level": 2},
            {"summary_id": "L1-content", "status": "quarantined", "level": 1},
        ]
        plan["quarantine"] = [
            {"summary_id": "L2-content", "reason": "model-failure-limit"},
            {"summary_id": "L2-infra", "reason": "infra-blocked"},
            {"summary_id": "L2-conflict", "reason": "conflicting-existing-sidecars"},
            {"summary_id": "L1-content", "reason": "model-failure-limit"},
        ]
        config = self.base / "config.yaml"
        config.write_text("ai_summary: {}\n", encoding="utf-8")
        with (
            patch("summary_v2_backfill._refresh_plan", return_value=plan),
            patch(
                "summary_v2_backfill._select_single_attempt_candidates",
                return_value=([], []),
            ) as select,
        ):
            receipt = run_parent_rescue(
                self.archive,
                self.output,
                config,
                maximum_jobs=3,
            )
        selected_pool = select.call_args.args[0]
        self.assertEqual(
            ["L2-content", "L2-infra"],
            [task["summary_id"] for task in selected_pool],
        )
        self.assertEqual(PARENT_RESCUE_REVISION, select.call_args.args[3])
        self.assertEqual(0, receipt["attempted"])


if __name__ == "__main__":
    unittest.main()
