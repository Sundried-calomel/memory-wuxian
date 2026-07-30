from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment_capabilities import (  # noqa: E402
    REASON_CODES,
    capability_offer_sha256,
    local_device_capability_offer,
    negotiate_device_capabilities,
    validate_device_capability_offer,
)


class DeviceCapabilityNegotiationTests(unittest.TestCase):
    @staticmethod
    def offer(
        *,
        platform: str = "macos",
        version: str = "2.5.0",
        minimum_peer_version: str = "2.5",
    ):
        return {
            "schema_version": 1,
            "offer_version": 1,
            "product": {
                "id": "memory-wuxian",
                "version": version,
                "minimum_peer_version": minimum_peer_version,
            },
            "platform": platform,
            "supported_peer_platforms": ["macos", "windows", "linux"],
            "runtimes": [
                {
                    "name": "python",
                    "version": "3.14.1",
                    "minimum_peer_version": "3.14",
                    "required": True,
                }
            ],
            "protocols": [
                {
                    "name": "archive-v1",
                    "version": "1",
                    "minimum_peer_version": "1.0",
                    "required": True,
                },
                {
                    "name": "environment-v1",
                    "version": "1.0.0",
                    "minimum_peer_version": "1",
                    "required": True,
                },
            ],
            "interfaces": [
                {
                    "name": "semantic-runtime",
                    "version": "1.0",
                    "minimum_peer_version": "1",
                    "required": False,
                }
            ],
        }

    @staticmethod
    def schema(name: str):
        return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))

    def test_schemas_are_closed_and_have_no_privacy_scope(self):
        device = self.schema("device-capability.schema.json")
        negotiation = self.schema("capability-negotiation.schema.json")
        self.assertFalse(device["additionalProperties"])
        self.assertFalse(negotiation["additionalProperties"])
        text = json.dumps([device, negotiation], sort_keys=True).casefold()
        for forbidden in (
            "privacy_scope",
            "privacy-scope",
            "path",
            "username",
            "hostname",
            "credential",
            "configuration",
        ):
            self.assertNotIn(forbidden, text)

    def test_offer_validation_is_closed_and_deterministic(self):
        first = self.offer()
        second = {
            **first,
            "supported_peer_platforms": list(
                reversed(first["supported_peer_platforms"])
            ),
        }
        self.assertEqual(
            capability_offer_sha256(first),
            capability_offer_sha256(second),
        )
        changed = self.offer()
        changed["privacy_scope"] = "private"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_device_capability_offer(changed)

    def test_duplicate_capability_and_unsupported_version_fail_closed(self):
        duplicate = self.offer()
        duplicate["runtimes"].append(dict(duplicate["runtimes"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate capability"):
            validate_device_capability_offer(duplicate)
        invalid = self.offer(version="v2.5.0")
        with self.assertRaisesRegex(ValueError, "unsupported version"):
            validate_device_capability_offer(invalid)

    def test_local_offer_builder_emits_only_implemented_closed_capabilities(self):
        offer = local_device_capability_offer("2.5.0", "macos", "3.14.0")
        self.assertEqual(
            {
                "schema_version",
                "offer_version",
                "product",
                "platform",
                "supported_peer_platforms",
                "runtimes",
                "protocols",
                "interfaces",
            },
            set(offer),
        )
        self.assertEqual(
            ["archive-v1", "configuration-v1", "environment-v1"],
            [item["name"] for item in offer["protocols"]],
        )
        self.assertEqual("3.14", offer["runtimes"][0]["minimum_peer_version"])
        self.assertEqual([], offer["interfaces"])
        self.assertEqual(offer, validate_device_capability_offer(offer))

        text = json.dumps(offer, sort_keys=True).casefold()
        for forbidden in (
            "privacy_scope",
            "privacy-scope",
            "path",
            "username",
            "hostname",
            "credential",
            "read-only",
        ):
            self.assertNotIn(forbidden, text)

    def test_local_offer_builder_semantic_runtime_flag_is_explicit(self):
        offer = local_device_capability_offer(
            "2.5.0",
            "windows",
            "3.14",
            semantic_runtime=True,
        )
        self.assertEqual(
            [
                {
                    "name": "semantic-runtime",
                    "version": "1",
                    "minimum_peer_version": "1",
                    "required": False,
                }
            ],
            offer["interfaces"],
        )
        with self.assertRaisesRegex(ValueError, "expected boolean"):
            local_device_capability_offer(
                "2.5.0",
                "windows",
                "3.14",
                semantic_runtime=1,
            )

    def test_local_offer_builder_is_deterministic_and_non_authorizing(self):
        first = local_device_capability_offer("2.5", "linux", "3.14.0")
        second = local_device_capability_offer("2.5", "linux", "3.14")
        result = negotiate_device_capabilities(first, second)
        self.assertEqual(
            capability_offer_sha256(first),
            capability_offer_sha256(
                local_device_capability_offer("2.5", "linux", "3.14.0")
            ),
        )
        self.assertEqual("compatible", result["status"])
        self.assertFalse(result["blocks_existing_sync"])
        self.assertFalse(any(result["authorization"].values()))

    def test_missing_remote_offer_is_unknown_legacy_and_does_not_block_sync(self):
        result = negotiate_device_capabilities(self.offer(), None)
        self.assertEqual("unknown-legacy", result["status"])
        self.assertIsNone(result["compatible"])
        self.assertEqual(["unknown-legacy-offer"], result["reason_codes"])
        self.assertFalse(result["blocks_existing_sync"])
        self.assertIsNone(result["remote_offer_sha256"])
        self.assertEqual(
            {
                "installation": False,
                "trust": False,
                "permission_expansion": False,
                "synchronization": False,
            },
            result["authorization"],
        )

    def test_compatible_cross_platform_offer_uses_padded_version_comparison(self):
        local = self.offer(platform="macos", version="2.5")
        remote = self.offer(platform="windows", version="2.5.0")
        result = negotiate_device_capabilities(local, remote)
        self.assertEqual("compatible", result["status"])
        self.assertTrue(result["compatible"])
        self.assertEqual(["compatible"], result["reason_codes"])
        self.assertEqual([], result["findings"])
        self.assertFalse(any(result["authorization"].values()))

    def test_product_version_and_platform_failures_use_stable_reason_codes(self):
        local = self.offer(minimum_peer_version="2.6")
        local["supported_peer_platforms"] = ["macos"]
        remote = self.offer(platform="windows", version="2.5")
        result = negotiate_device_capabilities(local, remote)
        self.assertEqual("incompatible", result["status"])
        self.assertEqual(
            {
                "peer-platform-unsupported",
                "peer-product-version-too-old",
            },
            set(result["reason_codes"]),
        )
        self.assertFalse(result["blocks_existing_sync"])

    def test_product_mismatch_is_diagnostic_not_a_validation_failure(self):
        remote = self.offer()
        remote["product"]["id"] = "other-product"
        result = negotiate_device_capabilities(self.offer(), remote)
        self.assertEqual("incompatible", result["status"])
        self.assertIn("product-mismatch", result["reason_codes"])
        self.assertFalse(result["blocks_existing_sync"])
        self.assertFalse(any(result["authorization"].values()))

    def test_required_missing_and_old_capabilities_are_directional(self):
        local = self.offer()
        remote = self.offer()
        remote["protocols"] = [
            item for item in remote["protocols"] if item["name"] != "archive-v1"
        ]
        remote["runtimes"][0]["version"] = "3.8.9"
        result = negotiate_device_capabilities(local, remote)
        self.assertEqual(
            {"required-protocol-missing", "runtime-version-too-old"},
            set(result["reason_codes"]),
        )
        self.assertEqual(
            {
                ("remote", "archive-v1", "required-protocol-missing"),
                ("remote", "python", "runtime-version-too-old"),
            },
            {
                (item["side"], item["subject"], item["reason_code"])
                for item in result["findings"]
            },
        )

    def test_optional_missing_capability_does_not_make_peers_incompatible(self):
        remote = self.offer()
        remote["interfaces"] = []
        result = negotiate_device_capabilities(self.offer(), remote)
        self.assertEqual("compatible", result["status"])

    def test_reason_codes_and_result_schema_are_stable(self):
        schema_codes = set(
            self.schema("capability-negotiation.schema.json")["$defs"][
                "reasonCode"
            ]["enum"]
        )
        self.assertEqual(REASON_CODES, schema_codes)
        result = negotiate_device_capabilities(self.offer(), self.offer())
        self.assertEqual(
            set(
                self.schema("capability-negotiation.schema.json")["required"]
            ),
            set(result),
        )


if __name__ == "__main__":
    unittest.main()
