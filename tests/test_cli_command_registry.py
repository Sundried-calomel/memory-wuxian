import argparse
import dataclasses
import inspect
import sys
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_cli
import memory_cli_runtime
from memory_cli_contract import (
    COMMAND_NAMES,
    COMMAND_REGISTRY,
    COMMAND_SPECS,
    CommandSpec,
    command_lock_path,
    command_spec,
    validate_parser_commands,
)


class CliCommandRegistryTests(unittest.TestCase):
    def parser_commands(self):
        parser = memory_cli.build_parser()
        action = next(
            item
            for item in parser._actions
            if isinstance(item, argparse._SubParsersAction)
        )
        return tuple(action.choices)

    def test_registry_is_immutable_complete_and_in_parser_order(self):
        self.assertEqual(133, len(COMMAND_NAMES))
        self.assertEqual(COMMAND_NAMES, tuple(spec.name for spec in COMMAND_SPECS))
        self.assertEqual(COMMAND_NAMES, tuple(COMMAND_REGISTRY))
        self.assertEqual(COMMAND_NAMES, self.parser_commands())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            COMMAND_SPECS[0].lock_policy = "archive"
        with self.assertRaises(TypeError):
            COMMAND_REGISTRY["new-command"] = COMMAND_SPECS[0]

    def test_every_spec_has_bounded_contract_fields(self):
        for spec in COMMAND_SPECS:
            with self.subTest(command=spec.name):
                self.assertIn(spec.archive_access, {"none", "read", "write", "conditional-write"})
                self.assertIn(spec.external_fs_access, {"none", "read", "write", "read-write"})
                self.assertIn(spec.lock_policy, {
                    "none", "archive", "federation", "environment-exchange",
                    "project-evidence-command", "project-attachment-command", "content-store",
                })
                self.assertIn(spec.mutation_predicate, {"never", "always", "apply"})
                self.assertIn(spec.output_kind, {"json", "text", "service", "binary-or-json"})
                self.assertIn(spec.lifecycle_kind, {"oneshot", "http-server", "mcp-server"})

    def test_parser_registry_validation_is_bidirectional_and_order_sensitive(self):
        validate_parser_commands(COMMAND_NAMES)
        with self.assertRaisesRegex(RuntimeError, "missing=.*status"):
            validate_parser_commands(tuple(name for name in COMMAND_NAMES if name != "status"))
        with self.assertRaisesRegex(RuntimeError, "extra=.*not-a-command"):
            validate_parser_commands(COMMAND_NAMES + ("not-a-command",))
        swapped = list(COMMAND_NAMES)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        with self.assertRaisesRegex(RuntimeError, "order_matches=False"):
            validate_parser_commands(tuple(swapped))

    def test_unknown_command_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown CLI command"):
            command_spec("not-a-command")

    def test_lock_policy_preserves_all_existing_lock_families(self):
        root = Path("X:/archive")
        cases = {
            "append": (False, "archive.lock"),
            "init-node": (False, "federation.lock"),
            "environment-export-delta": (False, "environment-exchange.lock"),
            "project-evidence-build": (True, "project-evidence-command.lock"),
            "project-attachment-build": (True, "project-attachment-command.lock"),
            "content-shadow-build": (True, "content-store.lock"),
        }
        for command, (apply, filename) in cases.items():
            with self.subTest(command=command):
                path = command_lock_path(command_spec(command), SimpleNamespace(apply=apply), root)
                self.assertEqual(root / ".locks" / filename, path)

    def test_all_133_commands_preserve_the_frozen_lock_partition(self):
        expected = {
            "archive": {
                "init", "append", "sync-codex", "token-usage-backfill", "import-chatgpt",
                "status", "backup", "make-summary-job", "ingest-summary", "register-title",
                "rebuild-state", "rebuild-conversations", "rebuild-indexes",
                "index-generation-build", "index-generation-status",
                "index-generation-activate", "index-generation-rollback",
                "rebuild-deterministic-indexes", "export-delta", "migration-apply",
                "project-package-export", "project-package-import", "retrieval-evaluate",
                "semantic-index-build", "semantic-index-clear",
            },
            "federation": {
                "init-node", "add-peer", "revoke-peer", "import-delta",
                "rebuild-global-index", "sync-peer", "cloud-configure", "cloud-pair-import",
                "cloud-sync", "project-attachment-sync", "cloud-enable", "cloud-disable",
            },
            "environment-exchange": {"environment-export-delta"},
            "project-evidence-command": {
                "project-evidence-build", "project-evidence-reconstruct",
                "project-evidence-owner-register", "project-evidence-owner-refresh",
            },
            "project-attachment-command": {
                "project-attachment-build", "project-attachment-reconstruct",
                "project-attachment-owner-register", "project-attachment-owner-refresh",
            },
            "content-store": {
                "content-shadow-build", "content-shadow-reconstruct",
                "content-shadow-disable", "content-transfer",
            },
        }
        claimed = set().union(*expected.values())
        expected["none"] = set(COMMAND_NAMES) - claimed
        for policy, commands in expected.items():
            with self.subTest(policy=policy):
                self.assertEqual(
                    commands,
                    {spec.name for spec in COMMAND_SPECS if spec.lock_policy == policy},
                )
        conditional = {
            "token-usage-backfill",
            *expected["project-evidence-command"],
            *expected["project-attachment-command"],
            *expected["content-store"],
        }
        root = Path("X:/archive")
        for command in COMMAND_NAMES:
            for apply in (False, True):
                with self.subTest(command=command, apply=apply):
                    path = command_lock_path(
                        command_spec(command), SimpleNamespace(apply=apply), root
                    )
                    if command in conditional and not apply:
                        self.assertIsNone(path)
                    elif command_spec(command).lock_policy == "none":
                        self.assertIsNone(path)
                    else:
                        self.assertIsNotNone(path)

    def test_apply_preview_and_direct_routes_remain_unlocked(self):
        root = Path("X:/archive")
        for command in (
            "token-usage-backfill", "project-evidence-build", "project-attachment-build",
            "content-shadow-build",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    command_lock_path(command_spec(command), SimpleNamespace(apply=False), root)
                )
        for command in (
            "configuration-compile", "readonly-http", "readonly-mcp", "heartbeat",
            "environment-governance-ai-status",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    command_lock_path(command_spec(command), SimpleNamespace(), root)
                )

    def test_long_lived_and_output_metadata_preserve_public_shapes(self):
        self.assertEqual("http-server", command_spec("readonly-http").lifecycle_kind)
        self.assertEqual("mcp-server", command_spec("readonly-mcp").lifecycle_kind)
        self.assertEqual("service", command_spec("readonly-http").output_kind)
        self.assertEqual("text", command_spec("retrieve").output_kind)
        self.assertEqual("binary-or-json", command_spec("export-delta").output_kind)

    def test_main_contains_no_embedded_lock_routing_table(self):
        facade_source = inspect.getsource(memory_cli.main)
        self.assertIn("configure_unicode_stdio()", facade_source)
        self.assertIn("run_cli(argv, cli_module=sys.modules[__name__])", facade_source)
        source = inspect.getsource(memory_cli_runtime.run_cli)
        for literal in (
            "archive.lock", "federation.lock", "environment-exchange.lock",
            "project-evidence-command.lock", "project-attachment-command.lock",
            "content-store.lock",
        ):
            self.assertNotIn(literal, source)
        self.assertIn("cli_module.command_lock_path", source)
        self.assertIn("cli_module.command_spec(args.command)", source)

    def test_runtime_resolves_compatibility_dependencies_at_call_time(self):
        parser = mock.Mock()
        args = SimpleNamespace(command="append", config="config.yaml", root=None)
        parser.parse_known_args.return_value = (args, [])
        store = SimpleNamespace(root=Path("X:/archive"))
        with (
            mock.patch.object(memory_cli, "build_parser", return_value=parser),
            mock.patch.object(memory_cli, "dispatch_stateless_read_only_command", return_value=None),
            mock.patch.object(memory_cli, "resolve_config", return_value={"loaded": True}),
            mock.patch.object(memory_cli, "resolve_root", return_value=store.root),
            mock.patch.object(memory_cli, "MemoryStore", return_value=store) as store_factory,
            mock.patch.object(memory_cli, "command_lock_path", return_value=None),
            mock.patch.object(memory_cli, "dispatch_command", return_value=37) as dispatch,
        ):
            self.assertEqual(37, memory_cli_runtime.run_cli([], cli_module=memory_cli))
        store_factory.assert_called_once_with(store.root, {"loaded": True})
        dispatch.assert_called_once_with(args, parser, store)


if __name__ == "__main__":
    unittest.main()
