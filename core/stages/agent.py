"""Stage 3 — agent review of the ambiguous band.

Two implementations behind one contract (core.schemas.AgentVerdict):

- Live (`MOCK_LLM=false`): Anthropic structured-output call per pair. Model
  self-reported confidence is a routing signal, not a calibrated probability;
  the eval harness validates the thresholds.
- Mock (`MOCK_LLM=true`): a seeded oracle that reads generator ground truth.
  Decisions are labeled `decided_by: "mock-oracle"` and costs are simulated —
  the labels are load-bearing and must never be weakened (CLAUDE.md rule 4).

Oracle determinism: every quantity derives from sha256(seed, sorted pair ids) —
a pair's decision never depends on what other pairs are in the band. Injected
errors (configured rate) are false *matches* with confidence above the human
threshold, so they surface in the false-positive metric rather than the queue.
The injected list is written as a sidecar for exact eval assertions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from core.config import Settings
from core.schemas import AgentVerdict
from core.stages.common import decision_row, pair_key

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an identity-resolution reviewer for a customer data platform.
Given two company records from different source systems, decide whether they refer to the
same real-world company. Weigh domains, names, addresses, phones, industries, and any CRM
notes. Notes may contain decisive evidence (subsidiaries, franchises, rebrands).
If evidence is thin or conflicting, say so and LOWER your confidence rather than guess.
Respond with match (bool), confidence (0-1), evidence (list of short factor strings), and
reason (one or two sentences)."""


def _hash_floats(seed: int, pair: tuple[str, str], n: int = 4) -> list[float]:
    digest = hashlib.sha256(f"{seed}:{pair[0]}:{pair[1]}".encode()).digest()
    return [int.from_bytes(digest[i * 8:(i + 1) * 8], "big") / 2**64 for i in range(n)]


def _field_evidence(a: dict, b: dict) -> tuple[list[str], dict]:
    evidence = []
    name_sim = fuzz.token_sort_ratio(a.get("normalized_name") or "", b.get("normalized_name") or "")
    if a.get("normalized_domain") and a.get("normalized_domain") == b.get("normalized_domain"):
        evidence.append("domain_match")
    if name_sim >= 90:
        evidence.append("name_similarity_high")
    elif name_sim >= 70:
        evidence.append("name_similarity_moderate")
    else:
        evidence.append("name_similarity_low")
    if a.get("normalized_phone") and a.get("normalized_phone") == b.get("normalized_phone"):
        evidence.append("phone_match")
    if a.get("industry") and a.get("industry") == b.get("industry"):
        evidence.append("industry_match")
    if a.get("notes") or b.get("notes"):
        evidence.append("crm_notes_present")
    return evidence, {"name_similarity": name_sim}


class OracleAgent:
    """Ground-truth-reading simulator of the live agent. Labeled, seeded, honest."""

    def __init__(self, settings: Settings, ground_truth: pd.DataFrame):
        self.settings = settings
        self.truth = dict(zip(ground_truth["record_id"], ground_truth["entity_id"]))
        self.injected_errors: list[list[str]] = []

    def decide(self, a: dict, b: dict) -> AgentVerdict:
        pair = pair_key(a["record_id"], b["record_id"])
        h_conf, h_err, h_queue, _ = _hash_floats(self.settings.seed, pair)
        same_entity = self.truth.get(pair[0]) == self.truth.get(pair[1])
        evidence, _ = _field_evidence(a, b)
        threshold = self.settings.agent_confidence_threshold

        if same_entity:
            # ~30% of true matches land below the routing threshold -> human queue.
            if h_queue < 0.30:
                conf = 0.70 + 0.14 * h_conf
            else:
                conf = threshold + 0.01 + (0.99 - threshold - 0.01) * h_conf
            return AgentVerdict(
                match=True, confidence=round(conf, 4), evidence=evidence,
                reason="Simulated review: records identified as the same company.",
            )

        if h_err < self.settings.oracle_error_rate:
            # Injected error: a false match, confidently wrong on purpose so it
            # bypasses the queue and shows up in the false-positive metric.
            self.injected_errors.append(list(pair))
            conf = threshold + 0.01 + (0.99 - threshold - 0.01) * h_conf
            return AgentVerdict(
                match=True, confidence=round(conf, 4), evidence=evidence,
                reason="Simulated review: records assessed as the same company (injected error).",
            )

        if h_queue < 0.25:
            conf = 0.70 + 0.14 * h_conf
        else:
            conf = threshold + 0.01 + (0.99 - threshold - 0.01) * h_conf
        return AgentVerdict(
            match=False, confidence=round(conf, 4), evidence=evidence,
            reason="Simulated review: records identified as distinct companies.",
        )

    def simulated_cost(self, pair: tuple[str, str]) -> float:
        h_conf, *_ = _hash_floats(self.settings.seed, pair)
        return round(0.0003 + 0.0002 * h_conf, 6)


class LiveAgent:
    """Anthropic structured-output client. Requires ANTHROPIC_API_KEY."""

    def __init__(self, settings: Settings):
        import anthropic

        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def decide(self, a: dict, b: dict) -> AgentVerdict:
        payload = {
            "record_a": {k: a.get(k) for k in (
                "record_id", "source_system", "company_name", "domain", "address",
                "phone", "industry", "employees", "revenue", "notes")},
            "record_b": {k: b.get(k) for k in (
                "record_id", "source_system", "company_name", "domain", "address",
                "phone", "industry", "employees", "revenue", "notes")},
        }
        message = self.client.messages.parse(
            model=self.settings.agent_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
            output_format=AgentVerdict,
        )
        return message.parsed_output


def run_stage3(
    silver: pd.DataFrame,
    band: list[tuple[str, str]],
    settings: Settings,
    ground_truth: pd.DataFrame | None,
) -> tuple[list[dict], list[list[str]]]:
    """Returns (decisions, injected_error_pairs)."""
    records = silver.set_index("record_id").to_dict("index")
    for rid, row in records.items():
        row["record_id"] = rid

    if settings.mock_llm:
        if ground_truth is None:
            raise RuntimeError("Mock agent requires the ground-truth artifact.")
        agent: OracleAgent | LiveAgent = OracleAgent(settings, ground_truth)
        decided_by = "mock-oracle"
    else:
        if not settings.anthropic_api_key:
            raise RuntimeError("MOCK_LLM=false requires ANTHROPIC_API_KEY.")
        agent = LiveAgent(settings)
        decided_by = f"model:{settings.agent_model}"

    decisions = []
    for pair in band:
        a, b = records.get(pair[0]), records.get(pair[1])
        if a is None or b is None:
            continue
        verdict = agent.decide(a, b)
        routed = verdict.confidence < settings.agent_confidence_threshold
        if isinstance(agent, OracleAgent):
            cost = agent.simulated_cost(pair)
            evidence: dict = {"factors": verdict.evidence, "cost_simulated": True}
        else:
            cost = None
            evidence = {"factors": verdict.evidence}
        decisions.append(
            decision_row(
                *pair,
                stage="agent",
                match=verdict.match,
                confidence=verdict.confidence,
                evidence=evidence,
                reason=verdict.reason,
                decided_by=decided_by,
                cost_usd=cost,
                routed_to_human=routed,
            )
        )
    injected = agent.injected_errors if isinstance(agent, OracleAgent) else []
    if injected:
        logger.info("Oracle injected %d deliberate false matches (sidecar written).", len(injected))
    return decisions, injected


def write_error_sidecar(path: Path, injected: list[list[str]]) -> None:
    path.write_text(json.dumps({"injected_false_matches": injected}, indent=2))
