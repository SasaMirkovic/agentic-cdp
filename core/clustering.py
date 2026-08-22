"""Pairwise decisions → entity clusters.

Connected components over accepted match edges, plus the conflict policy: a
confirmed no-match inside a component triggers a minimum cut between the pair.

Accepted edge:   match=true AND (stage != agent OR confidence >= threshold).
Confirmed no-match: human/oracle_review no-match, or agent no-match with
                 confidence >= threshold. Stage-2 non-candidates never trigger.
Capacities are rank-dominant (human/oracle/deterministic uncuttable = inf;
deterministic > agent > probabilistic among cuttable ranks is expressed as
agent 2000+conf, probabilistic 1000+conf). NetworkXUnbounded — an uncuttable
path connects a confirmed no-match pair — means a *rule* is wrong, not an
edge: the whole cluster is flagged for human review instead of cut.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field

import networkx as nx

logger = logging.getLogger(__name__)

UNCUTTABLE_STAGES = {"human", "oracle_review", "deterministic"}
_CAPACITY_BASE = {"agent": 2000.0, "probabilistic": 1000.0}


@dataclass
class ClusterResult:
    clusters: dict[str, list[str]] = field(default_factory=dict)  # cluster_id -> record_ids
    flagged: set[str] = field(default_factory=set)                # cluster_ids needing human review
    cut_edges: list[dict] = field(default_factory=list)


def _capacity(stage: str, confidence: float) -> float:
    if stage in UNCUTTABLE_STAGES:
        return math.inf
    return _CAPACITY_BASE[stage] + confidence


def cluster_id_for(members: list[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(members)).encode()).hexdigest()[:16]
    return f"CL-{digest}"


def build_clusters(
    all_records: list[str],
    decisions: list[dict],
    agent_confidence_threshold: float,
    pending_pairs: set[tuple[str, str]],
) -> ClusterResult:
    graph = nx.Graph()
    graph.add_nodes_from(all_records)

    no_match_triggers: list[tuple[str, str, str]] = []  # (a, b, match_id)

    for d in decisions:
        pair = (d["record_a_id"], d["record_b_id"])
        if pair in pending_pairs:
            continue  # queued and undecided: no edge, no trigger
        if d["match"]:
            if d["stage"] == "agent" and d["confidence"] < agent_confidence_threshold:
                continue  # audit row only; routed to human
            graph.add_edge(
                *pair,
                capacity=_capacity(d["stage"], d["confidence"]),
                stage=d["stage"],
                match_id=d["match_id"],
            )
        else:
            confirmed = d["stage"] in ("human", "oracle_review") or (
                d["stage"] == "agent" and d["confidence"] >= agent_confidence_threshold
            )
            if confirmed:
                no_match_triggers.append((*pair, d["match_id"]))

    result = ClusterResult()
    flagged_records: set[str] = set()

    # Deterministic processing order.
    for a, b, nomatch_id in sorted(no_match_triggers):
        if not (graph.has_node(a) and graph.has_node(b)):
            continue
        if a in flagged_records or b in flagged_records:
            continue
        component = nx.node_connected_component(graph, a)
        if b not in component:
            continue  # already separate
        sub = graph.subgraph(component).copy()
        try:
            cut_value, (part_a, part_b) = nx.minimum_cut(sub, a, b, capacity="capacity")
        except nx.NetworkXUnbounded:
            # Only uncuttable evidence connects a confirmed no-match pair: a
            # deterministic rule or a human decision is wrong. Escalate.
            flagged_records.update(component)
            logger.info(
                "Cluster containing %s/%s flagged for human review: confirmed "
                "no-match connected by uncuttable edges.", a, b,
            )
            continue
        removed = [
            (u, v)
            for u, v in sub.edges
            if (u in part_a) != (v in part_a)
        ]
        for u, v in sorted(removed):
            edge = graph.edges[u, v]
            result.cut_edges.append(
                {
                    "record_a_id": min(u, v),
                    "record_b_id": max(u, v),
                    "cut_by": nomatch_id,
                    "stage": edge["stage"],
                    "match_id": edge["match_id"],
                }
            )
            graph.remove_edge(u, v)

    for component in nx.connected_components(graph):
        members = sorted(component)
        cid = cluster_id_for(members)
        result.clusters[cid] = members
        if any(m in flagged_records for m in members):
            result.flagged.add(cid)

    return result
