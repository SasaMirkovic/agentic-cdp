"""Pipeline orchestrator: bronze → silver → stages 1-4 → clustering → gold → eval.

Idempotent by construction: decisions upsert on the sorted-pair natural key,
every automated quantity is seeded or hash-derived, and re-running on unchanged
inputs produces byte-identical tables and no new decisions.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pandas as pd

from adapters import local_store, queue_store
from core.config import Settings
from core import normalize
from core.clustering import build_clusters
from core.evaluate import evaluate, write_metrics
from core.generator import generate
from core.stages.agent import run_stage3, write_error_sidecar
from core.stages.common import pair_key
from core.stages.deterministic import run_stage1
from core.stages.probabilistic import run_stage2
from core.stages.review import enqueue_cut_edges, ingest_human_decisions, route_stage4
from core.survivorship import assign_customer_ids, build_gold
from core.config import REPO_ROOT

logger = logging.getLogger(__name__)


DERIVED_TABLES = [
    "silver_records", "match_decisions", "stage2_scores", "entity_clusters",
    "gold_profiles", "customer_id_crosswalk",
]


def _clear_derived_state(settings: Settings, con) -> None:
    """New bronze data invalidates everything downstream: record ids are minted by
    position, so stale decisions would silently attach to different companies."""
    for table in DERIVED_TABLES:
        con.execute(f"DELETE FROM {table}")
    for name in ("queue.db", "queue.db-wal", "queue.db-shm", "metrics.json",
                 "oracle_injected_errors.json"):
        path = settings.cdp_data_dir / name
        if path.exists():
            path.unlink()


def run_generate(settings: Settings) -> dict:
    data = generate(settings)
    con = local_store.connect(settings.duckdb_path)
    try:
        _clear_derived_state(settings, con)
        for source, df in data.bronze.items():
            local_store.write_df(con, f"bronze_{source}", df, replace=True)
    finally:
        con.close()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    data.ground_truth.to_parquet(settings.artifacts_dir / "ground_truth.parquet", index=False)
    data.enrichment.to_parquet(settings.artifacts_dir / "enrichment_reference.parquet", index=False)
    data.hard_negative_pairs.to_parquet(
        settings.artifacts_dir / "hard_negative_pairs.parquet", index=False
    )
    return {
        "records": int(sum(len(df) for df in data.bronze.values())),
        "entities": int(data.ground_truth["entity_id"].nunique()),
        "hard_negative_pairs": len(data.hard_negative_pairs),
    }


def _build_silver(con) -> pd.DataFrame:
    frames = []
    for source in local_store.SOURCES:
        df = local_store.read_df(con, f"SELECT * FROM bronze_{source}")
        if not df.empty:
            frames.append(df)
    if not frames:
        raise RuntimeError("No bronze data. Run `cdp generate` first.")
    bronze = pd.concat(frames, ignore_index=True)
    bronze["record_id"] = bronze["source_system"] + ":" + bronze["source_record_id"]
    bronze["normalized_name"] = bronze["company_name"].map(normalize.normalize_name)
    bronze["normalized_domain"] = bronze["domain"].map(normalize.normalize_domain)
    bronze["normalized_phone"] = bronze["phone"].map(normalize.normalize_phone)
    bronze["normalized_address"] = bronze["address"].map(normalize.normalize_address)
    bronze["name_first_token"] = bronze["normalized_name"].map(
        lambda n: n.split(" ")[0] if isinstance(n, str) and n else None
    )
    bronze["pii_flags"] = bronze.apply(
        lambda r: normalize.pii_flags(r["phone"], r["address"], r["notes"]), axis=1
    )
    silver = bronze.drop(columns=["raw_payload"])
    return silver


def _existing_pairs(con) -> set[tuple[str, str]]:
    df = local_store.read_df(con, "SELECT record_a_id, record_b_id FROM match_decisions")
    return {pair_key(a, b) for a, b in zip(df["record_a_id"], df["record_b_id"])}


def _upsert_decisions(con, decisions: list[dict], overwrite: bool = False) -> int:
    written = 0
    for d in decisions:
        exists = con.execute(
            "SELECT stage FROM match_decisions WHERE record_a_id = ? AND record_b_id = ?",
            [d["record_a_id"], d["record_b_id"]],
        ).fetchone()
        if exists and not overwrite:
            continue
        if exists:
            con.execute(
                "DELETE FROM match_decisions WHERE record_a_id = ? AND record_b_id = ?",
                [d["record_a_id"], d["record_b_id"]],
            )
        con.execute(
            "INSERT INTO match_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                d["match_id"], d["record_a_id"], d["record_b_id"], d["stage"], d["match"],
                d["confidence"], d["evidence"], d["reason"], d["decided_by"],
                d["decided_at"], d["cost_usd"], d["routed_to_human"],
            ],
        )
        written += 1
    return written


def _load_decisions(con) -> list[dict]:
    df = local_store.read_df(con, "SELECT * FROM match_decisions")
    return df.to_dict("records")


def run_pipeline(
    settings: Settings, record_mock: bool = False, finalize_pending: bool = False
) -> dict:
    con = local_store.connect(settings.duckdb_path)
    qcon = queue_store.connect(settings.queue_db_path)
    try:
        silver = _build_silver(con)
        silver_out = silver.copy()
        local_store.write_df(con, "silver_records", silver_out, replace=True)
        # Defensive: decisions referencing records absent from silver are orphans
        # (stale generation); they must never reach clustering.
        con.execute(
            "DELETE FROM match_decisions WHERE record_a_id NOT IN (SELECT record_id FROM silver_records) "
            "OR record_b_id NOT IN (SELECT record_id FROM silver_records)"
        )
        silver_ids = set(silver["record_id"])

        gt_path = settings.artifacts_dir / "ground_truth.parquet"
        ground_truth = pd.read_parquet(gt_path) if gt_path.exists() else None
        enrich_path = settings.artifacts_dir / "enrichment_reference.parquet"
        enrichment = pd.read_parquet(enrich_path) if enrich_path.exists() else pd.DataFrame()

        # Human decisions from the UI outrank everything: ingest before stages run.
        human = ingest_human_decisions(qcon, silver_ids)
        _upsert_decisions(con, human, overwrite=True)

        decided = _existing_pairs(con)

        stage1_decisions, demoted = run_stage1(silver, settings, decided)
        _upsert_decisions(con, stage1_decisions)
        decided |= {pair_key(d["record_a_id"], d["record_b_id"]) for d in stage1_decisions}

        stage2_decisions, scores, band = run_stage2(silver, settings, decided, demoted)
        _upsert_decisions(con, stage2_decisions)
        decided |= {pair_key(d["record_a_id"], d["record_b_id"]) for d in stage2_decisions}
        if not scores.empty:
            local_store.write_df(con, "stage2_scores", scores, replace=True)

        band_new = [p for p in band if p not in decided]
        agent_decisions, _ = run_stage3(silver, band_new, settings, ground_truth)
        _upsert_decisions(con, agent_decisions)

        # The injected-error sidecar is derived from the persisted decision log
        # (agent matches that contradict ground truth), not this run's increment
        # — a re-run with an empty band must not clobber it. Confident injected
        # errors are never overwritten by review (they bypass the queue).
        injected: list[list[str]] = []
        if ground_truth is not None:
            truth_map = dict(zip(ground_truth["record_id"], ground_truth["entity_id"]))
            injected = sorted(
                [d["record_a_id"], d["record_b_id"]]
                for d in _load_decisions(con)
                if d["stage"] == "agent" and d["match"]
                and truth_map.get(d["record_a_id"]) != truth_map.get(d["record_b_id"])
            )
        write_error_sidecar(settings.cdp_data_dir / "oracle_injected_errors.json", injected)
        if record_mock:
            settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(agent_decisions).to_parquet(
                settings.artifacts_dir / "mock_agent_decisions.parquet", index=False
            )
            write_error_sidecar(
                settings.artifacts_dir / "mock_injected_errors.json", injected
            )

        all_agent = [d for d in _load_decisions(con) if d["stage"] == "agent"]
        oracle_decisions = route_stage4(
            silver, all_agent, settings, qcon, ground_truth, finalize_pending
        )
        _upsert_decisions(con, oracle_decisions, overwrite=True)

        pending_pairs = {
            pair_key(i["record_a_id"], i["record_b_id"])
            for i in queue_store.unresolved_items(qcon)
        }

        decisions = _load_decisions(con)
        result = build_clusters(
            sorted(silver["record_id"]),
            decisions,
            settings.agent_confidence_threshold,
            pending_pairs,
        )
        for cut in result.cut_edges:
            con.execute(
                "UPDATE match_decisions SET evidence = json_merge_patch(evidence, ?) "
                "WHERE record_a_id = ? AND record_b_id = ?",
                [json.dumps({"cut_by": cut["cut_by"]}), cut["record_a_id"], cut["record_b_id"]],
            )
        # Cut splits are routed to human review for confirmation (priority items,
        # not subject to the queue cap); a human match here is uncuttable and a
        # human no-match confirms the split.
        enqueue_cut_edges(silver, result.cut_edges, decisions, qcon)
        # Metrics report the queue as it stands after cut confirmations were added.
        pending_pairs = {
            pair_key(i["record_a_id"], i["record_b_id"])
            for i in queue_store.unresolved_items(qcon)
        }

        # Deterministic run id: hash of the decision set, so an unchanged re-run
        # writes identical rows everywhere.
        run_id = "run-" + hashlib.sha256(
            json.dumps(sorted(d["match_id"] for d in decisions)).encode()
        ).hexdigest()[:12]

        cluster_rows = [
            {
                "record_id": m,
                "cluster_id": cid,
                "needs_human_review": cid in result.flagged,
                "run_id": run_id,
            }
            for cid, members in sorted(result.clusters.items())
            for m in members
        ]
        local_store.write_df(con, "entity_clusters", pd.DataFrame(cluster_rows), replace=True)

        prior_crosswalk = local_store.read_df(con, "SELECT * FROM customer_id_crosswalk")
        records = silver.set_index("record_id").to_dict("index")
        assignments, crosswalk_rows = assign_customer_ids(result, records, prior_crosswalk, run_id)
        if crosswalk_rows:
            xdf = pd.DataFrame(crosswalk_rows)
            local_store.write_df(con, "customer_id_crosswalk", xdf, replace=False)

        gold = build_gold(result, silver, enrichment, assignments)
        local_store.write_df(con, "gold_profiles", gold, replace=True)

        metrics: dict = {}
        if ground_truth is not None:
            metrics = evaluate(
                silver, decisions, ground_truth,
                settings.agent_confidence_threshold, pending_pairs, injected,
            )
            # The committed docs/metrics.md snapshot belongs to the default-artifacts
            # run only; isolated runs (tests) must not clobber it.
            md_path = None if settings.cdp_artifacts_dir else REPO_ROOT / "docs" / "metrics.md"
            write_metrics(metrics, settings.metrics_path, md_path)

        return {
            "silver_records": len(silver),
            "decisions": {
                stage: sum(1 for d in decisions if d["stage"] == stage)
                for stage in ("deterministic", "probabilistic", "agent", "oracle_review", "human")
            },
            "new_agent_decisions": len(agent_decisions),
            "queue_pending": queue_store.counts(qcon)["pending"],
            "clusters": len(result.clusters),
            "flagged_clusters": len(result.flagged),
            "cut_edges": len(result.cut_edges),
            "gold_profiles": len(gold),
            "metrics": metrics.get("stages", []),
        }
    finally:
        con.close()
        qcon.close()
