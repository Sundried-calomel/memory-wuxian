from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import Iterable


README_PATHS = ("README.md", "README.zh-CN.md", "README.ja.md")


def project_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]


def assert_minimum_project_version(
    case: unittest.TestCase,
    root: Path,
    minimum: tuple[int, int, int],
) -> str:
    version = project_version(root)
    case.assertGreaterEqual(tuple(map(int, version.split("."))), minimum)
    return version


def assert_documentation_version(
    case: unittest.TestCase,
    root: Path,
    version: str,
) -> None:
    documentation = json.loads(
        (root / "docs/documentation-contract.json").read_text(encoding="utf-8")
    )
    case.assertEqual(documentation["reviewed_version"], version)


def assert_readme_tokens(
    case: unittest.TestCase,
    root: Path,
    tokens: Iterable[str],
) -> None:
    expected = tuple(tokens)
    for readme in README_PATHS:
        text = (root / readme).read_text(encoding="utf-8")
        for token in expected:
            case.assertIn(token, text, f"{readme}: {token}")


def assert_source_tokens(
    case: unittest.TestCase,
    root: Path,
    relative: str,
    *,
    present: Iterable[str] = (),
    absent: Iterable[str] = (),
) -> str:
    source = (root / relative).read_text(encoding="utf-8")
    for token in present:
        case.assertIn(token, source)
    for token in absent:
        case.assertNotIn(token, source)
    return source
