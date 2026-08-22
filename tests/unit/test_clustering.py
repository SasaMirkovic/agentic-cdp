from core.clustering import build_clusters
from core.stages.common import decision_row

THRESHOLD = 0.85


def d(a, b, stage, match, conf, routed=False):
    return decision_row(a, b, stage=stage, match=match, confidence=conf,
                        evidence={}, reason="t", decided_by="t", routed_to_human=routed)


def test_transitive_component_forms_one_cluster():
    records = ["r1", "r2", "r3", "r4"]
    decisions = [
        d("r1", "r2", "deterministic", True, 1.0),
        d("r2", "r3", "probabilistic", True, 0.95),
    ]
    result = build_clusters(records, decisions, THRESHOLD, set())
    clusters = {tuple(m) for m in result.clusters.values()}
    assert ("r1", "r2", "r3") in clusters
    assert ("r4",) in clusters


def test_low_confidence_agent_match_is_not_an_edge():
    decisions = [d("r1", "r2", "agent", True, 0.75, routed=True)]
    result = build_clusters(["r1", "r2"], decisions, THRESHOLD, set())
    assert {tuple(m) for m in result.clusters.values()} == {("r1",), ("r2",)}


def test_pending_pair_is_not_an_edge():
    decisions = [d("r1", "r2", "agent", True, 0.95)]
    result = build_clusters(["r1", "r2"], decisions, THRESHOLD, {("r1", "r2")})
    assert {tuple(m) for m in result.clusters.values()} == {("r1",), ("r2",)}


def test_confirmed_nomatch_cuts_weakest_edge():
    # r1-r2 strong agent edge, r2-r3 weaker probabilistic edge, human says r1 != r3.
    decisions = [
        d("r1", "r2", "agent", True, 0.99),
        d("r2", "r3", "probabilistic", True, 0.91),
        d("r1", "r3", "human", False, 1.0),
    ]
    result = build_clusters(["r1", "r2", "r3"], decisions, THRESHOLD, set())
    clusters = {tuple(m) for m in result.clusters.values()}
    # The probabilistic edge (capacity 1000.91) is cheaper than the agent edge (2000.99).
    assert ("r1", "r2") in clusters and ("r3",) in clusters
    assert len(result.cut_edges) == 1
    assert result.cut_edges[0]["stage"] == "probabilistic"
    assert not result.flagged


def test_uncuttable_path_escalates_whole_cluster():
    # Deterministic chain connects the confirmed-no-match pair: a rule is wrong.
    decisions = [
        d("r1", "r2", "deterministic", True, 1.0),
        d("r2", "r3", "deterministic", True, 1.0),
        d("r1", "r3", "human", False, 1.0),
    ]
    result = build_clusters(["r1", "r2", "r3"], decisions, THRESHOLD, set())
    assert len(result.flagged) == 1
    assert not result.cut_edges
    flagged_members = result.clusters[next(iter(result.flagged))]
    assert flagged_members == ["r1", "r2", "r3"]


def test_agent_nomatch_below_threshold_never_triggers():
    decisions = [
        d("r1", "r2", "probabilistic", True, 0.95),
        d("r1", "r2b", "agent", False, 0.75, routed=True),
    ]
    result = build_clusters(["r1", "r2", "r2b"], decisions, THRESHOLD, set())
    assert not result.cut_edges and not result.flagged


def test_deterministic_output_across_runs():
    decisions = [
        d("r1", "r2", "agent", True, 0.9),
        d("r2", "r3", "agent", True, 0.9),
        d("r1", "r3", "oracle_review", False, 1.0),
    ]
    results = [build_clusters(["r1", "r2", "r3"], decisions, THRESHOLD, set()) for _ in range(3)]
    shapes = [sorted(tuple(m) for m in r.clusters.values()) for r in results]
    assert shapes[0] == shapes[1] == shapes[2]
