"""The review -> re-run -> refresh loop: UI decisions must be ingested as
`human` stage decisions, outrank the agent, and reshape clusters."""

import shutil

import duckdb
import pandas as pd
import pytest

from adapters import queue_store
from core.config import REPO_ROOT, Settings
from core.pipeline import run_generate, run_pipeline

PINNED_MODEL = REPO_ROOT / "data" / "artifacts" / "splink_model.json"


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    if not PINNED_MODEL.exists():
        pytest.skip("pinned Splink model missing — run the full pipeline once first")
    data_dir = tmp_path_factory.mktemp("local")
    artifacts_dir = tmp_path_factory.mktemp("artifacts")
    shutil.copy(PINNED_MODEL, artifacts_dir / "splink_model.json")
    return Settings(
        _env_file=None,
        cdp_data_dir=data_dir,
        cdp_artifacts_dir=artifacts_dir,
        n_companies=150,
        seed=11,
        mock_llm=True,
    )


def test_human_decisions_ingest_and_outrank(settings):
    run_generate(settings)
    run_pipeline(settings)

    qcon = queue_store.connect(settings.queue_db_path)
    try:
        first = queue_store.next_pending(qcon)
        assert first is not None, "expected at least one queued pair"
        second_row = queue_store.decide(qcon, first["id"], "approve")
        assert second_row["status"] == "approved"
        another = queue_store.next_pending(qcon)
        if another is not None:
            queue_store.decide(qcon, another["id"], "reject")
        assert queue_store.pending_ingestion_count(qcon) >= 1
    finally:
        qcon.close()

    run_pipeline(settings)

    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        human = con.execute(
            "SELECT record_a_id, record_b_id, match, decided_by FROM match_decisions "
            "WHERE stage = 'human'"
        ).df()
        clusters = con.execute("SELECT record_id, cluster_id FROM entity_clusters").df()
    finally:
        con.close()

    assert len(human) >= 1
    assert set(human["decided_by"]) == {"human-reviewer"}

    # The approved pair must now share a cluster.
    approved_a, approved_b = first["record_a_id"], first["record_b_id"]
    by_record = dict(zip(clusters["record_id"], clusters["cluster_id"]))
    assert by_record[approved_a] == by_record[approved_b]

    qcon = queue_store.connect(settings.queue_db_path)
    try:
        assert queue_store.pending_ingestion_count(qcon) == 0
    finally:
        qcon.close()


def test_pending_items_survive_reruns_without_finalize(settings):
    qcon = queue_store.connect(settings.queue_db_path)
    try:
        pending_before = queue_store.counts(qcon)["pending"]
    finally:
        qcon.close()
    if pending_before == 0:
        pytest.skip("no pending items left in this seed")

    run_pipeline(settings)
    qcon = queue_store.connect(settings.queue_db_path)
    try:
        assert queue_store.counts(qcon)["pending"] == pending_before
    finally:
        qcon.close()


def test_finalize_pending_drains_queue_with_oracle(settings):
    run_pipeline(settings, finalize_pending=True)
    qcon = queue_store.connect(settings.queue_db_path)
    try:
        counts = queue_store.counts(qcon)
        assert counts["pending"] == 0 and counts["skipped"] == 0
    finally:
        qcon.close()
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        oracle = con.execute(
            "SELECT COUNT(*) AS n FROM match_decisions WHERE stage = 'oracle_review' "
            "AND decided_by = 'oracle-bulk-reviewer'"
        ).fetchone()[0]
    finally:
        con.close()
    assert oracle >= 1
