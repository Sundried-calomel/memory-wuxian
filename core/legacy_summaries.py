from __future__ import annotations

import base64
import hashlib
import json
import re
from itertools import product
from pathlib import PurePosixPath
from copy import deepcopy
from typing import Any

FORMAT = "memory-wuxian-summary-v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(record: dict[str, Any]) -> str:
    return "sum-" + _sha(_canonical({k: v for k, v in record.items() if k not in {"id", "summary_sha256"}}))[:32]


def _finish(record: dict[str, Any]) -> dict[str, Any]:
    record["id"] = _identity(record)
    record["summary_sha256"] = _sha(_canonical({k: v for k, v in record.items() if k != "summary_sha256"}))
    return record


def _legacy_original(row: dict[str, Any]) -> dict[str, Any]:
    original = row.get("legacy_original")
    return original if isinstance(original, dict) else row


def _raw_map(raw_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    new_ids = set()
    for row in raw_records:
        new_id = row.get("message_id")
        original = _legacy_original(row)
        message_id = original.get("message_id")
        if (not isinstance(new_id, str) or not new_id or new_id in new_ids or
            not isinstance(message_id, str) or not message_id):
            raise ValueError("normalized raw source identities are missing or duplicated")
        new_ids.add(new_id)
        by_id.setdefault(message_id, []).append(row)
    for rows in by_id.values():
        rows.sort(key=lambda row: int(_legacy_original(row).get("sequence", row.get("sequence", 0))))
    return by_id


def _old_sequence(row: dict[str, Any]) -> int:
    return int(_legacy_original(row).get("sequence", row["sequence"]))


def _old_hash(row: dict[str, Any]) -> str | None:
    original = _legacy_original(row)
    return row.get("legacy_sha256", original.get("content_sha256"))


def _legacy_row_hash(records: list[dict[str, Any]]) -> str:
    payload = [{"sequence": _old_sequence(row), "message_id": _legacy_original(row)["message_id"],
                "content_sha256": _old_hash(row)}
               for row in sorted(records, key=_old_sequence)]
    if not payload or any(not isinstance(item["content_sha256"], str) for item in payload):
        raise ValueError("legacy raw hash evidence is missing")
    return _sha(_canonical(payload))


def _old_raw_candidates(raws: dict[str, list[dict[str, Any]]], message_id: str,
                        conversation_id: str) -> list[dict[str, Any]]:
    return [row for row in raws.get(message_id, [])
            if row.get("conversation_id") == conversation_id and
            _legacy_original(row).get("conversation_id", conversation_id) == conversation_id]


def _select_v1_raw(document: dict[str, Any], raws: dict[str, list[dict[str, Any]]],
                   conversation_id: str) -> list[dict[str, Any]]:
    old_ids = list(document.get("source_message_ids") or [])
    start_id, end_id = document.get("source_start"), document.get("source_end")
    start_sequence = document.get("source_start_sequence")
    end_sequence = document.get("source_end_sequence")
    source_hash = document.get("source_sha256")

    if old_ids:
        if any(not isinstance(value, str) or not value for value in old_ids):
            raise ValueError("legacy V1 source IDs are malformed")
        candidate_lists = [_old_raw_candidates(raws, mid, conversation_id) for mid in old_ids]
        if any(not candidates for candidates in candidate_lists):
            raise ValueError("legacy V1 summary references missing raw records")
        if start_sequence is not None or end_sequence is not None:
            if start_sequence is None or end_sequence is None:
                raise ValueError("legacy V1 source sequence bounds are incomplete")
            candidate_lists = [[row for row in group if int(start_sequence) <= _old_sequence(row) <= int(end_sequence)]
                               for group in candidate_lists]
        choices_count = 1
        for candidates in candidate_lists:
            choices_count *= len(candidates)
        if choices_count > 65536:
            raise ValueError("legacy V1 raw source ambiguity exceeds the bounded resolver")
        matches = []
        seen = set()
        for choice in product(*candidate_lists):
            selected = list(choice)
            sequences = [_old_sequence(row) for row in selected]
            if len(set(sequences)) != len(sequences):
                continue
            selected.sort(key=_old_sequence)
            if start_sequence is not None and (sequences and min(sequences) != int(start_sequence)):
                continue
            if end_sequence is not None and (sequences and max(sequences) != int(end_sequence)):
                continue
            if start_id and _legacy_original(selected[0]).get("message_id") != start_id:
                continue
            if end_id and _legacy_original(selected[-1]).get("message_id") != end_id:
                continue
            if source_hash and _legacy_row_hash(selected) != source_hash:
                continue
            signature = tuple(row["message_id"] for row in selected)
            if signature not in seen:
                seen.add(signature)
                matches.append(selected)
                if len(matches) > 1:
                    break
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("legacy V1 source IDs, boundaries, and source hash do not resolve to raw records")
        raise ValueError("legacy V1 source IDs remain ambiguous after boundary/hash checks")

    all_rows = [row for rows in raws.values() for row in rows if row.get("conversation_id") == conversation_id]
    if start_sequence is not None or end_sequence is not None:
        if start_sequence is None or end_sequence is None:
            raise ValueError("legacy V1 source sequence bounds are incomplete")
        selected = sorted((row for row in all_rows if int(start_sequence) <= _old_sequence(row) <= int(end_sequence)),
                          key=_old_sequence)
        if not selected or _old_sequence(selected[0]) != int(start_sequence) or _old_sequence(selected[-1]) != int(end_sequence):
            raise ValueError("legacy V1 sequence bounds do not resolve to raw records")
        if source_hash and _legacy_row_hash(selected) != source_hash:
            raise ValueError("legacy V1 source hash does not match bounded raw records")
        return selected

    starts = _old_raw_candidates(raws, start_id, conversation_id) if isinstance(start_id, str) else []
    ends = _old_raw_candidates(raws, end_id, conversation_id) if isinstance(end_id, str) else []
    if not starts or not ends:
        raise ValueError("legacy V1 raw IDs or sequence bounds are missing")
    matches = []
    for first in starts:
        for last in ends:
            if _old_sequence(first) > _old_sequence(last):
                continue
            selected = sorted((row for row in all_rows if _old_sequence(first) <= _old_sequence(row) <= _old_sequence(last)),
                              key=_old_sequence)
            if selected and selected[0]["message_id"] == first["message_id"] and selected[-1]["message_id"] == last["message_id"]:
                if source_hash and _legacy_row_hash(selected) != source_hash:
                    continue
                matches.append(selected)
                if len(matches) > 1:
                    break
        if len(matches) > 1:
            break
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError("legacy V1 source boundaries/hash do not resolve to raw records")
    raise ValueError("legacy V1 source boundaries remain ambiguous")


def _resolve_v2_raw(source: dict[str, Any], raw_ids: list[str], raws: dict[str, list[dict[str, Any]]],
                    conversation_id: str) -> list[dict[str, Any]]:
    manifest = source.get("raw_message_manifest") or []
    if not manifest:
        raise ValueError("Summary V2 raw source manifest is missing")
    manifest_ids = [item.get("message_id") for item in manifest if isinstance(item, dict)]
    if len(manifest_ids) != len(manifest) or raw_ids != manifest_ids:
        raise ValueError("Summary V2 raw IDs disagree with its ordered raw manifest")
    selected = []
    seen_new_ids = set()
    for item in manifest:
        message_id = item.get("message_id")
        sequence = item.get("sequence")
        content_hash = item.get("content_sha256")
        candidates = [row for row in _old_raw_candidates(raws, message_id, conversation_id)
                      if _old_sequence(row) == sequence and _old_hash(row) == content_hash]
        if len(candidates) != 1:
            raise ValueError(f"Summary V2 raw manifest entry does not resolve uniquely: {message_id}@{sequence}")
        row = candidates[0]
        if row["message_id"] in seen_new_ids:
            raise ValueError("Summary V2 raw manifest maps duplicate new message IDs")
        seen_new_ids.add(row["message_id"])
        selected.append(row)
    return selected


def _raw_source_hash(records: list[dict[str, Any]]) -> str:
    return _sha(_canonical([{"message_id": row["message_id"], "sequence": row["sequence"],
                             "conversation_id": row["conversation_id"], "content_sha256": row["content_sha256"]}
                            for row in sorted(records, key=lambda r: int(r["sequence"]))]))


def _legacy_bytes(document: dict[str, Any], original_bytes: bytes | None) -> dict[str, Any]:
    evidence = {"legacy_original": deepcopy(document)}
    if original_bytes is not None:
        if not isinstance(original_bytes, bytes):
            raise TypeError("original_bytes must be bytes")
        evidence["legacy_original_bytes_b64"] = base64.b64encode(original_bytes).decode("ascii")
        evidence["legacy_original_bytes_sha256"] = _sha(original_bytes)
    return evidence


def _v1(document: dict[str, Any], raws: dict[str, list[dict[str, Any]]], prior: dict[str, dict[str, Any]]) -> dict[str, Any]:
    level = document.get("level")
    conversation = document.get("conversation_id")
    alias = document.get("summary_id")
    if not isinstance(level, int) or level < 1 or not isinstance(conversation, str) or not conversation or not isinstance(alias, str):
        raise ValueError("legacy V1 summary identity is incomplete")
    if level == 1:
        selected = _select_v1_raw(document, raws, conversation)
        refs = [row["message_id"] for row in selected]
        raw_ids = list(refs)
        source_sha = _raw_source_hash(selected)
    else:
        aliases = list(document.get("source_summaries") or [])
        if not aliases or len(aliases) != len(set(aliases)) or any(alias not in prior for alias in aliases):
            raise ValueError("legacy V1 parent has missing child summaries")
        children = [prior[alias] for alias in aliases]
        if any(child["conversation_id"] != conversation or child["level"] + 1 != level for child in children):
            raise ValueError("legacy V1 child summaries disagree on conversation or level")
        refs = [child["id"] for child in children]
        raw_ids = list(dict.fromkeys(mid for child in children for mid in child["raw_source_ids"]))
        source_sha = _sha(_canonical([{"id": child["id"], "summary_sha256": child["summary_sha256"]} for child in children]))
    sections = []
    for key, title in (("topics", "Topics"), ("established_conclusions", "Established conclusions"),
                       ("open_questions", "Open questions"), ("concepts", "Concepts"), ("policy_events", "Policy events")):
        values = document.get(key) or []
        if values:
            sections.append("## " + title + "\n" + "\n".join("- " + (json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, dict) else str(v)) for v in values))
    text = "\n\n".join(sections).strip()
    if not text:
        raise ValueError("legacy V1 summary has no convertible summary content")
    return {"format": FORMAT, "conversation_id": conversation, "level": level, "text": text,
            "source_refs": refs, "raw_source_ids": raw_ids, "source_sha256": source_sha,
            "legacy_id": alias, "legacy_format": "summary-v1"}


