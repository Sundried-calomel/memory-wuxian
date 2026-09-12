import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_atoms import _source_sha256  # noqa: E402
from memory_summary_v2 import (  # noqa: E402
    FORMAT,
    SummaryV2Error,
    build_level_1_source,
    build_parent_source,
    build_parent_rescue_reduce_source,
    build_rescue_reduce_source,
    comparison_report,
    merge_ledgers,
    normalize_model_candidate,
    parent_model_projection,
    persist_sidecar,
    project,
    promotion_ledger,
    render_markdown,
    rescue_model_projection,
    semantic_atom_identity,
    sidecar_to_ledger,
    validate_candidate,
    validate_sidecar,
)
from platform_transaction import canonical_json_bytes  # noqa: E402
from semantic_plan import (  # noqa: E402
    canonical_hash,
    partition_ordered,
    plan_level_1_jobs,
    plan_reduction_frontiers,
    utf8_size,
    validate_node_receipt,
)
from summary_v2_worker import (  # noqa: E402
    _compact_parent_rescue_map,
    build_prompt,
    codex_command,
    run_source,
)
from summary_v2_backfill import (  # noqa: E402
    PARENT_RESCUE_REVISION,
    _compact_parent_rescue_maps,
    _require_stage_receipt,
    _stage_receipt,
    run_parent_rescue,
)
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

    def test_derived_file_locator_has_no_surrounding_whitespace(self):
        job = self.job()
        job["source_records"][1]["text"] = "File: C:\\tmp\\报告.txt    [edited]"
        job["source_sha256"] = _source_sha256(job["source_records"])
        source = build_level_1_source(job)
        self.assertEqual("C:\\tmp\\报告.txt", source["required_locators"][1]["text"])
        sidecar = project(source, self.candidate(source))
        self.assertEqual("C:\\tmp\\报告.txt", sidecar["retrieval_anchors"][1]["text"])

    def test_deterministic_locators_can_exceed_the_model_anchor_limit(self):
        job = self.job()
        locator_texts = [f"command-{index:04d}" for index in range(513)]
        job["source_records"][0]["text"] = " ".join(locator_texts)
        job["source_sha256"] = _source_sha256(job["source_records"])
        source = build_level_1_source(job)
        source["required_locators"] = [
            {
                "source_ref": source["source_refs"][0],
                "text": text,
                "kind": "command",
            }
            for text in locator_texts
        ]
        candidate = normalize_model_candidate(
            self.candidate(source, include_locator=False),
            source,
        )
        sidecar = project(source, candidate)
        self.assertEqual(513, len(sidecar["retrieval_anchors"]))
        self.assertEqual(sidecar, validate_sidecar(sidecar, source))

    def test_rescue_maps_reduce_to_the_unchanged_formal_l1_identity(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        formal_records = [*left_job["source_records"], *right_job["source_records"]]
        formal_job = {
            **left_job,
            "job_id": "formal-rescue-job",
            "target_summary_id": "L1-000777",
            "source_sha256": _source_sha256(formal_records),
            "source_message_ids": [item["message_id"] for item in formal_records],
            "source_records": formal_records,
        }
        formal_source = build_level_1_source(formal_job)
        maps = []
        for job in (left_job, right_job):
            source = build_level_1_source(job)
            maps.append(project(source, self.candidate(source)))
        rescue_source = build_rescue_reduce_source(formal_source, maps)
        self.assertEqual(formal_source["parallel_summary_id"], rescue_source["parallel_summary_id"])
        self.assertEqual(formal_source["source_sha256"], rescue_source["source_sha256"])
        self.assertEqual(formal_source["source_refs"], rescue_source["source_refs"])
        rescue_prompt = build_prompt(rescue_source)
        self.assertIn('"deterministic_locator_count":2', rescue_prompt)
        self.assertIn('"deterministic_locator_source_ref_count":2', rescue_prompt)
        self.assertIn('"deterministic_locator_source_refs_sha256"', rescue_prompt)
        self.assertIn('"source_ref_catalog_sha256"', rescue_prompt)
        self.assertNotIn('"deterministic_locator_source_refs"', rescue_prompt)
        self.assertIn('"canonical_atoms"', rescue_prompt)
        self.assertIn('"maximum_model_scenes":126', rescue_prompt)
        self.assertNotIn('"promotion_manifest"', rescue_prompt)
        self.assertNotIn(
            'Ran rg -n "summary-v1|summary-v2" scripts tests',
            rescue_prompt,
        )
        self.assertIn("deterministic projector injects", rescue_prompt)
        self.assertIn("every formal locator after the model call", rescue_prompt)
        reduced = project(rescue_source, self.candidate(rescue_source))
        self.assertEqual("L1-000777", reduced["parallel_summary_id"])
        self.assertEqual(formal_source["source_refs"], reduced["coverage"]["raw_message_ids"])

    def test_large_rescue_prompt_hash_binds_local_proof_collections(self):
        template = self.job()["source_records"][0]

        def records(start, count):
            return [
                {
                    **template,
                    "sequence": index + 1,
                    "message_id": f"large-ref-{index:04d}",
                    "round_number": index + 1,
                    "text": (
                        f"ordinary semantic message {index}; "
                        f"deterministic-command-large-ref-{index:04d}-0; "
                        f"deterministic-command-large-ref-{index:04d}-1"
                    ),
                    "completes_round": True,
                }
                for index in range(start, start + count)
            ]

        def source_for(name, group, locator_count):
            job = {
                "format_version": 1,
                "job_id": name,
                "target_summary_id": name,
                "summary_level": 1,
                "conversation_id": template["conversation_id"],
                "source_sha256": _source_sha256(group),
                "source_message_ids": [item["message_id"] for item in group],
                "source_records": group,
            }
            source = build_level_1_source(job)
            source["required_locators"] = []
            for index in range(locator_count):
                source_ref = source["source_refs"][index % len(source["source_refs"])]
                occurrence = index // len(source["source_refs"])
                source["required_locators"].append(
                    {
                        "source_ref": source_ref,
                        "text": f"deterministic-command-{source_ref}-{occurrence}",
                        "kind": "command",
                    }
                )
            return source

        def bounded_candidate(source):
            chunks = [
                source["source_refs"][index : index + 128]
                for index in range(0, len(source["source_refs"]), 128)
            ]
            return {
                "format_version": 2,
                "job_id": source["job_id"],
                "summary_level": 1,
                "source_sha256": source["source_sha256"],
                "overview": [
                    {
                        "local_id": "overview1",
                        "text": "A bounded overview of the semantic interval.",
                        "source_refs": chunks[0],
                    }
                ],
                "scenes": [
                    {
                        "local_id": f"scene{index}",
                        "title": f"Semantic interval {index}",
                        "summary": "The interval remains available through this route.",
                        "source_refs": chunk,
                    }
                    for index, chunk in enumerate(chunks, 1)
                ],
                "atoms": [
                    {
                        "local_id": "atom1",
                        "atom_type": "work_fact",
                        "statement": f"The semantic interval {source['job_id']} was processed.",
                        "epistemic_status": "explicit_fact",
                        "scope": "Summary V2 bounded rescue",
                        "source_refs": chunks[0],
                    }
                ],
                "relations": [],
                "retrieval_anchors": [],
                "omissions": [],
            }

        left_records = records(0, 638)
        right_records = records(638, 638)
        left = source_for("large-left", left_records, 750)
        right = source_for("large-right", right_records, 757)
        maps = [
            project(left, normalize_model_candidate(bounded_candidate(left), left)),
            project(right, normalize_model_candidate(bounded_candidate(right), right)),
        ]
        formal = source_for("large-formal", [*left_records, *right_records], 1507)
        rescue = build_rescue_reduce_source(formal, maps)
        projection = rescue_model_projection(rescue, maps)
        prompt = build_prompt(rescue)

        self.assertEqual(1507, projection["anchor_count"])
        self.assertIn("anchors_sha256", projection)
        self.assertNotIn("anchors", projection)
        self.assertLessEqual(len(prompt.encode("utf-8")), 900_000)
        self.assertNotIn("deterministic-command-large-ref-0000-0", prompt)
        self.assertNotIn('"source_ref_catalog"', prompt)
        self.assertIn('"source_ref_catalog_sha256"', prompt)

    def test_parent_rescue_maps_keep_original_direct_child_routes(self):
        children = []
        for offset in (0, 3, 6, 9):
            source = build_level_1_source(self.job(offset=offset))
            children.append(project(source, self.candidate(source)))
        formal = build_parent_source(children, parallel_summary_id="L2-000777")
        maps = []
        for index in (0, 2):
            source = build_parent_source(
                children[index : index + 2],
                parallel_summary_id=f"L2-000777-map-{index // 2 + 1}",
            )
            maps.append(project(source, self.parent_candidate(source)))
        rescue = build_parent_rescue_reduce_source(formal, maps)
        self.assertEqual(formal["source_refs"], rescue["source_refs"])
        self.assertEqual(formal["source_sha256"], rescue["source_sha256"])
        reduced = project(rescue, self.parent_candidate(rescue))
        self.assertEqual("L2-000777", reduced["parallel_summary_id"])
        self.assertEqual(formal["source_refs"], reduced["coverage"]["represented_source_refs"])

    def test_parent_rescue_restores_promoted_state_without_prompt_duplication(self):
        children = []
        for offset in (0, 3, 6, 9):
            source = build_level_1_source(self.job(offset=offset))
            children.append(project(source, self.candidate(source)))
        formal = build_parent_source(children, parallel_summary_id="L2-000780")
        maps = []
        for index in (0, 2):
            source = build_parent_source(
                children[index : index + 2],
                parallel_summary_id=f"L2-000780-map-{index // 2 + 1}",
            )
            maps.append(project(source, self.parent_candidate(source)))
        rescue = build_parent_rescue_reduce_source(formal, maps)
        prompt = build_prompt(rescue)
        first = formal["promotion_manifest"][0]
        self.assertIn("Additional durable state is retained", prompt)
        self.assertIn('"canonical_atoms"', prompt)
        self.assertNotIn('"promotion_manifest"', prompt)
        self.assertNotIn(
            children[0]["source"]["raw_message_ids"][0],
            prompt,
        )

        candidate = self.parent_candidate(rescue)
        candidate["atoms"] = [
            atom
            for atom in candidate["atoms"]
            if not (
                atom["atom_type"] == first["atom_type"]
                and atom["statement"] == first["statement"]
                and atom["epistemic_status"] == first["epistemic_status"]
                and atom["scope"] == first["scope"]
                and first["child_summary_id"] in atom["source_refs"]
            )
        ]
        normalization_source = {
            **rescue,
            "_canonical_projection": rescue_model_projection(rescue, maps),
        }
        normalized = normalize_model_candidate(candidate, normalization_source)
        restored = [
            atom
            for atom in normalized["atoms"]
            if atom["atom_type"] == first["atom_type"]
            and atom["statement"] == first["statement"]
            and atom["epistemic_status"] == first["epistemic_status"]
            and atom["scope"] == first["scope"]
            and first["child_summary_id"] in atom["source_refs"]
        ]
        self.assertEqual(1, len(restored))
        project(normalization_source, normalized)

    def test_parent_round_trip_injects_relations_and_preserves_exact_provenance(self):
        children = []
        relation_source_ids = None
        for offset in (0, 3, 6, 9):
            source = build_level_1_source(self.job(offset=offset))
            candidate = self.candidate(source)
            if offset == 0:
                candidate["atoms"][0]["source_refs"] = [source["source_refs"][0]]
                candidate["atoms"].append(
                    {
                        "local_id": "atom2",
                        "atom_type": "work_method",
                        "statement": "旧的摘要处理方法已撤回。",
                        "epistemic_status": "withdrawn",
                        "scope": "Memory无限 / summary-v2",
                        "source_refs": [source["source_refs"][2]],
                    }
                )
                candidate["relations"] = [
                    {
                        "from_local_id": "atom1",
                        "to_local_id": "atom2",
                        "relation_type": "revises",
                        "source_refs": [source["source_refs"][0], source["source_refs"][2]],
                    }
                ]
                relation_source_ids = [source["source_refs"][0], source["source_refs"][2]]
            children.append(project(source, candidate))
        formal = build_parent_source(children, parallel_summary_id="L2-000783")
        self.assertEqual(1, len(formal["promotion_relations"]))
        maps = []
        for index in (0, 2):
            map_source = build_parent_source(
                children[index : index + 2],
                parallel_summary_id=f"L2-000783-map-{index // 2 + 1}",
            )
            map_candidate = self.parent_candidate(map_source)
            map_candidate["relations"] = []
            maps.append(project(map_source, normalize_model_candidate(map_candidate, map_source)))
        rescue = build_parent_rescue_reduce_source(formal, maps)
        normalization_source = {
            **rescue,
            "_canonical_projection": rescue_model_projection(rescue, maps),
        }
        candidate = self.parent_candidate(rescue)
        candidate["atoms"] = []
        candidate["relations"] = []
        final = project(
            normalization_source,
            normalize_model_candidate(candidate, normalization_source),
        )
        self.assertEqual(1, len(final["relations"]))
        self.assertEqual(relation_source_ids, final["relations"][0]["source_message_ids"])
        promoted = formal["promotion_manifest"]
        for atom in final["atoms"]:
            matches = [
                item for item in promoted
                if item["atom_type"] == atom["atom_type"]
                and item["statement"] == atom["statement"]
                and item["epistemic_status"] == atom["epistemic_status"]
                and item["scope"] == atom["scope"]
                and item["child_summary_id"] in atom["source_refs"]
            ]
            if matches:
                expected_ids = sorted(
                    {message_id for item in matches for message_id in item["source_message_ids"]},
                    key=final["source"]["raw_message_ids"].index,
                )
                self.assertEqual(expected_ids, atom["source_message_ids"])

    def test_relation_evidence_can_be_disjoint_from_both_endpoint_atoms(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        endpoint_left, endpoint_right, relation_evidence = source["source_refs"]
        candidate["atoms"][0]["source_refs"] = [endpoint_left]
        candidate["atoms"].append(
            {
                "local_id": "atom2",
                "atom_type": "work_task",
                "statement": "对新的摘要关系执行独立核验。",
                "epistemic_status": "accepted_decision",
                "scope": "Memory无限 / summary-v2",
                "source_refs": [endpoint_right],
            }
        )
        candidate["relations"] = [
            {
                "from_local_id": "atom1",
                "to_local_id": "atom2",
                "relation_type": "supports",
                "source_refs": [relation_evidence],
            }
        ]

        normalized = normalize_model_candidate(candidate, source)
        self.assertEqual([relation_evidence], normalized["relations"][0]["source_refs"])
        sidecar = project(source, normalized)
        self.assertEqual([relation_evidence], sidecar["relations"][0]["source_refs"])
        self.assertEqual(
            [source["ref_catalog"][2]["source_message_ids"][0]],
            sidecar["relations"][0]["source_message_ids"],
        )

    def test_parent_without_promotable_atoms_round_trips(self):
        children = []
        for offset in (0, 3):
            source = build_level_1_source(self.job(offset=offset))
            candidate = self.candidate(source)
            candidate["atoms"][0]["atom_type"] = "work_fact"
            candidate["atoms"][0]["epistemic_status"] = "explicit_fact"
            children.append(project(source, candidate))

        source = build_parent_source(children, parallel_summary_id="L2-000-zero-state")
        self.assertEqual([], source["promotion_manifest"])
        candidate = self.parent_candidate(source)
        self.assertEqual([], candidate["atoms"])
        sidecar = project(source, candidate)

        self.assertEqual([], sidecar["atoms"])
        self.assertEqual(sidecar, validate_sidecar(sidecar, source))
        self.assertIn("## Promoted Durable State", render_markdown(sidecar))

    def test_relation_evidence_still_rejects_unknown_source_refs(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        candidate["atoms"].append(
            {
                "local_id": "atom2",
                "atom_type": "work_task",
                "statement": "对关系来源执行归属核验。",
                "epistemic_status": "accepted_decision",
                "scope": "Memory无限 / summary-v2",
                "source_refs": [source["source_refs"][1]],
            }
        )
        candidate["relations"] = [
            {
                "from_local_id": "atom1",
                "to_local_id": "atom2",
                "relation_type": "supports",
                "source_refs": ["outside-source"],
            }
        ]
        with self.assertRaisesRegex(SummaryV2Error, "outside the source"):
            project(source, normalize_model_candidate(candidate, source))

    def test_l3_round_trip_preserves_formal_relation_and_exact_provenance(self):
        level_1 = []
        expected_relation_ids = None
        for offset in range(0, 24, 3):
            source = build_level_1_source(self.job(offset=offset))
            candidate = self.candidate(source)
            if offset == 0:
                candidate["atoms"][0]["source_refs"] = [source["source_refs"][0]]
                candidate["atoms"].append(
                    {
                        "local_id": "atom2",
                        "atom_type": "work_method",
                        "statement": "旧的层级摘要方案已撤回。",
                        "epistemic_status": "withdrawn",
                        "scope": "Memory无限 / summary-v2",
                        "source_refs": [source["source_refs"][2]],
                    }
                )
                candidate["relations"] = [
                    {
                        "from_local_id": "atom1",
                        "to_local_id": "atom2",
                        "relation_type": "revises",
                        "source_refs": [source["source_refs"][0], source["source_refs"][2]],
                    }
                ]
                expected_relation_ids = [source["source_refs"][0], source["source_refs"][2]]
            level_1.append(project(source, candidate))

        level_2 = []
        for index in range(2):
            source = build_parent_source(
                level_1[index * 4 : (index + 1) * 4],
                parallel_summary_id=f"L2-00079{index}",
            )
            candidate = self.parent_candidate(source)
            candidate["relations"] = []
            level_2.append(project(source, normalize_model_candidate(candidate, source)))

        level_3_source = build_parent_source(level_2, parallel_summary_id="L3-000079")
        self.assertEqual(1, len(level_3_source["promotion_relations"]))
        level_3_candidate = self.parent_candidate(level_3_source)
        level_3_candidate["atoms"] = []
        level_3_candidate["relations"] = []
        level_3 = project(
            level_3_source,
            normalize_model_candidate(level_3_candidate, level_3_source),
        )
        self.assertEqual(1, len(level_3["relations"]))
        self.assertEqual(expected_relation_ids, level_3["relations"][0]["source_message_ids"])

    def test_full_l3_parent_rescue_uses_public_dag_and_generation_time_receipts(self):
        level_2_by_parallel = {}
        for parent_index in range(8):
            level_1 = []
            for child_index in range(2):
                offset = (parent_index * 2 + child_index) * 3
                source = build_level_1_source(self.job(offset=offset))
                level_1.append(project(source, self.candidate(source)))
            parallel_id = f"L2-synthetic-{parent_index:03d}"
            source = build_parent_source(level_1, parallel_summary_id=parallel_id)
            level_2_by_parallel[parallel_id] = project(
                source,
                normalize_model_candidate(self.parent_candidate(source), source),
            )

        output = self.base / "l3-output"
        plan_path = output / "backfill" / "plan.json"
        plan_path.parent.mkdir(parents=True)
        task = {
            "summary_id": "L3-synthetic-001",
            "level": 3,
            "status": "quarantined",
            "children": list(level_2_by_parallel),
        }
        plan = {
            "tasks": [task],
            "quarantine": [
                {"summary_id": task["summary_id"], "reason": "model-failure-limit"}
            ],
        }
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        config = self.base / "config.yaml"
        config.write_text("ai_summary: {}\n", encoding="utf-8")

        def staged_prompt(source):
            map_count = len(source.get("prompt_payload", {}).get("map_sidecars", []))
            return "x" * 900_001 if map_count > 2 else build_prompt(source)

        bundle_index = 0

        def deterministic_run(source, output_directory, archive_root, **kwargs):
            nonlocal bundle_index
            bundle_index += 1
            return run_source(
                source,
                self.base / "b" / f"{bundle_index:02d}",
                archive_root,
                candidate=self.parent_candidate(source),
                diagnostic_path=kwargs["diagnostic_path"],
                invocation_context=kwargs["invocation_context"],
            )

        with (
            patch("summary_v2_backfill._validate_execution_contract"),
            patch(
                "summary_v2_backfill._load_sidecars",
                return_value=(level_2_by_parallel, {}),
            ),
            patch("summary_v2_backfill._refresh_plan", return_value=plan),
            patch("summary_v2_backfill._bind_rescue_attempt"),
            patch("summary_v2_backfill.build_prompt", side_effect=staged_prompt),
            patch("summary_v2_backfill.run_source", side_effect=deterministic_run) as calls,
        ):
            receipt = run_parent_rescue(
                self.archive,
                output,
                config,
                maximum_jobs=1,
            )
        self.assertEqual("completed", receipt["status"], receipt)
        self.assertEqual(7, calls.call_count)
        completed = receipt["completed"][0]
        self.assertEqual(3, completed["summary_level"])
        state_path = (
            output
            / "backfill"
            / "rescue"
            / "node-state"
            / PARENT_RESCUE_REVISION
            / "L3-synthetic-001.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(4, len(state["maps"]))
        self.assertEqual(2, len(state["reductions"]), state)
        for record in [*state["maps"].values(), *state["reductions"].values()]:
            self.assertEqual(
                "memory-wuxian-summary-v2-stage-receipt-v1",
                record["receipt"]["format"],
            )

    def test_parent_rescue_prompt_exposes_canonical_promotions_once(self):
        children = []
        for offset in (0, 3, 6, 9):
            source = build_level_1_source(self.job(offset=offset))
            children.append(project(source, self.candidate(source)))
        formal = build_parent_source(children, parallel_summary_id="L2-000782")
        map_source = build_parent_source(
            children[:2], parallel_summary_id="L2-000782-map-001"
        )
        sidecar = project(map_source, self.parent_candidate(map_source))
        compact = _compact_parent_rescue_map(sidecar, formal["promotion_manifest"])
        semantic_ids = [item["semantic_id"] for item in compact["canonical_atoms"]]
        self.assertTrue(semantic_ids)
        self.assertEqual(len(semantic_ids), len(set(semantic_ids)))
        self.assertEqual(len(semantic_ids), compact["promoted_atom_count"])
        self.assertRegex(compact["promoted_semantic_ids_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("atoms", compact["map_payloads"][0])
        self.assertTrue(all("source_message_ids" not in atom for atom in compact["canonical_atoms"]))
        self.assertTrue(
            all("source_message_ids" not in route for route in compact["direct_child_routes"])
        )
        self.assertTrue(
            all(
                "source_message_ids" not in item
                for payload in compact["map_payloads"]
                for field in ("overview", "scenes", "omissions")
                for item in payload[field]
            )
        )
        for relation in compact["canonical_relations"]:
            self.assertIn(relation["from_semantic_id"], semantic_ids)
            self.assertIn(relation["to_semantic_id"], semantic_ids)

    def test_parent_rescue_final_injects_every_canonical_relation(self):
        children = []
        for offset in (0, 3, 6, 9):
            source = build_level_1_source(self.job(offset=offset))
            child_candidate = self.candidate(source)
            child_candidate["atoms"][0]["statement"] = (
                f"采用第 {offset // 3 + 1} 个可追溯阶段且不修改 summary-v1。"
            )
            child_candidate["atoms"][0]["scope"] = f"阶段 {offset // 3 + 1}"
            children.append(project(source, child_candidate))
        formal = build_parent_source(children, parallel_summary_id="L2-canonical-relations")
        maps = []
        for index, group in enumerate((children[:2], children[2:]), 1):
            map_source = build_parent_source(
                group,
                parallel_summary_id=f"L2-canonical-relations-map-{index:03d}",
            )
            map_candidate = self.parent_candidate(map_source)
            map_candidate["relations"] = [
                {
                    "from_local_id": "state1",
                    "to_local_id": "state2",
                    "relation_type": "supports",
                    "source_refs": [map_source["source_refs"][0]],
                }
            ]
            maps.append(project(map_source, map_candidate))
        rescue = build_parent_rescue_reduce_source(formal, maps)
        canonical = parent_model_projection(formal, maps)
        normalization_source = {**rescue, "_canonical_projection": canonical}
        final_candidate = self.parent_candidate(rescue)
        final_candidate["atoms"] = []
        final_candidate["relations"] = []
        final = project(
            normalization_source,
            normalize_model_candidate(final_candidate, normalization_source),
        )
        self.assertEqual(len(canonical["canonical_atoms"]), len(final["atoms"]))
        self.assertEqual(len(canonical["canonical_relations"]), len(final["relations"]))
        self.assertEqual(
            {tuple(item["source_refs"]) for item in canonical["canonical_relations"]},
            {tuple(item["source_refs"]) for item in final["relations"]},
        )

    def test_planner_preserves_status_in_semantic_identity_and_has_finite_dag(self):
        accepted = {
            "atom_type": "work_task",
            "statement": "保留同一句任务文本。",
            "epistemic_status": "accepted_decision",
            "scope": "当前任务",
        }
        withdrawn = {**accepted, "epistemic_status": "withdrawn"}
        self.assertNotEqual(
            semantic_atom_identity(accepted), semantic_atom_identity(withdrawn)
        )
        dag = plan_reduction_frontiers([f"map-{index}" for index in range(7)])
        self.assertEqual([7, 4, 2, 1], dag["rank"])
        self.assertTrue(
            all(left > right for left, right in zip(dag["rank"], dag["rank"][1:]))
        )

    def test_planner_partitions_actual_utf8_bytes_and_binds_receipts(self):
        self.assertEqual(7, utf8_size("甲😀"))
        groups = partition_ordered(
            ["甲" * 3, "乙" * 3, "丙" * 3],
            lambda values: "|".join(values),
            target_bytes=19,
            hard_limit=30,
        )
        self.assertEqual([["甲" * 3, "乙" * 3], ["丙" * 3]], groups)
        hard_groups = partition_ordered(
            ["a" * 8, "b" * 8, "c" * 8],
            lambda values: "|".join(values),
            target_bytes=10,
            hard_limit=17,
        )
        self.assertEqual([["a" * 8], ["b" * 8], ["c" * 8]], hard_groups)
        digest = canonical_hash({"fixture": "receipt"})
        expected = {
            "source_sha256": digest,
            "prompt_sha256": digest,
            "schema_sha256": digest,
            "projector_sha256": digest,
            "runner_sha256": digest,
            "worker_sha256": digest,
            "output_projection_sha256": digest,
            "ordered_input_projection_sha256s": [digest],
        }
        self.assertEqual(expected, validate_node_receipt(expected, dict(expected)))
        with self.assertRaisesRegex(ValueError, "prompt_sha256"):
            validate_node_receipt(expected, {**expected, "prompt_sha256": "0" * 64})

    def test_canonical_owners_plan_l1_and_merge_summary_v2_ledgers(self):
        job = self.job()
        source = build_level_1_source(job)
        sidecar = project(source, self.candidate(source))
        ledger = sidecar_to_ledger(sidecar)
        merged = merge_ledgers([ledger], source["source_refs"])
        self.assertEqual(
            [item["semantic_id"] for item in ledger["atoms"]],
            [item["semantic_id"] for item in merged["atoms"]],
        )

        right_source = build_level_1_source(self.job(offset=3))
        formal = build_parent_source(
            [sidecar, project(right_source, self.candidate(right_source))],
            parallel_summary_id="L2-canonical-owner",
        )
        promoted = promotion_ledger(formal)
        self.assertTrue(promoted["atoms"])
        self.assertEqual(formal["source_refs"], promoted["ordered_source_refs"])

        planned = plan_level_1_jobs(
            job,
            build_level_1_source,
            lambda value: {"prompt_utf8_bytes": len(canonical_json_bytes(value))},
            _source_sha256,
            target_bytes=900_000,
            hard_limit=900_000,
        )
        self.assertEqual(
            job["source_message_ids"],
            [record["message_id"] for item in planned for record in item["source_records"]],
        )
        self.assertGreaterEqual(len(planned), 2)

    def test_runtime_stage_receipt_rejects_any_prompt_binding_drift(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        saved = {"receipt": _stage_receipt(source, sidecar)}
        _require_stage_receipt(saved, source, sidecar)
        saved["receipt"]["prompt_sha256"] = "0" * 64
        with self.assertRaisesRegex(SummaryV2Error, "receipt drifted"):
            _require_stage_receipt(saved, source, sidecar)

    def test_parent_rescue_compacts_hierarchically_and_only_resumes_state_receipts(self):
        children = []
        for offset in range(0, 24, 3):
            source = build_level_1_source(self.job(offset=offset))
            children.append(project(source, self.candidate(source)))
        formal = build_parent_source(children, parallel_summary_id="L2-000781")
        maps = []
        for index in range(0, len(children), 2):
            source = build_parent_source(
                children[index : index + 2],
                parallel_summary_id=f"L2-000781-map-{index // 2 + 1}",
            )
            maps.append(project(source, self.parent_candidate(source)))

        output = self.base / "summary-v2"
        state_path = output / "state.json"
        state = {
            "revision": PARENT_RESCUE_REVISION,
            "summary_id": "L2-000781",
            "maps": {},
        }
        before = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }

        def staged_prompt(source):
            map_count = len(source.get("prompt_payload", {}).get("map_sidecars", []))
            return "x" * (900_001 if map_count > 2 else 100)

        def persist_reduction(source, output_directory, archive_root, **kwargs):
            return run_source(
                source,
                output_directory,
                archive_root,
                candidate=self.parent_candidate(source),
                diagnostic_path=kwargs["diagnostic_path"],
                invocation_context=kwargs["invocation_context"],
            )

        with (
            patch("summary_v2_backfill.build_prompt", side_effect=staged_prompt),
            patch(
                "summary_v2_backfill.run_source",
                side_effect=persist_reduction,
            ) as model_calls,
        ):
            model_call_budget = {"maximum": 7, "used": 0}
            reduced = _compact_parent_rescue_maps(
                formal,
                children,
                maps,
                state,
                state_path,
                output,
                self.archive,
                self.base / "config.yaml",
                model_call_budget,
            )
        self.assertEqual(2, len(reduced))
        self.assertEqual(2, model_calls.call_count)
        self.assertEqual(2, model_call_budget["used"])
        self.assertEqual(2, len(state["reductions"]))

        resumed_state = json.loads(state_path.read_text(encoding="utf-8"))
        with (
            patch("summary_v2_backfill.build_prompt", side_effect=staged_prompt),
            patch("summary_v2_backfill.run_source") as resumed_calls,
        ):
            resumed_budget = {
                "maximum": 7,
                "used": len(resumed_state["reductions"]),
            }
            resumed = _compact_parent_rescue_maps(
                formal,
                children,
                maps,
                resumed_state,
                state_path,
                output,
                self.archive,
                self.base / "config.yaml",
                resumed_budget,
            )
        self.assertEqual(
            [item["projection_sha256"] for item in reduced],
            [item["projection_sha256"] for item in resumed],
        )
        resumed_calls.assert_not_called()

        orphan_state = json.loads(state_path.read_text(encoding="utf-8"))
        orphan_state["reductions"] = {}
        with (
            patch("summary_v2_backfill.build_prompt", side_effect=staged_prompt),
            patch("summary_v2_backfill.run_source") as orphan_calls,
            self.assertRaisesRegex(SummaryV2Error, "already dispatched"),
        ):
            _compact_parent_rescue_maps(
                formal,
                children,
                maps,
                orphan_state,
                state_path,
                output,
                self.archive,
                self.base / "config.yaml",
                {
                    "maximum": 7,
                    "used": len(orphan_state.get("reductions", {})),
                },
            )
        orphan_calls.assert_not_called()
        after = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_rescue_normalizer_restores_scene_routes_from_maps(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        records = [*left_job["source_records"], *right_job["source_records"]]
        formal_job = {
            **left_job,
            "job_id": "formal-route-repair",
            "target_summary_id": "L1-000778",
            "source_sha256": _source_sha256(records),
            "source_message_ids": [item["message_id"] for item in records],
            "source_records": records,
        }
        formal = build_level_1_source(formal_job)
        maps = []
        for job in (left_job, right_job):
            source = build_level_1_source(job)
            maps.append(project(source, self.candidate(source)))
        rescue = build_rescue_reduce_source(formal, maps)
        normalization_source = {
            **rescue,
            "_canonical_projection": rescue_model_projection(rescue, maps),
        }
        candidate = self.candidate(rescue)
        lost = rescue["source_refs"][-1]
        candidate["scenes"][0]["source_refs"].remove(lost)
        normalized = normalize_model_candidate(candidate, normalization_source)
        self.assertTrue(
            any(lost in scene["source_refs"] for scene in normalized["scenes"])
        )
        project(normalization_source, normalized)

    def test_rescue_normalizer_restores_a_totally_lost_ref_from_validated_map(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        records = [*left_job["source_records"], *right_job["source_records"]]
        formal_job = {
            **left_job,
            "job_id": "formal-total-loss-repair",
            "target_summary_id": "L1-000779",
            "source_sha256": _source_sha256(records),
            "source_message_ids": [item["message_id"] for item in records],
            "source_records": records,
        }
        formal = build_level_1_source(formal_job)
        maps = []
        lost = formal["source_refs"][-1]
        for job in (left_job, right_job):
            map_source = build_level_1_source(job)
            map_candidate = self.candidate(map_source)
            if lost in map_source["source_refs"]:
                for group in ("overview", "atoms", "retrieval_anchors"):
                    for item in map_candidate[group]:
                        item["source_refs"] = [
                            ref for ref in item["source_refs"] if ref != lost
                        ]
            maps.append(
                project(
                    map_source,
                    normalize_model_candidate(map_candidate, map_source),
                )
            )
        rescue = build_rescue_reduce_source(formal, maps)
        canonical_projection = rescue_model_projection(rescue, maps)
        self.assertFalse(
            any(
                lost in atom["source_refs"]
                for atom in canonical_projection["canonical_atoms"]
            )
        )
        self.assertTrue(
            any(
                lost in route["source_refs"]
                for route in canonical_projection["direct_child_routes"]
            )
        )
        normalization_source = {
            **rescue,
            "_canonical_projection": canonical_projection,
        }
        candidate = self.candidate(rescue)
        for group in ("overview", "scenes", "atoms", "retrieval_anchors"):
            for item in candidate[group]:
                item["source_refs"] = [ref for ref in item["source_refs"] if ref != lost]
        normalized = normalize_model_candidate(candidate, normalization_source)
        self.assertTrue(any(lost in item["source_refs"] for item in normalized["scenes"]))
        self.assertFalse(any(lost in item["source_refs"] for item in normalized["atoms"]))
        self.assertFalse(
            any(
                item["local_id"].startswith("detail_route_")
                for item in normalized["atoms"]
            )
        )
        self.assertFalse(
            any(item["local_id"].startswith("restored_") for item in normalized["scenes"])
        )
        project(normalization_source, normalized)

    def test_l1_rescue_uses_one_canonical_ledger_without_per_ref_restoration(self):
        template = self.job()["source_records"][0]
        records = []
        for index in range(166):
            record = {
                **template,
                "sequence": index + 1,
                "message_id": f"ledger-ref-{index:03d}",
                "round_number": index + 1,
                "text": f"durable fact {index}",
                "completes_round": True,
            }
            records.append(record)

        def make_job(name, group):
            return {
                "format_version": 1,
                "job_id": name,
                "target_summary_id": name,
                "summary_level": 1,
                "conversation_id": template["conversation_id"],
                "source_sha256": _source_sha256(group),
                "source_message_ids": [item["message_id"] for item in group],
                "source_records": group,
            }

        map_jobs = [make_job("map-a", records[:96]), make_job("map-b", records[96:])]
        formal = build_level_1_source(make_job("L1-ledger", records))
        maps = []
        for index, job in enumerate(map_jobs, 1):
            source = build_level_1_source(job)
            candidate = self.candidate(source, include_locator=False)
            candidate["atoms"][0]["statement"] = f"canonical map fact {index}"
            maps.append(project(source, candidate))

        rescue = build_rescue_reduce_source(formal, maps)
        canonical = rescue_model_projection(rescue, maps)
        self.assertEqual(2, len(canonical["canonical_atoms"]))
        self.assertEqual(126, canonical["maximum_model_scenes"])
        normalization_source = {**rescue, "_canonical_projection": canonical}
        candidate = self.candidate(rescue, include_locator=False)
        for group in ("overview", "scenes", "atoms"):
            for item in candidate[group]:
                item["source_refs"] = list(rescue["source_refs"][:96])

        normalized = normalize_model_candidate(candidate, normalization_source)
        self.assertEqual(len(canonical["canonical_atoms"]), len(normalized["atoms"]))
        self.assertEqual(
            len(canonical["canonical_relations"]), len(normalized["relations"])
        )
        self.assertFalse(
            any(
                item["local_id"].startswith("restored_")
                for group in ("scenes", "atoms")
                for item in normalized[group]
            )
        )
        self.assertLessEqual(len(normalized["scenes"]), 3)
        final = project(normalization_source, normalized)
        self.assertEqual(166, final["coverage"]["source_ref_count"])
        self.assertEqual(2, len(final["internal_routes"]))
        self.assertEqual(
            rescue["source_refs"],
            [
                source_ref
                for route in final["internal_routes"]
                for source_ref in route["source_refs"]
            ],
        )
        canonical_statements = {item["statement"] for item in canonical["canonical_atoms"]}
        self.assertTrue(canonical_statements.issubset({item["statement"] for item in final["atoms"]}))

    def test_l1_rescue_routes_ordinary_atom_overflow_inside_one_formal_node(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        records = [*left_job["source_records"], *right_job["source_records"]]
        formal = build_level_1_source(
            {
                **left_job,
                "job_id": "ordinary-overflow",
                "target_summary_id": "L1-ordinary-overflow",
                "source_sha256": _source_sha256(records),
                "source_message_ids": [item["message_id"] for item in records],
                "source_records": records,
            }
        )
        maps = []
        for map_index, job in enumerate((left_job, right_job), 1):
            source = build_level_1_source(job)
            candidate = self.candidate(source)
            candidate["atoms"] = [
                {
                    **candidate["atoms"][0],
                    "local_id": f"atom-{map_index}-{atom_index}",
                    "atom_type": "work_fact",
                    "statement": f"map {map_index} ordinary fact {atom_index}",
                    "epistemic_status": "explicit_fact",
                }
                for atom_index in range(512)
            ]
            maps.append(project(source, candidate))
        rescue = build_rescue_reduce_source(formal, maps)
        canonical = rescue_model_projection(rescue, maps)
        self.assertEqual(1, len(canonical["canonical_atoms"]))
        self.assertEqual(1023, canonical["routed_atom_count"])
        normalization_source = {**rescue, "_canonical_projection": canonical}
        final = project(
            normalization_source,
            normalize_model_candidate(
                self.candidate(rescue, include_locator=False), normalization_source
            ),
        )
        self.assertEqual(2, len(final["internal_routes"]))
        self.assertTrue(
            {item["statement"] for item in canonical["canonical_atoms"]}.issubset(
                {item["statement"] for item in final["atoms"]}
            )
        )
        self.assertFalse(
            any(item["item_id"].startswith("restored_") for item in final["atoms"])
        )

    def test_l1_rescue_routes_durable_atom_overflow_with_bounded_top_state(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        records = [*left_job["source_records"], *right_job["source_records"]]
        formal_job = {
            **left_job,
            "job_id": "canonical-overflow",
            "target_summary_id": "L1-canonical-overflow",
            "source_sha256": _source_sha256(records),
            "source_message_ids": [item["message_id"] for item in records],
            "source_records": records,
        }
        formal = build_level_1_source(formal_job)
        maps = []
        for map_index, job in enumerate((left_job, right_job), 1):
            source = build_level_1_source(job)
            candidate = self.candidate(source)
            candidate["atoms"] = [
                {
                    **candidate["atoms"][0],
                    "local_id": f"atom-{map_index}-{atom_index}",
                    "statement": f"map {map_index} durable fact {atom_index}",
                }
                for atom_index in range(512)
            ]
            maps.append(project(source, candidate))
        rescue = build_rescue_reduce_source(formal, maps)
        canonical = rescue_model_projection(rescue, maps)
        self.assertLessEqual(len(canonical["canonical_atoms"]), 512)
        self.assertGreater(canonical["routed_atom_count"], 0)
        normalization_source = {**rescue, "_canonical_projection": canonical}
        final = project(
            normalization_source,
            normalize_model_candidate(
                self.candidate(rescue, include_locator=False), normalization_source
            ),
        )
        self.assertEqual(2, len(final["internal_routes"]))

    def test_parent_routes_promoted_overflow_and_repromotes_it_to_next_level(self):
        l1_children = []
        for offset in range(0, 24, 3):
            source = build_level_1_source(self.job(offset=offset))
            candidate = self.candidate(source)
            candidate["atoms"] = [
                {
                    **candidate["atoms"][0],
                    "local_id": f"state-{offset}-{index}",
                    "statement": f"durable state {offset}-{index}",
                    "scope": f"scope {offset}-{index}",
                }
                for index in range(75)
            ]
            if offset == 21:
                candidate["relations"] = [
                    {
                        "from_local_id": "state-21-73",
                        "to_local_id": "state-21-74",
                        "relation_type": "revises",
                        "source_refs": list(source["source_refs"]),
                    }
                ]
            l1_children.append(project(source, candidate))
        l2_children = []
        for index in range(0, 8, 2):
            source = build_parent_source(
                l1_children[index : index + 2],
                parallel_summary_id=f"L2-overflow-{index // 2}",
            )
            l2_children.append(
                project(
                    source,
                    normalize_model_candidate(self.parent_candidate(source), source),
                )
            )
        formal = build_parent_source(l2_children, parallel_summary_id="L3-overflow")
        maps = []
        for index in range(0, 4, 2):
            source = build_parent_source(
                l2_children[index : index + 2],
                parallel_summary_id=f"L3-overflow-map-{index // 2}",
            )
            maps.append(
                project(
                    source,
                    normalize_model_candidate(self.parent_candidate(source), source),
                )
            )
        rescue = build_parent_rescue_reduce_source(formal, maps)
        canonical = rescue_model_projection(rescue, maps)
        self.assertEqual(512, len(canonical["canonical_atoms"]))
        self.assertEqual(88, canonical["routed_promoted_count"])
        self.assertEqual(1, canonical["promoted_relation_count"])
        self.assertEqual(1, canonical["routed_promoted_relation_count"])
        normalization_source = {**rescue, "_canonical_projection": canonical}
        final_candidate = self.parent_candidate(rescue)
        final_candidate["atoms"] = []
        final = project(
            normalization_source,
            normalize_model_candidate(final_candidate, normalization_source),
        )
        peer_source = build_parent_source(
            l2_children[:2], parallel_summary_id="L3-repromotion-peer"
        )
        peer = project(
            peer_source,
            normalize_model_candidate(self.parent_candidate(peer_source), peer_source),
        )
        next_level = build_parent_source(
            [final, peer], parallel_summary_id="L4-repromotion-check"
        )
        self.assertEqual(
            600,
            len(
                [
                    item
                    for item in next_level["promotion_manifest"]
                    if item["child_summary_id"] == final["summary_v2_id"]
                ]
            ),
        )
        routed_relations = [
            relation
            for relation in next_level["promotion_relations"]
            if relation["child_summary_id"] == final["summary_v2_id"]
        ]
        self.assertEqual(1, len(routed_relations))
        self.assertEqual("revises", routed_relations[0]["relation_type"])
        self.assertEqual(1, final["delegated_state"]["routed_promoted_relation_count"])

    def test_l1_rescue_rejects_model_resurrection_of_map_omission(self):
        left_job = self.job()
        right_job = self.job(offset=3)
        records = [*left_job["source_records"], *right_job["source_records"]]
        formal = build_level_1_source(
            {
                **left_job,
                "job_id": "omission-resurrection",
                "target_summary_id": "L1-omission-resurrection",
                "source_sha256": _source_sha256(records),
                "source_message_ids": [item["message_id"] for item in records],
                "source_records": records,
            }
        )
        left_source = build_level_1_source(left_job)
        right_source = build_level_1_source(right_job)
        map_candidate = self.candidate(right_source)
        omitted_ref = right_source["source_refs"][-1]
        for group in ("overview", "scenes", "atoms", "retrieval_anchors"):
            for item in map_candidate[group]:
                item["source_refs"] = [
                    source_ref
                    for source_ref in item["source_refs"]
                    if source_ref != omitted_ref
                ]
        map_candidate["retrieval_anchors"] = [
            item for item in map_candidate["retrieval_anchors"] if item["source_refs"]
        ]
        map_candidate["omissions"] = [
            {"source_ref": omitted_ref, "reason": "content-free source event"}
        ]
        maps = [
            project(left_source, self.candidate(left_source)),
            project(right_source, map_candidate),
        ]
        rescue = build_rescue_reduce_source(formal, maps)
        canonical = rescue_model_projection(rescue, maps)
        normalization_source = {**rescue, "_canonical_projection": canonical}

        with self.assertRaisesRegex(
            SummaryV2Error, "rescue model represents map-omitted source refs"
        ):
            normalize_model_candidate(
                self.candidate(rescue, include_locator=False), normalization_source
            )

    def test_normalizer_drops_conflicting_omission_and_invalid_relation(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        represented = source["source_refs"][0]
        candidate["omissions"] = [{"source_ref": represented, "reason": "conflict"}]
        candidate["relations"] = [
            {
                "from_local_id": "missing",
                "to_local_id": "missing",
                "relation_type": "supports",
                "source_refs": [represented],
            }
        ]
        normalized = normalize_model_candidate(candidate, source)
        self.assertEqual([], normalized["omissions"])
        self.assertEqual([], normalized["relations"])

    def test_normalizer_does_not_fabricate_semantic_atoms_for_scene_routes(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        missing = source["source_refs"][-1]
        candidate["atoms"][0]["source_refs"].remove(missing)
        normalized = normalize_model_candidate(candidate, source)
        self.assertFalse(any(missing in atom["source_refs"] for atom in normalized["atoms"]))
        self.assertTrue(any(missing in scene["source_refs"] for scene in normalized["scenes"]))
        project(source, normalized)

    def test_l1_prompts_share_the_scene_route_contract(self):
        prompts = [
            (ROOT / "prompts" / name).read_text(encoding="utf-8")
            for name in ("summarize-v2.md", "summarize-v2-rescue-reduce.md")
        ]
        for prompt in prompts:
            normalized = " ".join(prompt.split())
            self.assertIn("ordinary detail may remain", normalized)
            self.assertIn("scene", normalized)
            self.assertNotIn(
                "Every represented ref must appear in a scene "
                "and in an atom or required retrieval anchor",
                normalized,
            )

    def test_model_candidate_normalization_is_conservative_and_deterministic(self):
        source = build_level_1_source(self.job())
        candidate = self.candidate(source)
        candidate["scenes"][0]["title"] = "  可追溯摘要设计与核验  "
        candidate["scenes"][0]["source_refs"] = list(reversed(source["source_refs"]))
        candidate["atoms"][0]["source_refs"] = list(reversed(source["source_refs"]))
        candidate["atoms"][0]["source_refs"][0] += "a3327706528e4bb45ed549d441ded0f"
        candidate["relations"] = [
            {
                "from_local_id": "atom1",
                "to_local_id": "atom1",
                "relation_type": "supports",
                "source_refs": ["outside-source"],
            }
        ]
        candidate["retrieval_anchors"] = [
            {
                "local_id": "badAnchor",
                "text": "  changed locator  ",
                "kind": "other",
                "source_refs": [source["source_refs"][0]],
            }
        ]
        normalized = normalize_model_candidate(candidate, source)
        self.assertEqual("可追溯摘要设计与核验", normalized["scenes"][0]["title"])
        self.assertEqual(source["source_refs"], normalized["scenes"][0]["source_refs"])
        self.assertEqual(source["source_refs"], normalized["atoms"][0]["source_refs"])
        self.assertFalse(normalized["relations"])
        self.assertEqual(
            [item["text"] for item in source["required_locators"]],
            [item["text"] for item in normalized["retrieval_anchors"]],
        )
        project(source, normalized)

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

    def test_worker_persists_structured_invocation_failure_diagnostic(self):
        source = build_level_1_source(self.job())
        config = self.base / "config.yaml"
        config.write_text("ai_summary:\n  timeout_seconds: 30\n", encoding="utf-8")
        diagnostic = self.base / "diagnostics" / "call.json"

        def failing_invoker(command, timeout, prompt):
            raise SummaryV2Error("fixture network failure with complete evidence")

        with self.assertRaisesRegex(SummaryV2Error, "fixture network failure"):
            run_source(
                source,
                self.base / "worker-output",
                self.archive,
                config_path=config,
                invoker=failing_invoker,
                diagnostic_path=diagnostic,
                invocation_context={"revision": "fixture-v2", "stage": "map-001"},
            )
        receipt = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual("failed", receipt["status"])
        self.assertTrue(receipt["model_called"])
        self.assertEqual("fixture-v2", receipt["revision"])
        self.assertEqual(64, len(receipt["prompt_sha256"]))

    def test_real_cli_candidate_path_handles_multilingual_paths(self):
        job = self.job()
        source = build_level_1_source(job)
        long_root = self.base / " - ".join(["长路径 日本語 ¥ 😀"] * 3)
        long_root.mkdir()
        job_path = long_root / "-任务 ¥ 😀.json"
        candidate_path = long_root / "候选 日本語.json"
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
                str(long_root / "输出 sidecar"),
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

    def test_worker_cli_cannot_dispatch_a_model(self):
        job_path = self.base / "job.json"
        job_path.write_text(json.dumps(self.job()), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "summary_v2_worker.py"),
                "l1",
                "--job",
                str(job_path),
                "--config",
                str(self.base / "config.yaml"),
                "--output-dir",
                str(self.base / "output"),
                "--archive-root",
                str(self.archive),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("standalone worker CLI cannot dispatch a model", completed.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows extended-length path regression")
    def test_persist_sidecar_uses_native_windows_path_beyond_max_path(self):
        source = build_level_1_source(self.job())
        sidecar = project(source, self.candidate(source))
        output = self.base / ("长路径-日本語-" * 18)
        temporary_path = (
            output
            / FORMAT
            / "level-1"
            / (f".{sidecar['summary_v2_id']}." + "x" * 8)
            / "summary.json"
        )
        self.assertGreater(len(str(temporary_path)), 259)

        bundle, status = persist_sidecar(sidecar, output, self.archive)

        self.assertEqual("created", status)
        self.assertEqual(sidecar, json.loads((bundle / "summary.json").read_text(encoding="utf-8")))
        (bundle / "summary.json").unlink()
        (bundle / "summary.md").unlink()
        bundle.rmdir()
        bundle.parent.rmdir()
        bundle.parent.parent.rmdir()
        bundle.parent.parent.parent.rmdir()

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
        children = [
            project(first_source, self.candidate(first_source)),
            project(second_source, self.candidate(second_source)),
        ]
        lower_message_ids = {
            message_id
            for child in children
            for message_id in child["source"]["raw_message_ids"]
        }
        parent_source = build_parent_source(children)
        self.assertTrue(parent_source["compact_parent_prompt"])
        parent_prompt = build_prompt(parent_source)
        self.assertIn("lossless navigation layer", parent_prompt)
        self.assertIn("promotion_manifest", parent_prompt)
        self.assertNotIn("raw_message_manifest", parent_prompt)
        self.assertNotIn("source_message_ids", parent_prompt)
        payload = json.loads(parent_prompt.rstrip().rsplit("\n", 1)[-1])
        serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for message_id in lower_message_ids:
            self.assertNotIn(message_id, parent_prompt)
            self.assertNotIn(message_id, serialized_payload)
        self.assertEqual(
            {"kind", "children", "promotion_manifest", "promotion_relations"},
            set(payload["source_manifest"]),
        )
        self.assertEqual(
            {"direct_child_routes"}, set(payload["source_payload"])
        )
        for child in payload["source_payload"]["direct_child_routes"]:
            self.assertEqual(
                {
                    "summary_v2_id",
                    "summary_level",
                    "projection_sha256",
                    "source_sha256",
                    "orientation",
                    "coverage",
                },
                set(child),
            )
            self.assertNotIn("atoms", child)
            self.assertNotIn("relations", child)
            self.assertNotIn("omissions", child)
            self.assertNotIn("internal_routes", child)
        full_source = dict(parent_source)
        full_source["compact_parent_prompt"] = False
        full_prompt = build_prompt(full_source)
        self.assertLess(len(parent_prompt.encode("utf-8")), len(full_prompt.encode("utf-8")))
        self.assertIn(parent_source["source_sha256"], parent_prompt)
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
        required_atom_fields = {
            "local_id",
            "atom_type",
            "statement",
            "epistemic_status",
            "scope",
            "source_refs",
        }
        for result_schema in (schema, parent_schema):
            atom_variants = result_schema["$defs"]["atom"]["anyOf"]
            self.assertEqual(4, len(atom_variants))
            self.assertEqual(
                {"work_fact", "work_task", "work_method", "work_artifact"},
                {
                    variant["properties"]["atom_type"]["const"]
                    for variant in atom_variants
                },
            )
            for variant in atom_variants:
                self.assertEqual("object", variant["type"])
                self.assertFalse(variant["additionalProperties"])
                self.assertEqual(required_atom_fields, set(variant["required"]))
                self.assertEqual(required_atom_fields, set(variant["properties"]))
                self.assertEqual("string", variant["properties"]["atom_type"]["type"])
                self.assertEqual("string", variant["properties"]["epistemic_status"]["type"])
            pending = [schema]
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    self.assertNotIn("uniqueItems", node)
                    if node.get("type") == "array":
                        self.assertIn("items", node)
                    pending.extend(node.values())
                elif isinstance(node, list):
                    pending.extend(node)
        for path in (
            ROOT / "scripts" / "semantic_worker.py",
            ROOT / "scripts" / "semantic_dispatch.py",
            ROOT / "scripts" / "maintenance_supervisor.py",
        ):
            # The isolated runtime excludes the old standalone V1 orchestration.
            # Its original boundary test is retained in the frozen source descriptor.
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
