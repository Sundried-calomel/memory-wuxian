import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from memory_configuration import (  # noqa: E402
    ConfigurationError,
    canonical_json_bytes,
    canonical_sha256,
    compile_configuration,
    explain_configuration,
)
from migrate_config import migrate_config  # noqa: E402


class MemoryConfigurationTests(unittest.TestCase):
    REPOSITORY_EFFECTIVE_SHA256 = (
        "14971d80303d3ef1041deec9ff8941da9c7ac98db9a7ffbd008520461532055b"
    )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.missing_pointer = self.base / "missing-pointer.txt"

    def write_config(self, text):
        path = self.base / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def compile(self, path, **kwargs):
        return compile_configuration(
            path,
            environ={},
            active_root_pointer_path=self.missing_pointer,
            **kwargs,
        )

    def test_repository_config_matches_closed_defaults_and_remains_byte_exact(self):
        config_path = ROOT / "config.yaml"
        before = config_path.read_bytes()
        compiled = self.compile(config_path)
        after = config_path.read_bytes()
        defaults = json.loads(
            (ROOT / "contracts/configuration-v1.defaults.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(before, after)
        self.assertEqual(yaml.safe_load(before), defaults["configuration"])
        self.assertEqual(defaults["configuration"], compiled["effective_configuration"])
        self.assertEqual(
            self.REPOSITORY_EFFECTIVE_SHA256,
            compiled["effective_configuration_sha256"],
        )
        self.assertTrue(
            all(
                item["layer"] == "configuration-source"
                for item in compiled["value_sources"].values()
            )
        )
        self.assertEqual(
            hashlib.sha256(before).hexdigest(),
            compiled["source"]["sha256"],
        )
        self.assertEqual(
            str(ROOT / "memory"),
            compiled["root_resolution"]["path"],
        )

    def test_sparse_source_uses_defaults_and_explains_every_leaf(self):
        path = self.write_config("summaries:\n  level_1_trigger_rounds: 7\n")
        compiled = self.compile(path)
        effective = compiled["effective_configuration"]

        self.assertEqual(7, effective["summaries"]["level_1_trigger_rounds"])
        self.assertTrue(effective["backup"]["enabled"])
        self.assertEqual(
            "configuration-source",
            compiled["value_sources"]["/summaries/level_1_trigger_rounds"]["layer"],
        )
        self.assertEqual(
            "defaults-v1",
            compiled["value_sources"]["/backup/enabled"]["layer"],
        )
        self.assertEqual(
            len(self.leaf_paths(effective)),
            len(compiled["value_sources"]),
        )

    def test_canonical_hash_is_stable_across_mapping_order(self):
        left = {"z": [3, 2, 1], "a": {"β": True, "n": 4}}
        right = {"a": {"n": 4, "β": True}, "z": [3, 2, 1]}

        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(left)).hexdigest(),
            canonical_sha256(left),
        )

    def test_duplicate_keys_fail_closed(self):
        path = self.write_config(
            "memory:\n  root_directory: ./one\n  root_directory: ./two\n"
        )
        with self.assertRaisesRegex(ConfigurationError, "duplicate key"):
            self.compile(path)

    def test_unknown_keys_fail_closed(self):
        path = self.write_config("memory:\n  root_directory: ./memory\n  mystery: true\n")
        with self.assertRaisesRegex(
            ConfigurationError, "/memory/mystery: unknown configuration key"
        ):
            self.compile(path)

    def test_invalid_type_and_range_fail_closed(self):
        cases = {
            "type": (
                "backup:\n  enabled: yes\n  retention_count: \"one\"\n",
                "/backup/retention_count: expected type integer",
            ),
            "range": (
                "retrieval:\n  maximum_initial_candidates: 0\n",
                "/retrieval/maximum_initial_candidates: value must be at least 1",
            ),
            "relationship": (
                "context_refresh:\n"
                "  utilization_low_percent: 90\n"
                "  utilization_high_percent: 80\n",
                "utilization_low_percent must be less",
            ),
        }
        for label, (text, message) in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(ConfigurationError, message):
                    self.compile(self.write_config(text))

    def test_root_precedence_is_explicit_environment_pointer_configuration(self):
        path = self.write_config("memory:\n  root_directory: ./from-config\n")
        pointer = self.base / "active-root.txt"
        pointer.write_text("~/from-pointer\n", encoding="utf-8")
        environment = {
            "HOME": str(self.base),
            "MEMORY_WUXIAN_ROOT": "~/from-environment",
        }

        explicit = compile_configuration(
            path,
            root_argument="~/from-explicit",
            environ=environment,
            active_root_pointer_path=pointer,
            skill_root=self.base,
        )
        self.assertEqual("explicit-root", explicit["root_resolution"]["layer"])
        self.assertEqual(
            str(Path("~/from-explicit").expanduser()),
            explicit["root_resolution"]["path"],
        )

        from_environment = compile_configuration(
            path,
            environ=environment,
            active_root_pointer_path=pointer,
            skill_root=self.base,
        )
        self.assertEqual("environment", from_environment["root_resolution"]["layer"])

        from_pointer = compile_configuration(
            path,
            environ={"HOME": str(self.base)},
            active_root_pointer_path=pointer,
            skill_root=self.base,
        )
        self.assertEqual(
            "active-root-pointer", from_pointer["root_resolution"]["layer"]
        )

        pointer.write_text("\n", encoding="utf-8")
        from_configuration = compile_configuration(
            path,
            environ={"HOME": str(self.base)},
            active_root_pointer_path=pointer,
            skill_root=self.base,
        )
        self.assertEqual(
            "configuration-source", from_configuration["root_resolution"]["layer"]
        )
        self.assertEqual(
            str(self.base / "from-config"),
            from_configuration["root_resolution"]["path"],
        )

    def test_unreadable_or_malformed_inputs_do_not_create_state(self):
        absent = self.base / "absent.yaml"
        with self.assertRaisesRegex(ConfigurationError, "not readable"):
            self.compile(absent)
        path = self.write_config("memory: [unterminated\n")
        before = sorted(item.name for item in self.base.iterdir())
        with self.assertRaisesRegex(ConfigurationError, "invalid YAML"):
            self.compile(path)
        after = sorted(item.name for item in self.base.iterdir())
        self.assertEqual(before, after)

    def test_explain_is_a_detached_diagnostic_subset(self):
        path = self.write_config("{}\n")
        compiled = self.compile(path)
        explained = explain_configuration(compiled)

        self.assertEqual(
            {
                "effective_configuration_sha256",
                "root_resolution",
                "value_sources",
            },
            set(explained),
        )
        explained["root_resolution"]["path"] = "changed"
        self.assertNotEqual(
            explained["root_resolution"]["path"],
            compiled["root_resolution"]["path"],
        )

    def test_contracts_are_closed_and_have_no_memory_scope_field(self):
        source_schema = json.loads(
            (ROOT / "schemas/configuration-source.schema.json").read_text(
                encoding="utf-8"
            )
        )
        effective_schema = json.loads(
            (ROOT / "schemas/effective-configuration.schema.json").read_text(
                encoding="utf-8"
            )
        )
        defaults = json.loads(
            (ROOT / "contracts/configuration-v1.defaults.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertFalse(source_schema["additionalProperties"])
        self.assertFalse(effective_schema["additionalProperties"])
        serialized = json.dumps(
            [source_schema, effective_schema, defaults],
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        for prohibited in ("privacy_scope", "sharing_scope", "memory_scope"):
            self.assertNotIn(prohibited, serialized)

    def test_cli_compile_and_explain_are_stdout_only(self):
        path = self.write_config("{}\n")
        before = sorted(item.name for item in self.base.iterdir())
        for command in ("compile", "explain"):
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/memory_configuration.py",
                    command,
                    "--config",
                    str(path),
                    "--root",
                    str(self.base / "archive"),
                ],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                env={**os.environ, "CODEX_HOME": str(self.base / "codex-home")},
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIsInstance(json.loads(completed.stdout), dict)
            self.assertEqual("", completed.stderr)
        self.assertEqual(before, sorted(item.name for item in self.base.iterdir()))

    def test_upgrade_migration_adds_defaults_without_overwriting_user_values(self):
        current = self.write_config(
            "summaries:\n  level_1_trigger_rounds: 99\nbackup:\n  enabled: false\n"
        )
        defaults = self.base / "defaults.yaml"
        defaults.write_text(
            "summaries:\n  level_1_trigger_rounds: 5\n  level_1_trigger_tokens: 5000\n"
            "governance_ai:\n  enabled: false\n",
            encoding="utf-8",
        )
        before = current.read_bytes()

        preview = migrate_config(current, defaults, apply=False)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(current.read_bytes(), before)

        applied = migrate_config(current, defaults, apply=True)
        value = yaml.safe_load(current.read_text(encoding="utf-8"))
        self.assertEqual(value["summaries"]["level_1_trigger_rounds"], 99)
        self.assertEqual(value["summaries"]["level_1_trigger_tokens"], 5000)
        self.assertFalse(value["governance_ai"]["enabled"])
        rollback = Path(applied["rollback"])
        self.assertEqual(rollback.read_bytes(), before)
        self.assertEqual(
            json.loads(Path(applied["receipt"]).read_text(encoding="utf-8"))["after_sha256"],
            applied["after_sha256"],
        )

    @classmethod
    def leaf_paths(cls, value, parts=()):
        if type(value) is dict:
            result = set()
            for key, child in value.items():
                result.update(cls.leaf_paths(child, parts + (key,)))
            return result
        escaped = [part.replace("~", "~0").replace("/", "~1") for part in parts]
        return {"/" + "/".join(escaped)}


if __name__ == "__main__":
    unittest.main()
