"""Gold layer: golden profiles with per-field survivorship, enrichment join,
and anchor-based stable customer ids via the row-versioned crosswalk.

customer_id authority: the cluster's anchor record (oldest by loaded_at, then
source priority, then record_id). Minted on first appearance, persisted in the
crosswalk. On split, the fragment containing the anchor keeps the id; on merge,
the id whose anchor is oldest survives and the other is marked superseded.
Ids are never reused.
"""

from __future__ import annotations

import json
import uuid

import pandas as pd

from core.config import SOURCE_PRIORITY
from core.clustering import ClusterResult
from core.stages.common import PIPELINE_TS

_NAMESPACE = uuid.UUID("a3bb189e-8bf9-3888-9912-ace4e6543002")

SURVIVOR_FIELDS = ["company_name", "domain", "address", "phone", "industry", "employees", "revenue"]


def _priority_index(source: str) -> int:
    return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def anchor_of(members: list[str], records: dict[str, dict]) -> str:
    def sort_key(record_id: str):
        row = records[record_id]
        return (str(row["loaded_at"]), _priority_index(row["source_system"]), record_id)

    return min(members, key=sort_key)


def mint_customer_id(anchor_record_id: str, generation: int = 0) -> str:
    """Deterministic id from the anchor record. `generation` salts re-mints when
    the base id already exists in crosswalk history (e.g. merge then split back)
    so that ids are never reused."""
    key = anchor_record_id if generation == 0 else f"{anchor_record_id}#{generation}"
    return f"C-{uuid.uuid5(_NAMESPACE, key).hex[:12]}"


def assign_customer_ids(
    result: ClusterResult,
    records: dict[str, dict],
    prior_crosswalk: pd.DataFrame,
    run_id: str,
) -> tuple[dict[str, str], list[dict]]:
    """Returns (cluster_id -> customer_id, new crosswalk rows to append)."""
    # Latest row per customer_id from prior runs; every id ever issued is
    # reserved so a re-mint can never collide with a superseded one.
    latest: dict[str, dict] = {}
    if not prior_crosswalk.empty:
        for _, row in prior_crosswalk.iterrows():
            latest[row["customer_id"]] = row.to_dict()
    ever_issued: set[str] = set(latest)
    active = {cid: r for cid, r in latest.items() if r["status"] == "active"}

    def mint_unused(anchor: str) -> str:
        generation = 0
        while True:
            candidate = mint_customer_id(anchor, generation)
            if candidate not in ever_issued:
                ever_issued.add(candidate)
                return candidate
            generation += 1

    anchor_to_prior: dict[str, str] = {
        r["anchor_record_id"]: cid for cid, r in active.items()
    }

    assignments: dict[str, str] = {}
    new_rows: list[dict] = []

    def record_age_key(record_id: str):
        row = records[record_id]
        return (str(row["loaded_at"]), _priority_index(row["source_system"]), record_id)

    for cluster_id in sorted(result.clusters):
        members = result.clusters[cluster_id]
        anchor = anchor_of(members, records)
        # Prior ids whose anchor records now live in this cluster (merge detection).
        prior_ids = sorted(
            {anchor_to_prior[m] for m in members if m in anchor_to_prior}
        )
        if not prior_ids:
            customer_id = mint_unused(anchor)
        else:
            # Survivor: the prior id whose anchor record is oldest.
            survivor = min(
                prior_ids, key=lambda cid: record_age_key(active[cid]["anchor_record_id"])
            )
            customer_id = survivor
            for other in prior_ids:
                if other != survivor:
                    new_rows.append(
                        {
                            "customer_id": other,
                            "anchor_record_id": active[other]["anchor_record_id"],
                            "member_set_hash": active[other]["member_set_hash"],
                            "status": "superseded",
                            "superseded_by_customer_id": survivor,
                            "run_id": run_id,
                        }
                    )
        assignments[cluster_id] = customer_id
        member_hash = cluster_id  # cluster_id already hashes the sorted member set
        prior = active.get(customer_id)
        if prior is None or prior["member_set_hash"] != member_hash or prior["anchor_record_id"] != anchor:
            new_rows.append(
                {
                    "customer_id": customer_id,
                    "anchor_record_id": anchor,
                    "member_set_hash": member_hash,
                    "status": "active",
                    "superseded_by_customer_id": None,
                    "run_id": run_id,
                }
            )
    return assignments, new_rows


def build_gold(
    result: ClusterResult,
    silver: pd.DataFrame,
    enrichment: pd.DataFrame,
    assignments: dict[str, str],
) -> pd.DataFrame:
    records = silver.set_index("record_id").to_dict("index")
    enrich_by_domain = (
        enrichment.drop_duplicates(subset="domain", keep="first")
        .set_index("domain")
        .to_dict("index")
        if not enrichment.empty
        else {}
    )

    rows = []
    for cluster_id in sorted(result.clusters):
        members = result.clusters[cluster_id]
        member_rows = sorted(
            (records[m] | {"record_id": m} for m in members),
            key=lambda r: (_priority_index(r["source_system"]), r["record_id"]),
        )
        survivors: dict[str, dict] = {}
        for fld in SURVIVOR_FIELDS:
            chosen = None
            for row in member_rows:
                value = row.get(fld)
                if value is not None and not (isinstance(value, float) and pd.isna(value)):
                    chosen = {"value": value, "from_record": row["record_id"], "source": row["source_system"]}
                    break
            survivors[fld] = chosen or {"value": None, "from_record": None, "source": None}

        domain = survivors["domain"]["value"]
        normalized_domain = None
        if domain:
            from core.normalize import normalize_domain

            normalized_domain = normalize_domain(str(domain))
        enrich = enrich_by_domain.get(normalized_domain, {})

        employees = survivors["employees"]["value"]
        revenue = survivors["revenue"]["value"]
        rows.append(
            {
                "customer_id": assignments[cluster_id],
                "canonical_name": survivors["company_name"]["value"],
                "canonical_domain": normalized_domain,
                "canonical_address": survivors["address"]["value"],
                "canonical_phone": survivors["phone"]["value"],
                "industry": survivors["industry"]["value"],
                "employees": int(employees) if employees is not None else None,
                "revenue": float(revenue) if revenue is not None else None,
                "industry_code": enrich.get("industry_code"),
                "employee_band": enrich.get("employee_band"),
                "tech_tags": enrich.get("tech_tags"),
                "source_record_ids": members,
                "survivorship_notes": json.dumps(
                    {
                        "fields": survivors,
                        "enriched_fields": sorted(k for k in ("industry_code", "employee_band", "tech_tags") if enrich),
                    },
                    default=str,
                ),
                "needs_human_review": cluster_id in result.flagged,
                "last_updated": PIPELINE_TS,
            }
        )
    return pd.DataFrame(rows)
