"""Pure deterministic algorithms used by MemoryStore retrieval paths."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Sequence


SEARCH_STOP_TERMS = {
    "一个", "一样", "已经", "之前", "什么", "他们", "但是", "你们", "你应该",
    "你的", "这个", "这些", "这样", "还是", "然后", "现在", "的话", "知道",
    "我们", "我的", "意思", "怎么", "就是", "可以", "如果", "进行", "里面",
    "对应", "时候", "一下", "因为", "所以", "the", "and", "for", "that", "this",
    "with", "from", "into", "about",
}


def deterministic_excerpt(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit]


def normalize_search_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def search_terms(
    query: str,
    limit: int = 128,
    *,
    normalizer: Callable[[str], str] = normalize_search_text,
) -> List[str]:
    normalized = normalizer(query)
    ascii_terms = {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) >= 2 and token not in SEARCH_STOP_TERMS
    }
    cjk_terms = set()
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        if 2 <= len(run) <= 8 and run not in SEARCH_STOP_TERMS:
            cjk_terms.add(run)
        for width in (4, 3, 2):
            for start in range(0, len(run) - width + 1):
                term = run[start:start + width]
                if term not in SEARCH_STOP_TERMS:
                    cjk_terms.add(term)
    ordered_ascii = sorted(ascii_terms, key=lambda term: (-len(term), term))
    ordered_cjk = sorted(cjk_terms, key=lambda term: (-len(term), term))
    return (ordered_ascii + ordered_cjk)[:limit]


def ranked_search(
    records: Sequence[Dict[str, Any]],
    query_normalized: str,
    terms: Sequence[str],
    text_getter,
    *,
    normalizer: Callable[[str], str] = normalize_search_text,
) -> List[Dict[str, Any]]:
    if not records:
        return []
    normalized_texts = [normalizer(text_getter(record)) for record in records]
    document_frequencies = {
        term: sum(1 for text in normalized_texts if term in text)
        for term in terms
    }
    record_count = len(records)
    ranked = []
    for record, text in zip(records, normalized_texts):
        matched = [term for term in terms if term in text]
        exact_match = query_normalized in text
        if not matched and not exact_match:
            continue
        score = sum(
            (1.0 + min(len(term), 8) / 4.0)
            * (1.0 + math.log((record_count + 1) / (document_frequencies[term] + 1)))
            for term in matched
        )
        if exact_match:
            score += 1000.0
        ranked.append({
            "record": record,
            "score": score,
            "matched_terms": matched,
            "exact_match": exact_match,
        })
    return sorted(
        ranked,
        key=lambda item: (
            -float(item["score"]),
            -len(item["matched_terms"]),
            int(item["record"].get(
                "sequence",
                item["record"].get("source_start_sequence", 0),
            )),
        ),
    )


def strongest_matches(
    ranked: Sequence[Dict[str, Any]],
    term_count: int,
    limit: int,
) -> List[Dict[str, Any]]:
    if not ranked:
        return []
    minimum_terms = 1 if term_count <= 2 else 2
    top_score = float(ranked[0]["score"])
    threshold = top_score * 0.55
    selected = [
        item for item in ranked
        if (item["exact_match"] or len(item["matched_terms"]) >= minimum_terms)
        and float(item["score"]) >= threshold
    ]
    return selected[:limit]


def unique_values(values: Iterable[str], limit: int) -> List[str]:
    selected: List[str] = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        selected.append(value)
        if len(selected) >= limit:
            break
    return selected
