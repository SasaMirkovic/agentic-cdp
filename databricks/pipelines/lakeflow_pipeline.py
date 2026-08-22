"""Lakeflow Declarative Pipeline for bronze → silver.

Thin wrappers over `core/` transforms: the declarative layer owns orchestration
and table management; the matching logic stays engine-agnostic in core/.

Deployment notes and stage-2 placement (Splink runs single-node on its DuckDB
engine in a notebook task, writing scored edges to Delta — serverless has no
JAR support for Splink's Spark UDFs): see databricks/README.md.

NOT locally testable; requires a workspace (see the verification checklist).
"""

from __future__ import annotations

import dlt
from pyspark.sql import functions as F
from pyspark.sql.functions import pandas_udf

from core import normalize

SOURCES = ["salesforce", "xactly", "marketo", "sixsense"]

VOLUME_PATH = "/Volumes/agentic_cdp/main/landing"


def _norm_udf(fn):
    @pandas_udf("string")
    def udf(col):
        return col.map(fn)

    return udf


normalize_name = _norm_udf(normalize.normalize_name)
normalize_domain = _norm_udf(normalize.normalize_domain)
normalize_phone = _norm_udf(normalize.normalize_phone)
normalize_address = _norm_udf(normalize.normalize_address)


for source in SOURCES:

    @dlt.table(name=f"bronze_{source}", comment=f"Raw {source} records, as received.")
    def bronze(source=source):
        return (
            spark.readStream.format("cloudFiles")  # noqa: F821 (Lakeflow runtime global)
            .option("cloudFiles.format", "csv")
            .option("header", "true")
            .load(f"{VOLUME_PATH}/{source}/")
            .withColumn("source_system", F.lit(source))
            .withColumn("loaded_at", F.current_timestamp())
        )


@dlt.table(
    name="silver_records",
    comment="Normalized, PII-tagged records from all sources.",
)
@dlt.expect_or_drop("has_record_id", "source_record_id IS NOT NULL")
def silver_records():
    frames = [dlt.read(f"bronze_{s}") for s in SOURCES]
    unioned = frames[0]
    for frame in frames[1:]:
        unioned = unioned.unionByName(frame, allowMissingColumns=True)
    return (
        unioned.withColumn(
            "record_id", F.concat_ws(":", F.col("source_system"), F.col("source_record_id"))
        )
        .withColumn("normalized_name", normalize_name(F.col("company_name")))
        .withColumn("normalized_domain", normalize_domain(F.col("domain")))
        .withColumn("normalized_phone", normalize_phone(F.col("phone")))
        .withColumn("normalized_address", normalize_address(F.col("address")))
        .withColumn("name_first_token", F.split(F.col("normalized_name"), " ").getItem(0))
    )
