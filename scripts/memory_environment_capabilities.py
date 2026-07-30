"""Minimal, non-authorizing device capability negotiation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from memory_environment_skills import _runtime_satisfies


PLATFORMS = {"macos", "windows", "linux"}
CATEGORIES = ("runtime", "protocol", "interface")
CATEGORY_FIELDS = {
    "runtime": "runtimes",
    "protocol": "protocols",
    "interface": "interfaces",
}
MISSING_REASON = {
    "runtime": "required-runtime-missing",
    "protocol": "required-protocol-missing",
    "interface": "required-interface-missing",
}
VERSION_REASON = {
    "runtime": "runtime-version-too-old",
    "protocol": "protocol-version-too-old",
    "interface": "interface-version-too-old",
}
REASON_CODES = frozenset(
    {
        "compatible",
        "unknown-legacy-offer",
        "product-mismatch",
        "peer-product-version-too-old",
        "peer-platform-unsupported",
        *MISSING_REASON.values(),
        *VERSION_REASON.values(),
    }
)
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
TOP_LEVEL_FIELDS = {
    "schema_version",
    "offer_version",
    "product",
    "platform",
    "supported_peer_platforms",
    "runtimes",
    "protocols",
    "interfaces",
}
CAPABILITY_FIELDS = {"name", "version", "minimum_peer_version", "required"}
AUTHORIZATION = {
    "installation": False,
    "trust": False,
    "permission_expansion": False,
    "synchronization": False,
}
MINIMUM_PEER_PRODUCT_VERSION = "2.5"
MINIMUM_PYTHON_VERSION = "3.14"
IMPLEMENTED_PROTOCOLS = (
    "archive-v1",
    "environment-v1",
    "configuration-v1",
)


def _strict_fields(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise ValueError(f"{label}: unknown fields: {unknown}")
    if missing:
        raise ValueError(f"{label}: missing fields: {missing}")


def _version(value: Any, label: str) -> str:
    if not isinstance(value, str) or VERSION_RE.fullmatch(value) is None:
        raise ValueError(f"{label}: unsupported version")
    return value


def _capability_list(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected an array")
    normalized = []
    names: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{item_label}: expected an object")
        _strict_fields(item, CAPABILITY_FIELDS, item_label)
        name = item["name"]
        if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
            raise ValueError(f"{item_label}.name: invalid capability name")
        if name in names:
            raise ValueError(f"{label}: duplicate capability name: {name}")
        names.add(name)
        required = item["required"]
        if not isinstance(required, bool):
            raise ValueError(f"{item_label}.required: expected boolean")
        normalized.append(
            {
                "name": name,
                "version": _version(item["version"], f"{item_label}.version"),
                "minimum_peer_version": _version(
                    item["minimum_peer_version"],
                    f"{item_label}.minimum_peer_version",
                ),
                "required": required,
            }
        )
    return normalized


def validate_device_capability_offer(value: Any) -> dict[str, Any]:
    """Validate and normalize the closed, privacy-minimal offer contract."""

    if not isinstance(value, Mapping):
        raise ValueError("device capability offer: expected an object")
    _strict_fields(value, TOP_LEVEL_FIELDS, "device capability offer")
    if value["schema_version"] != 1:
        raise ValueError("device capability offer: unsupported schema_version")
    if value["offer_version"] != 1:
        raise ValueError("device capability offer: unsupported offer_version")

    product = value["product"]
    if not isinstance(product, Mapping):
        raise ValueError("device capability offer.product: expected an object")
    _strict_fields(
        product,
        {"id", "version", "minimum_peer_version"},
        "device capability offer.product",
    )
    product_id = product["id"]
    if not isinstance(product_id, str) or NAME_RE.fullmatch(product_id) is None:
        raise ValueError("device capability offer.product.id: invalid product")

    platform = value["platform"]
    if platform not in PLATFORMS:
        raise ValueError("device capability offer.platform: unsupported platform")
    peers = value["supported_peer_platforms"]
    if (
        not isinstance(peers, list)
        or not peers
        or any(peer not in PLATFORMS for peer in peers)
        or len(peers) != len(set(peers))
    ):
        raise ValueError(
            "device capability offer.supported_peer_platforms: invalid platforms"
        )

    return {
        "schema_version": 1,
        "offer_version": 1,
        "product": {
            "id": product_id,
            "version": _version(
                product["version"], "device capability offer.product.version"
            ),
            "minimum_peer_version": _version(
                product["minimum_peer_version"],
                "device capability offer.product.minimum_peer_version",
            ),
        },
        "platform": platform,
        "supported_peer_platforms": sorted(peers),
        **{
            field: sorted(
                _capability_list(value[field], f"device capability offer.{field}"),
                key=lambda item: item["name"],
            )
            for field in CATEGORY_FIELDS.values()
        },
    }


def local_device_capability_offer(
    product_version: str,
    platform: str,
    python_version: str,
    semantic_runtime: bool = False,
) -> dict[str, Any]:
    """Build the closed technical offer for capabilities implemented locally."""

    if not isinstance(semantic_runtime, bool):
        raise ValueError("semantic_runtime: expected boolean")
    offer = {
        "schema_version": 1,
        "offer_version": 1,
        "product": {
            "id": "memory-wuxian",
            "version": product_version,
            "minimum_peer_version": MINIMUM_PEER_PRODUCT_VERSION,
        },
        "platform": platform,
        "supported_peer_platforms": sorted(PLATFORMS),
        "runtimes": [
            {
                "name": "python",
                "version": python_version,
                "minimum_peer_version": MINIMUM_PYTHON_VERSION,
                "required": True,
            }
        ],
        "protocols": [
            {
                "name": name,
                "version": "1",
                "minimum_peer_version": "1",
                "required": True,
            }
            for name in IMPLEMENTED_PROTOCOLS
        ],
        "interfaces": (
            [
                {
                    "name": "semantic-runtime",
                    "version": "1",
                    "minimum_peer_version": "1",
                    "required": False,
                }
            ]
            if semantic_runtime
            else []
        ),
    }
    return validate_device_capability_offer(offer)


def capability_offer_sha256(offer: Mapping[str, Any]) -> str:
    normalized = validate_device_capability_offer(offer)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finding(
    reason_code: str,
    side: str,
    category: str,
    subject: str,
    offered_version: str | None,
    required_version: str | None,
) -> dict[str, Any]:
    if reason_code not in REASON_CODES:
        raise ValueError(f"unsupported capability reason code: {reason_code}")
    return {
        "reason_code": reason_code,
        "side": side,
        "category": category,
        "subject": subject,
        "offered_version": offered_version,
        "required_version": required_version,
    }


def _check_direction(
    requester: Mapping[str, Any],
    peer: Mapping[str, Any],
    peer_side: str,
) -> list[dict[str, Any]]:
    findings = []
    if peer["platform"] not in requester["supported_peer_platforms"]:
        findings.append(
            _finding(
                "peer-platform-unsupported",
                peer_side,
                "platform",
                peer["platform"],
                None,
                None,
            )
        )

    for category in CATEGORIES:
        field = CATEGORY_FIELDS[category]
        peer_by_name = {item["name"]: item for item in peer[field]}
        for requirement in requester[field]:
            if not requirement["required"]:
                continue
            offered = peer_by_name.get(requirement["name"])
            if offered is None:
                findings.append(
                    _finding(
                        MISSING_REASON[category],
                        peer_side,
                        category,
                        requirement["name"],
                        None,
                        requirement["minimum_peer_version"],
                    )
                )
            elif not _runtime_satisfies(
                offered["version"],
                f">={requirement['minimum_peer_version']}",
            ):
                findings.append(
                    _finding(
                        VERSION_REASON[category],
                        peer_side,
                        category,
                        requirement["name"],
                        offered["version"],
                        requirement["minimum_peer_version"],
                    )
                )
    return findings


def negotiate_device_capabilities(
    local_offer: Mapping[str, Any],
    remote_offer: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return compatibility diagnostics without granting operational authority."""

    local = validate_device_capability_offer(local_offer)
    local_digest = capability_offer_sha256(local)
    if remote_offer is None:
        return {
            "schema_version": 1,
            "negotiation_version": 1,
            "status": "unknown-legacy",
            "compatible": None,
            "reason_codes": ["unknown-legacy-offer"],
            "findings": [],
            "local_offer_sha256": local_digest,
            "remote_offer_sha256": None,
            "blocks_existing_sync": False,
            "authorization": dict(AUTHORIZATION),
        }

    remote = validate_device_capability_offer(remote_offer)
    findings = []
    if local["product"]["id"] != remote["product"]["id"]:
        findings.append(
            _finding(
                "product-mismatch",
                "remote",
                "product",
                remote["product"]["id"],
                None,
                None,
            )
        )
    else:
        if not _runtime_satisfies(
            remote["product"]["version"],
            f">={local['product']['minimum_peer_version']}",
        ):
            findings.append(
                _finding(
                    "peer-product-version-too-old",
                    "remote",
                    "product",
                    remote["product"]["id"],
                    remote["product"]["version"],
                    local["product"]["minimum_peer_version"],
                )
            )
        if not _runtime_satisfies(
            local["product"]["version"],
            f">={remote['product']['minimum_peer_version']}",
        ):
            findings.append(
                _finding(
                    "peer-product-version-too-old",
                    "local",
                    "product",
                    local["product"]["id"],
                    local["product"]["version"],
                    remote["product"]["minimum_peer_version"],
                )
            )

    findings.extend(_check_direction(local, remote, "remote"))
    findings.extend(_check_direction(remote, local, "local"))
    findings.sort(
        key=lambda item: (
            item["side"],
            item["category"],
            item["subject"],
            item["reason_code"],
        )
    )
    compatible = not findings
    return {
        "schema_version": 1,
        "negotiation_version": 1,
        "status": "compatible" if compatible else "incompatible",
        "compatible": compatible,
        "reason_codes": (
            ["compatible"]
            if compatible
            else sorted({item["reason_code"] for item in findings})
        ),
        "findings": findings,
        "local_offer_sha256": local_digest,
        "remote_offer_sha256": capability_offer_sha256(remote),
        "blocks_existing_sync": False,
        "authorization": dict(AUTHORIZATION),
    }


validate_capability_offer = validate_device_capability_offer
negotiate_capabilities = negotiate_device_capabilities
