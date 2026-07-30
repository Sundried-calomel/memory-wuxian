#!/usr/bin/env python3
"""Run one deterministic shard of the complete unittest suite."""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(flatten(item))
        else:
            tests.append(item)
    return tests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    if args.count < 1 or not 0 <= args.index < args.count:
        parser.error("require count >= 1 and 0 <= index < count")

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    tests = sorted(flatten(discovered), key=lambda test: test.id())
    selected = tests[args.index :: args.count]
    if not selected:
        parser.error("selected shard is empty")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
