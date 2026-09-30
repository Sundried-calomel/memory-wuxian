"""Device-local bindings and guarded Rules, Skills and config application."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from install import ChangedFileInstaller
from storage import bytes_sha256, exclusive_lock, file_sha256, safe_target


def _managed_body(text: str, name: str):
    begin, end = f"<!-- memory-wuxian:{name}:start -->", f"<!-- memory-wuxian:{name}:end -->"
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise RuntimeError("managed block markers are ambiguous")
    if begin not in text:
        return None
    a, b = text.index(begin) + len(begin), text.index(end)
    if b < a:
        raise RuntimeError("managed block marker order is invalid")
    return text[a:b].strip("\r\n")


def _managed_replace(text: str, name: str, body: str):
    begin, end = f"<!-- memory-wuxian:{name}:start -->", f"<!-- memory-wuxian:{name}:end -->"
    prior = _managed_body(text, name)
    body = body.strip('\r\n')
    block = f"{begin}\n{body}\n{end}"
    if prior is None:
        return text + ("\n\n" if text else "") + block + "\n"
    a, b = text.index(begin), text.index(end) + len(end)
    return text[:a] + block + text[b:]


class EnvironmentService:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.registry = safe_target(self.root, ".assembly-bindings.json")
        self.lock = self.registry.with_suffix(".lock")

    def _read(self):
        if not self.registry.exists():
            return {"schema": 1, "bindings": {}}
        value = json.loads(self.registry.read_text("utf-8"))
        if value.get("schema") != 1 or not isinstance(value.get("bindings"), dict):
            raise ValueError("invalid local binding registry")
        return value

    def bind(self, name: str, target, strategy: str = "whole-file") -> dict:
        if not name or strategy not in {"whole-file", "managed-block", "skill-tree", "small-files"}:
            raise ValueError("invalid binding name or strategy")
        path = Path(target)
        if path.is_absolute():
            try:
                relative = path.absolute().relative_to(self.root).as_posix()
            except ValueError as exc:
                raise ValueError("binding target must be beneath the local environment root") from exc
        else:
            relative = path.as_posix()
        target_path = safe_target(self.root, relative)
        baseline = {}
        if target_path.is_file():
            baseline[""] = file_sha256(target_path)
        elif target_path.is_dir() and strategy != "managed-block":
            for file in sorted(p for p in target_path.rglob("*") if p.is_file()):
                rel = file.relative_to(target_path).as_posix()
                safe_target(target_path, rel)
                baseline[rel] = file_sha256(file)
        managed_baseline = None
        if strategy == "managed-block":
            current = target_path.read_bytes().decode("utf-8") if target_path.is_file() else ""
            block = _managed_body(current, name)
            managed_baseline = bytes_sha256(block.encode("utf-8")) if block is not None else None
        item = {"name": name, "target": relative, "strategy": strategy,
                "baseline": baseline, "managed_baseline": managed_baseline}
        with exclusive_lock(self.lock):
            state = self._read()
            state["bindings"][name] = item
            from storage import atomic_write_json
            atomic_write_json(self.registry, state)
        return item

    def apply(self, name: str, files: Mapping[str, bytes], expected_base=None,
              dependency_check: Callable[[Mapping[str, bytes]], bool] | None = None) -> dict:
        if not isinstance(files, Mapping) or not files or any(not isinstance(v, bytes) for v in files.values()):
            raise ValueError("files must be a nonempty mapping of relative paths to exact bytes")
        with exclusive_lock(self.lock):
            state = self._read()
            binding = state["bindings"].get(name)
            if binding is None:
                raise KeyError(name)
            base = safe_target(self.root, binding["target"])
            strategy = binding["strategy"]
            old_baseline = binding["baseline"]
            if strategy == "whole-file":
                if set(files) not in ({""}, {base.name}):
                    raise ValueError("whole-file binding accepts one byte payload under key ''")
                content = files.get("", files.get(base.name))
                actual = file_sha256(base) if base.is_file() else None
                permitted = expected_base if expected_base is not None else old_baseline.get("")
                if actual != permitted and actual != bytes_sha256(content):
                    raise RuntimeError("local target changed since binding/base selection")
                target_rel = base.relative_to(self.root).as_posix()
                writes = {target_rel: content}
                next_baseline = {"": bytes_sha256(content)}
            elif strategy == "managed-block":
                if set(files) != {""} and set(files) != {base.name}:
                    raise ValueError("managed-block binding accepts one UTF-8 block payload")
                content = files.get("", files.get(base.name))
                prior = base.read_bytes().decode("utf-8") if base.exists() else ""
                actual_block = _managed_body(prior, name)
                actual_hash = bytes_sha256(actual_block.encode("utf-8")) if actual_block is not None else None
                expected = expected_base if expected_base is not None else binding.get("managed_baseline")
                if actual_hash != expected:
                    raise RuntimeError("managed block changed locally since binding/base selection")
                desired = content.decode("utf-8")
                merged = _managed_replace(prior, name, desired).encode("utf-8")
                writes = {base.relative_to(self.root).as_posix(): merged}
                next_baseline = dict(old_baseline)
                next_baseline[""] = bytes_sha256(merged)
                binding["managed_baseline"] = bytes_sha256(desired.strip("\r\n").encode("utf-8"))
            else:
                if strategy == "skill-tree":
                    skill_file = next((rel for rel in files if Path(rel).as_posix() == "SKILL.md"), None)
                    if skill_file is None or dependency_check is None:
                        raise ValueError("skill-tree requires SKILL.md and an explicit dependency_check")
                    if dependency_check(files) is not True:
                        raise ValueError("declared Skill dependencies did not validate")
                if expected_base is not None and expected_base != old_baseline:
                    raise RuntimeError("explicit base does not match registered local baseline")
                writes = {}
                for relative, content in files.items():
                    path = safe_target(base, relative)
                    current = file_sha256(path) if path.is_file() else None
                    if current != old_baseline.get(relative) and current != bytes_sha256(content):
                        raise RuntimeError(f"local target changed since binding: {relative}")
                    writes[(Path(binding["target"]) / relative).as_posix()] = content
                next_baseline = dict(old_baseline)
                next_baseline.update({rel: bytes_sha256(data) for rel, data in files.items()})
            binding["baseline"] = next_baseline
            state["bindings"][name] = binding
            registry_bytes = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            # Binding baseline and target files share the same durable changed-file journal.
            writes[".assembly-bindings.json"] = registry_bytes
            def skill_entry_check(_target_root, _changed):
                skill = safe_target(base, "SKILL.md")
                return skill.is_file() and bool(skill.read_bytes())
            result = ChangedFileInstaller(self.root).apply(
                writes, load_check=skill_entry_check if strategy == "skill-tree" else None)
            return {**result, "binding": name, "strategy": strategy,
                    "skill_entry_scope": "SKILL.md-readable; runtime session load not asserted" if strategy == "skill-tree" else None}
