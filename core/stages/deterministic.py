"""Stage 1 — deterministic matching.

Exact normalized-domain match, guarded: freemail/shared domains never match, and
a domain-match pair auto-matches only when the two names' token sets agree
one-to-one (see core.normalize.tokens_match_one_to_one). Pairs failing the name
guard are demoted: they are force-routed to the agent stage and can never be
auto-matched by an automated stage — this is what keeps shared-domain
subsidiaries ("Acme" vs "Acme Labs" on acme.com) out of silent merges.
"""

from __future__ import annotations

from itertools import combinations

import pandas as pd

from core.config import Settings
from core.normalize import FREEMAIL_DOMAINS, tokens_match_one_to_one
from core.stages.common import decision_row, pair_key


def run_stage1(
    silver: pd.DataFrame, settings: Settings, already_decided: set[tuple[str, str]]
) -> tuple[list[dict], set[tuple[str, str]]]:
    """Returns (decisions, demoted_pairs)."""
    decisions: list[dict] = []
    demoted: set[tuple[str, str]] = set()

    candidates = silver[
        silver["normalized_domain"].notna()
        & ~silver["normalized_domain"].isin(FREEMAIL_DOMAINS)
    ]
    for domain, group in candidates.groupby("normalized_domain"):
        if len(group) < 2:
            continue
        rows = group.sort_values("record_id").to_dict("records")
        for r1, r2 in combinations(rows, 2):
            pair = pair_key(r1["record_id"], r2["record_id"])
            # Demotion is recomputed on every run (it feeds stage2_scores and the
            # agent-band cap); only the *decision* is skipped for decided pairs.
            if tokens_match_one_to_one(
                r1["company_name"], r2["company_name"], settings.stage1_token_ratio
            ):
                if pair in already_decided:
                    continue
                decisions.append(
                    decision_row(
                        *pair,
                        stage="deterministic",
                        match=True,
                        confidence=1.0,
                        evidence={
                            "rule": "exact_domain_name_agree",
                            "domain": domain,
                            "name_a": r1["company_name"],
                            "name_b": r2["company_name"],
                        },
                        reason="Exact normalized-domain match with one-to-one name token agreement.",
                        decided_by="rule:exact_domain_name_agree",
                    )
                )
            else:
                # Same domain but distinguishing name tokens: likely subsidiary /
                # sister entity. Never auto-match; the agent stage decides.
                demoted.add(pair)
    return decisions, demoted