def _v2(document: dict[str, Any], raws: dict[str, list[dict[str, Any]]], prior: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if "sidecar" in document:
        document = document["sidecar"]
    if document.get("format_version") != 2 or not isinstance(document.get("source"), dict):
        raise ValueError("unknown or incomplete Summary V2 sidecar version")
    conversation, level = document.get("conversation_id"), document.get("summary_level")
    source = document["source"]
    if not isinstance(conversation, str) or not conversation or not isinstance(level, int) or level < 1:
        raise ValueError("Summary V2 identity is incomplete")
    old_raw_ids = list(source.get("raw_message_ids") or [])
    if not old_raw_ids:
        old_raw_ids = list(document.get("coverage", {}).get("raw_message_ids") or [])
    if not old_raw_ids:
        raise ValueError("Summary V2 raw source IDs are missing")
    selected = _resolve_v2_raw(source, old_raw_ids, raws, conversation)
    raw_ids = [row["message_id"] for row in selected]
    if level == 1:
        refs_old = list(source.get("source_refs") or old_raw_ids)
        if refs_old != old_raw_ids:
            raise ValueError("Summary V2 level-1 refs do not match its raw message IDs")
        refs = list(raw_ids)
        source_sha = _raw_source_hash(selected)
    else:
        source_manifest = source.get("source_manifest") or {}
        direct = list(source.get("children") or source_manifest.get("children") or [])
        if not direct:
            refs_catalog = source.get("ref_catalog") or []
            refs = [entry.get("source_ref") for entry in refs_catalog if isinstance(entry, dict)]
            direct = [{"summary_v2_id": ref} for ref in refs]
        child_aliases = [item.get("target_summary_id") or item.get("summary_v2_id") or item.get("parallel_summary_id")
                         for item in direct]
        if not child_aliases or any(alias not in prior for alias in child_aliases):
            raise ValueError("Summary V2 parent direct child summaries are missing")
        children = [prior[alias] for alias in child_aliases]
        if any(child["conversation_id"] != conversation or child["level"] + 1 != level for child in children):
            raise ValueError("Summary V2 children disagree on conversation or level")
        for descriptor, child in zip(direct, children):
            if descriptor.get("summary_level") is not None and descriptor["summary_level"] != child["level"]:
                raise ValueError("Summary V2 child descriptor level changed")
            old_doc = child.get("legacy_original", {})
            old_sidecar = old_doc.get("sidecar", old_doc)
            if descriptor.get("projection_sha256") is not None and descriptor["projection_sha256"] != old_sidecar.get("projection_sha256"):
                raise ValueError("Summary V2 child projection binding changed")
        child_raw_ids = list(dict.fromkeys(mid for child in children for mid in child["raw_source_ids"]))
        if set(child_raw_ids) != set(raw_ids) or len(child_raw_ids) != len(raw_ids):
            raise ValueError("Summary V2 parent raw source IDs disagree with direct child closure")
        refs = [child["id"] for child in children]
        source_sha = _sha(_canonical([{"id": child["id"], "summary_sha256": child["summary_sha256"]} for child in children]))
    # Keep a deterministic readable projection of canonical V2 content; original sidecar remains attached as evidence.
    content = {}
    for key in ("overview", "scenes", "atoms", "relations", "retrieval_anchors", "omissions", "policy_events"):
        if document.get(key):
            content[key] = document[key]
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2)
    if not content:
        raise ValueError("Summary V2 sidecar has no summary content")
    return {"format": FORMAT, "conversation_id": conversation, "level": level, "text": text,
            "source_refs": refs, "raw_source_ids": raw_ids, "source_sha256": source_sha,
            "legacy_id": document.get("parallel_summary_id") or document.get("summary_v2_id"),
            "legacy_format": "summary-v2"}


