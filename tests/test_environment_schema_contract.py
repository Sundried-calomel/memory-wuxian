import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


class EnvironmentSchemaContractTests(unittest.TestCase):
    EXPECTED = {
        "environment-artifact.schema.json": {
            "schema_version",
            "artifact_id",
            "object_class",
            "scope",
            "created_at",
        },
        "environment-revision.schema.json": {
            "schema_version",
            "revision_id",
            "artifact_id",
            "origin_node_id",
            "version",
            "base_revision_id",
            "content_sha256",
            "object_path",
            "supported_platforms",
            "runtime_requirements",
            "provenance",
            "lifecycle_state",
            "created_at",
        },
        "environment-project.schema.json": {
            "schema_version",
            "project_id",
            "display_name",
            "active",
            "rule_bindings",
            "skill_bindings",
        },
        "skill-package-manifest.schema.json": {
            "schema_version",
            "skill_id",
            "version",
            "scope",
            "project_id",
            "source_revision",
            "files",
            "supported_platforms",
            "runtime_requirements",
            "network_access",
            "persistent_components",
            "checks",
            "rollback",
        },
        "environment-promotion.schema.json": {
            "schema_version",
            "promotion_id",
            "source_project_id",
            "source_skill_id",
            "source_capability",
            "classification",
            "proposed_global_owner",
            "interface_contract",
            "retained_project_adapter",
            "provenance",
            "validation_matrix",
            "review_state",
            "approval",
        },
        "environment-receipt.schema.json": {
            "schema_version",
            "receipt_id",
            "artifact_id",
            "revision_id",
            "content_sha256",
            "target_node_id",
            "target_binding",
            "previous_installed_sha256",
            "final_installed_sha256",
            "rehearsal",
            "result",
            "rollback",
            "created_at",
        },
        "environment-conflict.schema.json": {
            "schema_version",
            "conflict_id",
            "event_sequence",
            "artifact_id",
            "object_class",
            "base_revision_id",
            "local_revision_id",
            "remote_revision_id",
            "base_content_sha256",
            "local_content_sha256",
            "remote_content_sha256",
            "conflict_kind",
            "decision",
            "status",
            "details",
            "resolution",
            "created_at",
        },
        "semantic-runtime-contract.schema.json": {
            "schema_version",
            "contract_id",
            "interface_version",
            "provider",
            "supported_platforms",
            "model",
            "runtime",
            "embedding",
            "installation",
        },
    }

    def load(self, name):
        return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))

    def test_environment_schemas_are_closed_draft_2020_contracts(self):
        for name, required in self.EXPECTED.items():
            with self.subTest(schema=name):
                schema = self.load(name)
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema["$schema"],
                )
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(required, set(schema["required"]))
                self.assertTrue(required.issubset(schema["properties"]))

    def test_identifiers_and_hashes_are_constrained(self):
        revision = self.load("environment-revision.schema.json")["properties"]
        self.assertEqual("^rev:[0-9a-f]{64}$", revision["revision_id"]["pattern"])
        self.assertEqual(
            "^[0-9a-f]{64}$",
            revision["content_sha256"]["pattern"],
        )
        self.assertEqual(
            "^objects/sha256/[0-9a-f]{2}/[0-9a-f]{62}$",
            revision["object_path"]["pattern"],
        )

    def test_environment_object_classes_are_exact(self):
        expected = {
            "global-rule",
            "project-rule",
            "global-skill",
            "project-skill",
            "global-runtime-contract",
        }
        for schema_name in (
            "environment-artifact.schema.json",
            "environment-conflict.schema.json",
        ):
            classes = self.load(schema_name)["properties"]["object_class"]["enum"]
            self.assertEqual(expected, set(classes), schema_name)

    def test_promotion_requires_review_and_explicit_approval(self):
        promotion = self.load("environment-promotion.schema.json")
        approval = promotion["properties"]["approval"]
        self.assertEqual(True, approval["properties"]["required"]["const"])
        self.assertIn("approved", approval["required"])
        self.assertNotIn(
            "auto-promoted",
            promotion["properties"]["review_state"]["enum"],
        )

    def test_reference_document_names_existing_schemas(self):
        text = (ROOT / "references" / "schemas.md").read_text(encoding="utf-8")
        for name in self.EXPECTED:
            self.assertIn(f"`schemas/{name}`", text)


if __name__ == "__main__":
    unittest.main()
