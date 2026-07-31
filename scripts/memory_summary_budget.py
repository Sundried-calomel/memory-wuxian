#!/usr/bin/env python3
"""Deterministic summary-budget eligibility without AI invocation."""

from __future__ import annotations

from typing import Any


def evaluate_summary_budget(metrics: Any, policy: Any) -> dict[str, Any]:
    required_metrics = {"conversation_id", "completed_rounds", "summarized_rounds", "unsummarized_characters", "estimated_unsummarized_tokens", "round_complete"}
    required_policy = {"minimum_completed_rounds", "character_threshold", "token_threshold"}
    if not isinstance(metrics, dict) or set(metrics) != required_metrics:
        raise ValueError("summary metrics have an invalid closed field set")
    if not isinstance(policy, dict) or set(policy) != required_policy:
        raise ValueError("summary policy has an invalid closed field set")
    integers = [metrics[key] for key in ("completed_rounds", "summarized_rounds", "unsummarized_characters", "estimated_unsummarized_tokens")]
    thresholds = [policy[key] for key in required_policy]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers + thresholds):
        raise ValueError("summary metrics and thresholds must be non-negative integers")
    if not isinstance(metrics["conversation_id"], str) or not metrics["conversation_id"]:
        raise ValueError("conversation_id is required")
    if not isinstance(metrics["round_complete"], bool) or metrics["summarized_rounds"] > metrics["completed_rounds"]:
        raise ValueError("summary round boundary is invalid")
    pending_rounds = metrics["completed_rounds"] - metrics["summarized_rounds"]
    reasons = []
    if pending_rounds >= policy["minimum_completed_rounds"]:
        reasons.append("completed-round-threshold")
    if metrics["unsummarized_characters"] >= policy["character_threshold"]:
        reasons.append("character-threshold")
    if metrics["estimated_unsummarized_tokens"] >= policy["token_threshold"]:
        reasons.append("token-threshold")
    due = bool(reasons) and metrics["round_complete"] and pending_rounds > 0
    return {
        "schema_version": 1,
        "due": due,
        "reasons": reasons,
        "blocked_by_incomplete_round": bool(reasons) and not metrics["round_complete"],
        "completed_round": metrics["completed_rounds"],
        "pending_rounds": pending_rounds,
        "ai_invocations": 0,
    }
