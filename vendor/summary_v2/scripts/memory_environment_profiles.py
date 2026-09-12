"""Deterministic, path-free personal Environment profiles for Memory Wuxian."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from memory_environment import EnvironmentRegistry, canonical_bytes, read_json
from memory_environment_bindings import EnvironmentBindingRegistry
from memory_environment_skills import (
    EnvironmentSkillInstaller,
    skill_package_contract_bytes,
)
from memory_federation import atomic_write_bytes, atomic_write_json, atomic_write_jsonl, safe_node_id
from platform_lock import exclusive_lock
from platform_paths import is_link_like


PROFILE_FORMAT = "memory-wuxian-personal-environment-v1"
PLATFORMS = {"windows", "macos", "linux"}
PROVIDER_TYPES = {"user-managed", "system-bundled", "plugin-managed"}
INCOMPLETE_REASONS = {"not-installed", "platform-inapplicable", "provider-unavailable"}
ID_RE = re.compile(r"^(?:skill|system|plugin):[a-z0-9][a-z0-9._-]{1,183}$")
PROVIDER_RE = re.compile(r"^(?:user(?::[a-z0-9][a-z0-9._-]{1,119})?|system:[a-z0-9][a-z0-9._-]{1,119}|plugin:[a-z0-9][a-z0-9._-]{1,119})$")
BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
BLOCK_RE = re.compile(
    rb"<!-- memory-wuxian:managed-block:([a-z0-9][a-z0-9._-]{1,127}):begin -->"
    rb"(.*?)"
    rb"<!-- memory-wuxian:managed-block:\1:end -->",
    re.DOTALL,
)
MAX_SKILLS = 512
MAX_SKILL_FILES = 4096
MAX_SKILL_BYTES = 64 * 1024 * 1024
MAX_RULE_BYTES = 1024 * 1024
MAX_PROFILE_BYTES = 4 * 1024 * 1024
MAX_TREE_DEPTH = 32
MAX_SCAN_SECONDS = 30.0
MAX_TREE_DIRECTORIES = 4096
MAX_RELATIVE_PATH_BYTES = 4096
MAX_TOTAL_PATH_BYTES = 4 * 1024 * 1024
MAX_GENERATIONS = 1024
MAX_GENERATION_STORAGE_BYTES = 256 * 1024 * 1024
MAX_PROFILE_EVENT_BYTES = 4 * 1024 * 1024
MAX_ASSESSMENT_DIFFERENCES = 2048
IGNORED_NAMES = {
    ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", "__pycache__", "cache", "caches", "node_modules", "venv",
    "models", "semantic-index", "semantic-indexes", "archives", "conversations",
}
PROHIBITED_FILE_NAMES = {
    ".env", "credentials", "credentials.json", "id_rsa", "id_ed25519",
    "known_hosts", "secrets.json", "token", "tokens.json",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _public_string(value: Any, label: str, pattern: re.Pattern[str], maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    lowered = value.casefold()
    if any(marker in value for marker in ("/", "\\", "$", "%")) or value.startswith("~") or re.match(r"^[a-zA-Z]:", value):
        raise ValueError(f"{label} contains path or environment syntax")
    return value


def _safe_public_string(value: Any, label: str, pattern: re.Pattern[str], maximum: int) -> str:
    value = _public_string(value, label, pattern, maximum)
    lowered = value.casefold()
    if lowered.startswith(("ghp_", "github_pat_", "sk-", "akia", "age-secret-key-")):
        raise ValueError(f"{label} resembles secret material")
    local_identities = {
        str(candidate).strip().casefold()
        for candidate in (
            os.environ.get("USERNAME"), os.environ.get("USER"),
            os.environ.get("COMPUTERNAME"), os.environ.get("HOSTNAME"),
            socket.gethostname(),
        )
        if candidate and str(candidate).strip().casefold() != "user"
    }
    if any(identity in lowered for identity in local_identities):
        raise ValueError(f"{label} contains local user or host identity")
    return value


def _strict(value: Dict[str, Any], allowed: Iterable[str], required: Iterable[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing fields: {sorted(missing)}")


def _atomic_pointer(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EnvironmentProfileManager:
    """Capture and compare immutable profiles without granting activation authority."""

    def __init__(self, archive_root: Path | str):
        self.archive_root = Path(archive_root)
        self.registry = EnvironmentRegistry(self.archive_root)
        self.root = self.registry.root / "profiles"
        self.generations = self.root / "generations"
        self.current_path = self.root / "current.json"
        self.events_path = self.root / "local-events.jsonl"
        self.transaction_path = self.root / "transaction.json"
        self.lock_path = self.archive_root / ".locks" / "environment-exchange.lock"

    def init_layout(self) -> None:
        self.registry.init()
        self._resolve_local_layout()
        self.generations.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            atomic_write_bytes(self.events_path, b"")

    def capture(self, specification: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        profile = self._build_profile(specification)
        if len(canonical_bytes(profile)) > MAX_PROFILE_BYTES:
            raise ValueError("environment profile exceeds size limit")
        self.validate_profile(profile)
        if not apply:
            self._resolve_local_layout()
            previous = self._validated_pointer(optional=True)
            if previous is None and self.generations.is_dir() and any(self.generations.glob("*.json")):
                raise ValueError("Environment profile pointer is missing; rebuild current before capture")
            return self._capture_result(profile, previous, apply=False)
        self.init_layout()
        with exclusive_lock(self.lock_path):
            self._recover_transaction_unlocked()
            previous = self._validated_pointer(optional=True)
            generations = self._load_generations()
            if generations and previous is None:
                raise ValueError("Environment profile pointer is missing; rebuild current before capture")
            if previous is not None:
                head = self._chain_head(generations)
                if previous["generation_id"] != head["generation_id"]:
                    raise ValueError("current Environment profile pointer is not the generation head")
            events = self._read_profile_events()
            self._validate_events(
                events, {item["generation_id"]: item for item in generations}
            )
            return self._capture_result(profile, previous, apply=True, generations=generations)

    def _capture_result(
        self,
        profile: Dict[str, Any],
        previous: Optional[Dict[str, Any]],
        *,
        apply: bool,
        generations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if previous and previous.get("profile_sha256") == profile["profile_sha256"]:
            return {"status": "unchanged", "applied": False, "profile": profile, "generation_id": previous["generation_id"]}
        generation_base = {
            "schema_version": 1,
            "generation_format": "memory-wuxian-environment-profile-generation-v1",
            "previous_generation_id": previous.get("generation_id") if previous else None,
            "profile_sha256": profile["profile_sha256"],
            "profile": profile,
        }
        generation_sha256 = _sha256(canonical_bytes(generation_base))
        generation_value = {**generation_base, "generation_id": f"generation:{generation_sha256}"}
        self.validate_generation(generation_value)
        if not apply:
            return {"status": "preview", "applied": False, "profile": profile, "generation": generation_value}
        encoded = canonical_bytes(generation_value) + b"\n"
        generations = generations or []
        existing_bytes = sum(len(canonical_bytes(item)) + 1 for item in generations)
        if len(generations) >= MAX_GENERATIONS or existing_bytes + len(encoded) > MAX_GENERATION_STORAGE_BYTES:
            raise ValueError("Environment profile generation storage exceeds limit")
        events = self._read_profile_events()
        self._validate_events(events, {item["generation_id"]: item for item in generations})
        generation = self.generations / f"{generation_sha256}.json"
        generation_was_present = generation.exists()
        if generation.exists():
            if is_link_like(generation) or generation.read_bytes() != encoded:
                raise ValueError("environment profile generation conflicts with immutable content")
        source_event_id = f"profile-generation:{generation_sha256}"
        matching = [event for event in events if event.get("source_event_id") == source_event_id]
        event = {
            "source_event_id": source_event_id,
            "profile_id": profile["profile_id"],
            "generation_id": generation_value["generation_id"],
            "generation_sha256": generation_sha256,
        }
        if matching and (len(matching) != 1 or matching[0] != event):
            raise ValueError("environment profile export event conflicts with immutable content")
        pointer = {"schema_version": 1, "generation_id": generation_value["generation_id"], "generation_sha256": generation_sha256, "profile_id": profile["profile_id"], "profile_sha256": profile["profile_sha256"]}
        transaction = {
            "schema_version": 1,
            "generation_sha256": generation_sha256,
            "generation_was_present": generation_was_present,
            "previous_events": events,
            "previous_pointer": previous,
            "new_event": event,
            "new_pointer": pointer,
            "new_generation": generation_value,
        }
        transaction["transaction_sha256"] = _sha256(canonical_bytes(transaction))
        atomic_write_json(self.transaction_path, transaction)
        try:
            if not generation_was_present:
                atomic_write_bytes(generation, encoded)
            if not matching:
                atomic_write_jsonl(self.events_path, [*events, event])
            _atomic_pointer(self.current_path, pointer)
            self.transaction_path.unlink()
        except Exception:
            self._recover_transaction_unlocked()
            raise
        return {"status": "created", "applied": True, "profile": profile, "generation": generation_value}

    def _build_profile(self, specification: Dict[str, Any]) -> Dict[str, Any]:
        _strict(specification, {"schema_version", "platform", "skills", "rules"}, {"schema_version", "platform", "skills", "rules"}, "profile specification")
        if specification["schema_version"] != 1:
            raise ValueError("unsupported profile specification version")
        platform = specification["platform"]
        if platform not in PLATFORMS:
            raise ValueError("unsupported profile platform")
        skill_specs = specification["skills"]
        rule_specs = specification["rules"]
        if not isinstance(skill_specs, list) or len(skill_specs) > MAX_SKILLS:
            raise ValueError("profile skill count is invalid")
        if not isinstance(rule_specs, list) or len(rule_specs) > 2:
            raise ValueError("profile Rule count is invalid")
        skills = [self._inventory_skill(item, platform) for item in skill_specs]
        rules = [self._inventory_rule(item) for item in rule_specs]
        if len({item["installation_id"] for item in skills}) != len(skills):
            raise ValueError("duplicate Skill installation identity")
        if len({item["rule_id"] for item in rules}) != len(rules):
            raise ValueError("duplicate global Rule identity")
        skills.sort(key=lambda item: (item["installation_id"], item["provider_type"], item["provider_id"]))
        rules.sort(key=lambda item: item["rule_id"])
        base = {"schema_version": 1, "profile_format": PROFILE_FORMAT, "platform": platform, "skills": skills, "rules": rules}
        digest = _sha256(canonical_bytes(base))
        return {**base, "profile_sha256": digest, "profile_id": f"profile:{digest}"}

    def _inventory_skill(self, value: Dict[str, Any], platform: str) -> Dict[str, Any]:
        allowed = {"installation_id", "provider_type", "provider_id", "applicable_platforms", "declared_version", "root", "incomplete_reason"}
        required = {"installation_id", "provider_type", "provider_id", "applicable_platforms"}
        _strict(value, allowed, required, "Skill source")
        installation_id = value["installation_id"]
        provider_type = value["provider_type"]
        provider_id = value["provider_id"]
        platforms = value["applicable_platforms"]
        version = value.get("declared_version")
        installation_id = _safe_public_string(installation_id, "Skill installation identity", ID_RE, 192)
        if provider_type not in PROVIDER_TYPES:
            raise ValueError("invalid Skill provider type")
        provider_id = _safe_public_string(provider_id, "Skill provider identity", PROVIDER_RE, 128)
        provider_prefixes = {
            "user-managed": ("user", "user:"),
            "system-bundled": ("system:",),
            "plugin-managed": ("plugin:",),
        }
        if not provider_id.startswith(provider_prefixes[provider_type]):
            raise ValueError("Skill provider type and identity disagree")
        installation_prefixes = {
            "user-managed": "skill:",
            "system-bundled": "system:",
            "plugin-managed": "plugin:",
        }
        if not installation_id.startswith(installation_prefixes[provider_type]):
            raise ValueError("Skill installation identity and provider type disagree")
        if not isinstance(platforms, list) or not platforms or len(platforms) > 3 or len(set(platforms)) != len(platforms) or any(item not in PLATFORMS for item in platforms):
            raise ValueError("invalid Skill platform applicability")
        platforms = sorted(platforms)
        if version is not None:
            version = _safe_public_string(version, "declared Skill version", re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$"), 128)
        incomplete = value.get("incomplete_reason")
        root = value.get("root")
        if platform not in platforms:
            incomplete = "platform-inapplicable"
        if incomplete is not None:
            if incomplete not in INCOMPLETE_REASONS or root is not None:
                raise ValueError("incomplete Skill source must have one allowed reason and no root")
            return {
                "installation_id": installation_id, "provider_type": provider_type,
                "provider_id": provider_id, "applicable_platforms": platforms,
                "inventory_status": "incomplete", "incomplete_reason": incomplete,
                "declared_version": version, "tree_sha256": None, "file_count": 0, "byte_count": 0,
            }
        if not isinstance(root, str) or not root:
            raise ValueError("complete Skill source requires an explicit root")
        root_path = Path(root)
        metadata_name, metadata_version = self._skill_metadata(root_path)
        if (
            provider_type == "user-managed"
            and installation_id != f"skill:{metadata_name}"
        ):
            raise ValueError("user-managed Skill identity does not match SKILL.md name")
        if metadata_version is not None:
            metadata_version = _safe_public_string(
                metadata_version,
                "SKILL.md version",
                re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$"),
                128,
            )
        if version is not None and metadata_version is not None and version != metadata_version:
            raise ValueError("declared Skill version conflicts with SKILL.md metadata")
        version = metadata_version or version
        tree_sha256, file_count, byte_count = self._hash_tree(root_path)
        return {
            "installation_id": installation_id, "provider_type": provider_type,
            "provider_id": provider_id, "applicable_platforms": platforms,
            "inventory_status": "complete", "incomplete_reason": None,
            "declared_version": version, "tree_sha256": tree_sha256,
            "file_count": file_count, "byte_count": byte_count,
        }

    def _hash_tree(self, root: Path) -> Tuple[str, int, int]:
        if not root.is_dir() or is_link_like(root):
            raise ValueError("Skill root is missing, unreadable, or link-like")
        portable_paths = set()
        stack = [(root, 0)]
        records: List[Tuple[str, bytes]] = []
        started = time.monotonic()
        directory_count = 0
        path_bytes = 0
        file_count = 0
        byte_count = 0
        while stack:
            if time.monotonic() - started > MAX_SCAN_SECONDS:
                raise ValueError("Skill tree exceeds inventory time limit")
            directory, depth = stack.pop()
            directory_count += 1
            if directory_count > MAX_TREE_DIRECTORIES:
                raise ValueError("Skill tree exceeds directory limit")
            if depth > MAX_TREE_DEPTH:
                raise ValueError("Skill tree exceeds depth limit")
            try:
                children = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        children.append(Path(entry.path))
                        if len(children) > MAX_SKILL_FILES + MAX_TREE_DIRECTORIES:
                            raise ValueError("Skill directory exceeds entry limit")
                        if time.monotonic() - started > MAX_SCAN_SECONDS:
                            raise ValueError("Skill tree exceeds inventory time limit")
                children.sort(key=lambda item: unicodedata.normalize("NFC", item.name))
            except OSError as error:
                raise ValueError("Skill tree cannot be enumerated") from error
            for path in children:
                if path.name.lower() in IGNORED_NAMES:
                    continue
                if is_link_like(path):
                    raise ValueError("Skill tree contains a link or junction")
                try:
                    directory_entry = path.is_dir()
                except OSError as error:
                    raise ValueError("Skill tree contains an unreadable entry") from error
                if directory_entry:
                    stack.append((path, depth + 1))
                else:
                    if not path.is_file():
                        raise ValueError("Skill tree contains a non-regular file")
                    relative_text = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
                    encoded_path = relative_text.encode("utf-8")
                    path_bytes += len(encoded_path)
                    if len(encoded_path) > MAX_RELATIVE_PATH_BYTES or path_bytes > MAX_TOTAL_PATH_BYTES:
                        raise ValueError("Skill tree exceeds portable path limits")
                    relative_key = relative_text.casefold()
                    if relative_key in portable_paths:
                        raise ValueError("Skill tree contains colliding portable paths")
                    portable_paths.add(relative_key)
                    components = [part.casefold() for part in path.relative_to(root).parts]
                    if any(self._credential_component(part) for part in components):
                        raise ValueError("Skill tree contains prohibited credential material")
                    try:
                        size = path.stat().st_size
                    except OSError as error:
                        raise ValueError("Skill tree contains an unreadable file") from error
                    file_count += 1
                    byte_count += int(size)
                    if file_count > MAX_SKILL_FILES or byte_count > MAX_SKILL_BYTES:
                        raise ValueError("Skill tree exceeds inventory limits")
                    digest = hashlib.sha256()
                    actual_size = 0
                    try:
                        with path.open("rb") as handle:
                            while True:
                                chunk = handle.read(1024 * 1024)
                                if time.monotonic() - started > MAX_SCAN_SECONDS:
                                    raise ValueError("Skill tree exceeds inventory time limit")
                                if not chunk:
                                    break
                                actual_size += len(chunk)
                                if actual_size > int(size) or byte_count - int(size) + actual_size > MAX_SKILL_BYTES:
                                    raise ValueError("Skill file changed or exceeds inventory limits during capture")
                                digest.update(chunk)
                    except OSError as error:
                        raise ValueError("Skill tree contains an unreadable file") from error
                    if actual_size != int(size):
                        raise ValueError("Skill file changed during capture")
                    record = encoded_path + b"\0" + digest.hexdigest().encode("ascii") + b"\0" + str(actual_size).encode("ascii") + b"\n"
                    records.append((relative_key, record))
        return _sha256(b"".join(record for _, record in sorted(records))), file_count, byte_count

    @staticmethod
    def _skill_metadata(root: Path) -> Tuple[str, Optional[str]]:
        path = root / "SKILL.md"
        if not path.is_file() or is_link_like(path):
            raise ValueError("Skill root must contain one regular SKILL.md")
        try:
            size = path.stat().st_size
            if size > 1024 * 1024:
                raise ValueError("SKILL.md metadata exceeds size limit")
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError("SKILL.md metadata is unreadable or not UTF-8") from error
        try:
            frontmatter = EnvironmentSkillInstaller._parse_frontmatter(text)
        except ValueError as error:
            raise ValueError("SKILL.md metadata frontmatter is malformed") from error
        name = frontmatter.get("name")
        if not isinstance(name, str) or not isinstance(frontmatter.get("description"), str):
            raise ValueError("SKILL.md metadata frontmatter is malformed")
        version = frontmatter.get("version")
        if version is not None and not isinstance(version, str):
            raise ValueError("SKILL.md metadata version must be text")
        return name, version

    @staticmethod
    def _credential_component(name: str) -> bool:
        exact = PROHIBITED_FILE_NAMES | {".npmrc", ".pypirc", ".ssh", "secrets", "credential", "api_key", "apikey", "access_token", "refresh_token"}
        return (
            name in exact
            or name.startswith(".env.")
            or name.startswith("credentials.")
            or name.startswith("secrets.")
            or name.startswith("token.")
            or name.startswith("access_token.")
            or name.startswith("refresh_token.")
            or "private-key" in name
        )

    def _inventory_rule(self, value: Dict[str, Any]) -> Dict[str, Any]:
        _strict(value, {"rule_id", "path"}, {"rule_id", "path"}, "Rule source")
        rule_id = value["rule_id"]
        if rule_id not in {"global-agents", "global-agents-override"}:
            raise ValueError("only global AGENTS Rules may enter a personal profile")
        path = Path(value["path"])
        if not path.is_file() or is_link_like(path):
            raise ValueError("Rule source is missing or link-like")
        try:
            size = path.stat().st_size
            if size > MAX_RULE_BYTES:
                raise ValueError("Rule source exceeds size limit")
            content = path.read_bytes()
            content.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError("Rule source must be readable UTF-8") from error
        if len(content) > MAX_RULE_BYTES or len(content) != size:
            raise ValueError("Rule source exceeds size limit")
        blocks = []
        seen = set()
        for match in BLOCK_RE.finditer(content):
            block_id = match.group(1).decode("ascii")
            if block_id in seen:
                raise ValueError("duplicate managed Rule block identity")
            seen.add(block_id)
            block = match.group(0)
            blocks.append({"managed_block_id": block_id, "content_sha256": _sha256(block), "byte_count": len(block)})
        blocks.sort(key=lambda item: item["managed_block_id"])
        return {"rule_id": rule_id, "content_sha256": _sha256(content), "byte_count": len(content), "managed_blocks": blocks}

    def _validated_pointer(
        self,
        *,
        optional: bool = False,
        generations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.current_path.exists():
            if optional:
                return None
            raise ValueError("no current Environment profile")
        pointer = read_json(self.current_path)
        fields = {"schema_version", "generation_id", "generation_sha256", "profile_id", "profile_sha256"}
        _strict(pointer, fields, fields, "profile pointer")
        digest = pointer["generation_sha256"]
        if re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None or pointer["generation_id"] != f"generation:{digest}":
            raise ValueError("current Environment profile pointer hash is invalid")
        if generations_by_id is None:
            path = self.generations / f"{digest}.json"
            if is_link_like(path) or not path.is_file():
                raise ValueError("current Environment profile generation is missing")
            generation = self.validate_generation(read_json(path))
        else:
            generation = generations_by_id.get(pointer["generation_id"])
            if generation is None:
                raise ValueError("current Environment profile generation is missing")
        profile = generation["profile"]
        if generation["generation_id"] != pointer["generation_id"] or profile["profile_id"] != pointer["profile_id"] or profile["profile_sha256"] != pointer["profile_sha256"]:
            raise ValueError("current Environment profile pointer mismatch")
        return pointer

    def current(self) -> Dict[str, Any]:
        self._resolve_local_layout()
        generations = self._load_generations()
        pointer = self._validated_pointer(
            generations_by_id={item["generation_id"]: item for item in generations}
        )
        head = self._chain_head(generations)
        if head["generation_id"] != pointer["generation_id"]:
            raise ValueError("current Environment profile pointer is not the generation head")
        return head["profile"]

    def _read_profile_events(self) -> List[Dict[str, Any]]:
        self._resolve_local_layout()
        if not self.events_path.exists():
            return []
        if is_link_like(self.events_path) or not self.events_path.is_file():
            raise ValueError("Environment profile event log is unsafe")
        try:
            if self.events_path.stat().st_size > MAX_PROFILE_EVENT_BYTES:
                raise ValueError("Environment profile event log exceeds size limit")
            events = []
            total_bytes = 0
            with self.events_path.open("rb") as handle:
                for raw_line in handle:
                    total_bytes += len(raw_line)
                    if total_bytes > MAX_PROFILE_EVENT_BYTES:
                        raise ValueError("Environment profile event log exceeds size limit")
                    if not raw_line.strip():
                        continue
                    if len(events) >= MAX_GENERATIONS:
                        raise ValueError("Environment profile event count exceeds limit")
                    value = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(value, dict):
                        raise ValueError("profile event: expected object")
                    events.append(value)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Environment profile event log is unreadable") from error
        return events

    def local_events(
        self,
        generations_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        events = self._read_profile_events()
        expanded = []
        for event in events:
            fields = {"source_event_id", "profile_id", "generation_id", "generation_sha256"}
            _strict(event, fields, fields, "profile event")
            if generations_by_id is None:
                generation_path = self.generations / f"{event['generation_sha256']}.json"
                if is_link_like(generation_path) or not generation_path.is_file():
                    raise ValueError("profile event generation is missing or unsafe")
                generation = self.validate_generation(read_json(generation_path))
            else:
                generation = generations_by_id.get(event["generation_id"])
                if generation is None:
                    raise ValueError("profile event generation is missing or unsafe")
            if event["profile_id"] != generation["profile"]["profile_id"] or event["generation_id"] != generation["generation_id"] or event["generation_sha256"] != generation["generation_id"].split(":", 1)[1]:
                raise ValueError("profile event identity mismatch")
            expanded.append({**event, "generation": generation})
        return expanded

    def _validate_events(
        self,
        events: List[Dict[str, Any]],
        generations: Dict[str, Dict[str, Any]],
    ) -> None:
        if len(events) > MAX_GENERATIONS:
            raise ValueError("Environment profile event count exceeds limit")
        seen = set()
        for event in events:
            fields = {"source_event_id", "profile_id", "generation_id", "generation_sha256"}
            _strict(event, fields, fields, "profile event")
            generation_id = event["generation_id"]
            digest = event["generation_sha256"]
            if (
                event["source_event_id"] != f"profile-generation:{digest}"
                or generation_id != f"generation:{digest}"
                or generation_id in seen
                or generation_id not in generations
                or event["profile_id"] != generations[generation_id]["profile"]["profile_id"]
            ):
                raise ValueError("profile event identity mismatch")
            seen.add(generation_id)
        if seen != set(generations):
            raise ValueError("Environment profile generation and export event sets differ")

    @classmethod
    def validate_generation(cls, generation: Dict[str, Any]) -> Dict[str, Any]:
        fields = {"schema_version", "generation_format", "previous_generation_id", "profile_sha256", "profile", "generation_id"}
        _strict(generation, fields, fields, "Environment profile generation")
        if generation["schema_version"] != 1 or generation["generation_format"] != "memory-wuxian-environment-profile-generation-v1":
            raise ValueError("unsupported Environment profile generation")
        previous = generation["previous_generation_id"]
        if previous is not None and re.fullmatch(r"generation:[0-9a-f]{64}", str(previous)) is None:
            raise ValueError("invalid previous Environment profile generation")
        profile = cls.validate_profile(generation["profile"])
        if generation["profile_sha256"] != profile["profile_sha256"]:
            raise ValueError("Environment profile generation profile hash mismatch")
        base = dict(generation)
        generation_id = base.pop("generation_id", None)
        expected = f"generation:{_sha256(canonical_bytes(base))}"
        if generation_id != expected:
            raise ValueError("Environment profile generation hash mismatch")
        return generation

    def rebuild_current(self, *, apply: bool = False) -> Dict[str, Any]:
        self._resolve_local_layout()
        if not self.generations.is_dir():
            raise ValueError("no Environment profile generations to rebuild")
        if apply:
            self.init_layout()
            with exclusive_lock(self.lock_path):
                self._recover_transaction_unlocked()
                return self._rebuild_current(apply=True)
        if self.transaction_path.exists():
            raise ValueError("Environment profile transaction requires apply recovery")
        return self._rebuild_current(apply=False)

    def _recover_transaction_unlocked(self) -> None:
        if not self.transaction_path.exists():
            return
        if is_link_like(self.transaction_path) or not self.transaction_path.is_file():
            raise ValueError("Environment profile transaction path is unsafe")
        transaction = read_json(self.transaction_path)
        fields = {
            "schema_version", "generation_sha256", "generation_was_present",
            "previous_events", "previous_pointer", "new_event", "new_pointer",
            "new_generation", "transaction_sha256",
        }
        _strict(transaction, fields, fields, "Environment profile transaction")
        unsealed = dict(transaction)
        transaction_sha256 = unsealed.pop("transaction_sha256")
        if (
            transaction["schema_version"] != 1
            or type(transaction["generation_was_present"]) is not bool
            or transaction_sha256 != _sha256(canonical_bytes(unsealed))
        ):
            raise ValueError("Environment profile transaction is invalid")
        generation = self.validate_generation(transaction["new_generation"])
        digest = generation["generation_id"].split(":", 1)[1]
        if digest != transaction["generation_sha256"]:
            raise ValueError("Environment profile transaction generation mismatch")
        expected_event = {
            "source_event_id": f"profile-generation:{digest}",
            "profile_id": generation["profile"]["profile_id"],
            "generation_id": generation["generation_id"],
            "generation_sha256": digest,
        }
        expected_pointer = {
            "schema_version": 1,
            "generation_id": generation["generation_id"],
            "generation_sha256": digest,
            "profile_id": generation["profile"]["profile_id"],
            "profile_sha256": generation["profile_sha256"],
        }
        if (
            transaction["new_event"] != expected_event
            or transaction["new_pointer"] != expected_pointer
            or not isinstance(transaction["previous_events"], list)
        ):
            raise ValueError("Environment profile transaction before/after state is invalid")
        stored_generations = {
            item["generation_id"]: item for item in self._load_generations()
        }
        previous_ids = {
            item.get("generation_id")
            for item in transaction["previous_events"]
            if isinstance(item, dict)
        }
        previous_generations = {
            generation_id: stored_generations[generation_id]
            for generation_id in previous_ids
            if generation_id in stored_generations
        }
        self._validate_events(transaction["previous_events"], previous_generations)
        previous_pointer = transaction["previous_pointer"]
        if previous_pointer is not None:
            pointer_fields = {
                "schema_version", "generation_id", "generation_sha256",
                "profile_id", "profile_sha256",
            }
            _strict(previous_pointer, pointer_fields, pointer_fields, "previous profile pointer")
            previous_generation = previous_generations.get(previous_pointer["generation_id"])
            if (
                previous_generation is None
                or previous_pointer["schema_version"] != 1
                or previous_pointer["generation_sha256"]
                != previous_pointer["generation_id"].split(":", 1)[-1]
                or previous_pointer["profile_id"]
                != previous_generation["profile"]["profile_id"]
                or previous_pointer["profile_sha256"]
                != previous_generation["profile_sha256"]
            ):
                raise ValueError("previous Environment profile pointer is invalid")
        generation_path = self.generations / f"{digest}.json"
        expected_generation = canonical_bytes(generation) + b"\n"
        expected_events = [*transaction["previous_events"], transaction["new_event"]]
        try:
            complete = (
                generation_path.is_file()
                and not is_link_like(generation_path)
                and generation_path.read_bytes() == expected_generation
                and self._read_profile_events() == expected_events
                and self.current_path.is_file()
                and not is_link_like(self.current_path)
                and read_json(self.current_path) == transaction["new_pointer"]
            )
        except (OSError, ValueError, json.JSONDecodeError):
            complete = False
        if complete:
            self.transaction_path.unlink()
            return
        atomic_write_jsonl(self.events_path, transaction["previous_events"])
        if previous_pointer is None:
            self.current_path.unlink(missing_ok=True)
        else:
            # Recovery must not depend on the operation that failed while
            # committing the new pointer.
            atomic_write_json(self.current_path, previous_pointer)
        if not transaction["generation_was_present"] and generation_path.exists():
            if is_link_like(generation_path) or generation_path.read_bytes() != expected_generation:
                raise ValueError("Environment profile transaction generation changed during recovery")
            generation_path.unlink()
        self.transaction_path.unlink()

    def _rebuild_current(self, *, apply: bool) -> Dict[str, Any]:
        generations = self._load_generations()
        self._validate_events(
            self._read_profile_events(),
            {item["generation_id"]: item for item in generations},
        )
        head = self._chain_head(generations)
        digest = head["generation_id"].split(":", 1)[1]
        pointer = {"schema_version": 1, "generation_id": head["generation_id"], "generation_sha256": digest, "profile_id": head["profile"]["profile_id"], "profile_sha256": head["profile_sha256"]}
        if apply:
            _atomic_pointer(self.current_path, pointer)
        return {"status": "rebuilt" if apply else "preview", "applied": apply, "current": pointer}

    @staticmethod
    def _chain_head(generations: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not generations:
            raise ValueError("no Environment profile generations to rebuild")
        by_id = {item["generation_id"]: item for item in generations}
        ids = set(by_id)
        referenced = {item["previous_generation_id"] for item in generations if item["previous_generation_id"] is not None}
        dangling = referenced - ids
        heads = [item for item in generations if item["generation_id"] not in referenced]
        if dangling or len(heads) != 1:
            raise ValueError("Environment profile generation chain has no unique complete head")
        head = heads[0]
        visited = set()
        cursor = head
        while cursor is not None:
            generation_id = cursor["generation_id"]
            if generation_id in visited:
                raise ValueError("Environment profile generation chain contains a cycle")
            visited.add(generation_id)
            previous_id = cursor["previous_generation_id"]
            cursor = by_id.get(previous_id) if previous_id is not None else None
        if visited != ids:
            raise ValueError("Environment profile generations do not form one complete chain")
        return head

    @classmethod
    def validate_generation_chain(
        cls, generations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if len(generations) > MAX_GENERATIONS:
            raise ValueError("Environment profile generation count exceeds limit")
        validated = [cls.validate_generation(item) for item in generations]
        if sum(len(canonical_bytes(item)) + 1 for item in validated) > MAX_GENERATION_STORAGE_BYTES:
            raise ValueError("Environment profile generation storage exceeds limit")
        if len({item["generation_id"] for item in validated}) != len(validated):
            raise ValueError("duplicate Environment profile generation identity")
        return cls._chain_head(validated)

    def _load_generations(self) -> List[Dict[str, Any]]:
        paths = []
        if self.generations.is_dir():
            if is_link_like(self.generations):
                raise ValueError("Environment profile generation root is unsafe")
            started = time.monotonic()
            entries_seen = 0
            with os.scandir(self.generations) as entries:
                for entry in entries:
                    entries_seen += 1
                    if (
                        entries_seen > MAX_GENERATIONS
                        or time.monotonic() - started > MAX_SCAN_SECONDS
                    ):
                        raise ValueError("Environment profile generation scan exceeds limit")
                    path = Path(entry.path)
                    if path.suffix == ".json":
                        paths.append(path)
            paths.sort()
        total_bytes = 0
        generations = []
        for path in paths:
            if is_link_like(path) or not path.is_file():
                raise ValueError("Environment profile generation path is unsafe")
            try:
                size = path.stat().st_size
            except OSError as error:
                raise ValueError("Environment profile generation is unreadable") from error
            total_bytes += int(size)
            if size > MAX_PROFILE_BYTES * 2 or total_bytes > MAX_GENERATION_STORAGE_BYTES:
                raise ValueError("Environment profile generation storage exceeds limit")
            generation = self.validate_generation(read_json(path))
            if path.stem != generation["generation_id"].split(":", 1)[1]:
                raise ValueError("Environment profile generation filename hash mismatch")
            generations.append(generation)
        if len({item["generation_id"] for item in generations}) != len(generations):
            raise ValueError("duplicate Environment profile generation identity")
        return generations

    def _resolve_local_layout(self) -> None:
        # The registry's write-mode resolver permits a missing leaf while still
        # rejecting link-like existing parents; it does not create the path.
        self.root = self.registry._resolve_relative(
            "profiles", "Environment profile root", for_write=True
        )
        self.generations = self.registry._resolve_relative(
            "profiles/generations",
            "Environment profile generation root",
            for_write=True,
        )
        self.current_path = self.registry._resolve_relative(
            "profiles/current.json",
            "Environment profile current pointer",
            for_write=True,
        )
        self.events_path = self.registry._resolve_relative(
            "profiles/local-events.jsonl",
            "Environment profile event ledger",
            for_write=True,
        )
        self.transaction_path = self.registry._resolve_relative(
            "profiles/transaction.json",
            "Environment profile transaction marker",
            for_write=True,
        )

    @classmethod
    def validate_peer_profile_record(
        cls, path: Path, record: Dict[str, Any], origin: str
    ) -> Dict[str, Any]:
        fields = {
            "schema_version", "stream_id", "origin_node_id", "event_sequence",
            "generation", "received_bundle_id", "automatic_activation",
        }
        _strict(record, fields, fields, "peer profile replica")
        if (
            record["schema_version"] != 1
            or record["stream_id"] != "environment-v1"
            or record["origin_node_id"] != safe_node_id(origin)
            or type(record["event_sequence"]) is not int
            or record["event_sequence"] < 1
            or record["automatic_activation"] is not False
        ):
            raise ValueError("peer Environment profile replica authority is invalid")
        generation = cls.validate_generation(record["generation"])
        if path.stem != generation["generation_id"].split(":", 1)[1]:
            raise ValueError("peer Environment profile replica filename mismatch")
        return generation

    @classmethod
    def validate_profile(cls, profile: Dict[str, Any]) -> Dict[str, Any]:
        _strict(profile, {"schema_version", "profile_format", "platform", "skills", "rules", "profile_sha256", "profile_id"}, {"schema_version", "profile_format", "platform", "skills", "rules", "profile_sha256", "profile_id"}, "Environment profile")
        if profile["schema_version"] != 1 or profile["profile_format"] != PROFILE_FORMAT or profile["platform"] not in PLATFORMS:
            raise ValueError("unsupported Environment profile")
        if not isinstance(profile["skills"], list) or len(profile["skills"]) > MAX_SKILLS or not isinstance(profile["rules"], list) or len(profile["rules"]) > 2:
            raise ValueError("Environment profile inventory count is invalid")
        if len(canonical_bytes(profile)) > MAX_PROFILE_BYTES:
            raise ValueError("Environment profile exceeds size limit")
        expected = dict(profile)
        digest = expected.pop("profile_sha256", None)
        profile_id = expected.pop("profile_id", None)
        actual = _sha256(canonical_bytes(expected))
        if digest != actual or profile_id != f"profile:{actual}":
            raise ValueError("Environment profile content hash mismatch")
        # Rebuild strict validation without source paths by checking exact item keys and values.
        skill_ids = set()
        for item in profile["skills"]:
            required = {"installation_id", "provider_type", "provider_id", "applicable_platforms", "inventory_status", "incomplete_reason", "declared_version", "tree_sha256", "file_count", "byte_count"}
            _strict(item, required, required, "profile Skill")
            installation_id = _public_string(item["installation_id"], "profile Skill identity", ID_RE, 192)
            if installation_id in skill_ids:
                raise ValueError("invalid or duplicate profile Skill identity")
            skill_ids.add(installation_id)
            if item["provider_type"] not in PROVIDER_TYPES:
                raise ValueError("invalid profile Skill provider")
            provider_id = _public_string(
                item["provider_id"], "profile Skill provider", PROVIDER_RE, 128
            )
            provider_prefixes = {
                "user-managed": ("user", "user:"),
                "system-bundled": ("system:",),
                "plugin-managed": ("plugin:",),
            }
            if not provider_id.startswith(provider_prefixes[item["provider_type"]]):
                raise ValueError("profile Skill provider type and identity disagree")
            installation_prefixes = {
                "user-managed": "skill:",
                "system-bundled": "system:",
                "plugin-managed": "plugin:",
            }
            if not installation_id.startswith(
                installation_prefixes[item["provider_type"]]
            ):
                raise ValueError(
                    "profile Skill installation identity and provider type disagree"
                )
            platforms = item["applicable_platforms"]
            if not isinstance(platforms, list) or not platforms or len(platforms) > 3 or platforms != sorted(set(platforms)) or any(value not in PLATFORMS for value in platforms):
                raise ValueError("invalid profile Skill platforms")
            version = item["declared_version"]
            if version is not None:
                _public_string(version, "profile Skill version", re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$"), 128)
            if type(item["file_count"]) is not int or not 0 <= item["file_count"] <= MAX_SKILL_FILES or type(item["byte_count"]) is not int or not 0 <= item["byte_count"] <= MAX_SKILL_BYTES:
                raise ValueError("invalid profile Skill inventory bounds")
            if item["inventory_status"] == "complete":
                if item["incomplete_reason"] is not None or not re.fullmatch(r"[0-9a-f]{64}", str(item["tree_sha256"])):
                    raise ValueError("complete profile Skill has invalid evidence")
            elif item["inventory_status"] == "incomplete":
                if item["incomplete_reason"] not in INCOMPLETE_REASONS or item["tree_sha256"] is not None or item["file_count"] != 0 or item["byte_count"] != 0:
                    raise ValueError("incomplete profile Skill has invalid evidence")
            else:
                raise ValueError("invalid profile Skill inventory status")
        rule_ids = set()
        if profile["skills"] != sorted(profile["skills"], key=lambda item: (item["installation_id"], item["provider_type"], item["provider_id"])):
            raise ValueError("profile Skill inventory is not canonical")
        for item in profile["rules"]:
            required = {"rule_id", "content_sha256", "byte_count", "managed_blocks"}
            _strict(item, required, required, "profile Rule")
            if item["rule_id"] in rule_ids or item["rule_id"] not in {"global-agents", "global-agents-override"}:
                raise ValueError("invalid or duplicate profile Rule identity")
            rule_ids.add(item["rule_id"])
            if re.fullmatch(r"[0-9a-f]{64}", str(item["content_sha256"])) is None:
                raise ValueError("invalid profile Rule hash")
            if type(item["byte_count"]) is not int or not 0 <= item["byte_count"] <= MAX_RULE_BYTES or not isinstance(item["managed_blocks"], list) or len(item["managed_blocks"]) > 128:
                raise ValueError("invalid profile Rule bounds")
            block_ids = set()
            for block in item["managed_blocks"]:
                required_block = {"managed_block_id", "content_sha256", "byte_count"}
                _strict(block, required_block, required_block, "managed Rule block")
                block_id = _public_string(block["managed_block_id"], "managed Rule block identity", BLOCK_ID_RE, 128)
                if block_id in block_ids or re.fullmatch(r"[0-9a-f]{64}", str(block["content_sha256"])) is None:
                    raise ValueError("invalid or duplicate managed Rule block")
                if type(block["byte_count"]) is not int or not 0 <= block["byte_count"] <= 262144:
                    raise ValueError("invalid managed Rule block bounds")
                block_ids.add(block_id)
            if item["managed_blocks"] != sorted(item["managed_blocks"], key=lambda block: block["managed_block_id"]):
                raise ValueError("managed Rule blocks are not canonical")
        if profile["rules"] != sorted(profile["rules"], key=lambda item: item["rule_id"]):
            raise ValueError("profile Rule inventory is not canonical")
        return profile

    def peer_profile(self, node_id: str, generation_sha256: Optional[str] = None) -> Dict[str, Any]:
        peer = safe_node_id(node_id)
        federation_root = self.archive_root / "federation"
        peer_record_root = federation_root / "peers"
        peer_record_path = peer_record_root / f"{peer}.json"
        if is_link_like(federation_root) or is_link_like(peer_record_root):
            raise ValueError("peer trust registry is link-like")
        if is_link_like(peer_record_path) or not peer_record_path.is_file():
            raise ValueError("peer is not currently trusted")
        peer_record = read_json(peer_record_path)
        if peer_record.get("node_id") != peer or peer_record.get("trusted") is not True:
            raise ValueError("peer is not currently trusted")
        root = self.registry._resolve_relative(
            f"replicas/peers/{peer}/profiles",
            "peer Environment profile replica root",
            for_write=True,
        )
        records, head = self.load_peer_profile_records(peer, root)
        if generation_sha256 is None:
            return head["profile"]
        if re.fullmatch(r"[0-9a-f]{64}", generation_sha256) is None:
            raise ValueError("invalid peer profile generation hash")
        generation_id = f"generation:{generation_sha256}"
        try:
            return records[generation_id]["profile"]
        except KeyError as error:
            raise ValueError("peer Environment profile replica is missing") from error

    def load_peer_profile_records(
        self, peer: str, root: Optional[Path] = None
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        peer = safe_node_id(peer)
        root = root or self.registry._resolve_relative(
            f"replicas/peers/{peer}/profiles",
            "peer Environment profile replica root",
            for_write=True,
        )
        if not root.is_dir():
            raise ValueError("peer has no Environment profile replica")
        candidates = []
        with os.scandir(root) as entries:
            for entry in entries:
                candidates.append(Path(entry.path))
                if len(candidates) > MAX_GENERATIONS:
                    raise ValueError("peer Environment profile replica count exceeds limit")
        candidates.sort(key=lambda path: path.name)
        total_bytes = 0
        records: Dict[str, Dict[str, Any]] = {}
        sequences: Dict[str, int] = {}
        for candidate in candidates:
            if is_link_like(candidate):
                raise ValueError("peer Environment profile replica path is unsafe")
            if candidate.suffix != ".json":
                continue
            safe_candidate = self.registry._resolve_relative(
                candidate.relative_to(self.registry.root).as_posix(),
                "peer Environment profile replica",
            )
            if not safe_candidate.is_file():
                raise ValueError("peer Environment profile replica path is unsafe")
            size = safe_candidate.stat().st_size
            total_bytes += int(size)
            if size > MAX_PROFILE_BYTES * 2 or total_bytes > MAX_GENERATION_STORAGE_BYTES:
                raise ValueError("peer Environment profile replica storage exceeds limit")
            record = read_json(safe_candidate)
            generation = self.validate_peer_profile_record(safe_candidate, record, peer)
            generation_id = generation["generation_id"]
            if generation_id in records:
                raise ValueError("duplicate peer Environment profile generation")
            records[generation_id] = generation
            sequences[generation_id] = int(record["event_sequence"])
        if not records:
            raise ValueError("peer has no Environment profile replica")
        if len(set(sequences.values())) != len(sequences):
            raise ValueError("peer profile event sequences must be unique")
        head = self.validate_generation_chain(list(records.values()))
        ordered = sorted(records.values(), key=lambda item: sequences[item["generation_id"]])
        previous_sequence = 0
        for index, generation in enumerate(ordered):
            sequence = sequences[generation["generation_id"]]
            if sequence <= previous_sequence:
                raise ValueError(
                    "peer profile event sequences must be positive and strictly increasing"
                )
            previous_sequence = sequence
            expected_previous = None if index == 0 else ordered[index - 1]["generation_id"]
            if generation["previous_generation_id"] != expected_previous:
                raise ValueError("peer profile event order conflicts with generation chain")
        if ordered[-1]["generation_id"] != head["generation_id"]:
            raise ValueError("peer profile event order does not end at chain head")
        return records, head

    def compare(self, peer_node_id: str, peer_generation_sha256: Optional[str] = None) -> Dict[str, Any]:
        local = self.current()
        peer = self.peer_profile(peer_node_id, peer_generation_sha256)
        differences = []
        differences.extend(self._compare_items(local, peer, "skills", "installation_id"))
        differences.extend(self._compare_items(local, peer, "rules", "rule_id"))
        differences.extend(self._compare_rule_blocks(local, peer))
        if len(differences) > MAX_ASSESSMENT_DIFFERENCES:
            raise ValueError("Environment profile assessment exceeds difference limit")
        return {"status": "same" if all(item["outcome"] == "same" for item in differences) else "different", "local_profile_id": local["profile_id"], "peer_profile_id": peer["profile_id"], "peer_node_id": safe_node_id(peer_node_id), "differences": differences}

    @staticmethod
    def _compare_items(local: Dict[str, Any], peer: Dict[str, Any], collection: str, identity: str) -> List[Dict[str, Any]]:
        left = {item[identity]: item for item in local[collection]}
        right = {item[identity]: item for item in peer[collection]}
        results = []
        for item_id in sorted(set(left) | set(right)):
            local_item, peer_item = left.get(item_id), right.get(item_id)
            outcome = "same"
            reason = "identical-content-evidence"
            if local_item is None:
                if collection == "skills" and local["platform"] not in peer_item["applicable_platforms"]:
                    outcome, reason = "platform-inapplicable", "not applicable on local platform"
                else:
                    outcome, reason = "missing-local", "identity is absent locally"
            elif peer_item is None:
                if collection == "skills" and peer["platform"] not in local_item["applicable_platforms"]:
                    outcome, reason = "platform-inapplicable", "not applicable on peer platform"
                else:
                    outcome, reason = "missing-peer", "identity is absent on peer"
            elif collection == "skills" and (local_item["inventory_status"] == "incomplete" or peer_item["inventory_status"] == "incomplete"):
                outcome, reason = "inventory-incomplete", "one or both inventories are incomplete"
            elif canonical_bytes(local_item) != canonical_bytes(peer_item):
                outcome = "content-differs"
                reason = "provider, version, applicability, or content evidence differs"
            results.append({
                "kind": "skill" if collection == "skills" else "rule",
                "identity": item_id,
                "outcome": outcome,
                "reason": reason,
                "local_provider_type": local_item.get("provider_type") if local_item else None,
                "peer_provider_type": peer_item.get("provider_type") if peer_item else None,
                "local_provider_id": local_item.get("provider_id") if local_item else None,
                "peer_provider_id": peer_item.get("provider_id") if peer_item else None,
                "local_declared_version": local_item.get("declared_version") if local_item else None,
                "peer_declared_version": peer_item.get("declared_version") if peer_item else None,
                "local_applicable_platforms": local_item.get("applicable_platforms") if local_item else None,
                "peer_applicable_platforms": peer_item.get("applicable_platforms") if peer_item else None,
                "local_content_sha256": (local_item.get("tree_sha256") or local_item.get("content_sha256")) if local_item else None,
                "peer_content_sha256": (peer_item.get("tree_sha256") or peer_item.get("content_sha256")) if peer_item else None,
            })
        return results

    @staticmethod
    def _compare_rule_blocks(local: Dict[str, Any], peer: Dict[str, Any]) -> List[Dict[str, Any]]:
        def blocks(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
            result = {}
            for rule in profile["rules"]:
                for block in rule["managed_blocks"]:
                    identity = f"{rule['rule_id']}:{block['managed_block_id']}"
                    result[identity] = block
            return result

        left, right = blocks(local), blocks(peer)
        results = []
        for identity in sorted(set(left) | set(right)):
            local_item, peer_item = left.get(identity), right.get(identity)
            if local_item is None:
                outcome, reason = "missing-local", "managed block is absent locally"
            elif peer_item is None:
                outcome, reason = "missing-peer", "managed block is absent on peer"
            elif canonical_bytes(local_item) == canonical_bytes(peer_item):
                outcome, reason = "same", "identical managed-block evidence"
            else:
                outcome, reason = "content-differs", "managed-block content evidence differs"
            results.append({
                "kind": "rule-block", "identity": identity, "outcome": outcome,
                "reason": reason,
                "local_provider_type": None, "peer_provider_type": None,
                "local_provider_id": None, "peer_provider_id": None,
                "local_declared_version": None, "peer_declared_version": None,
                "local_applicable_platforms": None, "peer_applicable_platforms": None,
                "local_content_sha256": local_item.get("content_sha256") if local_item else None,
                "peer_content_sha256": peer_item.get("content_sha256") if peer_item else None,
            })
        return results

    def convergence_plan(self, peer_node_id: str, artifact_links: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
        comparison = self.compare(peer_node_id)
        return self.convergence_plan_from_comparison(comparison, artifact_links)

    @staticmethod
    def _validate_artifact_links(
        links: Dict[str, Dict[str, str]], differences: List[Dict[str, Any]]
    ) -> None:
        actionable = {
            f"{difference['kind']}:{difference['identity']}": difference
            for difference in differences
            if difference.get("outcome") not in {"same", "platform-inapplicable"}
            and difference.get("kind") in {"skill", "rule-block"}
        }
        for key, link in links.items():
            difference = actionable.get(key)
            if difference is None:
                raise ValueError(
                    "convergence artifact link does not match an actionable difference"
                )
            provider_owned = {
                difference.get("local_provider_type"),
                difference.get("peer_provider_type"),
            } & {"system-bundled", "plugin-managed"}
            if provider_owned:
                raise ValueError(
                    "provider-managed differences cannot use Environment artifact links"
                )
            common = {"artifact_id", "revision_id", "source"}
            specific = (
                {"installation_id"}
                if difference["kind"] == "skill"
                else {"binding_id"}
            )
            _strict(
                link,
                common | specific,
                common | specific,
                "convergence artifact link",
            )
            if link["source"] not in {"local", "peer"}:
                raise ValueError("convergence link source must be local or peer")
            if re.fullmatch(r"rev:[0-9a-f]{64}", str(link["revision_id"])) is None:
                raise ValueError("convergence link revision identity is invalid")
            if difference["kind"] == "skill":
                if (
                    link["installation_id"] != difference["identity"]
                    or re.fullmatch(
                        r"global-skill:[a-z0-9][a-z0-9._-]{1,183}",
                        str(link["artifact_id"]),
                    )
                    is None
                ):
                    raise ValueError("convergence Skill link identity is invalid")
            elif (
                not isinstance(link["binding_id"], str)
                or not 1 <= len(link["binding_id"]) <= 256
                or re.fullmatch(
                    r"global-rule:[a-z0-9][a-z0-9._-]{1,183}",
                    str(link["artifact_id"]),
                )
                is None
            ):
                raise ValueError("convergence Rule link identity is invalid")

    def convergence_plan_from_comparison(self, comparison: Dict[str, Any], artifact_links: Optional[Dict[str, Dict[str, str]]] = None) -> Dict[str, Any]:
        if not isinstance(comparison, dict) or not isinstance(comparison.get("differences"), list):
            raise ValueError("convergence comparison is invalid")
        if len(comparison["differences"]) > MAX_ASSESSMENT_DIFFERENCES:
            raise ValueError("convergence comparison exceeds difference limit")
        peer_node_id = safe_node_id(str(comparison.get("peer_node_id", "")))
        links = artifact_links or {}
        if not isinstance(links, dict) or len(links) > MAX_SKILLS + 256:
            raise ValueError("convergence artifact links are invalid")
        self._validate_artifact_links(links, comparison["differences"])
        plan = []
        registry = self.registry
        for difference in comparison["differences"]:
            if difference["outcome"] in {"same", "platform-inapplicable"}:
                continue
            key = f"{difference['kind']}:{difference['identity']}"
            link = links.get(key)
            route = "evidence-only"
            artifact = None
            provider_owned = {
                difference.get("local_provider_type"),
                difference.get("peer_provider_type"),
            } & {"system-bundled", "plugin-managed"}
            if link is not None and not provider_owned and difference["kind"] != "rule":
                evidence_hash = difference.get(f"{link['source']}_content_sha256")
                if re.fullmatch(r"[0-9a-f]{64}", str(evidence_hash)) is None:
                    raise ValueError("convergence link source has no complete content evidence")
                shown = registry.show(link["artifact_id"])
                if shown["revision"]["revision_id"] != link["revision_id"]:
                    raise ValueError("convergence link does not name the current immutable revision")
                expected_class = "global-skill" if difference["kind"] == "skill" else "global-rule"
                if shown["artifact"]["object_class"] != expected_class:
                    raise ValueError("convergence link has the wrong Environment artifact class")
                if difference["kind"] == "skill":
                    if link["installation_id"] != difference["identity"]:
                        raise ValueError("convergence Skill link installation identity mismatch")
                    skill_id = difference["identity"].removeprefix("skill:")
                    if difference["identity"] != f"skill:{skill_id}" or shown["artifact"]["artifact_id"] != f"global-skill:{skill_id}":
                        raise ValueError("convergence Skill link stable identity mismatch")
                    manifest = self._validate_skill_link(shown, link["revision_id"], str(evidence_hash))
                    if (
                        manifest.get("skill_id") != skill_id
                        or manifest.get("version") != difference.get(f"{link['source']}_declared_version")
                        or sorted(manifest.get("supported_platforms") or [])
                        != sorted(difference.get(f"{link['source']}_applicable_platforms") or [])
                        or difference.get(f"{link['source']}_provider_type") != "user-managed"
                    ):
                        raise ValueError("convergence Skill package metadata does not match profile evidence")
                else:
                    block_id = difference["identity"].split(":", 1)[1]
                    if shown["revision"]["content_sha256"] != evidence_hash:
                        raise ValueError("convergence Rule link content hash mismatch")
                    self._validate_rule_binding_link(
                        link["binding_id"],
                        difference["identity"].split(":", 1)[0],
                        block_id,
                        link["revision_id"],
                        str(evidence_hash),
                    )
                route = "existing-preview-required"
                artifact = dict(link)
            if provider_owned:
                route = "provider-reference-only"
                artifact = None
            plan.append({**difference, "route": route, "artifact": artifact, "automatic_activation": False})
        return {"status": "preview", "peer_node_id": peer_node_id, "items": plan, "activation_authorized": False, "installer_invoked": False}

    def _validate_skill_link(
        self,
        shown: Dict[str, Any],
        revision_id: str,
        expected_tree_sha256: str,
    ) -> Dict[str, Any]:
        digest = revision_id.split(":", 1)[1]
        reference_path = self.registry._resolve_relative(
            f"packages/by-revision/{digest}.json",
            "convergence Skill package reference",
        )
        reference = read_json(reference_path)
        required = {
            "schema_version", "artifact_id", "revision_id", "package_sha256",
            "package_path", "package_contract_sha256", "verified_at",
        }
        if not isinstance(reference, dict) or set(reference) != required:
            raise ValueError("convergence Skill package reference is invalid")
        if reference["artifact_id"] != shown["artifact"]["artifact_id"] or reference["revision_id"] != revision_id:
            raise ValueError("convergence Skill package reference identity mismatch")
        package_path = self.registry._resolve_relative(
            reference["package_path"], "convergence Skill package"
        )
        verified_package = EnvironmentSkillInstaller.verify_package_archive(
            package_path
        )
        if verified_package["package_sha256"] != reference["package_sha256"]:
            raise ValueError("convergence Skill package hash mismatch")
        manifest = verified_package["manifest"]
        contract_hash = _sha256(skill_package_contract_bytes(manifest))
        if (
            manifest.get("source_revision") != revision_id
            or contract_hash != reference["package_contract_sha256"]
            or contract_hash != shown["revision"]["content_sha256"]
        ):
            raise ValueError("convergence Skill package contract mismatch")
        records = []
        seen = set()
        for item in manifest.get("files") or []:
            path = unicodedata.normalize("NFC", str(item.get("path", "")))
            key = path.casefold()
            if not path or key in seen or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))) is None:
                raise ValueError("convergence Skill package file manifest is invalid")
            seen.add(key)
            record = path.encode("utf-8") + b"\0" + item["sha256"].encode("ascii") + b"\0" + str(item.get("size")).encode("ascii") + b"\n"
            records.append((key, record))
        if _sha256(b"".join(record for _, record in sorted(records))) != expected_tree_sha256:
            raise ValueError("convergence Skill package tree hash mismatch")
        return manifest

    def _validate_rule_binding_link(
        self,
        binding_id: str,
        rule_id: str,
        managed_block_id: str,
        revision_id: str,
        content_sha256: str,
    ) -> None:
        node = read_json(self.archive_root / "federation" / "node.json")
        registry = EnvironmentBindingRegistry(
            self.registry,
            node_id=safe_node_id(str(node.get("node_id", ""))),
        )
        matches = [
            binding
            for binding in registry.get_rule_bindings()
            if binding["binding_id"] == binding_id
        ]
        expected_name = "AGENTS.md" if rule_id == "global-agents" else "AGENTS.override.md"
        if len(matches) != 1:
            raise ValueError("convergence Rule link has no unique verified binding")
        binding = matches[0]
        if (
            binding["scope"] != "global"
            or Path(binding["relative_path"]).name != expected_name
            or binding["install_strategy"] != "managed-block"
            or binding["managed_block_id"] != managed_block_id
            or binding["installed_revision_id"] != revision_id
            or binding["installed_content_sha256"] != content_sha256
        ):
            raise ValueError("convergence Rule link binding identity mismatch")

    def status(self) -> Dict[str, Any]:
        self._resolve_local_layout()
        if self.transaction_path.exists():
            raise ValueError("Environment profile transaction requires apply recovery")
        generations = self._load_generations()
        generations_by_id = {item["generation_id"]: item for item in generations}
        generation_ids = set(generations_by_id)
        current = self._validated_pointer(
            optional=True, generations_by_id=generations_by_id
        )
        if current is not None:
            head = self._chain_head(generations)
            if current["generation_id"] != head["generation_id"]:
                raise ValueError("current Environment profile pointer is not the generation head")
        elif generations:
            raise ValueError("Environment profile current pointer is missing")
        events = self.local_events(generations_by_id)
        event_generation_ids = {item["generation_id"] for item in events}
        if event_generation_ids != generation_ids:
            raise ValueError("Environment profile generation and export event sets differ")
        return {"initialized": self.root.exists(), "generation_count": len(generations), "current": current, "export_event_count": len(events)}
