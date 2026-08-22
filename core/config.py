"""Central configuration. All thresholds and knobs live here, loaded from .env / environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

# Trust order across stages. Governs decision-log overwrites and cluster edge
# capacities. Higher = more trusted.
STAGE_RANK = {
    "probabilistic": 1,
    "agent": 2,
    "deterministic": 3,
    "oracle_review": 4,
    "human": 5,
}

# Field survivorship: which source wins when values conflict.
SOURCE_PRIORITY = ["salesforce", "xactly", "marketo", "sixsense"]

# Deterministic per-source load timestamps (no wall clock anywhere in the
# pipeline: anchors and re-runs must be byte-stable).
SOURCE_LOADED_AT = {
    "salesforce": "2026-07-01T00:00:00",
    "xactly": "2026-07-02T00:00:00",
    "marketo": "2026-07-03T00:00:00",
    "sixsense": "2026-07-04T00:00:00",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    dev: bool = True
    mock_llm: bool = True
    anthropic_api_key: str = ""
    cdp_data_dir: Path = REPO_ROOT / "data" / "local"
    # Overridable so tests can use an isolated artifacts dir (committed default below).
    cdp_artifacts_dir: Path | None = None

    # Generator
    seed: int = 42
    n_companies: int = 3000

    # Stage 2 bands (on Splink match_probability)
    stage2_automatch: float = 0.90
    stage2_agent_band: float = 0.70

    # Stage 1 per-token fuzzy agreement threshold
    stage1_token_ratio: int = 85

    # Stage 3
    agent_confidence_threshold: float = 0.85
    agent_model: str = "claude-haiku-4-5-20251001"
    oracle_error_rate: float = 0.02

    # Stage 4
    human_queue_cap: int = 50

    @property
    def artifacts_dir(self) -> Path:
        return self.cdp_artifacts_dir or (REPO_ROOT / "data" / "artifacts")

    @property
    def duckdb_path(self) -> Path:
        return self.cdp_data_dir / "cdp.duckdb"

    @property
    def queue_db_path(self) -> Path:
        return self.cdp_data_dir / "queue.db"

    @property
    def metrics_path(self) -> Path:
        return self.cdp_data_dir / "metrics.json"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.cdp_data_dir.mkdir(parents=True, exist_ok=True)
    return s
