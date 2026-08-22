"""Stage 4 — human review routing.

The genuinely-human queue is capped (default 50, lowest agent confidence
first); the overflow is adjudicated immediately by the oracle bulk reviewer
(ground truth, labeled `oracle_review`) — disclosed in the README as a
simulated review team. Pending/skipped items survive re-runs indefinitely;
only `--finalize-pending` hands aged items to the oracle.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from core.config import Settings
from core.schemas import AgentSnapshot, NormalizedFields, RecordView
from core.stages.common import decision_row, pair_key
from adapters import queue_store


def _record_view(row: dict) -> dict:
    return RecordView(
        record_id=row["record_id"],
        source_system=row["source_system"],
        company_name=row.get("company_name"),
        domain=row.get("domain"),
        address=row.get("address"),
        phone=row.get("phone"),
        industry=row.get("industry"),
        employees=None if pd.isna(row.get("employees")) else int(row["employees"]),
        revenue=None if pd.isna(row.get("revenue")) else float(row["revenue"]),
        notes=row.get("notes"),
        normalized=NormalizedFields(
            name=row.get("normalized_name"),
            domain=row.get("normalized_domain"),
            phone=row.get("normalized_phone"),
            address=row.get("normalized_address"),
        ),
        pii_flags=list(row.get("pii_flags") or []),
    ).model_dump()


def ingest_human_decisions(qcon: sqlite3.Connection, known_record_ids: set[str]) -> list[dict]:
    """Convert un-ingested UI decisions into `human` stage decision rows.

    Items whose records are unknown to silver (e.g. a demo fixture served
    against real data) are marked ingested but produce no decision — a human
    verdict on a record that doesn't exist must never become an uncuttable edge.
    """
    decisions = []
    ingested_ids = []
    for item in queue_store.uningested_decisions(qcon):
        if item["record_a_id"] not in known_record_ids or item["record_b_id"] not in known_record_ids:
            ingested_ids.append(item["id"])
            continue
        match = item["status"] == "approved"
        decisions.append(
            decision_row(
                item["record_a_id"],
                item["record_b_id"],
                stage="human",
                match=match,
                confidence=1.0,
                evidence={"queue_item_id": item["id"]},
                reason="Human adjudication via the review-queue UI.",
                decided_by="human-reviewer",
                decided_at=item["reviewed_at"],
            )
        )
        ingested_ids.append(item["id"])
    queue_store.mark_ingested(qcon, ingested_ids)
    return decisions


def enqueue_cut_edges(
    silver: pd.DataFrame,
    cut_edges: list[dict],
    decisions: list[dict],
    qcon: sqlite3.Connection,
) -> int:
    """Queue each edge removed by the min-cut conflict policy for confirmation."""
    if not cut_edges:
        return 0
    records = silver.set_index("record_id").to_dict("index")
    for rid, row in records.items():
        row["record_id"] = rid
    by_pair = {(d["record_a_id"], d["record_b_id"]): d for d in decisions}
    existing = queue_store.existing_pairs(qcon)
    added = 0
    for cut in cut_edges:
        pair = (cut["record_a_id"], cut["record_b_id"])
        if pair in existing:
            continue
        a, b = records.get(pair[0]), records.get(pair[1])
        original = by_pair.get(pair)
        if a is None or b is None or original is None:
            continue
        payload = {
            "record_a": _record_view(a),
            "record_b": _record_view(b),
            "agent": AgentSnapshot(
                match=bool(original["match"]),
                confidence=float(original["confidence"]),
                evidence=["edge_cut_by_conflict_policy", f"original_stage:{original['stage']}"],
                reason=(
                    "This match was removed by the min-cut conflict policy because a "
                    "confirmed no-match contradicted it. Approve to restore the link "
                    "(overrides the split), reject to confirm the split."
                ),
                decided_by="conflict-policy:min-cut",
            ).model_dump(),
        }
        queue_store.upsert_pending(qcon, pair[0], pair[1], payload)
        added += 1
    return added


def route_stage4(
    silver: pd.DataFrame,
    agent_decisions: list[dict],
    settings: Settings,
    qcon: sqlite3.Connection,
    ground_truth: pd.DataFrame | None,
    finalize_pending: bool = False,
) -> list[dict]:
    """Fill the human queue up to the cap; oracle-review the overflow.
    Returns oracle_review decisions to upsert."""
    records = silver.set_index("record_id").to_dict("index")
    for rid, row in records.items():
        row["record_id"] = rid
    truth = (
        dict(zip(ground_truth["record_id"], ground_truth["entity_id"]))
        if ground_truth is not None
        else {}
    )

    routed = [d for d in agent_decisions if d["routed_to_human"]]
    routed.sort(key=lambda d: (d["confidence"], d["record_a_id"], d["record_b_id"]))

    existing = queue_store.existing_pairs(qcon)
    current_pending = queue_store.counts(qcon)["pending"]
    capacity = max(0, settings.human_queue_cap - current_pending)

    oracle_decisions: list[dict] = []

    def oracle_decide(a_id: str, b_id: str) -> dict:
        same = truth.get(a_id) == truth.get(b_id)
        return decision_row(
            a_id,
            b_id,
            stage="oracle_review",
            match=same,
            confidence=1.0,
            evidence={"simulated_review_team": True},
            reason="Simulated bulk review adjudicated from generator ground truth "
            "(disclosed: stands in for a human review team at demo scale).",
            decided_by="oracle-bulk-reviewer",
        )

    for d in routed:
        pair = pair_key(d["record_a_id"], d["record_b_id"])
        if pair in existing:
            continue
        if capacity > 0:
            a, b = records.get(pair[0]), records.get(pair[1])
            if a is None or b is None:
                continue
            payload = {
                "record_a": _record_view(a),
                "record_b": _record_view(b),
                "agent": AgentSnapshot(
                    match=d["match"],
                    confidence=d["confidence"],
                    evidence=json.loads(d["evidence"]).get("factors", []),
                    reason=d["reason"],
                    decided_by=d["decided_by"],
                ).model_dump(),
            }
            queue_store.upsert_pending(qcon, pair[0], pair[1], payload)
            capacity -= 1
        else:
            if not truth:
                raise RuntimeError("Oracle bulk review requires the ground-truth artifact.")
            oracle_decisions.append(oracle_decide(*pair))

    if finalize_pending:
        for item in queue_store.unresolved_items(qcon):
            if not truth:
                raise RuntimeError("--finalize-pending requires the ground-truth artifact.")
        for item in queue_store.unresolved_items(qcon):
            d = oracle_decide(item["record_a_id"], item["record_b_id"])
            oracle_decisions.append(d)
            queue_store.resolve_externally(
                qcon, item["id"], "approved" if d["match"] else "rejected"
            )

    return oracle_decisions
