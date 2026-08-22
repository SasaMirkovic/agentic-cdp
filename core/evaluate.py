"""Eval harness. Fixed definitions so numbers can't be silently inflated:

- Universe: all true-match pairs = within-entity record pairs from ground truth.
  Recall's denominator is this universe, never "pairs the pipeline saw".
- Per-stage-prefix metrics: clusters are rebuilt from each prefix's accepted
  edges — the min-cut conflict policy runs with whatever triggers exist in the
  prefix — and scored on implied pairs (transitive closure).
- % routed to human is the pre-cap band share (the honest number).
- The full-pipeline row is oracle-assisted (the bulk reviewer reads ground
  truth) and is labeled as such wherever it is printed.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from core.clustering import build_clusters

PREFIXES: list[tuple[str, list[str], str]] = [
    ("stage1_deterministic", ["deterministic"], "Deterministic rules only"),
    ("stage12_probabilistic", ["deterministic", "probabilistic"], "+ Splink auto-matches"),
    ("stage123_agent", ["deterministic", "probabilistic", "agent"], "+ agent decisions"),
    (
        "stage1234_full",
        ["deterministic", "probabilistic", "agent", "oracle_review", "human"],
        "+ review (oracle-assisted, simulated review team)",
    ),
]


def true_pairs(ground_truth: pd.DataFrame) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for _, group in ground_truth.groupby("entity_id"):
        ids = sorted(group["record_id"])
        pairs.update(combinations(ids, 2))
    return pairs


def implied_pairs(clusters: dict[str, list[str]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for members in clusters.values():
        pairs.update(combinations(sorted(members), 2))
    return pairs


def adjusted_rand_index(labels_true: list[str], labels_pred: list[str]) -> float:
    """ARI from the pair-counting contingency formulation (no sklearn dependency)."""
    from collections import Counter

    n = len(labels_true)
    if n < 2:
        return 1.0
    contingency: Counter = Counter(zip(labels_true, labels_pred))
    a = Counter(labels_true)
    b = Counter(labels_pred)

    def comb2(x: int) -> float:
        return x * (x - 1) / 2

    sum_comb_c = sum(comb2(v) for v in contingency.values())
    sum_comb_a = sum(comb2(v) for v in a.values())
    sum_comb_b = sum(comb2(v) for v in b.values())
    total = comb2(n)
    expected = sum_comb_a * sum_comb_b / total if total else 0.0
    max_index = (sum_comb_a + sum_comb_b) / 2
    if max_index == expected:
        return 1.0
    return (sum_comb_c - expected) / (max_index - expected)


def evaluate(
    silver: pd.DataFrame,
    decisions: list[dict],
    ground_truth: pd.DataFrame,
    agent_confidence_threshold: float,
    pending_pairs: set[tuple[str, str]],
    injected_errors: list[list[str]],
) -> dict:
    all_records = sorted(silver["record_id"])
    truth_pairs = true_pairs(ground_truth)
    truth_labels = dict(zip(ground_truth["record_id"], ground_truth["entity_id"]))

    stage_rows = []
    flagged_final = 0
    for key, stages, label in PREFIXES:
        subset = [d for d in decisions if d["stage"] in stages]
        result = build_clusters(all_records, subset, agent_confidence_threshold, pending_pairs)
        flagged_final = len(result.flagged)
        implied = implied_pairs(result.clusters)
        tp = len(implied & truth_pairs)
        fp = len(implied - truth_pairs)
        fn = len(truth_pairs - implied)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        record_to_cluster = {
            m: cid for cid, members in result.clusters.items() for m in members
        }
        labels_true = [truth_labels.get(r, r) for r in all_records]
        labels_pred = [record_to_cluster.get(r, r) for r in all_records]
        stage_rows.append(
            {
                "prefix": key,
                "label": label,
                "true_positive_pairs": tp,
                "false_positive_pairs": fp,
                "false_negative_pairs": fn,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "ari": round(adjusted_rand_index(labels_true, labels_pred), 4),
                "n_clusters": len(result.clusters),
            }
        )

    # Every oracle_review/human row originated in the agent band (they only
    # adjudicate routed agent pairs), so band size and routing are derived from
    # the persisted decision log — stable across re-runs even though overflow
    # review overwrites the agent row for its pair.
    agent_rows = [d for d in decisions if d["stage"] == "agent"]
    review_rows = [d for d in decisions if d["stage"] in ("oracle_review", "human")]
    band_size = len(agent_rows) + len(review_rows)
    routed = len(review_rows) + sum(1 for d in agent_rows if d["routed_to_human"])
    return {
        "definitions": {
            "universe": "all within-entity record pairs from ground truth",
            "pair_metrics": "implied pairs after transitive closure per stage prefix",
            "note": "final row is oracle-assisted: the bulk reviewer reads ground truth "
            "(simulated review team, disclosed)",
        },
        "true_match_pairs": len(truth_pairs),
        "stages": stage_rows,
        "agent_band_size": band_size,
        "agent_decisions": len(agent_rows),
        "routed_to_human": routed,
        "routed_to_human_pct_of_band": round(routed / band_size, 4) if band_size else 0.0,
        "injected_oracle_errors": len(injected_errors),
        "pending_pairs": len(pending_pairs),
        "flagged_clusters": flagged_final,
    }


def write_metrics(metrics: dict, json_path: Path, md_path: Path | None) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(metrics, indent=2))
    if md_path is None:
        return
    lines = [
        "# Pipeline metrics",
        "",
        "_Snapshot state: post-oracle, pre-human unless the queue was drained._",
        f"_True-match pair universe: {metrics['true_match_pairs']} pairs "
        "(all within-entity pairs from ground truth)._",
        "",
        "| Stage prefix | Precision | Recall | F1 | FP pairs | ARI | Clusters |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in metrics["stages"]:
        lines.append(
            f"| {row['label']} | {row['precision']} | {row['recall']} | {row['f1']} "
            f"| **{row['false_positive_pairs']}** | {row['ari']} | {row['n_clusters']} |"
        )
    lines += [
        "",
        f"- Agent band size (pre-cap, the honest routing denominator): {metrics['agent_band_size']}",
        f"- Routed to human review: {metrics['routed_to_human']} "
        f"({metrics['routed_to_human_pct_of_band']:.1%} of band)",
        f"- Deliberately injected simulated-agent errors: {metrics['injected_oracle_errors']} "
        "(see README: error injection keeps the false-positive story observable)",
        f"- Pairs still pending human review: {metrics['pending_pairs']}",
        "",
        "_The final stage row is oracle-assisted: overflow review is adjudicated from "
        "generator ground truth by a disclosed simulated review team._",
    ]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines))
