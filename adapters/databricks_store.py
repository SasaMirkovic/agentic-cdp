"""Databricks (DEV=false) storage adapter: Delta tables via Unity Catalog for
pipeline data, Lakebase Postgres for the review queue.

Mirrors the local adapters' interfaces so `core/` never changes between modes.

NOT locally testable — this module is written to documented APIs and requires
workspace validation (see databricks/README.md, including the free-tier
verification checklist) before it is claimed working.
"""

from __future__ import annotations

import pandas as pd

CATALOG = "agentic_cdp"
SCHEMA = "main"


def _spark():
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.getOrCreate()


def qualified(table: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{table}"


def write_df(table: str, df: pd.DataFrame, replace: bool = True) -> None:
    spark = _spark()
    sdf = spark.createDataFrame(df)
    mode = "overwrite" if replace else "append"
    sdf.write.format("delta").mode(mode).saveAsTable(qualified(table))


def read_df(query: str) -> pd.DataFrame:
    return _spark().sql(query).toPandas()


def apply_governance() -> None:
    """Unity Catalog PII tagging and the analyst/steward masking views.

    Column masks and lineage capture should be verified in the target
    workspace tier before relying on them (see databricks/README.md).
    """
    spark = _spark()
    for column in ("phone", "address", "notes"):
        spark.sql(
            f"ALTER TABLE {qualified('silver_records')} "
            f"ALTER COLUMN {column} SET TAGS ('pii' = 'true')"
        )
    spark.sql(
        f"""
        CREATE OR REPLACE VIEW {qualified('vw_profiles_analyst')} AS
        SELECT customer_id, canonical_name, canonical_domain,
               CASE WHEN canonical_address IS NULL THEN NULL ELSE '•••' END AS canonical_address,
               CASE WHEN canonical_phone   IS NULL THEN NULL ELSE '•••' END AS canonical_phone,
               industry, employees, revenue, industry_code, employee_band
        FROM {qualified('gold_profiles')}
        """
    )


class LakebaseQueue:
    """Review-queue store on Lakebase Postgres. Same surface as adapters.queue_store."""

    def __init__(self, dsn: str):
        import psycopg

        self.con = psycopg.connect(dsn)
        with self.con.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    id          SERIAL PRIMARY KEY,
                    record_a_id TEXT NOT NULL,
                    record_b_id TEXT NOT NULL,
                    payload     JSONB NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    reviewed_at TIMESTAMPTZ,
                    ingested_at TIMESTAMPTZ,
                    UNIQUE (record_a_id, record_b_id)
                )
                """
            )
        self.con.commit()

    def upsert_pending(self, record_a_id: str, record_b_id: str, payload: dict) -> None:
        import json

        with self.con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO review_queue (record_a_id, record_b_id, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (record_a_id, record_b_id) DO NOTHING
                """,
                (record_a_id, record_b_id, json.dumps(payload)),
            )
        self.con.commit()
