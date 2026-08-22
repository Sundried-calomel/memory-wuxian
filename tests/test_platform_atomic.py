from pathlib import Path
import ast
import fnmatch
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class PlatformAtomicContractTest(unittest.TestCase):
    def test_architecture_rejects_unregistered_atomic_writer_copies(self):
        contract = json.loads(
            (ROOT / "docs" / "module-architecture.json").read_text(encoding="utf-8")
        )
        rules = contract["python_atomic_storage_rules"]
        required_calls = set(rules["forbidden_call_set"])
        approved = {
            (entry["path"], entry["symbol"])
            for entry in rules["approved_specialized_writers"]
        }
        canonical_path, canonical_symbol = rules["exact_byte_primitive"].split(":", 1)
        approved.add((canonical_path, canonical_symbol))

        modules = contract["modules"]
        canonical_owners = [
            module["id"]
            for module in modules
            if any(
                fnmatch.fnmatchcase(canonical_path, pattern)
                for pattern in module["patterns"]
            )
        ]
        self.assertEqual(canonical_owners, [rules["canonical_owner"]])

        discovered = set()
        for path in sorted((ROOT / "scripts").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = {
                    ast.unparse(call.func)
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                }
                if required_calls <= calls:
                    discovered.add((relative, node.name))

        self.assertEqual(discovered, approved)

    def test_exact_bytes_round_trip_special_character_path(self):
        from platform_atomic import ParentSync, atomic_replace_bytes

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "中文 日本語 ￥ emoji-😀" / "-state.json"
            payload = b'\x00\xffexact\r\nbytes\n'

            result = atomic_replace_bytes(
                path,
                payload,
                parent_sync=ParentSync.NONE,
            )

            self.assertIsNone(result)
            self.assertEqual(path.read_bytes(), payload)

    def test_failure_order_is_fsync_callback_replace_parent_sync(self):
        import platform_atomic

        events = []
        real_fsync = platform_atomic.os.fsync
        real_replace = platform_atomic.os.replace

        def fsync(descriptor):
            events.append("file-fsync")
            return real_fsync(descriptor)

        def before_replace(temporary, destination):
            events.append("callback")
            self.assertEqual(temporary.read_bytes(), b"new")
            self.assertEqual(destination.read_bytes(), b"old")

        def replace(source, destination):
            events.append("replace")
            return real_replace(source, destination)

        def sync_directory(path, *, policy):
            events.append(("parent-sync", policy))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.bin"
            path.write_bytes(b"old")
            with (
                patch.object(platform_atomic.os, "fsync", side_effect=fsync),
                patch.object(platform_atomic.os, "replace", side_effect=replace),
                patch.object(platform_atomic, "sync_directory", side_effect=sync_directory),
            ):
                platform_atomic.atomic_replace_bytes(
                    path,
                    b"new",
                    parent_sync=platform_atomic.ParentSync.BEST_EFFORT,
                    before_replace=before_replace,
                )

            self.assertEqual(
                events,
                [
                    "file-fsync",
                    "callback",
                    "replace",
                    ("parent-sync", platform_atomic.ParentSync.BEST_EFFORT),
                ],
            )
            self.assertEqual(path.read_bytes(), b"new")

    def test_interrupted_callback_preserves_destination_and_cleans_temporary(self):
        from platform_atomic import ParentSync, atomic_replace_bytes

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state.bin"
            path.write_bytes(b"old")

            def interrupt(_temporary, _destination):
                raise RuntimeError("injected before replace")

            with self.assertRaisesRegex(RuntimeError, "injected before replace"):
                atomic_replace_bytes(
                    path,
                    b"new",
                    parent_sync=ParentSync.NONE,
                    before_replace=interrupt,
                )

            self.assertEqual(path.read_bytes(), b"old")
            self.assertEqual([item.name for item in root.iterdir()], ["state.bin"])

    def test_parent_creation_policy_preserves_existing_wrapper_contract(self):
        import migrate_config

        with tempfile.TemporaryDirectory() as temporary:
            missing_parent = Path(temporary) / "missing"
            with self.assertRaises(FileNotFoundError):
                migrate_config.atomic_write(missing_parent / "config.yaml", b"value")
            self.assertFalse(missing_parent.exists())

    def test_cleanup_failure_does_not_mask_original_exception(self):
        import platform_atomic

        def interrupt(_temporary, _destination):
            raise RuntimeError("original failure")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.bin"
            with patch.object(Path, "unlink", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(RuntimeError, "original failure"):
                    platform_atomic.atomic_replace_bytes(
                        path,
                        b"new",
                        parent_sync=platform_atomic.ParentSync.NONE,
                        before_replace=interrupt,
                    )

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not authoritative on Windows")
    def test_mode_is_applied_before_replacement(self):
        from platform_atomic import ParentSync, atomic_replace_bytes

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.bin"
            observed = []

            def before_replace(candidate, _destination):
                observed.append(candidate.stat().st_mode & 0o777)

            atomic_replace_bytes(
                path,
                b"private",
                mode=0o600,
                parent_sync=ParentSync.NONE,
                before_replace=before_replace,
            )

            self.assertEqual(observed, [0o600])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_fchmod_precedes_write_fsync_and_replace_callback(self):
        import platform_atomic

        events = []
        real_fsync = platform_atomic.os.fsync

        def fchmod(_descriptor, mode):
            events.append(("fchmod", mode))

        def fsync(descriptor):
            events.append("file-fsync")
            return real_fsync(descriptor)

        def before_replace(_temporary, _destination):
            events.append("callback")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.bin"
            with (
                patch.object(platform_atomic.os, "fchmod", side_effect=fchmod, create=True),
                patch.object(platform_atomic.os, "fsync", side_effect=fsync),
            ):
                platform_atomic.atomic_replace_bytes(
                    path,
                    b"private",
                    mode=0o600,
                    before_replace=before_replace,
                )

        self.assertEqual(events, [("fchmod", 0o600), "file-fsync", "callback"])

    def test_parent_sync_policy_distinguishes_best_effort_and_required(self):
        import platform_atomic

        with (
            patch.object(platform_atomic.os, "name", "posix"),
            patch.object(platform_atomic.os, "open", side_effect=OSError("unsupported")),
        ):
            platform_atomic.sync_directory(
                Path("missing"),
                policy=platform_atomic.ParentSync.BEST_EFFORT,
            )
            with self.assertRaisesRegex(OSError, "unsupported"):
                platform_atomic.sync_directory(
                    Path("missing"),
                    policy=platform_atomic.ParentSync.REQUIRED,
                )

    def test_existing_serializers_keep_distinct_exact_bytes(self):
        from auto_update import atomic_json as update_json
        from memory_environment import atomic_write_json as environment_json
        from memory_federation import atomic_write_jsonl
        from platform_transaction import atomic_write_canonical_json

        value = {"z": 1, "a": "円"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "canonical.json"
            pretty = root / "pretty.json"
            environment = root / "environment.json"
            jsonl = root / "events.jsonl"

            returned = atomic_write_canonical_json(canonical, value)
            update_json(pretty, value)
            environment_json(environment, value)
            atomic_write_jsonl(jsonl, [value, {"n": 2}])

            self.assertEqual(returned, b'{"a":"\xe5\x86\x86","z":1}')
            self.assertEqual(canonical.read_bytes(), returned)
            expected_pretty = b'{\n  "a": "\xe5\x86\x86",\n  "z": 1\n}\n'
            self.assertEqual(pretty.read_bytes(), expected_pretty)
            self.assertEqual(environment.read_bytes(), expected_pretty)
            self.assertEqual(
                jsonl.read_bytes(),
                b'{"a":"\xe5\x86\x86","z":1}\n{"n":2}\n',
            )

    def test_migrated_wrappers_preserve_bytes_and_return_values(self):
        import collector_activation
        import install_macos_transaction
        import memory_cli
        import memory_environment_bindings
        import memory_environment_rules
        import memory_environment_skills
        import memory_guarded_features
        import memory_indexing
        import migrate_config
        import semantic_plan
        import token_usage

        value = {"z": 1, "a": "円"}
        expected_json = b'{\n  "a": "\xe5\x86\x86",\n  "z": 1\n}\n'
        wrappers = [
            (collector_activation._atomic_json, value, expected_json),
            (memory_cli.atomic_write_json, value, expected_json),
            (semantic_plan.atomic_write_json, value, expected_json),
            (memory_guarded_features.atomic_json, value, expected_json),
            (token_usage.atomic_write_json, value, expected_json),
            (memory_environment_rules._atomic_json, value, expected_json),
            (memory_environment_skills._atomic_json, value, expected_json),
            (memory_environment_bindings._atomic_write_json, value, expected_json),
            (install_macos_transaction.atomic_json, value, expected_json),
            (memory_cli.atomic_write_text, "line1\n円", "line1\n円".encode("utf-8")),
            (memory_indexing._atomic_write_text, "line1\n円", "line1\n円".encode("utf-8")),
            (memory_indexing._atomic_write_bytes, b"\x00raw", b"\x00raw"),
            (migrate_config.atomic_write, b"\xef\xbb\xbfraw\r\n", b"\xef\xbb\xbfraw\r\n"),
            (install_macos_transaction.atomic_bytes, b"\x00raw", b"\x00raw"),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (writer, payload, expected) in enumerate(wrappers):
                path = root / f"{index}-円.bin"
                with self.subTest(writer=f"{writer.__module__}.{writer.__name__}"):
                    self.assertIsNone(writer(path, payload))
                    self.assertEqual(path.read_bytes(), expected)

    def test_domain_wrappers_declare_existing_parent_sync_and_mode_policies(self):
        import install_macos_transaction
        import memory_environment_rules
        import memory_environment_skills
        from platform_atomic import ParentSync

        path = Path("state.bin")
        with patch.object(memory_environment_rules, "atomic_replace_bytes") as writer:
            memory_environment_rules._atomic_bytes(path, b"private")
            writer.assert_called_once_with(
                path,
                b"private",
                mode=0o600,
                parent_sync=ParentSync.BEST_EFFORT,
            )
        with patch.object(memory_environment_skills, "atomic_replace_bytes") as writer:
            memory_environment_skills._atomic_json(path, {"a": 1})
            self.assertEqual(
                writer.call_args.kwargs["parent_sync"],
                ParentSync.BEST_EFFORT,
            )
        with patch.object(install_macos_transaction, "atomic_replace_bytes") as writer:
            install_macos_transaction.atomic_bytes(path, b"durable")
            writer.assert_called_once_with(
                path,
                b"durable",
                parent_sync=ParentSync.REQUIRED,
            )


if __name__ == "__main__":
    unittest.main()
