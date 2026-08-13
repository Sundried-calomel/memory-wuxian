import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_atoms import _source_sha256  # noqa: E402
from memory_summary_v2 import (  # noqa: E402
    SummaryV2Error,
    build_level_1_source,
    build_parent_source,
    comparison_report,
    persist_sidecar,
    project,
    render_markdown,
    validate_candidate,
    validate_sidecar,
)
from platform_transaction import canonical_json_bytes  # noqa: E402
from summary_v2_worker import build_prompt, codex_command, run_source  # noqa: E402


class SummaryV2Test(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "archive"
        self.archive.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def job(self, offset=0):
        records = [
            {
                "record_type": "raw_message",
                "sequence": offset + 1,
                "message_id": f"消息-¥-{offset + 1}",
                "conversation_id": "codex:多语言-😀",
                "timestamp": "2026-08-10T09:00:00+09:00",
                "speaker": "user",
                "round_number": offset + 1,
                "text": "请保留 summary-v1，并新增可追溯的 summary-v2。",
            },
            {
                "record_type": "raw_message",
                "sequence": offset + 2,
                "message_id": f"tool-{offset + 2}",
                "conversation_id": "codex:多语言-😀",
                "timestamp": "2026-08-10T09:00:01+09:00",
                "speaker": "tool",
                "round_number": offset + 1,
                "text": "Ran rg -n \"summary-v1|summary-v2\" scripts tests",
                "source": {"phase": "tool_activity"},
            },
            {
                "record_type": "raw_message",
                "sequence": offset + 3,
                "message_id": f"assistant-{offset + 3}",
                "conversation_id": "codex:多语言-😀",
                "timestamp": "2026-08-10T09:00:02+09:00",
                "speaker": "assistant",
                "round_number": offset + 1,
                "text": "采用并行侧车，不修改旧摘要或原始归档。日本語 😀",
                "completes_round": True,
            },
        ]
        return {
            "format_version": 1,
            "job_id": f"job-{offset + 1:06d}",
            "target_summary_id": f"L1-{offset + 1:06d}",
            "summary_level": 1,
            "conversation_id": "codex:多语言-😀",
            "source_sha256": _source_sha256(records),
            "source_message_ids": [record["message_id"] for record in records],
            "source_records": records,
        }

    def candidate(self, source, *, include_locator=True):
        refs = list(source["source_refs"])
        anchors = []
        if include_locator:
            for index, locator in enumerate(source["required_locators"], 1):
                anchors.append(
                    {
                        "local_id": f"locator{index}",
                        "text": locator["text"],
                        "kind": locator["kind"],
                        "source_refs": [locator["source_ref"]],
                    }
                )
        return {
            "format_version": 2,
            "job_id": source["job_id"],
            "summary_level": source["summary_level"],
            "source_sha256": source["source_sha256"],
            "overview": [
                {
                    "local_id": "overview1",
                    "text": "决定并行建立可追溯摘要，同时保持旧摘要和原始归档不变。",
                    "source_refs": list(refs),
                }
            ],
            "scenes": [
                {
                    "local_id": "scene1",
                    "title": "可追溯摘要设计与核验",
                    "summary": "用户提出可追溯要求，随后检查代码并确认采用外部并行侧车。",
                    "source_refs": list(refs),
                }
            ],
            "atoms": [
                {
                    "local_id": "atom1",
                    "atom_type": "work_method",
                    "statement": "采用并行 summary-v2 侧车且不修改 summary-v1。",
                    "epistemic_status": "accepted_decision",
                    "scope": "Memory无限 / summary-v2",
                    "source_refs": list(refs),
                }
            ],
            "relations": [],
            "retrieval_anchors": anchors,
            "omissions": [],
        }

    def parent_candidate(self, source):
        atoms = []
        for index, promoted in enumerate(source["promotion_manifest"], 1):
            atoms.append(
                {
                    "local_id": f"state{index}",
                    "atom_type": promoted["atom_type"],
                    "statement": promoted["statement"],
                    "epistemic_status": promoted["epistemic_status"],
                    "scope": promoted["scope"],
                    "source_refs": [promoted["child_summary_id"]],
                }
            )
        return {
            "format_version": 2,
            "job_id": source["job_id"],
            "summary_level": source["summary_level"],
            "source_sha256": source["source_sha256"],
            "overview": [
                {
                    "local_id": "overview1",
                    "text": "这一层概括各子摘要覆盖的工作阶段，并保留向下导航。",
                    "source_refs": list(source["source_refs"]),
                }
            ],
            "scenes": [
                {
                    "local_id": f"route{index}",
                    "title": f"阶段 {index}",
                    "summary": "详细事实保留在该直接子摘要中。",
                    "source_refs": [source_ref],
                }
                for index, source_ref in enumerate(source["source_refs"], 1)
            ],
            "atoms": atoms,
            "relations": [],
            "retrieval_anchors": [],
            "omissions": [],
        }

    def test_l1_projection_has_complete_raw_backreferences_and_locator(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        self.assertEqual(0, sidecar["coverage"]["silent_loss_count"])
        self.assertEqual(source["source_refs"], sidecar["coverage"]["represented_source_refs"])
        self.assertEqual(source["source_refs"], sidecar["coverage"]["raw_message_ids"])
        self.assertEqual(
            'Ran rg -n "summary-v1|summary-v2" scripts tests',
            sidecar["retrieval_anchors"][0]["text"],
        )
        self.assertEqual(sidecar, validate_sidecar(sidecar, source))

    def test_silent_source_loss_is_rejected(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        lost = source["source_refs"][-1]
        for group in ("overview", "scenes", "atoms"):
            candidate[group][0]["source_refs"].remove(lost)
        with self.assertRaisesRegex(SummaryV2Error, "silently loses"):
            validate_candidate(candidate, source)

    def test_every_represented_ref_needs_scene_and_detail(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        missing = source["source_refs"][0]
        candidate["scenes"][0]["source_refs"].remove(missing)
        with self.assertRaisesRegex(SummaryV2Error, "must appear in a scene"):
            validate_candidate(candidate, source)

    def test_required_tool_locator_cannot_be_dropped_or_reworded(self):
        source = build_level_1_source(self.job())
        with self.assertRaisesRegex(SummaryV2Error, "lost required locator"):
            validate_candidate(self.candidate(source, include_locator=False), source)
        candidate = self.candidate(source)
        candidate["retrieval_anchors"][0]["text"] = "Ran a search"
        with self.assertRaisesRegex(SummaryV2Error, "exact source substring"):
            validate_candidate(candidate, source)

    def test_duplicate_source_refs_are_rejected_locally(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        candidate["overview"][0]["source_refs"].append(
            candidate["overview"][0]["source_refs"][0]
        )
        with self.assertRaisesRegex(SummaryV2Error, "contains duplicates"):
            validate_candidate(candidate, source)

    def test_explicit_omission_cannot_also_be_represented(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        candidate["omissions"] = [
            {"source_ref": source["source_refs"][0], "reason": "重复寒暄"}
        ]
        with self.assertRaisesRegex(SummaryV2Error, "represents and omits"):
            validate_candidate(candidate, source)

    def test_parent_preserves_child_item_and_raw_message_routes(self):
        first_source = build_level_1_source(self.job(0))
        second_source = build_level_1_source(self.job(10))
        first = project(first_source, self.candidate(first_source))
        second = project(second_source, self.candidate(second_source))
        parent_source = build_parent_source([first, second])
        parent = project(parent_source, self.parent_candidate(parent_source))
        self.assertEqual(2, parent["summary_level"])
        self.assertEqual(
            [first["summary_v2_id"], second["summary_v2_id"]],
            parent_source["source_refs"],
        )
        self.assertEqual(6, parent["coverage"]["raw_message_count"])
        self.assertEqual(parent_source["source_refs"], parent["coverage"]["represented_source_refs"])
        self.assertFalse(parent["retrieval_anchors"])

    def test_parent_rejects_missing_promoted_state_but_not_ordinary_detail(self):
        first_source = build_level_1_source(self.job(0))
        second_source = build_level_1_source(self.job(10))
        first_candidate = self.candidate(first_source)
        first_candidate["atoms"].append(
            {
                "local_id": "ordinary1",
                "atom_type": "work_fact",
                "statement": "这是只需保留在一级摘要中的普通背景事实。",
                "epistemic_status": "explicit_fact",
                "scope": "局部背景",
                "source_refs": list(first_source["source_refs"]),
            }
        )
        first_candidate["atoms"].append(
            {
                "local_id": "artifact1",
                "atom_type": "work_artifact",
                "statement": "生成了可人工读取的 summary.md。",
                "epistemic_status": "explicit_fact",
                "scope": "Memory无限 / summary-v2",
                "source_refs": list(first_source["source_refs"]),
            }
        )
        first = project(first_source, first_candidate)
        second = project(second_source, self.candidate(second_source))
        parent_source = build_parent_source([first, second])
        candidate = self.parent_candidate(parent_source)
        promoted_statements = {
            item["statement"] for item in parent_source["promotion_manifest"]
        }
        self.assertNotIn(
            "这是只需保留在一级摘要中的普通背景事实。",
            promoted_statements,
        )
        self.assertIn("生成了可人工读取的 summary.md。", promoted_statements)
        candidate["atoms"].pop()
        with self.assertRaisesRegex(SummaryV2Error, "lost promoted durable state"):
            validate_candidate(candidate, parent_source)

    def test_higher_parent_promotes_state_and_keeps_direct_child_routes(self):
        l1_sidecars = []
        for offset in (0, 10, 20, 30):
            source = build_level_1_source(self.job(offset))
            l1_sidecars.append(project(source, self.candidate(source)))
        left_source = build_parent_source(l1_sidecars[:2])
        right_source = build_parent_source(l1_sidecars[2:])
        left = project(left_source, self.parent_candidate(left_source))
        right = project(right_source, self.parent_candidate(right_source))
        level3_source = build_parent_source([left, right])
        level3 = project(level3_source, self.parent_candidate(level3_source))
        self.assertEqual(3, level3["summary_level"])
        self.assertEqual(
            [left["summary_v2_id"], right["summary_v2_id"]],
            level3_source["source_refs"],
        )
        self.assertEqual(2, len(level3["scenes"]))
        self.assertEqual(
            len(level3_source["promotion_manifest"]), len(level3["atoms"])
        )
        self.assertLessEqual(
            len(level3["atoms"]), len(left["atoms"]) + len(right["atoms"])
        )

    def test_internal_tampering_fails_after_outer_hash_is_recomputed(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        sidecar["atoms"][0]["statement"] += " 篡改"
        unsigned = {key: value for key, value in sidecar.items() if key != "projection_sha256"}
        sidecar["projection_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        with self.assertRaisesRegex(SummaryV2Error, "item_id does not match"):
            validate_sidecar(sidecar)

    def test_source_identity_tampering_fails_after_outer_hash_is_recomputed(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        sidecar["conversation_id"] = "codex:wrong-conversation"
        unsigned = {key: value for key, value in sidecar.items() if key != "projection_sha256"}
        sidecar["projection_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        with self.assertRaisesRegex(SummaryV2Error, "top-level identity disagrees"):
            validate_sidecar(sidecar)

    def test_external_bundle_is_readable_idempotent_and_preserves_v1(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        v1 = self.archive / "summaries" / "level-1" / "L1-000001.md"
        v1.parent.mkdir(parents=True)
        v1.write_text("immutable summary-v1\n", encoding="utf-8")
        before = hashlib.sha256(v1.read_bytes()).hexdigest()
        bundle, status = persist_sidecar(sidecar, self.base / "sidecars", self.archive)
        self.assertEqual("created", status)
        self.assertIn("可追溯摘要", (bundle / "summary.md").read_text(encoding="utf-8"))
        _, repeated = persist_sidecar(sidecar, self.base / "sidecars", self.archive)
        self.assertEqual("existing-identical", repeated)
        self.assertEqual(before, hashlib.sha256(v1.read_bytes()).hexdigest())
        with self.assertRaisesRegex(SummaryV2Error, "outside the archive"):
            persist_sidecar(sidecar, self.archive / "derived", self.archive)

    def test_stubbed_worker_uses_prompt_and_validates_before_write(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        config = self.base / "config.yaml"
        config.write_text(
            "ai_summary:\n  codex_cli_path_windows: codex.exe\n  timeout_seconds: 30\n",
            encoding="utf-8",
        )

        def fake_invoker(command, timeout, prompt):
            self.assertIn("--sandbox", command)
            self.assertIn("read-only", command)
            self.assertEqual(30, timeout)
            self.assertIn("Never silently drop", prompt)
            self.assertIn(source["source_sha256"], prompt)
            return candidate

        result = run_source(
            source,
            self.base / "worker-output",
            self.archive,
            config_path=config,
            invoker=fake_invoker,
        )
        self.assertTrue(result["model_called"])
        self.assertEqual("created", result["status"])

    def test_real_cli_candidate_path_handles_multilingual_paths(self):
        job = self.job()
        source = build_level_1_source(job)
        job_path = self.base / "任务 ¥ 😀.json"
        candidate_path = self.base / "候选 日本語.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        candidate_path.write_text(
            json.dumps(self.candidate(source), ensure_ascii=False), encoding="utf-8"
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "summary_v2_worker.py"),
                "l1",
                "--job",
                str(job_path),
                "--candidate",
                str(candidate_path),
                "--output-dir",
                str(self.base / "输出 sidecar"),
                "--archive-root",
                str(self.archive),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("created", result["status"])
        self.assertTrue(Path(result["bundle"]).joinpath("summary.md").is_file())

    def test_comparison_report_keeps_human_review_explicit(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        v1 = self.base / "summary-v1.md"
        v1.write_text("# Summary\n\n- broad result\n", encoding="utf-8")
        report = comparison_report(v1, sidecar)
        self.assertEqual(0, report["summary_v2"]["silent_loss_count"])
        self.assertIn("do not prove semantic quality", report["interpretation_limit"])
        self.assertEqual(5, len(report["human_review_questions"]))

    def test_prompt_schema_and_production_activation_boundary(self):
        source = build_level_1_source(self.job())
        prompt = build_prompt(source)
        self.assertIn("required_locators", prompt)
        self.assertIn("exactly the declared `required_locators`", prompt)
        self.assertIn("exact contiguous source substring", prompt)
        self.assertIn("Message-level coverage is not fact-level coverage", prompt)
        self.assertIn("predominant natural language", prompt)
        schema = json.loads(
            (ROOT / "schemas" / "summary-v2-result.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(2, schema["properties"]["format_version"]["const"])
        first_source = build_level_1_source(self.job(0))
        second_source = build_level_1_source(self.job(10))
        parent_source = build_parent_source(
            [
                project(first_source, self.candidate(first_source)),
                project(second_source, self.candidate(second_source)),
            ]
        )
        parent_prompt = build_prompt(parent_source)
        self.assertIn("lossless navigation layer", parent_prompt)
        self.assertIn("promotion_manifest", parent_prompt)
        command, _, _ = codex_command({}, parent_source)
        self.assertTrue(
            any(value.endswith("summary-v2-parent-result.schema.json") for value in command)
        )
        parent_schema = json.loads(
            (ROOT / "schemas" / "summary-v2-parent-result.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(2, parent_schema["properties"]["summary_level"]["minimum"])
        self.assertEqual(0, parent_schema["properties"]["retrieval_anchors"]["maxItems"])
        for path in (
            ROOT / "scripts" / "semantic_worker.py",
            ROOT / "scripts" / "semantic_dispatch.py",
            ROOT / "scripts" / "maintenance_supervisor.py",
        ):
            self.assertNotIn("summary_v2", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
