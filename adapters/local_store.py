"""DuckDB storage adapter for the local (DEV=true) mode.

Single-writer discipline: the pipeline is the only process that opens this file.
The API server must never import this module (it reads SQLite + metrics.json).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SOURCES = ["salesforce", "xactly", "marketo", "sixsense"]

_BRONZE_COLUMNS = """
    source_system     VARCHAR,
    source_record_id  VARCHAR,
    company_name      VARCHAR,
    domain            VARCHAR,
    address           VARCHAR,
    phone             VARCHAR,
    industry          VARCHAR,
    employees         INTEGER,
    revenue           DOUBLE,
    notes             VARCHAR,
    loaded_at         TIMESTAMP,
    raw_payload       VARCHAR
"""

DDL = [
    *[f"CREATE TABLE IF NOT EXISTS bronze_{s} ({_BRONZE_COLUMNS})" for s in SOURCES],
    """
    CREATE TABLE IF NOT EXISTS silver_records (
        record_id          VARCHAR PRIMARY KEY,
        source_system      VARCHAR,
        source_record_id   VARCHAR,
        company_name       VARCHAR,
        domain             VARCHAR,
        address            VARCHAR,
        phone              VARCHAR,
        industry           VARCHAR,
        employees          INTEGER,
        revenue            DOUBLE,
        notes              VARCHAR,
        loaded_at          TIMESTAMP,
        normalized_name    VARCHAR,
        normalized_domain  VARCHAR,
        normalized_phone   VARCHAR,
        normalized_address VARCHAR,
        name_first_token   VARCHAR,
        pii_flags          VARCHAR[]
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS match_decisions (
        match_id        VARCHAR,
        record_a_id     VARCHAR,
        record_b_id     VARCHAR,
        stage           VARCHAR,
        match           BOOLEAN,
        confidence      DOUBLE,
        evidence        VARCHAR,
        reason          VARCHAR,
        decided_by      VARCHAR,
        decided_at      TIMESTAMP,
        cost_usd        DOUBLE,
        routed_to_human BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (record_a_id, record_b_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stage2_scores (
        record_a_id        VARCHAR,
        record_b_id        VARCHAR,
        splink_probability DOUBLE,
        baseline_score     DOUBLE,
        stage1_demoted     BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (record_a_id, record_b_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_clusters (
        record_id          VARCHAR PRIMARY KEY,
        cluster_id         VARCHAR,
        needs_human_review BOOLEAN DEFAULT FALSE,
        run_id             VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gold_profiles (
        customer_id        VARCHAR PRIMARY KEY,
        canonical_name     VARCHAR,
        canonical_domain   VARCHAR,
        canonical_address  VARCHAR,
        canonical_phone    VARCHAR,
        industry           VARCHAR,
        employees          INTEGER,
        revenue            DOUBLE,
        industry_code      VARCHAR,
        employee_band      VARCHAR,
        tech_tags          VARCHAR,
        source_record_ids  VARCHAR[],
        survivorship_notes VARCHAR,
        needs_human_review BOOLEAN DEFAULT FALSE,
        last_updated       TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customer_id_crosswalk (
        customer_id               VARCHAR,
        anchor_record_id          VARCHAR,
        member_set_hash           VARCHAR,
        status                    VARCHAR,
        superseded_by_customer_id VARCHAR,
        run_id                    VARCHAR
    )
    """,
]


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    for stmt in DDL:
        con.execute(stmt)
    return con


def write_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, replace: bool = True) -> None:
    con.register("_incoming", df)
    if replace:
        con.execute(f"DELETE FROM {table}")
    cols = ", ".join(df.columns)
    con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
    con.unregister("_incoming")


def read_df(con: duckdb.DuckDBPyConnection, query: str) -> pd.DataFrame:
    return con.execute(query).df()
