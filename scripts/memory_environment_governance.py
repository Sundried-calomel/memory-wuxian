"""Immutable, transport-neutral governance proposal envelopes."""

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


PROPOSAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
SCHEMA_ID = "work-system-governor/governance-insight-v1"
MAX_PROPOSAL_BYTES = 1024 * 1024

_CONTRACT = ImmutableRecordContract(
    format_name="memory-wuxian-governance-proposal-v1",
    schema_id=SCHEMA_ID,
    identity_field="proposal_id",
    identity_pattern=PROPOSAL_ID_RE,
    maximum_bytes=MAX_PROPOSAL_BYTES,
    collection_directory="governance-proposals",
    lock_filename="environment-governance-proposals.lock",
    source_event_prefix="governance-proposal",
    messages=ImmutableRecordMessages(
        object_required="governance proposal must be an object",
        identity_invalid="governance proposal_id is invalid",
        local_origin_required="governance proposal origin must equal the local node",
        schema_unsupported="governance proposal schema_version is unsupported",
        size_exceeded="governance proposal exceeds size limit",
        identity_conflict="governance proposal ID already has different content",
        appeared_before_apply="governance proposal appeared before apply",
        envelope_fields_invalid="governance proposal envelope fields are invalid",
        envelope_format_unsupported="governance proposal envelope format is unsupported",
        schema_identity_unsupported="governance proposal schema identity is unsupported",
        envelope_identity_invalid="governance proposal envelope ID is invalid",
        envelope_origin_mismatch="governance proposal envelope origin mismatch",
        content_encoding_invalid="governance proposal content encoding is invalid",
        content_hash_mismatch="governance proposal content hash mismatch",
        content_identity_mismatch="governance proposal identity mismatch",
        content_origin_mismatch="governance proposal content origin mismatch",
    ),
)


class GovernanceProposalStore(ImmutableRecordStore):
    """Store local proposals and enumerate read-only peer proposal replicas."""

    def __init__(self, store_or_root: Any):
        super().__init__(store_or_root, _CONTRACT)

    def propose(self, proposal: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        return self.put(
            proposal, apply=apply, preview_fields={"acceptance_implied": False}
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
            "automatic_acceptance": False,
        }
