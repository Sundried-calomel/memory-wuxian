import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_exchange_contract import (
    ExchangeStreamPort,
    build_bundle_manifest,
    classify_replica_window,
    select_jsonl_page,
    validate_authenticated_binding,
    validate_export_cursor,
    validate_strict_replica_continuity,
    verify_bundle_identity,
    verify_payload,
)


def canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bytes_sha256(value):
    return hashlib.sha256(value).hexdigest()


class ExchangeContractTests(unittest.TestCase):
    def test_cursor_policy_preserves_stream_specific_messages(self):
        policy = {
            "cursor_error": lambda _after, _latest: "cursor",
            "predecessor_error": "predecessor",
            "initial_predecessor_error": "initial",
            "predecessor_is_valid": lambda value: value == "a" * 64,
            "initial_predecessor_is_declared": lambda value: value is not None,
        }
        validate_export_cursor(0, 2, None, **policy)
        validate_export_cursor(1, 2, "a" * 64, **policy)
        with self.assertRaisesRegex(ValueError, "cursor"):
            validate_export_cursor(3, 2, "a" * 64, **policy)
        with self.assertRaisesRegex(ValueError, "predecessor"):
            validate_export_cursor(1, 2, None, **policy)
        with self.assertRaisesRegex(ValueError, "initial"):
            validate_export_cursor(0, 2, "a" * 64, **policy)

    def test_jsonl_page_is_bounded_without_consuming_the_next_page(self):
        records = [{"event_sequence": 1}, {"event_sequence": 2}, {"event_sequence": 3}]
        selected, payload = select_jsonl_page(
            records,
            encode=lambda item: f"{item['event_sequence']}\n".encode("ascii"),
            maximum_items=2,
            maximum_bytes=20,
            oversized_item_error=lambda _item: "oversized",
        )
        self.assertEqual(selected, records[:2])
        self.assertEqual(payload, b"1\n2\n")

        with self.assertRaisesRegex(ValueError, "oversized"):
            select_jsonl_page(
                records,
                encode=lambda _item: b"too-large",
                maximum_items=2,
                maximum_bytes=2,
                oversized_item_error=lambda _item: "oversized",
            )

    def test_manifest_and_payload_mechanics_preserve_exact_identity(self):
        base = {"origin_node_id": "node-a", "label": "归档 ¥"}
        manifest = build_bundle_manifest(base, canonical_sha256=canonical_sha256)
        self.assertEqual(set(manifest), {"origin_node_id", "label", "bundle_id"})
        self.assertEqual(manifest["bundle_id"], "mwb-" + canonical_sha256(base)[:32])
        verify_bundle_identity(
            manifest, canonical_sha256=canonical_sha256, error="identity"
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_bundle_identity(
                {**manifest, "label": "changed"},
                canonical_sha256=canonical_sha256,
                error="identity",
            )

        payload = "中文・日本語・¥".encode("utf-8")
        payload_manifest = {
            "payload_bytes": len(payload),
            "payload_sha256": bytes_sha256(payload),
        }
        verify_payload(
            payload_manifest,
            payload,
            bytes_sha256=bytes_sha256,
            size_error="size",
            hash_error="hash",
        )
        with self.assertRaisesRegex(ValueError, "size"):
            verify_payload(
                {**payload_manifest, "payload_bytes": 0},
                payload,
                bytes_sha256=bytes_sha256,
                size_error="size",
                hash_error="hash",
            )
        with self.assertRaisesRegex(ValueError, "hash"):
            verify_payload(
                {**payload_manifest, "payload_sha256": "0" * 64},
                payload,
                bytes_sha256=bytes_sha256,
                size_error="size",
                hash_error="hash",
            )

    def test_authenticated_binding_and_continuity_are_policy_parameterized(self):
        binding = validate_authenticated_binding(
            ("node-a", "node-b", "f" * 64),
            expected_origin="node-a",
            expected_target="node-b",
            expected_payload_sha256="f" * 64,
            identity_error="binding",
            payload_error="payload",
        )
        self.assertEqual(binding, ("node-a", "node-b", "f" * 64))
        with self.assertRaisesRegex(ValueError, "binding"):
            validate_authenticated_binding(
                ("node-c", "node-b", "f" * 64),
                expected_origin="node-a",
                expected_target="node-b",
                expected_payload_sha256="f" * 64,
                identity_error="binding",
            )

        state = {"last_event_sequence": 4, "last_bundle_sha256": "e" * 64}
        manifest = {
            "from_event_sequence": 5,
            "previous_bundle_sha256": "e" * 64,
        }
        validate_strict_replica_continuity(
            manifest,
            state,
            manifest_sequence_field="from_event_sequence",
            state_offset=1,
            sequence_error=lambda _expected, _actual: "sequence",
            predecessor_error="predecessor",
        )
        with self.assertRaisesRegex(ValueError, "predecessor"):
            validate_strict_replica_continuity(
                {**manifest, "previous_bundle_sha256": "d" * 64},
                state,
                manifest_sequence_field="from_event_sequence",
                state_offset=1,
                sequence_error=lambda _expected, _actual: "sequence",
                predecessor_error="predecessor",
            )

        window = classify_replica_window(
            {"from_event_sequence": 3, "to_event_sequence": 7},
            state,
            gap_error="gap",
            stale_error="stale",
        )
        self.assertEqual(window.expected_sequence, 5)
        self.assertTrue(window.overlap_recovery)

    def test_explicit_port_routes_plain_and_authenticated_imports(self):
        calls = []

        def plain(bundle, *, expected_node_id):
            calls.append(("plain", bundle, expected_node_id))
            return {"status": "plain"}

        def authenticated(bundle, *, expected_node_id, authenticated_open_result):
            calls.append(
                ("authenticated", bundle, expected_node_id, authenticated_open_result)
            )
            return {"status": "authenticated"}

        common = dict(
            store=object(),
            root=Path("root"),
            metadata_root=Path("metadata"),
            replica_root=Path("replica"),
            exchange_lock_path=Path("lock"),
            node=lambda: {},
            peers=lambda: [],
            status=lambda: {},
            exchange_observation=lambda _timestamp: {},
            replica_state=lambda _node: {},
            read_bundle_manifest=lambda _bundle: {},
            export_delta=lambda *_args, **_kwargs: {},
            import_delta=plain,
            log_sync=lambda *_args: None,
        )
        plain_port = ExchangeStreamPort(
            **common,
            requires_authenticated_transport=False,
            import_authenticated_delta=None,
        )
        self.assertEqual(
            plain_port.import_bundle(
                Path("plain.mwxb"),
                expected_node_id="node-a",
                authenticated_open_result=None,
            )["status"],
            "plain",
        )
        authenticated_port = ExchangeStreamPort(
            **common,
            requires_authenticated_transport=True,
            import_authenticated_delta=authenticated,
        )
        self.assertEqual(
            authenticated_port.import_bundle(
                Path("auth.mwxb"),
                expected_node_id="node-a",
                authenticated_open_result="proof",
            )["status"],
            "authenticated",
        )
        self.assertEqual([item[0] for item in calls], ["plain", "authenticated"])

        incomplete_port = ExchangeStreamPort(
            **{**common, "import_delta": None},
            requires_authenticated_transport=False,
            import_authenticated_delta=None,
        )
        with self.assertRaisesRegex(TypeError, "plain exchange port is incomplete"):
            incomplete_port.import_bundle(
                Path("missing.mwxb"),
                expected_node_id="node-a",
                authenticated_open_result=None,
            )


if __name__ == "__main__":
    unittest.main()
