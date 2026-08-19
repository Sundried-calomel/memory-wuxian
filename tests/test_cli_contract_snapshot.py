from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "cli-contract-v218.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_cli  # noqa: E402


PROGRAM = "memory-wuxian"


def _stable_value(value):
    if value is argparse.SUPPRESS:
        return "<SUPPRESS>"
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        try:
            path = Path(value)
            if path.is_absolute():
                try:
                    return "<SKILL_ROOT>/" + path.relative_to(ROOT).as_posix()
                except ValueError:
                    return "<ABSOLUTE_PATH>/" + path.name
        except (OSError, ValueError):
            pass
        return value
    if isinstance(value, tuple):
        return [_stable_value(item) for item in value]
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_stable_value(item) for item in value), key=str)
    if isinstance(value, dict):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"unstable CLI contract value: {type(value).__name__}")


def _callable_name(value):
    if value is None:
        return None
    module = getattr(value, "__module__", None)
    name = getattr(value, "__qualname__", getattr(value, "__name__", None))
    if name is None:
        raise TypeError(f"unstable CLI callable: {type(value).__name__}")
    return f"{module}.{name}" if module and module != "builtins" else name


def _action_contract(action: argparse.Action) -> dict:
    choices = action.choices
    if choices is not None and not isinstance(choices, dict):
        choices = _stable_value(choices)
    elif isinstance(choices, dict):
        choices = list(choices)
    return {
        "dest": action.dest,
        "option_strings": list(action.option_strings),
        "action_type": type(action).__name__,
        "value_type": _callable_name(action.type),
        "nargs": _stable_value(action.nargs),
        "required": action.required,
        "default": _stable_value(action.default),
        "const": _stable_value(action.const),
        "choices": choices,
        "metavar": _stable_value(action.metavar),
        "help": _stable_value(action.help),
    }


def _build_parser() -> argparse.ArgumentParser:
    with mock.patch.object(sys, "argv", [PROGRAM]):
        return memory_cli.build_parser()


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    matches = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one subparser action, found {len(matches)}")
    return matches[0]


def _capture_parser_exit(arguments: list[str]) -> dict:
    parser = _build_parser()
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            parser.parse_args(arguments)
        except SystemExit as exc:
            code = int(exc.code)
    return {
        "arguments": arguments,
        "exit_code": code,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }


def build_cli_contract_snapshot() -> dict:
    parser = _build_parser()
    subparsers = _subparsers_action(parser)
    command_help = {
        action.dest: action.help for action in subparsers._choices_actions
    }
    commands = []
    for name, command_parser in subparsers.choices.items():
        commands.append(
            {
                "name": name,
                "help": command_help.get(name),
                "description": command_parser.description,
                "actions": [
                    _action_contract(action)
                    for action in command_parser._actions
                    if action.dest != "help"
                ],
            }
        )

    return {
        "schema_version": 1,
        "contract_id": "memory-wuxian-cli-v218",
        "program": PROGRAM,
        "parser": {
            "description": parser.description,
            "actions": [
                _action_contract(action)
                for action in parser._actions
                if action.dest not in {"help", "command"}
            ],
        },
        "command_count": len(commands),
        "commands": commands,
        "parser_exit_cases": [
            _capture_parser_exit([]),
            _capture_parser_exit(["append"]),
            _capture_parser_exit(["status", "--unknown-option"]),
            _capture_parser_exit(["append", "--help"]),
        ],
    }


class CliContractSnapshotTests(unittest.TestCase):
    maxDiff = None

    def test_build_parser_matches_frozen_v218_contract(self):
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(expected, build_cli_contract_snapshot())

    def test_parser_exit_categories_are_stable(self):
        cases = build_cli_contract_snapshot()["parser_exit_cases"]
        self.assertEqual([2, 2, 2, 0], [case["exit_code"] for case in cases])
        self.assertTrue(all(not case["stdout"] for case in cases[:3]))
        self.assertTrue(all(case["stderr"].startswith("usage: ") for case in cases[:3]))
        self.assertIn("--speaker", cases[1]["stderr"])
        self.assertIn("unrecognized arguments", cases[2]["stderr"])
        self.assertIn("usage: memory-wuxian append", cases[3]["stdout"])
        self.assertEqual("", cases[3]["stderr"])

    def test_stateless_json_output_is_utf8_sorted_and_archive_free(self):
        args = argparse.Namespace(
            command="configuration-compile",
            config="unused-config.yaml",
            root=None,
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(
                memory_cli.memory_configuration,
                "compile_configuration",
                return_value={"z": 1, "a": "日本円¥"},
            ),
            mock.patch.object(
                memory_cli,
                "MemoryStore",
                side_effect=AssertionError("stateless output must not open an archive"),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = memory_cli.dispatch_stateless_read_only_command(args)

        self.assertEqual(0, code)
        self.assertEqual(
            '{\n  "a": "日本円¥",\n  "z": 1\n}\n',
            stdout.getvalue(),
        )

    def test_parser_round_trips_special_character_values(self):
        parser = _build_parser()
        value = "-中文 日本語 ¥ ￥ emoji😀 " + ("长" * 260)

        append_args = parser.parse_args(
            ["append", "--speaker", "user", f"--text={value}"]
        )
        path_args = parser.parse_args(
            [
                "environment-register-root",
                "--root-id",
                "unicode-fixture",
                "--role",
                "global-rules",
                "--owner",
                "contract-test",
                f"--path={value}",
            ]
        )

        self.assertEqual(value, append_args.text)
        self.assertEqual(value, path_args.path)

    def test_readonly_unknown_option_is_structured_error_without_archive(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                memory_cli,
                "MemoryStore",
                side_effect=AssertionError("parser errors must not open an archive"),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = memory_cli.main(["readonly-query", "--unknown-option"])

        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        payload = json.loads(stderr.getvalue())
        self.assertEqual("malformed-request", payload["error"]["code"])


def _write_fixture() -> None:
    payload = json.dumps(
        build_cli_contract_snapshot(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    FIXTURE.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(f"wrote {FIXTURE.relative_to(ROOT).as_posix()} sha256={digest}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-fixture"]:
        _write_fixture()
    else:
        unittest.main()
