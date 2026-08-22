import pandas as pd

from core.evaluate import adjusted_rand_index, evaluate, true_pairs
from core.stages.common import decision_row

GROUND_TRUTH = pd.DataFrame(
    {
        "record_id": ["a", "b", "c", "d"],
        "entity_id": ["E1", "E1", "E1", "E2"],
    }
)

SILVER = pd.DataFrame({"record_id": ["a", "b", "c", "d"]})


def d(a, b, stage, match, conf):
    return decision_row(a, b, stage=stage, match=match, confidence=conf,
                        evidence={}, reason="t", decided_by="t")


def test_true_pairs_universe():
    assert true_pairs(GROUND_TRUTH) == {("a", "b"), ("a", "c"), ("b", "c")}


def test_perfect_pipeline_scores_one():
    decisions = [
        d("a", "b", "deterministic", True, 1.0),
        d("b", "c", "probabilistic", True, 0.95),
    ]
    metrics = evaluate(SILVER, decisions, GROUND_TRUTH, 0.85, set(), [])
    final = metrics["stages"][-1]
    # Transitive closure implies (a, c): recall must be 1.0 without a direct decision.
    assert final["precision"] == 1.0
    assert final["recall"] == 1.0
    assert final["ari"] == 1.0


def test_false_positive_counted_at_pair_level():
    decisions = [
        d("a", "b", "deterministic", True, 1.0),
        d("c", "d", "agent", True, 0.9),  # wrong: c is E1, d is E2
    ]
    metrics = evaluate(SILVER, decisions, GROUND_TRUTH, 0.85, set(), [])
    final = metrics["stages"][-1]
    assert final["false_positive_pairs"] == 1
    assert final["precision"] < 1.0


def test_stage_prefixes_are_cumulative():
    decisions = [
        d("a", "b", "deterministic", True, 1.0),
        d("b", "c", "agent", True, 0.95),
    ]
    metrics = evaluate(SILVER, decisions, GROUND_TRUTH, 0.85, set(), [])
    by_key = {s["prefix"]: s for s in metrics["stages"]}
    assert by_key["stage1_deterministic"]["recall"] < by_key["stage123_agent"]["recall"]


def test_ari_bounds():
    assert adjusted_rand_index(["x", "x", "y"], ["p", "p", "q"]) == 1.0
    assert adjusted_rand_index(["x", "x", "y", "y"], ["p", "q", "p", "q"]) < 0.5

