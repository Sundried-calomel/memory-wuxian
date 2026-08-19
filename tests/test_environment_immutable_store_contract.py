import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import memory_environment_evolution as evolution_module
import memory_environment_governance as governance_module
from memory_environment_evolution import ProductEvolutionStore
from memory_environment_governance import GovernanceProposalStore
from memory_federation import FederationManager


NODE_ID = "node-a"
FIXED_TIME = "2026-08-19T00:00:00+00:00"

PROPOSAL = {
    "schema_version": 1,
    "proposal_id": "insight-golden-001",
    "origin_node_id": NODE_ID,
    "observed_problem": "中文与日本語 remain exact",
    "details": {
        "currency": "¥ € $",
        "emoji": "😀",
        "path": "資料/文献 - final",
    },
}
PROPOSAL_CONTENT = (
    '{"details":{"currency":"¥ € $","emoji":"😀","path":"資料/文献 - final"},'
    '"observed_problem":"中文与日本語 remain exact","origin_node_id":"node-a",'
    '"proposal_id":"insight-golden-001","schema_version":1}'
).encode("utf-8")

EVOLUTION = {
    "schema_version": 1,
    "record_id": "evolution-golden-001",
    "origin_node_id": NODE_ID,
    "product_id": "memory-wuxian",
    "history": [{"kind": "correction", "note": "修正 - 説明 😀"}],
}
EVOLUTION_CONTENT = (
    '{"history":[{"kind":"correction","note":"修正 - 説明 😀"}],'
    '"origin_node_id":"node-a","product_id":"memory-wuxian",'
    '"record_id":"evolution-golden-001","schema_version":1}'
).encode("utf-8")


def expected_contract(
    *,
    content: bytes,
    format_name: str,
    schema_id: str,
    identity_field: str,
    identity: str,
) -> tuple[str, dict, bytes]:
    digest = hashlib.sha256(content).hexdigest()
    encoded = base64.b64encode(content).decode("ascii")
    envelope = {
        "format": format_name,
        "schema_id": schema_id,
        identity_field: identity,
        "origin_node_id": NODE_ID,
        "content_sha256": digest,
        "content_base64": encoded,
    }
    file_bytes = (
        "{\n"
        f'  "content_base64": "{encoded}",\n'
        f'  "content_sha256": "{digest}",\n'
        f'  "format": "{format_name}",\n'
        f'  "origin_node_id": "{NODE_ID}",\n'
        f'  "{identity_field}": "{identity}",\n'
        f'  "schema_id": "{schema_id}"\n'
        "}\n"
    ).encode("utf-8")
    return digest, envelope, file_bytes


PROPOSAL_DIGEST, PROPOSAL_ENVELOPE, PROPOSAL_FILE_BYTES = expected_contract(
    content=PROPOSAL_CONTENT,
    format_name="memory-wuxian-governance-proposal-v1",
    schema_id="work-system-governor/governance-insight-v1",
    identity_field="proposal_id",
    identity="insight-golden-001",
)
EVOLUTION_DIGEST, EVOLUTION_ENVELOPE, EVOLUTION_FILE_BYTES = expected_contract(
    content=EVOLUTION_CONTENT,
    format_name="memory-wuxian-product-evolution-v1",
    schema_id="work-system-governor/product-evolution-v1",
    identity_field="record_id",
    identity="evolution-golden-001",
)


class EnvironmentImmutableStoreContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.store = SimpleNamespace(
            root=self.base / "archive",
            config={
                "federation": {
                    "replica_directory": str(self.base / "federation-cache")
                }
            },
        )
        with mock.patch("memory_federation.now_iso", return_value=FIXED_TIME):
            FederationManager(self.store).init_node(
                "Golden Node", requested_node_id=NODE_ID
            )
        self.governance = GovernanceProposalStore(self.store)
        self.evolution = ProductEvolutionStore(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_persisted_contract(
        self,
        *,
        store,
        public_call,
        value,
        content,
        identity_field,
        identity,
        digest,
        envelope,
        file_bytes,
        directory,
        source_prefix,
        implied_fields,
        duplicate_message,
    ):
        preview = public_call(value)
        self.assertEqual(
            preview,
            {
                "status": "preview",
                identity_field: identity,
                "content_sha256": digest,
                **implied_fields,
            },
        )

        recorded = public_call(value, apply=True)
        expected_path = directory / f"{identity}-{digest}.json"
        self.assertEqual(
            recorded,
            {**preview, "status": "recorded", "path": str(expected_path)},
        )
        self.assertEqual(expected_path.name, f"{identity}-{digest}.json")
        self.assertEqual(expected_path.read_bytes(), file_bytes)
        self.assertEqual(json.loads(expected_path.read_text(encoding="utf-8")), envelope)
        self.assertEqual(base64.b64decode(envelope["content_base64"]), content)
        self.assertEqual(hashlib.sha256(content).hexdigest(), digest)

        self.assertEqual(
            store.local_events(),
            [
                {
                    "source_event_id": f"{source_prefix}:{identity}:{digest}",
                    identity_field: identity,
                    "payload": envelope,
                }
            ],
        )
        self.assertEqual(
            public_call(value, apply=True),
            {
                "status": "no-change",
                identity_field: identity,
                "content_sha256": digest,
                "path": str(expected_path),
            },
        )

        changed = copy.deepcopy(value)
        changed["contract_change"] = True
        with self.assertRaises(ValueError) as raised:
            public_call(changed, apply=True)
        self.assertEqual(str(raised.exception), duplicate_message)

    def test_governance_propose_freezes_canonical_envelope_file_and_event(self):
        self.assertEqual(governance_module.canonical_bytes(PROPOSAL), PROPOSAL_CONTENT)
        self.assert_persisted_contract(
            store=self.governance,
            public_call=self.governance.propose,
            value=PROPOSAL,
            content=PROPOSAL_CONTENT,
            identity_field="proposal_id",
            identity="insight-golden-001",
            digest=PROPOSAL_DIGEST,
            envelope=PROPOSAL_ENVELOPE,
            file_bytes=PROPOSAL_FILE_BYTES,
            directory=self.governance.local_root,
            source_prefix="governance-proposal",
            implied_fields={"acceptance_implied": False},
            duplicate_message="governance proposal ID already has different content",
        )

    def test_product_evolution_record_freezes_canonical_envelope_file_and_event(self):
        self.assertEqual(evolution_module.canonical_bytes(EVOLUTION), EVOLUTION_CONTENT)
        self.assert_persisted_contract(
            store=self.evolution,
            public_call=self.evolution.record,
            value=EVOLUTION,
            content=EVOLUTION_CONTENT,
            identity_field="record_id",
            identity="evolution-golden-001",
            digest=EVOLUTION_DIGEST,
            envelope=EVOLUTION_ENVELOPE,
            file_bytes=EVOLUTION_FILE_BYTES,
            directory=self.evolution.local_root,
            source_prefix="product-evolution",
            implied_fields={
                "remediation_implied": False,
                "governance_acceptance_implied": False,
            },
            duplicate_message="product evolution record ID already has different content",
        )

    def test_public_apis_keep_distinct_origins_duplicates_and_size_limits(self):
        self.assertEqual(governance_module.MAX_PROPOSAL_BYTES, 1024 * 1024)
        self.assertEqual(evolution_module.MAX_RECORD_BYTES, 4 * 1024 * 1024)

        foreign_proposal = {**PROPOSAL, "origin_node_id": "node-b"}
        with self.assertRaises(ValueError) as governance_origin:
            self.governance.propose(foreign_proposal, apply=True)
        self.assertEqual(
            str(governance_origin.exception),
            "governance proposal origin must equal the local node",
        )

        foreign_evolution = {**EVOLUTION, "origin_node_id": "node-b"}
        with self.assertRaises(ValueError) as evolution_origin:
            self.evolution.record(foreign_evolution, apply=True)
        self.assertEqual(
            str(evolution_origin.exception),
            "product evolution origin must equal the local node",
        )

        oversized_proposal = {
            **PROPOSAL,
            "oversized": "x" * governance_module.MAX_PROPOSAL_BYTES,
        }
        with self.assertRaises(ValueError) as governance_size:
            self.governance.propose(oversized_proposal)
        self.assertEqual(
            str(governance_size.exception),
            "governance proposal exceeds size limit",
        )

        oversized_evolution = {
            **EVOLUTION,
            "oversized": "x" * evolution_module.MAX_RECORD_BYTES,
        }
        with self.assertRaises(ValueError) as evolution_size:
            self.evolution.record(oversized_evolution)
        self.assertEqual(
            str(evolution_size.exception),
            "product evolution record exceeds size limit",
        )

    def test_governance_validation_error_contract(self):
        self.assert_validation_errors(
            validator=GovernanceProposalStore.validate_envelope,
            envelope=PROPOSAL_ENVELOPE,
            content=PROPOSAL_CONTENT,
            identity_field="proposal_id",
            wrong_identity="insight-golden-002",
            maximum=governance_module.MAX_PROPOSAL_BYTES,
            messages={
                "fields": "governance proposal envelope fields are invalid",
                "format": "governance proposal envelope format is unsupported",
                "schema": "governance proposal schema identity is unsupported",
                "identity": "governance proposal envelope ID is invalid",
                "origin": "governance proposal envelope origin mismatch",
                "encoding": "governance proposal content encoding is invalid",
                "size": "governance proposal exceeds size limit",
                "hash": "governance proposal content hash mismatch",
                "content_identity": "governance proposal identity mismatch",
                "content_origin": "governance proposal content origin mismatch",
            },
        )

    def test_product_evolution_validation_error_contract(self):
        self.assert_validation_errors(
            validator=ProductEvolutionStore.validate_envelope,
            envelope=EVOLUTION_ENVELOPE,
            content=EVOLUTION_CONTENT,
            identity_field="record_id",
            wrong_identity="evolution-golden-002",
            maximum=evolution_module.MAX_RECORD_BYTES,
            messages={
                "fields": "product evolution envelope fields are invalid",
                "format": "product evolution envelope format is unsupported",
                "schema": "product evolution schema identity is unsupported",
                "identity": "product evolution envelope ID is invalid",
                "origin": "product evolution envelope origin mismatch",
                "encoding": "product evolution content encoding is invalid",
                "size": "product evolution record exceeds size limit",
                "hash": "product evolution content hash mismatch",
                "content_identity": "product evolution identity mismatch",
                "content_origin": "product evolution content origin mismatch",
            },
        )

    def assert_validation_errors(
        self,
        *,
        validator,
        envelope,
        content,
        identity_field,
        wrong_identity,
        maximum,
        messages,
    ):
        cases = []

        invalid_fields = {**envelope, "unexpected": True}
        cases.append((invalid_fields, {}, messages["fields"]))
        cases.append(
            ({**envelope, "format": "future-format"}, {}, messages["format"])
        )
        cases.append(
            ({**envelope, "schema_id": "future/schema"}, {}, messages["schema"])
        )
        cases.append(({**envelope, identity_field: "x"}, {}, messages["identity"]))
        cases.append((envelope, {"expected_origin": "node-b"}, messages["origin"]))
        cases.append(({**envelope, "content_base64": "***"}, {}, messages["encoding"]))

        oversized = b"x" * (maximum + 1)
        cases.append(
            (
                {
                    **envelope,
                    "content_base64": base64.b64encode(oversized).decode("ascii"),
                    "content_sha256": hashlib.sha256(oversized).hexdigest(),
                },
                {},
                messages["size"],
            )
        )
        cases.append(
            ({**envelope, "content_sha256": "0" * 64}, {}, messages["hash"])
        )

        decoded = json.loads(content)
        wrong_id_content = contract_bytes({**decoded, identity_field: wrong_identity})
        cases.append(
            (
                with_content(envelope, wrong_id_content),
                {},
                messages["content_identity"],
            )
        )
        wrong_origin_content = contract_bytes({**decoded, "origin_node_id": "node-b"})
        cases.append((with_content(envelope, wrong_origin_content), {}, messages["content_origin"]))

        for candidate, kwargs, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                with self.assertRaises(ValueError) as raised:
                    validator(candidate, **kwargs)
                self.assertEqual(str(raised.exception), expected_message)

    def test_read_only_replica_paths_and_safety_flags_remain_distinct(self):
        governance_path = (
            self.governance.registry.root
            / "replicas"
            / "peers"
            / "node-b"
            / "governance-proposals"
            / f"insight-golden-001-{PROPOSAL_DIGEST}.json"
        )
        evolution_path = (
            self.evolution.registry.root
            / "replicas"
            / "peers"
            / "node-b"
            / "product-evolution"
            / f"evolution-golden-001-{EVOLUTION_DIGEST}.json"
        )
        governance_path.parent.mkdir(parents=True, exist_ok=True)
        evolution_path.parent.mkdir(parents=True, exist_ok=True)
        governance_path.write_bytes(PROPOSAL_FILE_BYTES)
        evolution_path.write_bytes(EVOLUTION_FILE_BYTES)

        self.assertEqual(
            governance_path.relative_to(self.governance.registry.root).as_posix(),
            f"replicas/peers/node-b/governance-proposals/{governance_path.name}",
        )
        self.assertEqual(
            evolution_path.relative_to(self.evolution.registry.root).as_posix(),
            f"replicas/peers/node-b/product-evolution/{evolution_path.name}",
        )
        self.assertEqual(
            self.governance.list(),
            {
                "status": "listed",
                "local": [],
                "remote": [PROPOSAL_ENVELOPE],
                "automatic_acceptance": False,
            },
        )
        self.assertEqual(
            self.evolution.list(),
            {
                "status": "listed",
                "local": [],
                "remote": [EVOLUTION_ENVELOPE],
                "automatic_remediation": False,
                "automatic_governance_acceptance": False,
            },
        )


def contract_bytes(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def with_content(envelope: dict, content: bytes) -> dict:
    return {
        **envelope,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }


if __name__ == "__main__":
    unittest.main()
