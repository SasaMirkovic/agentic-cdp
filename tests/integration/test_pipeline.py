"""Full-pipeline integration test on a small seeded dataset.

Uses the committed pinned Splink model (data/artifacts/splink_model.json) —
EM training on a few hundred records is deliberately refused by the pipeline.
"""

import json
import shutil

import duckdb
import pandas as pd
import pytest

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
        seed=7,
        mock_llm=True,
    )


@pytest.fixture(scope="module")
def pipeline_summary(settings) -> dict:
    run_generate(settings)
    return run_pipeline(settings)


def _truth(settings) -> dict[str, str]:
    gt = pd.read_parquet(settings.artifacts_dir / "ground_truth.parquet")
    return dict(zip(gt["record_id"], gt["entity_id"]))


def _decisions(settings) -> pd.DataFrame:
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        return con.execute("SELECT * FROM match_decisions").df()
    finally:
        con.close()


def test_stage1_precision_is_perfect(settings, pipeline_summary):
    metrics = json.loads(settings.metrics_path.read_text())
    stage1 = metrics["stages"][0]
    assert stage1["prefix"] == "stage1_deterministic"
    assert stage1["precision"] == 1.0


def test_no_automated_cross_entity_matches_outside_sidecar(settings, pipeline_summary):
    truth = _truth(settings)
    decisions = _decisions(settings)
    auto = decisions[decisions["stage"].isin(["deterministic", "probabilistic"]) & decisions["match"]]
    cross = [
        (a, b) for a, b in zip(auto["record_a_id"], auto["record_b_id"])
        if truth[a] != truth[b]
    ]
    assert cross == []

    sidecar = json.loads(
        (settings.cdp_data_dir / "oracle_injected_errors.json").read_text()
    )["injected_false_matches"]
    agent = decisions[(decisions["stage"] == "agent") & decisions["match"]]
    agent_cross = sorted(
        (a, b) for a, b in zip(agent["record_a_id"], agent["record_b_id"])
        if truth[a] != truth[b]
    )
    assert agent_cross == sorted(map(tuple, sidecar))


def test_shared_domain_hard_negatives_never_automatched(settings, pipeline_summary):
    truth = _truth(settings)
    hard = pd.read_parquet(settings.artifacts_dir / "hard_negative_pairs.parquet")
    hard_entity_pairs = {tuple(sorted(p)) for p in zip(hard["entity_a"], hard["entity_b"])}
    decisions = _decisions(settings)
    auto = decisions[decisions["stage"].isin(["deterministic", "probabilistic"]) & decisions["match"]]
    violations = [
        (a, b) for a, b in zip(auto["record_a_id"], auto["record_b_id"])
        if tuple(sorted((truth[a], truth[b]))) in hard_entity_pairs
    ]
    assert violations == []


def test_queue_capped(settings, pipeline_summary):
    # Cut-split confirmations are priority items outside the cap.
    assert pipeline_summary["queue_pending"] <= settings.human_queue_cap + pipeline_summary["cut_edges"]


def test_regenerate_clears_derived_state(settings, pipeline_summary):
    # Regenerating with different parameters must not leave stale decisions
    # attached to re-minted record ids.
    other = settings.model_copy(update={"seed": 8, "n_companies": 120})
    run_generate(other)
    summary = run_pipeline(other)
    assert summary["decisions"]["human"] == 0
    metrics = json.loads(other.metrics_path.read_text(encoding="utf-8"))
    assert metrics["stages"][0]["precision"] == 1.0
    # Restore the module-scoped dataset for any later tests.
    run_generate(settings)
    run_pipeline(settings)


def test_rerun_is_idempotent(settings, pipeline_summary):
    first = _decisions(settings)
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        gold_first = con.execute(
            "SELECT customer_id, canonical_name FROM gold_profiles ORDER BY customer_id"
        ).df()
    finally:
        con.close()

    second_summary = run_pipeline(settings)
    second = _decisions(settings)
    assert len(first) == len(second)
    assert sorted(first["match_id"]) == sorted(second["match_id"])

    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        gold_second = con.execute(
            "SELECT customer_id, canonical_name FROM gold_profiles ORDER BY customer_id"
        ).df()
    finally:
        con.close()
    pd.testing.assert_frame_equal(gold_first, gold_second)
    assert second_summary["new_agent_decisions"] == 0


def test_recall_improves_across_stages(settings, pipeline_summary):
    metrics = json.loads(settings.metrics_path.read_text())
    recalls = [s["recall"] for s in metrics["stages"]]
    assert recalls == sorted(recalls)
    assert recalls[-1] > recalls[0]
