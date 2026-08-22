"""Shared helpers for pipeline stages: deterministic ids, pair keys, decision rows."""

from __future__ import annotations

import json
import uuid

# Fixed timestamp for automated decisions: re-runs must be byte-identical.
PIPELINE_TS = "2026-08-05T00:00:00"

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def pair_key(record_a: str, record_b: str) -> tuple[str, str]:
    """Canonical sorted pair ordering used everywhere a pair is stored or hashed."""
    return (record_a, record_b) if record_a <= record_b else (record_b, record_a)


def match_id_for(record_a: str, record_b: str, stage: str) -> str:
    a, b = pair_key(record_a, record_b)
    return str(uuid.uuid5(_NAMESPACE, f"{a}|{b}|{stage}"))


def decision_row(
    record_a: str,
    record_b: str,
    stage: str,
    match: bool,
    confidence: float,
    evidence: dict | list,
    reason: str,
    decided_by: str,
    cost_usd: float | None = None,
    routed_to_human: bool = False,
    decided_at: str = PIPELINE_TS,
) -> dict:
    a, b = pair_key(record_a, record_b)
    return {
        "match_id": match_id_for(a, b, stage),
        "record_a_id": a,
        "record_b_id": b,
        "stage": stage,
        "match": match,
        "confidence": round(confidence, 4),
        "evidence": json.dumps(evidence),
        "reason": reason,
        "decided_by": decided_by,
        "decided_at": decided_at,
        "cost_usd": cost_usd,
        "routed_to_human": routed_to_human,
    }