def convert_summary(document: dict[str, Any], raw_records: list[dict[str, Any]],
                    prior_summary_map: dict[str, dict[str, Any]] | None = None, *,
                    original_bytes: bytes | None = None) -> dict[str, Any]:
    """Convert one explicitly supplied V1 metadata record or V2 sidecar without model calls or archive reads."""
    if not isinstance(document, dict) or not isinstance(raw_records, list):
        raise TypeError("document and normalized raw_records are required")
    prior = prior_summary_map or {}
    raws = _raw_map(raw_records)
    if document.get("format_version") == 2 or "sidecar" in document:
        converted = _v2(document, raws, prior)
    elif isinstance(document.get("summary_id"), str) and "level" in document:
        converted = _v1(document, raws, prior)
    else:
        raise ValueError("unknown legacy summary format")
    converted.update(_legacy_bytes(document, original_bytes))
    return _finish(converted)


def _frontmatter_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"", "[]"}:
        return [] if value == "[]" else None
    if value in {"null", "None"}:
        return None
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _parse_v1_markdown(payload: bytes) -> dict[str, Any]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("legacy V1 Markdown is not UTF-8") from exc
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError("legacy V1 summary frontmatter is missing")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("legacy V1 summary frontmatter is not closed") from exc
    metadata: dict[str, Any] = {}
    current_list = None
    for line in lines[1:end]:
        if line.startswith("  - ") and current_list:
            metadata[current_list].append(_frontmatter_scalar(line[4:]))
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        parsed = _frontmatter_scalar(value)
        metadata[key] = parsed
        current_list = key if parsed is None else None
        if current_list:
            metadata[current_list] = []
    sections = {"Topics": [], "Established Conclusions": [], "Open Questions": [], "Concepts": [], "Policy Events": []}
    section = None
    for line in lines[end + 1:]:
        if line.startswith("## "):
            section = line[3:].strip()
        elif section in sections and line.startswith("- "):
            value = line[2:].strip()
            if value != "None recorded.":
                sections[section].append(value)
    events = []
    for value in sections["Policy Events"]:
        try:
            event = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("legacy V1 policy event is not JSON") from exc
        if not isinstance(event, dict):
            raise ValueError("legacy V1 policy event must be an object")
        events.append(event)
    return {**metadata, "topics": sections["Topics"],
            "established_conclusions": sections["Established Conclusions"],
            "open_questions": sections["Open Questions"], "concepts": sections["Concepts"],
            "policy_events": events}


