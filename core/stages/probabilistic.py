"""Stage 2 — probabilistic matching.

Primary scorer: Splink (Fellegi-Sunter, DuckDB backend) with blocking rules.
A simple weighted-similarity baseline is computed alongside for eval comparison;
the baseline never emits decisions.

Model determinism: u-estimation is seeded and EM runs under blocking. On the
full dataset the trained model is saved to data/artifacts/splink_model.json;
smaller runs (e.g. the integration-test seed) load that pinned model instead of
training — EM on a few hundred records is statistically unstable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from core.config import Settings
from core.stages.common import decision_row, pair_key

logger = logging.getLogger(__name__)

MIN_RECORDS_FOR_TRAINING = 2000


def _splink_input(silver: pd.DataFrame) -> pd.DataFrame:
    df = silver[
        [
            "record_id", "normalized_name", "normalized_domain", "normalized_phone",
            "normalized_address", "industry", "name_first_token",
        ]
    ].copy()
    df = df.rename(columns={"record_id": "unique_id"})
    return df


def _build_linker(df: pd.DataFrame, settings: Settings, model_path: Path):
    import splink.comparison_library as cl
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on

    blocking = [
        block_on("normalized_domain"),
        block_on("name_first_token"),
        block_on("normalized_phone"),
    ]
    if model_path.exists():
        linker = Linker(df, str(model_path), db_api=DuckDBAPI())
        logger.info("Loaded pinned Splink model from %s", model_path)
        return linker, False

    splink_settings = SettingsCreator(
        link_type="dedupe_only",
        blocking_rules_to_generate_predictions=blocking,
        comparisons=[
            cl.JaroWinklerAtThresholds("normalized_name", [0.92, 0.80]),
            cl.JaroWinklerAtThresholds("normalized_address", [0.90, 0.75]),
            cl.ExactMatch("normalized_phone"),
            cl.ExactMatch("industry"),
        ],
        retain_intermediate_calculation_columns=False,
    )
    linker = Linker(df, splink_settings, db_api=DuckDBAPI())
    return linker, True


def _train(linker, settings: Settings) -> None:
    from splink import block_on

    linker.training.estimate_probability_two_random_records_match(
        [
            "l.normalized_domain = r.normalized_domain "
            "and l.name_first_token = r.name_first_token"
        ],
        recall=0.7,
    )
    linker.training.estimate_u_using_random_sampling(
        max_pairs=5_000_000, seed=settings.seed
    )
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("normalized_domain")
    )
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("name_first_token")
    )


def _baseline_score(row: pd.Series) -> float:
    """The original hand-rolled weighted score, kept as an explainable baseline."""
    name = fuzz.token_sort_ratio(row["normalized_name_l"] or "", row["normalized_name_r"] or "") / 100
    addr = (
        fuzz.token_sort_ratio(row["normalized_address_l"], row["normalized_address_r"]) / 100
        if row["normalized_address_l"] and row["normalized_address_r"]
        else 0.0
    )
    phone = 1.0 if (
        row["normalized_phone_l"] and row["normalized_phone_l"] == row["normalized_phone_r"]
    ) else 0.0
    industry = 1.0 if (
        row["industry_l"] and row["industry_l"] == row["industry_r"]
    ) else 0.0
    return round(0.40 * name + 0.25 * addr + 0.20 * phone + 0.15 * industry, 4)


def run_stage2(
    silver: pd.DataFrame,
    settings: Settings,
    already_decided: set[tuple[str, str]],
    demoted: set[tuple[str, str]],
) -> tuple[list[dict], pd.DataFrame, list[tuple[str, str]]]:
    """Returns (decisions, scores_df, agent_band_pairs)."""
    df = _splink_input(silver)
    model_path = settings.artifacts_dir / "splink_model.json"
    linker, needs_training = _build_linker(df, settings, model_path)
    if needs_training:
        if len(df) < MIN_RECORDS_FOR_TRAINING:
            raise RuntimeError(
                f"No pinned Splink model at {model_path} and only {len(df)} records — "
                "EM training below ~2k records is unstable. Run `cdp generate` + "
                "`cdp pipeline` at full scale once to produce the pinned model."
            )
        _train(linker, settings)
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        linker.misc.save_model_to_json(str(model_path), overwrite=True)
        logger.info("Trained and pinned Splink model to %s", model_path)

    predictions = linker.inference.predict(threshold_match_probability=0.01)
    scored = predictions.as_pandas_dataframe()

    scores_rows = []
    decisions: list[dict] = []
    band: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for _, row in scored.iterrows():
        pair = pair_key(row["unique_id_l"], row["unique_id_r"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        prob = float(row["match_probability"])
        is_demoted = pair in demoted
        scores_rows.append(
            {
                "record_a_id": pair[0],
                "record_b_id": pair[1],
                "splink_probability": round(prob, 4),
                "baseline_score": _baseline_score(row),
                "stage1_demoted": is_demoted,
            }
        )
        if pair in already_decided:
            continue
        if is_demoted:
            band.append(pair)  # capped at the agent band regardless of score
        elif prob > settings.stage2_automatch:
            decisions.append(
                decision_row(
                    *pair,
                    stage="probabilistic",
                    match=True,
                    confidence=prob,
                    evidence={"splink_probability": round(prob, 4), "model": "splink-fellegi-sunter"},
                    reason="Splink match probability above the auto-match band.",
                    decided_by="model:splink",
                )
            )
        elif prob >= settings.stage2_agent_band:
            band.append(pair)
        # below the band: non-candidate, score recorded, no decision

    # Demoted pairs Splink never scored (e.g. blocked-out) still go to the agent.
    for pair in demoted:
        if pair not in seen_pairs and pair not in already_decided:
            band.append(pair)
            scores_rows.append(
                {
                    "record_a_id": pair[0],
                    "record_b_id": pair[1],
                    "splink_probability": None,
                    "baseline_score": None,
                    "stage1_demoted": True,
                }
            )

    return decisions, pd.DataFrame(scores_rows), sorted(set(band))
