import hashlib
import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from unittest.mock import Mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_environment import revision_id_for
from memory_environment_bindings import EnvironmentBindingRegistry
from memory_environment_exchange import EnvironmentExchangeManager
from memory_cloud_transport import (
    AuthenticatedOpenResult,
    _AUTHENTICATED_OPEN_AUTHORITY,
)
from memory_environment_profiles import EnvironmentProfileManager
from memory_environment_profiles import ID_RE, _safe_public_string
from memory_federation import FederationManager, canonical_sha256


def verified_import(manager, bundle, expected_node_id=None):
    manifest = manager.read_bundle_manifest(bundle)
    return manager._import_authenticated_delta(
        bundle,
        expected_node_id=expected_node_id,
        authenticated_open_result=AuthenticatedOpenResult(
            _AUTHENTICATED_OPEN_AUTHORITY,
            {
                "origin_node_id": manifest["origin_node_id"],
                "target_node_id": manager.node()["node_id"],
                "payload_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            },
        ),
    )


from memory_dashboard import make_handler


class EnvironmentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.skill = self.base / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
        self.rule = self.base / "AGENTS.md"
        self.rule.write_text(
            "before\n<!-- memory-wuxian:managed-block:demo:begin -->\nrule\n"
            "<!-- memory-wuxian:managed-block:demo:end -->\nafter\n",
            encoding="utf-8",
        )
        self.archive = self.base / "archive"
        self.manager = EnvironmentProfileManager(self.archive)

    def tearDown(self):
        native = str(self.base.resolve())
        if os.name == "nt":
            native = "\\\\?\\" + native
        shutil.rmtree(native, ignore_errors=True)
        self.temporary.cleanup()

    def spec(self, platform="windows"):
        return {
            "schema_version": 1,
            "platform": platform,
            "skills": [
                {
                    "installation_id": "skill:demo",
                    "provider_type": "user-managed",
                    "provider_id": "user",
                    "applicable_platforms": ["windows", "macos"],
                    "declared_version": "1.0.0",
                    "root": str(self.skill),
                }
            ],
            "rules": [{"rule_id": "global-agents", "path": str(self.rule)}],
        }

    def test_mw210_01_capture_is_deterministic_and_deduplicated(self):
        preview = self.manager.capture(self.spec())
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(self.manager.root.exists())
        first = self.manager.capture(self.spec(), apply=True)
        second = self.manager.capture(self.spec(), apply=True)
        self.assertEqual(first["generation"]["generation_id"], second["generation_id"])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(self.manager.status()["generation_count"], 1)
        self.assertEqual(self.manager.status()["export_event_count"], 1)
        encoded = json.dumps(first["profile"], ensure_ascii=False)
        self.assertNotIn(str(self.base), encoded)
        self.assertNotIn(os.environ.get("USERNAME", "__missing__"), encoded)

    def test_mw210_02_skill_and_managed_rule_changes_are_visible_cache_is_ignored(self):
        original = self.manager.capture(self.spec(), apply=True)["profile"]
        cache = self.skill / "__pycache__"
        cache.mkdir()
        (cache / "ignored.pyc").write_bytes(b"ignored")
        ignored = self.manager.capture(self.spec(), apply=True)["profile"]
        self.assertEqual(original["profile_sha256"], ignored["profile_sha256"])
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: Changed demo\n---\n", encoding="utf-8")
        changed_skill = self.manager.capture(self.spec(), apply=True)["profile"]
        self.assertNotEqual(original["skills"][0]["tree_sha256"], changed_skill["skills"][0]["tree_sha256"])
        self.rule.write_text(self.rule.read_text(encoding="utf-8").replace("rule", "changed rule"), encoding="utf-8")
        changed_rule = self.manager.capture(self.spec(), apply=True)["profile"]
        self.assertNotEqual(changed_skill["rules"][0]["content_sha256"], changed_rule["rules"][0]["content_sha256"])
        self.assertNotEqual(changed_skill["rules"][0]["managed_blocks"][0]["content_sha256"], changed_rule["rules"][0]["managed_blocks"][0]["content_sha256"])

    def test_mw210_03_unknown_duplicate_non_utf8_and_credentials_fail_before_generation(self):
        bad = self.spec()
        bad["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.manager.capture(bad, apply=True)
        duplicate = self.spec()
        duplicate["skills"].append(dict(duplicate["skills"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.manager.capture(duplicate, apply=True)
        self.rule.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.manager.capture(self.spec(), apply=True)
        self.rule.write_text("ok", encoding="utf-8")
        (self.skill / ".env").write_text("TOKEN=secret", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "credential"):
            self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status()["generation_count"], 0)

    def test_mw210_provider_identity_and_skill_name_are_bound(self):
        mismatched_provider = self.spec()
        mismatched_provider["skills"][0]["provider_type"] = "plugin-managed"
        with self.assertRaisesRegex(ValueError, "provider type and identity disagree"):
            self.manager.capture(mismatched_provider, apply=True)
        mismatched_name = self.spec()
        mismatched_name["skills"][0]["installation_id"] = "skill:not-demo"
        with self.assertRaisesRegex(ValueError, "does not match SKILL.md name"):
            self.manager.capture(mismatched_name, apply=True)
        self.assertFalse(self.manager.current_path.exists())

    def test_mw210_04_links_fail_closed(self):
        target = self.base / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        link = self.skill / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "link"):
            self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status()["generation_count"], 0)

    def test_mw210_05_platform_inapplicable_is_evidence_not_copy_request(self):
        spec = self.spec("linux")
        spec["skills"][0].pop("root")
        profile = self.manager.capture(spec, apply=True)["profile"]
        item = profile["skills"][0]
        self.assertEqual(item["inventory_status"], "incomplete")
        self.assertEqual(item["incomplete_reason"], "platform-inapplicable")

    def test_mw210_17_link_like_entry_is_rejected_independently_of_os_privileges(self):
        linked = self.skill / "junction"
        linked.mkdir()
        original = __import__("memory_environment_profiles").is_link_like

        def classify(path):
            return Path(path) == linked or original(path)

        with mock.patch("memory_environment_profiles.is_link_like", side_effect=classify):
            with self.assertRaisesRegex(ValueError, "link or junction"):
                self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status()["generation_count"], 0)

    def test_mw210_environment_root_and_embedded_local_identity_fail_closed(self):
        registry = self.manager.registry
        registry.root.mkdir(parents=True)
        original = __import__("memory_environment").is_link_like

        def classify(path):
            return Path(path) == registry.root or original(path)

        with mock.patch("memory_environment.is_link_like", side_effect=classify):
            with self.assertRaisesRegex(ValueError, "environment root"):
                registry._resolve_relative("profiles", "profile root", for_write=True)
        with mock.patch.dict(os.environ, {"COMPUTERNAME": "private-host"}):
            with self.assertRaisesRegex(ValueError, "local user or host identity"):
                _safe_public_string(
                    "skill:x-private-host-addon", "identity", ID_RE, 192
                )
        staging = registry.root / "staging"
        staging.mkdir(exist_ok=True)

        def classify_child(path):
            return Path(path) == staging or original(path)

        with mock.patch(
            "memory_environment.is_link_like", side_effect=classify_child
        ):
            with self.assertRaisesRegex(ValueError, "layout"):
                registry.init()
        registry.registry_path.write_text("{}\n", encoding="utf-8")

        def classify_authority(path):
            return Path(path) == registry.registry_path or original(path)

        with mock.patch(
            "memory_environment.is_link_like", side_effect=classify_authority
        ):
            with self.assertRaisesRegex(ValueError, "symlink path"):
                registry.init()

    def test_mw210_06_tampered_generation_fails(self):
        captured = self.manager.capture(self.spec(), apply=True)
        generation_sha = captured["generation"]["generation_id"].split(":", 1)[1]
        path = self.manager.generations / f"{generation_sha}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["profile"]["platform"] = "macos"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.manager.current()

    def test_mw210_10_a_b_a_creates_a_new_linked_generation_and_pointer_rebuilds(self):
        first = self.manager.capture(self.spec(), apply=True)["generation"]
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: B\n---\n", encoding="utf-8")
        second = self.manager.capture(self.spec(), apply=True)["generation"]
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
        third = self.manager.capture(self.spec(), apply=True)["generation"]
        self.assertEqual(first["profile_sha256"], third["profile_sha256"])
        self.assertNotEqual(first["generation_id"], third["generation_id"])
        self.assertEqual(second["generation_id"], third["previous_generation_id"])
        self.manager.current_path.unlink()
        preview = self.manager.rebuild_current()
        self.assertEqual(preview["current"]["generation_id"], third["generation_id"])
        self.assertFalse(self.manager.current_path.exists())
        self.manager.rebuild_current(apply=True)
        self.assertEqual(self.manager.current()["profile_id"], third["profile"]["profile_id"])

    def test_mw210_11_malformed_metadata_and_bounded_tree_fail_without_partial_state(self):
        (self.skill / "SKILL.md").write_text("name: demo\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            self.manager.capture(self.spec(), apply=True)
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
        (self.skill / "large.bin").write_bytes(b"12345")
        with mock.patch("memory_environment_profiles.MAX_SKILL_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "limits"):
                self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status()["generation_count"], 0)

    def test_profile_event_log_is_bounded_before_parsing(self):
        self.manager.capture(self.spec(), apply=True)
        with mock.patch("memory_environment_profiles.MAX_PROFILE_EVENT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "event log exceeds size limit"):
                self.manager.status()

    def test_generation_scan_counts_non_json_entries(self):
        self.manager.capture(self.spec(), apply=True)
        (self.manager.generations / "noise-a.tmp").write_text("a", encoding="utf-8")
        (self.manager.generations / "noise-b.tmp").write_text("b", encoding="utf-8")
        with mock.patch("memory_environment_profiles.MAX_GENERATIONS", 2):
            with self.assertRaisesRegex(ValueError, "generation scan exceeds limit"):
                self.manager.status()

    def test_mw210_12_unreadable_skill_file_fails_without_partial_state(self):
        original = Path.read_bytes

        def guarded(path):
            if path.name == "SKILL.md":
                raise PermissionError("denied")
            return original(path)

        with mock.patch.object(Path, "read_bytes", guarded):
            with self.assertRaisesRegex(ValueError, "unreadable"):
                self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status()["generation_count"], 0)

    def test_mw210_22_crlf_metadata_and_receiver_independent_validation(self):
        (self.skill / "SKILL.md").write_bytes(
            b"---\r\nname: demo\r\ndescription: Demo\r\nversion: 1.0.0\r\n---\r\n"
        )
        profile = self.manager.capture(self.spec())["profile"]
        with mock.patch.dict(os.environ, {"USERNAME": "user", "COMPUTERNAME": "demo-host"}):
            self.assertEqual(
                EnvironmentProfileManager.validate_profile(profile)["profile_id"],
                profile["profile_id"],
            )

    def test_mw210_23_sensitive_paths_and_local_identity_fail_before_write(self):
        for relative in (".env.local", "credentials/api.json", ".ssh/id_test", "token.txt"):
            target = self.skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "credential"):
                self.manager.capture(self.spec(), apply=True)
            target.unlink()
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\nversion: ghp_abcdefghijklmnopqrstuvwxyz\n---\n",
            encoding="utf-8",
        )
        spec = self.spec()
        spec["skills"][0].pop("declared_version")
        with self.assertRaisesRegex(ValueError, "secret material"):
            self.manager.capture(spec, apply=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8"
        )
        local_identity = self.spec()
        local_identity["skills"][0]["installation_id"] = "skill:private-host"
        with mock.patch.dict(os.environ, {"COMPUTERNAME": "private-host"}):
            with self.assertRaisesRegex(ValueError, "local user or host identity"):
                self.manager.capture(local_identity, apply=True)
        self.assertFalse(self.manager.current_path.exists())

    def test_mw210_24_missing_pointer_requires_rebuild_and_failed_commit_rolls_back(self):
        self.manager.capture(self.spec(), apply=True)
        self.manager.current_path.unlink()
        with self.assertRaisesRegex(ValueError, "rebuild current"):
            self.manager.capture(self.spec(), apply=True)
        self.manager.rebuild_current(apply=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: changed\n---\n", encoding="utf-8"
        )
        before = self.manager.status()
        with mock.patch("memory_environment_profiles._atomic_pointer", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.manager.capture(self.spec(), apply=True)
        self.assertEqual(self.manager.status(), before)

    def test_mw210_26_unchanged_capture_and_status_reject_corrupt_history(self):
        self.manager.capture(self.spec(), apply=True)
        events = self.manager.events_path.read_bytes()
        self.manager.events_path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "event sets differ"):
            self.manager.capture(self.spec(), apply=True)
        self.manager.events_path.write_bytes(events)

        other = EnvironmentProfileManager(self.base / "other-archive")
        other_spec = self.spec()
        other_spec["skills"][0]["declared_version"] = "2.0.0"
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: other\nversion: 2.0.0\n---\n",
            encoding="utf-8",
        )
        other_generation = other.capture(other_spec, apply=True)["generation"]
        digest = other_generation["generation_id"].split(":", 1)[1]
        shutil.copy2(other.generations / f"{digest}.json", self.manager.generations)
        with self.assertRaisesRegex(ValueError, "unique complete head"):
            self.manager.status()

    def test_mw210_27_interrupted_profile_transaction_recovers_on_next_apply(self):
        with mock.patch(
            "memory_environment_profiles.atomic_write_jsonl",
            side_effect=KeyboardInterrupt("after-generation"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.capture(self.spec(), apply=True)
        self.assertTrue(self.manager.transaction_path.exists())
        self.assertEqual(len(list(self.manager.generations.glob("*.json"))), 1)
        recovered = self.manager.capture(self.spec(), apply=True)
        self.assertEqual(recovered["status"], "created")
        self.assertFalse(self.manager.transaction_path.exists())
        self.assertEqual(self.manager.status()["generation_count"], 1)

        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: next\n---\n", encoding="utf-8"
        )
        with mock.patch(
            "memory_environment_profiles._atomic_pointer",
            side_effect=KeyboardInterrupt("after-event"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.capture(self.spec(), apply=True)
        self.assertTrue(self.manager.transaction_path.exists())
        recovered = self.manager.capture(self.spec(), apply=True)
        self.assertEqual(recovered["status"], "created")
        self.assertFalse(self.manager.transaction_path.exists())
        self.assertEqual(self.manager.status()["generation_count"], 2)

    def test_mw210_13_raw_history_is_unchanged_by_capture_compare_and_plan(self):
        raw = self.archive / "raw" / "2026-08-01.jsonl"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b'{"speaker":"user","text":"keep"}\n')
        before = raw.read_bytes()
        self.manager.capture(self.spec(), apply=True)
        self.assertEqual(raw.read_bytes(), before)

    def test_mw210_18_cli_capture_is_preview_first_and_rebuild_requires_apply(self):
        specification = self.base / "profile-spec.json"
        specification.write_text(json.dumps(self.spec()), encoding="utf-8")

        def invoke(*arguments):
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "memory_cli.py"), "--root", str(self.archive), "--config", str(ROOT / "config.yaml"), *arguments],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        preview = invoke("environment-profile-capture", "--specification", str(specification))
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(self.manager.root.exists())
        applied = invoke("environment-profile-capture", "--specification", str(specification), "--apply")
        self.assertTrue(applied["applied"])
        pointer = self.manager.current_path.read_bytes()
        rebuilt_preview = invoke("environment-profile-rebuild-current")
        self.assertEqual(rebuilt_preview["status"], "preview")
        self.assertEqual(self.manager.current_path.read_bytes(), pointer)


class EnvironmentProfileExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config = load_simple_yaml(ROOT / "config.yaml")
        self.store_a = MemoryStore(self.base / "a", config)
        self.store_b = MemoryStore(self.base / "b", config)
        self.store_a.init()
        self.store_b.init()
        FederationManager(self.store_a).init_node("A", requested_node_id="node-a")
        FederationManager(self.store_b).init_node("B", requested_node_id="node-b")
        FederationManager(self.store_a).add_peer("node-b")
        FederationManager(self.store_b).add_peer("node-a")
        self.skill = self.base / "skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
        self.rule = self.base / "AGENTS.md"
        self.rule.write_text("global", encoding="utf-8")
        self.spec = {
            "schema_version": 1,
            "platform": "windows",
            "skills": [{"installation_id": "skill:demo", "provider_type": "user-managed", "provider_id": "user", "applicable_platforms": ["windows", "macos"], "root": str(self.skill)}],
            "rules": [{"rule_id": "global-agents", "path": str(self.rule)}],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_mw210_07_trusted_profile_exchange_is_read_only_and_replay_safe(self):
        captured = EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        sender = EnvironmentExchangeManager(self.store_a)
        receiver = EnvironmentExchangeManager(self.store_b)
        bundle = self.base / "profile.mwxb"
        exported = sender.export_delta(bundle, target_node_id="node-b")
        imported = verified_import(receiver, bundle, expected_node_id="node-a")
        self.assertEqual(imported["staged_profiles"], 1)
        replica = receiver.profiles.peer_profile("node-a")
        self.assertEqual(replica["profile_id"], captured["profile"]["profile_id"])
        self.assertEqual(verified_import(receiver, bundle)["status"], "no-change")
        self.assertFalse((self.store_b.root / "environment" / "profiles" / "current.json").exists())

    def test_mw210_peer_profile_read_revalidates_complete_generation_chain(self):
        first = EnvironmentProfileManager(self.store_a.root).capture(
            self.spec, apply=True
        )
        sender = EnvironmentExchangeManager(self.store_a)
        receiver = EnvironmentExchangeManager(self.store_b)
        first_bundle = self.base / "profile-chain-1.mwxb"
        first_export = sender.export_delta(first_bundle, target_node_id="node-b")
        verified_import(receiver, first_bundle, expected_node_id="node-a")
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Changed\n---\n", encoding="utf-8"
        )
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        second_bundle = self.base / "profile-chain-2.mwxb"
        sender.export_delta(
            second_bundle,
            after_event_sequence=first_export["to_event_sequence"],
            previous_bundle_sha256=first_export["sha256"],
            target_node_id="node-b",
        )
        verified_import(receiver, second_bundle, expected_node_id="node-a")
        first_hash = first["generation"]["generation_id"].split(":", 1)[1]
        selected = receiver.profiles.peer_profile("node-a", first_hash)
        self.assertEqual(selected["profile_id"], first["profile"]["profile_id"])
        replica = (
            self.store_b.root
            / "environment"
            / "replicas"
            / "peers"
            / "node-a"
            / "profiles"
            / f"{first_hash}.json"
        )
        replica.unlink()
        with self.assertRaisesRegex(ValueError, "generation chain"):
            receiver.profiles.peer_profile("node-a")

    def test_peer_profile_chain_rejects_duplicate_event_sequences(self):
        first = EnvironmentProfileManager(self.store_a.root).capture(
            self.spec, apply=True
        )
        sender = EnvironmentExchangeManager(self.store_a)
        receiver = EnvironmentExchangeManager(self.store_b)
        first_bundle = self.base / "profile-sequence-1.mwxb"
        first_export = sender.export_delta(first_bundle, target_node_id="node-b")
        verified_import(receiver, first_bundle, expected_node_id="node-a")
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Changed\n---\n", encoding="utf-8"
        )
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        second_bundle = self.base / "profile-sequence-2.mwxb"
        sender.export_delta(
            second_bundle,
            after_event_sequence=first_export["to_event_sequence"],
            previous_bundle_sha256=first_export["sha256"],
            target_node_id="node-b",
        )
        verified_import(receiver, second_bundle, expected_node_id="node-a")
        first_hash = first["generation"]["generation_id"].split(":", 1)[1]
        replica_root = (
            self.store_b.root
            / "environment"
            / "replicas"
            / "peers"
            / "node-a"
            / "profiles"
        )
        for path in replica_root.glob("*.json"):
            if path.name == f"{first_hash}.json":
                continue
            first_record = json.loads(
                (replica_root / f"{first_hash}.json").read_text(encoding="utf-8")
            )
            record = json.loads(path.read_text(encoding="utf-8"))
            record["event_sequence"] = first_record["event_sequence"]
            path.write_text(json.dumps(record), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "unique"):
            receiver.profiles.peer_profile("node-a")

    def test_mw210_08_untrusted_and_wrong_target_are_rejected(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        bundle = self.base / "profile.mwxb"
        EnvironmentExchangeManager(self.store_a).export_delta(bundle, target_node_id="node-b")
        FederationManager(self.store_b).revoke_peer("node-a")
        with self.assertRaisesRegex(ValueError, "not trusted"):
            verified_import(EnvironmentExchangeManager(self.store_b), bundle)

    def test_mw210_09_compare_and_plan_never_activate(self):
        local_spec = dict(self.spec)
        local_spec["skills"] = [dict(self.spec["skills"][0])]
        EnvironmentProfileManager(self.store_a.root).capture(local_spec, apply=True)
        mac_spec = dict(self.spec)
        mac_spec["platform"] = "macos"
        mac_spec["skills"] = [dict(
            self.spec["skills"][0],
            installation_id="plugin:demo",
            provider_type="plugin-managed",
            provider_id="plugin:demo",
        )]
        EnvironmentProfileManager(self.store_b.root).capture(mac_spec, apply=True)
        bundle = self.base / "peer.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)
        manager = EnvironmentProfileManager(self.store_a.root)
        comparison = manager.compare("node-b")
        skill_difference = next(
            item
            for item in comparison["differences"]
            if item["kind"] == "skill" and item["identity"] == "plugin:demo"
        )
        self.assertEqual(skill_difference["outcome"], "missing-local")
        plan = manager.convergence_plan("node-b")
        self.assertFalse(plan["activation_authorized"])
        self.assertFalse(plan["installer_invoked"])
        self.assertEqual(plan["items"][0]["route"], "provider-reference-only")
        with mock.patch.object(manager.registry, "show") as registry_show:
            with self.assertRaisesRegex(ValueError, "provider-managed differences"):
                manager.convergence_plan(
                    "node-b",
                    {
                        "skill:plugin:demo": {
                            "artifact_id": "global-skill:demo",
                            "revision_id": "rev:" + "0" * 64,
                            "source": "peer",
                            "installation_id": "plugin:demo",
                        }
                    },
                )
        registry_show.assert_not_called()

    def test_mw210_14_wrong_target_and_corrupt_profile_bundle_fail_closed(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        sender = EnvironmentExchangeManager(self.store_a)
        receiver = EnvironmentExchangeManager(self.store_b)
        wrong = self.base / "wrong.mwxb"
        sender.export_delta(wrong, target_node_id="node-c")
        with self.assertRaisesRegex(ValueError, "targets another node"):
            verified_import(receiver, wrong)
        clean = self.base / "clean.mwxb"
        sender.export_delta(clean, target_node_id="node-b")
        damaged = self.base / "damaged.mwxb"
        with zipfile.ZipFile(clean) as source, zipfile.ZipFile(damaged, "w", zipfile.ZIP_DEFLATED) as target:
            target.writestr("manifest.json", source.read("manifest.json"))
            payload = bytearray(source.read("payload/environment.jsonl"))
            payload[-2] ^= 1
            target.writestr("payload/environment.jsonl", payload)
        with self.assertRaises((ValueError, OSError)):
            verified_import(receiver, damaged)
        self.assertEqual(receiver.replica_state("node-a")["last_event_sequence"], 0)

    def test_mw210_duplicate_incoming_generation_is_rejected(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        sender = EnvironmentExchangeManager(self.store_a)
        clean = self.base / "single-profile.mwxb"
        sender.export_delta(clean, target_node_id="node-b")
        with zipfile.ZipFile(clean) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            record = json.loads(
                archive.read("payload/environment.jsonl").splitlines()[0]
            )
        duplicate = dict(record)
        duplicate["event_sequence"] = 2
        payload = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            + json.dumps(duplicate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        manifest.update(
            {
                "to_event_sequence": 2,
                "artifact_count": 2,
                "payload_bytes": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        manifest["bundle_id"] = "mwb-" + canonical_sha256(
            {key: value for key, value in manifest.items() if key != "bundle_id"}
        )[:32]
        damaged = self.base / "duplicate-profile.mwxb"
        with zipfile.ZipFile(damaged, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            archive.writestr("payload/environment.jsonl", payload)
        with self.assertRaisesRegex(ValueError, "duplicate incoming"):
            verified_import(
                EnvironmentExchangeManager(self.store_b),
                damaged,
                expected_node_id="node-a",
            )

    def test_mw210_15_profile_only_plan_cannot_invoke_installers(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        other = dict(self.spec)
        other["skills"] = [dict(self.spec["skills"][0], declared_version="2.0.0")]
        EnvironmentProfileManager(self.store_b.root).capture(other, apply=True)
        bundle = self.base / "plan.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)
        manager = EnvironmentProfileManager(self.store_a.root)
        with mock.patch("memory_environment_rules.EnvironmentRuleInstaller.install") as rule_install, mock.patch("memory_environment_skills.EnvironmentSkillInstaller.install") as skill_install:
            plan = manager.convergence_plan("node-b")
        rule_install.assert_not_called()
        skill_install.assert_not_called()
        self.assertFalse(plan["activation_authorized"])
        self.assertTrue(all(not item["automatic_activation"] for item in plan["items"]))

    def test_mw210_16_cross_platform_comparison_explains_all_six_outcomes(self):
        def make_skill(name, description, skill_name=None):
            root = self.base / name
            root.mkdir()
            (root / "SKILL.md").write_text(
                f"---\nname: {skill_name or name}\ndescription: {description}\n---\n",
                encoding="utf-8",
            )
            return str(root)

        common = make_skill("common", "same", "same")
        local_only = make_skill("local-only", "local", "missing-peer")
        peer_only = make_skill("peer-only", "peer", "missing-local")
        local_diff = make_skill("local-diff", "local difference", "differs")
        peer_diff = make_skill("peer-diff", "peer difference", "differs")
        incomplete_peer = make_skill(
            "incomplete-peer", "complete on peer", "incomplete"
        )
        mac_only = make_skill("mac-only", "mac only")

        def item(identity, root=None, platforms=None, incomplete=None):
            value = {
                "installation_id": identity,
                "provider_type": "user-managed",
                "provider_id": "user",
                "applicable_platforms": platforms or ["windows", "macos"],
            }
            if root is not None:
                value["root"] = root
            if incomplete is not None:
                value["incomplete_reason"] = incomplete
            return value

        local = {
            "schema_version": 1,
            "platform": "windows",
            "skills": [
                item("skill:same", common),
                item("skill:missing-peer", local_only),
                item("skill:differs", local_diff),
                item("skill:incomplete", incomplete="provider-unavailable"),
            ],
            "rules": [{"rule_id": "global-agents", "path": str(self.rule)}],
        }
        peer = {
            "schema_version": 1,
            "platform": "macos",
            "skills": [
                item("skill:same", common),
                item("skill:missing-local", peer_only),
                item("skill:differs", peer_diff),
                item("skill:incomplete", incomplete_peer),
                item("skill:mac-only", mac_only, ["macos"]),
            ],
            "rules": [{"rule_id": "global-agents", "path": str(self.rule)}],
        }
        EnvironmentProfileManager(self.store_a.root).capture(local, apply=True)
        EnvironmentProfileManager(self.store_b.root).capture(peer, apply=True)
        bundle = self.base / "all-outcomes.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)
        outcomes = {
            item["identity"]: item["outcome"]
            for item in EnvironmentProfileManager(self.store_a.root).compare("node-b")["differences"]
            if item["kind"] == "skill"
        }
        self.assertEqual(outcomes["skill:same"], "same")
        self.assertEqual(outcomes["skill:missing-local"], "missing-local")
        self.assertEqual(outcomes["skill:missing-peer"], "missing-peer")
        self.assertEqual(outcomes["skill:differs"], "content-differs")
        self.assertEqual(outcomes["skill:mac-only"], "platform-inapplicable")
        self.assertEqual(outcomes["skill:incomplete"], "inventory-incomplete")

    def test_mw210_19_dashboard_comparison_endpoint_is_read_only(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        other = dict(self.spec)
        other["platform"] = "macos"
        EnvironmentProfileManager(self.store_b.root).capture(other, apply=True)
        bundle = self.base / "dashboard-profile.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)

        def hashes():
            return {
                str(path.relative_to(self.store_a.root)): __import__("hashlib").sha256(path.read_bytes()).hexdigest()
                for path in self.store_a.root.rglob("*") if path.is_file()
            }

        before = hashes()
        handler_class = make_handler(self.store_a)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/environment-profile?peer_node_id=node-b"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        payload = json.loads(handler.wfile.getvalue())
        handler.send_response.assert_called_once_with(200)
        self.assertIn("comparison", payload)
        self.assertFalse(payload["plan"]["activation_authorized"])
        self.assertEqual(hashes(), before)

    def test_mw210_20_empty_exchange_does_not_initialize_profile_storage(self):
        manager = EnvironmentProfileManager(self.store_a.root)
        self.assertFalse(manager.root.exists())
        EnvironmentExchangeManager(self.store_a).refresh_export_ledger()
        self.assertFalse(manager.root.exists())

    def test_mw210_21_whole_rule_difference_stays_evidence_only(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        peer_rule = self.base / "peer-AGENTS.md"
        peer_rule.write_text("different whole Rule\n", encoding="utf-8")
        peer = dict(self.spec)
        peer["rules"] = [{"rule_id": "global-agents", "path": str(peer_rule)}]
        EnvironmentProfileManager(self.store_b.root).capture(peer, apply=True)
        bundle = self.base / "rule-profile.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)
        manager = EnvironmentProfileManager(self.store_a.root)
        with self.assertRaisesRegex(ValueError, "actionable difference"):
            manager.convergence_plan(
                "node-b",
                {
                    "rule:global-agents": {
                        "artifact_id": "global-rule:does-not-matter",
                        "revision_id": "rev:" + "0" * 64,
                    }
                },
            )
        comparison = manager.compare("node-b")
        block_difference = dict(
            next(item for item in comparison["differences"] if item["kind"] == "rule")
        )
        block_difference["kind"] = "rule-block"
        block_difference["identity"] = "global-agents:demo"
        comparison = {**comparison, "differences": [block_difference]}
        block_key = f"rule-block:{block_difference['identity']}"
        with self.assertRaisesRegex(ValueError, "missing fields"):
            manager.convergence_plan_from_comparison(
                comparison,
                {
                    block_key: {
                        "artifact_id": "global-rule:demo",
                        "revision_id": "rev:" + "0" * 64,
                        "binding_id": "demo-binding",
                    }
                },
            )
        with self.assertRaisesRegex(ValueError, "actionable difference"):
            manager.convergence_plan_from_comparison(
                comparison,
                {
                    block_key + "-typo": {
                        "artifact_id": "global-rule:demo",
                        "revision_id": "rev:" + "0" * 64,
                        "source": "peer",
                        "binding_id": "demo-binding",
                    }
                },
            )
        plan = manager.convergence_plan("node-b")
        whole = next(item for item in plan["items"] if item["kind"] == "rule")
        self.assertEqual(whole["route"], "evidence-only")
        self.assertIsNone(whole["artifact"])

    def test_mw210_25_rule_block_link_requires_exact_identity_and_hash(self):
        EnvironmentProfileManager(self.store_a.root).capture(self.spec, apply=True)
        block = (
            "<!-- memory-wuxian:managed-block:demo:begin -->\npeer rule\n"
            "<!-- memory-wuxian:managed-block:demo:end -->"
        )
        peer_rule = self.base / "peer-managed-rule.md"
        peer_rule.write_text(block, encoding="utf-8")
        block = peer_rule.read_bytes().decode("utf-8")
        peer = dict(self.spec)
        peer["rules"] = [{"rule_id": "global-agents", "path": str(peer_rule)}]
        EnvironmentProfileManager(self.store_b.root).capture(peer, apply=True)
        bundle = self.base / "rule-link-profile.mwxb"
        EnvironmentExchangeManager(self.store_b).export_delta(bundle, target_node_id="node-a")
        verified_import(EnvironmentExchangeManager(self.store_a), bundle)

        digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
        artifact = {
            "schema_version": 1,
            "artifact_id": "global-rule:profile-demo",
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Profile demo block",
            "created_at": "2026-08-01T00:00:00+00:00",
        }
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact["artifact_id"],
            "origin_node_id": "node-a",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {},
            "provenance": {"source": "profile-link-test"},
            "lifecycle_state": "staged",
            "created_at": "2026-08-01T00:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        manager = EnvironmentProfileManager(self.store_a.root)
        manager.registry.register(
            {"schema_version": 1, "artifacts": [{"artifact": artifact, "revision": revision, "content": block}], "projects": []},
            apply=True,
        )
        rules_root = self.base / "bound-rules"
        rules_root.mkdir()
        (rules_root / "AGENTS.md").write_text(block, encoding="utf-8")
        platform_name = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
        bindings = EnvironmentBindingRegistry(
            manager.registry,
            node_id="node-a",
            platform_name=platform_name,
        )
        bindings.register_root(
            root_id="global-rules",
            role="global-rules",
            owner="memory-wuxian-test",
            root=rules_root,
            apply=True,
        )
        bindings.register_rule_binding(
            {
                "binding_id": "profile-demo-binding",
                "scope": "global",
                "owner": "memory-wuxian-test",
                "root_id": "global-rules",
                "root": str(rules_root.resolve()),
                "relative_path": "AGENTS.md",
                "classification": "canonical",
                "install_strategy": "managed-block",
                "managed_block_id": "demo",
                "platform": platform_name,
                "installed_revision_id": revision["revision_id"],
                "installed_content_sha256": digest,
                "base_revision_id": None,
                "base_content_sha256": None,
            },
            apply=True,
        )
        link = {
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["revision_id"],
            "source": "peer",
            "binding_id": "profile-demo-binding",
        }
        plan = manager.convergence_plan(
            "node-b", {"rule-block:global-agents:demo": link}
        )
        item = next(row for row in plan["items"] if row["kind"] == "rule-block")
        self.assertEqual(item["route"], "existing-preview-required")
        with self.assertRaisesRegex(ValueError, "unique verified binding"):
            manager.convergence_plan(
                "node-b",
                {"rule-block:global-agents:demo": {**link, "binding_id": "wrong"}},
            )


if __name__ == "__main__":
    unittest.main()