def convert_files(files: list[tuple[str, bytes]], normalized_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert a fixed caller-supplied file set; never scans or writes an archive."""
    if not isinstance(files, list) or not files:
        raise ValueError("an explicit non-empty summary file list is required")
    entries = []
    seen_filenames = set()
    for filename, payload in files:
        if not isinstance(filename, str) or not isinstance(payload, bytes):
            raise TypeError("summary files must be (filename, bytes) pairs")
        normalized_name = filename.replace("\\", "/")
        if normalized_name in seen_filenames:
            raise ValueError(f"duplicate legacy summary filename: {filename}")
        seen_filenames.add(normalized_name)
        if filename.lower().endswith(".md"):
            if payload.startswith(b"---\n") or payload.startswith(b"---\r\n"):
                parsed = _parse_v1_markdown(payload)
                if (parsed.get("format") == "memory-wuxian-summary-v2" or
                    parsed.get("format_version") == 2):
                    entries.append((filename, payload, {}, "markdown"))
                else:
                    if "level" not in parsed and "summary_level" in parsed:
                        parsed["level"] = parsed["summary_level"]
                    entries.append((filename, payload, parsed, "v1"))
            else:
                entries.append((filename, payload, {}, "markdown"))
        elif filename.lower().endswith(".json"):
            try:
                document = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"legacy summary JSON is invalid: {filename}") from exc
            if not isinstance(document, dict):
                raise ValueError(f"legacy summary JSON must be an object: {filename}")
            if document.get("format") == "memory-wuxian-summary-v2-completion-v1":
                # Completion files are evidence only; sidecars carry the summary projection.
                entries.append((filename, payload, document, "completion"))
            elif document.get("format_version") == 2:
                entries.append((filename, payload, document, "v2"))
            else:
                raise ValueError(f"unknown legacy summary JSON version: {filename}")
        else:
            raise ValueError(f"unsupported legacy summary file type: {filename}")

    completions = {}
    markdowns = {}
    sidecars = {}
    sidecar_aliases = {}
    for name, payload, document, kind in entries:
        if kind == "completion":
            supplied = document.get("completion_sha256")
            expected = _sha(_canonical({k: v for k, v in document.items() if k != "completion_sha256"}))
            if supplied != expected:
                raise ValueError(f"legacy Summary V2 completion hash mismatch: {name}")
            alias = document.get("target_summary_id")
            if not isinstance(alias, str) or alias in completions:
                raise ValueError(f"duplicate or malformed Summary V2 completion alias: {name}")
            completions[alias] = (name, payload, document)
        elif kind == "markdown":
            parent = str(PurePosixPath(name.replace("\\", "/")).parent)
            if parent in markdowns:
                raise ValueError(f"multiple Markdown files supplied for one legacy bundle: {parent}")
            markdowns[parent] = (name, payload)
        elif kind == "v2":
            supplied = document.get("projection_sha256")
            expected = _sha(_canonical({k: v for k, v in document.items() if k != "projection_sha256"}))
            if supplied != expected:
                raise ValueError(f"legacy Summary V2 projection hash mismatch: {name}")
            alias = document.get("parallel_summary_id")
            if not isinstance(alias, str) or alias in sidecars:
                raise ValueError(f"duplicate or malformed Summary V2 sidecar alias: {name}")
            sidecars[alias] = (name, payload, document)
            summary_v2_id = document.get("summary_v2_id")
            if not isinstance(summary_v2_id, str) or summary_v2_id in sidecar_aliases:
                raise ValueError(f"duplicate or malformed Summary V2 object ID: {name}")
            sidecar_aliases[summary_v2_id] = alias

    consumed = set()
    for name, payload, _, kind in entries:
        if kind == "v1":
            consumed.add(name.replace("\\", "/"))
    for alias, (completion_name, completion_bytes, completion) in completions.items():
        found = sidecars.get(alias)
        if not found:
            raise ValueError(f"legacy Summary V2 completion has no supplied sidecar: {alias}")
        sidecar_name, sidecar_bytes, sidecar = found
        bundle = completion.get("bundle", {})
        if (completion.get("target_summary_id") != sidecar.get("parallel_summary_id") or
            completion.get("conversation_id") != sidecar.get("conversation_id") or
            completion.get("summary_level") != sidecar.get("summary_level") or
            bundle.get("summary_v2_id") != sidecar.get("summary_v2_id") or
            bundle.get("projection_sha256") != sidecar.get("projection_sha256")):
            raise ValueError(f"legacy Summary V2 completion identity disagrees with sidecar: {alias}")
        expected_json = bundle.get("summary_json_sha256")
        expected_markdown = bundle.get("summary_markdown_sha256")
        if not expected_json or _sha(sidecar_bytes) != expected_json:
            raise ValueError(f"legacy Summary V2 sidecar bytes disagree with completion: {alias}")
        parent = str(PurePosixPath(sidecar_name.replace("\\", "/")).parent)
        markdown = markdowns.get(parent)
        if expected_markdown and not markdown:
            raise ValueError(f"legacy Summary V2 completion requires its Markdown bytes: {alias}")
        if markdown and expected_markdown and _sha(markdown[1]) != expected_markdown:
            raise ValueError(f"legacy Summary V2 Markdown bytes disagree with completion: {alias}")
        enriched = {"sidecar": sidecar, "completion": completion,
                    "completion_bytes_sha256": _sha(completion_bytes)}
        if markdown:
            enriched["markdown_bytes_b64"] = base64.b64encode(markdown[1]).decode("ascii")
            enriched["markdown_bytes_sha256"] = _sha(markdown[1])
            consumed.add(markdown[0].replace("\\", "/"))
        sidecars[alias] = (sidecar_name, sidecar_bytes, enriched)
        consumed.add(completion_name.replace("\\", "/"))

    converted: list[dict[str, Any]] = []
    v1_aliases: dict[str, dict[str, Any]] = {}
    v2_aliases: dict[str, dict[str, Any]] = {}
    v1_documents = [entry for entry in entries if entry[3] == "v1" and entry[0].lower().endswith(".md")]
    v2_documents = [entry for entry in sidecars.values()]
    # V1 parents and V2 parents require children first; iterate only when all direct refs resolve.
    pending = [(name, payload, doc, "v1") for name, payload, doc, _ in v1_documents]
    pending.extend((name, payload, doc, "v2") for name, payload, doc in v2_documents)
    while pending:
        progressed = False
        for entry in list(pending):
            name, payload, document, kind = entry
            core = document.get("sidecar", document) if kind == "v2" else document
            level = core.get("level") if kind == "v1" else core.get("summary_level")
            source = core.get("source", {}) if kind == "v2" else core
            manifest = source.get("source_manifest", {}) if kind == "v2" else {}
            direct_children = source.get("children") or manifest.get("children") or []
            refs = (document.get("source_summaries") or []) if kind == "v1" else [
                (child.get("target_summary_id") or child.get("summary_v2_id") or child.get("parallel_summary_id"))
                for child in direct_children if isinstance(child, dict)
            ]
            if kind == "v2" and int(level or 0) > 1 and not refs:
                refs = [entry.get("source_ref") for entry in source.get("ref_catalog", []) if isinstance(entry, dict)]
            if kind == "v2":
                refs = [sidecar_aliases.get(ref, ref) for ref in refs]
            available = v1_aliases if kind == "v1" else v2_aliases
            if int(level or 0) > 1 and any(ref not in available for ref in refs):
                continue
            record = convert_summary(document, normalized_records, available, original_bytes=payload)
            converted.append(record)
            alias = (core.get("summary_id") if kind == "v1" else
                     core.get("parallel_summary_id") or core.get("summary_v2_id"))
            if isinstance(alias, str):
                alias_set = {alias}
                if kind == "v2":
                    alias_set.add(core.get("summary_v2_id"))
                aliases = v1_aliases if kind == "v1" else v2_aliases
                for old_alias in alias_set:
                    if old_alias in aliases and aliases[old_alias] is not record:
                        raise ValueError(f"duplicate legacy summary alias: {old_alias}")
                    aliases[old_alias] = record
            consumed.add(name.replace("\\", "/"))
            pending.remove(entry)
            progressed = True
        if not progressed:
            raise ValueError("legacy summary sources are missing, cyclic, or have unknown versions")
    unconsumed = sorted(seen_filenames - consumed)
    if unconsumed:
        raise ValueError("unmatched legacy summary files: " + ", ".join(unconsumed))
    return converted
