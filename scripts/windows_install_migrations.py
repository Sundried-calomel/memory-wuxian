#!/usr/bin/env python3
"""Ordered, idempotent cross-version migrations for Windows installation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


class MigrationError(ValueError):
    pass


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def additive_defaults(current: Any, defaults: Any) -> Any:
    """Add missing mapping keys recursively without replacing any user value."""
    if not isinstance(current, dict) or not isinstance(defaults, Mapping):
        return current
    result = {key: value for key, value in current.items()}
    for key, default in defaults.items():
        if key not in result:
            result[key] = default
        else:
            result[key] = additive_defaults(result[key], default)
    return result


@dataclass(frozen=True)
class MigrationStep:
    step_id: str
    from_version: str
    to_version: str
    behavior: str = "additive-defaults-only"


class MigrationRegistry:
    def __init__(self, steps: Sequence[MigrationStep]) -> None:
        self.steps = tuple(steps)
        by_source: dict[str, MigrationStep] = {}
        for step in self.steps:
            if not VERSION_RE.fullmatch(step.from_version) or not VERSION_RE.fullmatch(step.to_version):
                raise MigrationError("migration versions must be semantic x.y.z values")
            if step.from_version in by_source:
                raise MigrationError(f"duplicate migration source: {step.from_version}")
            by_source[step.from_version] = step
        self.by_source = by_source

    def plan(self, from_version: str | None, to_version: str) -> tuple[MigrationStep, ...]:
        if not VERSION_RE.fullmatch(to_version):
            raise MigrationError("target version is invalid")
        if from_version is None or from_version == to_version:
            return ()
        if not VERSION_RE.fullmatch(from_version):
            raise MigrationError("installed version is invalid")
        result: list[MigrationStep] = []
        seen: set[str] = set()
        current = from_version
        while current != to_version:
            if current in seen:
                raise MigrationError("migration registry contains a cycle")
            seen.add(current)
            step = self.by_source.get(current)
            if step is None:
                raise MigrationError(f"unsupported installed version: {current}")
            result.append(step)
            current = step.to_version
            if len(result) > len(self.steps):
                raise MigrationError("migration plan exceeded the registry")
        return tuple(result)

    def migrate_document(
        self,
        current: Mapping[str, Any],
        defaults: Mapping[str, Any],
        *,
        from_version: str | None,
        to_version: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = self.plan(from_version, to_version)
        before = dict(current)
        migrated: Any = before
        for _step in plan:
            migrated = additive_defaults(migrated, defaults)
        if not isinstance(migrated, dict):
            raise MigrationError("configuration migration did not produce a mapping")
        evidence = {
            "from_version": from_version,
            "to_version": to_version,
            "steps": [step.step_id for step in plan],
            "before_sha256": canonical_hash(before),
            "after_sha256": canonical_hash(migrated),
            "idempotent": additive_defaults(migrated, defaults) == migrated,
        }
        return migrated, evidence


def default_registry() -> MigrationRegistry:
    return MigrationRegistry(
        (
            MigrationStep("windows-2.15.0-to-2.18.0", "2.15.0", "2.18.0"),
            MigrationStep("windows-2.18.0-to-2.19.0", "2.18.0", "2.19.0"),
            MigrationStep("windows-2.19.0-to-2.19.1", "2.19.0", "2.19.1"),
            MigrationStep("windows-2.19.1-to-2.20.0", "2.19.1", "2.20.0"),
        )
    )

