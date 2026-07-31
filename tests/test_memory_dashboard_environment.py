from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_dashboard import (
    EnvironmentDashboardCache,
    _bounded_archive_files,
    make_handler,
)
from memory_environment import EnvironmentRegistry, revision_id_for
from memory_environment_conflicts import EnvironmentConflictStore
from memory_environment_promotions import PromotionStore
from memory_environment_profiles import EnvironmentProfileManager


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class EnvironmentDashboardCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.temporary.name) / "memory"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_archive_scan_rejects_link_like_root_before_traversal(self) -> None:
        root = self.archive_root / "raw"
        root.mkdir(parents=True)
        with patch("memory_dashboard.is_link_like", return_value=True):
            with self.assertRaisesRegex(ValueError, "root is a link or junction"):
                _bounded_archive_files([root])

    def test_signature_revalidates_authority_paths(self) -> None:
        self.fixture_environment()
        registry_path = self.archive_root / "environment" / "registry.json"
        original = sys.modules["memory_environment"].is_link_like
        with patch(
            "memory_environment.is_link_like",
            side_effect=lambda path: Path(path) == registry_path or original(path),
        ):
            with self.assertRaisesRegex(ValueError, "symlink path"):
                EnvironmentDashboardCache(self.archive_root).source_signature()

    def fixture_environment(self) -> None:
        content = "Shared rule\n"
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        artifact = {
            "schema_version": 1,
            "artifact_id": "global-rule:codex-agents",
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Codex shared rules",
            "created_at": "2026-07-28T00:00:00+00:00",
        }
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + ("0" * 64),
            "artifact_id": artifact["artifact_id"],
            "origin_node_id": "mac-mini-lab",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": content_sha256,
            "object_path": (
                f"objects/sha256/{content_sha256[:2]}/{content_sha256[2:]}"
            ),
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.12"},
            "provenance": {"source": "dashboard-fixture"},
            "lifecycle_state": "installed",
            "created_at": "2026-07-28T00:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        project = {
                "schema_version": 1,
                "project_id": "orf1-library",
                "display_name": "ORF1 Library",
                "local_root": "/projects/orf1",
                "active": True,
                "rule_bindings": [],
                "skill_bindings": [],
        }
        EnvironmentRegistry(self.archive_root).register(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "artifact": artifact,
                        "revision": revision,
                        "content": content,
                    }
                ],
                "projects": [project],
            },
            apply=True,
        )
        environment = self.archive_root / "environment"
        EnvironmentConflictStore(self.archive_root).assess(
            {
                "artifact_id": artifact["artifact_id"],
                "object_class": artifact["object_class"],
                "base_revision_id": None,
                "local_revision_id": revision["revision_id"],
                "remote_revision_id": "rev:" + "f" * 64,
                "base_content_sha256": None,
                "local_content_sha256": content_sha256,
                "remote_content_sha256": "f" * 64,
                "local_changed_blocks": ["shared"],
                "remote_changed_blocks": ["shared"],
                "local_deleted": False,
                "remote_deleted": False,
                "unregistered_local_change": False,
                "project_identity_ambiguous": False,
                "platform_incompatible": False,
                "permission_expansion": False,
                "network_expansion": False,
            },
            apply=True,
        )
        PromotionStore(self.archive_root).propose(
            {
                "schema_version": 1,
                "promotion_id": "promotion-1",
                "source_project_id": "orf1-library",
                "source_skill_id": "orf1-helper",
                "source_capability": "shared-helper",
                "classification": "extension",
                "proposed_global_owner": "global-helper",
                "interface_contract": {"input": "task"},
                "retained_project_adapter": {"owner": "orf1-helper"},
                "provenance": {"source_revision": revision["revision_id"]},
                "validation_matrix": [
                    {
                        "name": "candidate-evidence",
                        "status": "pass",
                        "evidence": "fixture",
                    }
                ],
                "review_state": "discovered",
                "approval": {
                    "required": True,
                    "approved": False,
                    "approved_at": None,
                    "evidence": None,
                },
            },
            apply=True,
        )
        write_json(
            environment / "receipts" / "install-1.json",
            {
                "schema_version": 1,
                "receipt_id": "install-1",
                "artifact_id": artifact["artifact_id"],
                "revision_id": revision["revision_id"],
                "content_sha256": content_sha256,
                "target_node_id": "mac-mini-lab",
                "target_binding": "/Users/test/.codex/AGENTS.md",
                "previous_installed_sha256": None,
                "final_installed_sha256": content_sha256,
                "rehearsal": {"status": "passed", "checks": ["read-only-fixture"]},
                "result": "installed",
                "rollback": {"available": True, "revision_id": None},
                "created_at": "2026-07-28T00:02:00+00:00",
            },
        )

    def test_uninitialized_inventory_is_fast_empty_and_creates_nothing(self) -> None:
        cache = EnvironmentDashboardCache(self.archive_root)
        payload = cache.get()

        self.assertFalse(payload["initialized"])
        self.assertEqual(payload["validation_status"], "not-initialized")
        self.assertIsNone(payload["validation_error"])
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(payload["projects"], [])
        self.assertEqual(
            {key: value["count"] for key, value in payload["object_classes"].items()},
            {
                "global-rule": 0,
                "project-rule": 0,
                "global-skill": 0,
                "project-skill": 0,
                "global-runtime-contract": 0,
            },
        )
        self.assertFalse(self.archive_root.exists())

    def test_registered_fixture_exposes_only_persisted_states(self) -> None:
        self.fixture_environment()
        receipt = json.loads(
            (
                self.archive_root
                / "environment"
                / "receipts"
                / "install-1.json"
            ).read_text(encoding="utf-8")
        )
        receipt_schema = json.loads(
            (
                SKILL_ROOT / "schemas" / "environment-receipt.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(receipt_schema["additionalProperties"])
        self.assertEqual(set(receipt_schema["required"]), set(receipt))
        for field in (
            "revision_id",
            "content_sha256",
            "previous_installed_sha256",
            "final_installed_sha256",
        ):
            value = receipt[field]
            pattern = receipt_schema["properties"][field].get("pattern")
            if value is not None and pattern:
                self.assertRegex(value, pattern)
        self.assertIn(
            receipt["result"], receipt_schema["properties"]["result"]["enum"]
        )

        payload = EnvironmentDashboardCache(self.archive_root).get()

        self.assertTrue(payload["initialized"])
        self.assertEqual(payload["validation_status"], "valid")
        self.assertIsNone(payload["validation_error"])
        self.assertEqual(payload["object_classes"]["global-rule"]["count"], 1)
        self.assertEqual(payload["object_classes"]["global-skill"]["count"], 0)
        self.assertEqual(payload["projects"][0]["project_id"], "orf1-library")
        artifact = payload["artifacts"][0]
        self.assertEqual(artifact["display_name"], "Codex shared rules")
        self.assertEqual(artifact["version"], 1)
        self.assertIsNone(artifact["base_revision_id"])
        self.assertEqual(artifact["lifecycle_state"], "installed")
        self.assertEqual(artifact["installation_status"], "installed")
        self.assertEqual(artifact["conflict_status"], "pending-review")
        self.assertIsNone(artifact["promotion_status"])
        self.assertEqual(len(payload["conflicts"]), 1)
        self.assertEqual(len(payload["promotions"]), 1)
        self.assertEqual(len(payload["installations"]), 1)

    def test_cache_invalidates_only_for_environment_inventory_changes(self) -> None:
        self.fixture_environment()
        cache = EnvironmentDashboardCache(self.archive_root)
        build_calls = 0
        original_build = cache.build

        def counted_build() -> dict:
            nonlocal build_calls
            build_calls += 1
            return original_build()

        cache.build = counted_build  # type: ignore[method-assign]
        cache.get()
        cache.get()
        self.assertEqual(build_calls, 1)

        raw = self.archive_root / "raw" / "2026-07-28.md"
        raw.parent.mkdir(parents=True)
        raw.write_text("archive-only change\n", encoding="utf-8")
        cache.get()
        self.assertEqual(build_calls, 1)

        content_object = next(
            (self.archive_root / "environment" / "objects").rglob("*")
        )
        while not content_object.is_file():
            content_object = next(
                path
                for path in (self.archive_root / "environment" / "objects").rglob("*")
                if path.is_file()
            )
        content_object.touch()
        cache.get()
        self.assertEqual(build_calls, 2)

        state = self.archive_root / "environment" / "state.json"
        state.touch()
        cache.get()
        self.assertEqual(build_calls, 3)

        registry = self.archive_root / "environment" / "registry.json"
        registry.touch()
        cache.get()
        self.assertEqual(build_calls, 4)

    def test_revoked_peer_is_removed_from_trusted_profile_list_and_cache(self) -> None:
        self.fixture_environment()
        peer_path = self.archive_root / "federation" / "peers" / "node-peer.json"
        write_json(
            peer_path,
            {"format_version": 1, "node_id": "node-peer", "trusted": True},
        )
        write_json(
            self.archive_root
            / "environment"
            / "replicas"
            / "peers"
            / "node-peer"
            / "profiles"
            / ("a" * 64 + ".json"),
            {"replica": True},
        )
        cache = EnvironmentDashboardCache(self.archive_root)
        self.assertEqual(
            cache.get()["profiles"]["peer_profiles"],
            [{"node_id": "node-peer", "profile_count": 1}],
        )
        write_json(
            peer_path,
            {"format_version": 1, "node_id": "node-peer", "trusted": False},
        )
        self.assertEqual(cache.get()["profiles"]["peer_profiles"], [])

    def test_inventory_read_does_not_modify_environment_or_archive_files(self) -> None:
        self.fixture_environment()
        state = self.archive_root / "state.json"
        raw = self.archive_root / "raw" / "2026-07-28.md"
        write_json(state, {"format_version": 1})
        raw.parent.mkdir(parents=True)
        raw.write_text("authoritative archive\n", encoding="utf-8")
        before = file_hashes(self.archive_root)

        EnvironmentDashboardCache(self.archive_root).get()

        self.assertEqual(file_hashes(self.archive_root), before)
        self.assertFalse(
            (self.archive_root / "dashboard" / "environment-status-snapshot.json").exists()
        )

    def test_environment_api_is_read_only_and_independent(self) -> None:
        self.fixture_environment()
        store = type("Store", (), {"root": self.archive_root})()
        before = file_hashes(self.archive_root)
        handler_class = make_handler(store)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/environment"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        handler.do_GET()

        payload = json.loads(handler.wfile.getvalue())
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Cache-Control", "no-store")
        self.assertTrue(payload["initialized"])
        self.assertEqual(file_hashes(self.archive_root), before)

    def test_corrupt_registry_returns_attention_payload_without_writes(self) -> None:
        self.fixture_environment()
        registry = self.archive_root / "environment" / "registry.json"
        registry.write_text("{not valid json\n", encoding="utf-8")
        before = file_hashes(self.archive_root)

        cache_payload = EnvironmentDashboardCache(self.archive_root).get()

        self.assertTrue(cache_payload["initialized"])
        self.assertEqual(cache_payload["validation_status"], "needs-attention")
        self.assertTrue(cache_payload["validation_error"])
        self.assertEqual(cache_payload["artifacts"], [])
        self.assertEqual(file_hashes(self.archive_root), before)

        store = type("Store", (), {"root": self.archive_root})()
        handler_class = make_handler(store)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/environment"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        api_payload = json.loads(handler.wfile.getvalue())

        handler.send_response.assert_called_once_with(200)
        self.assertTrue(api_payload["initialized"])
        self.assertEqual(api_payload["validation_status"], "needs-attention")
        self.assertTrue(api_payload["validation_error"])
        self.assertEqual(file_hashes(self.archive_root), before)

    def test_profile_summary_is_read_only_and_invalidates_environment_cache(self) -> None:
        skill = Path(self.temporary.name) / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8"
        )
        rule = Path(self.temporary.name) / "AGENTS.md"
        rule.write_text("global\n", encoding="utf-8")
        manager = EnvironmentProfileManager(self.archive_root)
        manager.capture(
            {
                "schema_version": 1,
                "platform": "windows",
                "skills": [{
                    "installation_id": "skill:demo",
                    "provider_type": "user-managed",
                    "provider_id": "user",
                    "applicable_platforms": ["windows"],
                    "root": str(skill),
                }],
                "rules": [{"rule_id": "global-agents", "path": str(rule)}],
            },
            apply=True,
        )
        before = file_hashes(self.archive_root)
        payload = EnvironmentDashboardCache(self.archive_root).get()
        self.assertEqual(payload["profiles"]["generation_count"], 1)
        self.assertEqual(payload["profiles"]["export_event_count"], 1)
        self.assertIsNotNone(payload["profiles"]["current"])
        self.assertEqual(file_hashes(self.archive_root), before)

    def test_frontend_has_environment_view_without_changing_archive_views(self) -> None:
        html = (SKILL_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        for marker in (
            'id="archive-view"',
            'id="active-tab"',
            'id="archived-tab"',
            'id="environment-view-tab"',
            'id="environment-view"',
            "fetch('/api/environment'",
            "a.display_name||a.artifact_id",
            "d.validation_status==='needs-attention'",
            "@media(max-width:850px)",
            "@media(max-width:480px)",
            ".conversation,.device-row,.environment-row{grid-template-columns:1fr 1fr}",
            ".conversation,.environment-row{grid-template-columns:1fr}",
            'id="environment-profiles"',
            'id="environment-profile-peer"',
            "fetch(`/api/environment-profile?peer_node_id=",
            ".environment-overview{grid-template-columns:1fr}",
            "planned?.artifact?` / `+esc(planned.artifact.artifact_id)",
        ):
            self.assertIn(marker, html)
        for restored in ("経路", "本机路径", "配置路径", "第一枚路标", "深层通路", "深層の経路"):
            self.assertIn(restored, html)


if __name__ == "__main__":
    unittest.main()
