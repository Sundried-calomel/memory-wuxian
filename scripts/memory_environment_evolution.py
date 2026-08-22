"""Immutable, transport-neutral product evolution record envelopes."""

from __future__ import annotations

import re
from typing import Any, Dict

from memory_environment import canonical_bytes  # compatibility export
from memory_environment_immutable_records import (
    ImmutableRecordContract,
    ImmutableRecordMessages,
    ImmutableRecordStore,
    validate_immutable_envelope,
)


RECORD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
SCHEMA_ID = "work-system-governor/product-evolution-v1"
MAX_RECORD_BYTES = 4 * 1024 * 1024

_CONTRACT = ImmutableRecordContract(
    format_name="memory-wuxian-product-evolution-v1",
    schema_id=SCHEMA_ID,
    identity_field="record_id",
    identity_pattern=RECORD_ID_RE,
    maximum_bytes=MAX_RECORD_BYTES,
    collection_directory="product-evolution",
    lock_filename="environment-product-evolution.lock",
    source_event_prefix="product-evolution",
    messages=ImmutableRecordMessages(
        object_required="product evolution record must be an object",
        identity_invalid="product evolution record_id is invalid",
        local_origin_required="product evolution origin must equal the local node",
        schema_unsupported="product evolution schema_version is unsupported",
        size_exceeded="product evolution record exceeds size limit",
        identity_conflict="product evolution record ID already has different content",
        appeared_before_apply="product evolution record appeared before apply",
        envelope_fields_invalid="product evolution envelope fields are invalid",
        envelope_format_unsupported="product evolution envelope format is unsupported",
        schema_identity_unsupported="product evolution schema identity is unsupported",
        envelope_identity_invalid="product evolution envelope ID is invalid",
        envelope_origin_mismatch="product evolution envelope origin mismatch",
        content_encoding_invalid="product evolution content encoding is invalid",
        content_hash_mismatch="product evolution content hash mismatch",
        content_identity_mismatch="product evolution identity mismatch",
        content_origin_mismatch="product evolution content origin mismatch",
    ),
)


class ProductEvolutionStore(ImmutableRecordStore):
    """Store local evolution records and enumerate read-only peer replicas."""

    def __init__(self, store_or_root: Any):
        super().__init__(store_or_root, _CONTRACT)

    def record(self, value: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        return self.put(
            value,
            apply=apply,
            preview_fields={
                "remediation_implied": False,
                "governance_acceptance_implied": False,
            },
        )

    @staticmethod
    def validate_envelope(
        envelope: Dict[str, Any], *, expected_origin: str | None = None
    ) -> Dict[str, Any]:
        return validate_immutable_envelope(
            envelope, _CONTRACT, expected_origin=expected_origin
        )

    def list(self) -> Dict[str, Any]:
        local = [item["payload"] for item in self.local_events()]
        return {
            "status": "listed",
            "local": local,
            "remote": self.remote_envelopes(),
            "automatic_remediation": False,
            "automatic_governance_acceptance": False,
        }
