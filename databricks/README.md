# Databricks deployment mode (DEV=false)

The local and lakehouse modes are the same system with swapped IO:

| Concern | Local (DEV=true) | Databricks (DEV=false) |
|---|---|---|
| Pipeline tables | DuckDB file | Delta tables in Unity Catalog (`agentic_cdp.main.*`) |
| Orchestration | `cdp pipeline` CLI | Lakeflow Declarative Pipeline (`pipelines/lakeflow_pipeline.py`) + notebook tasks |
| Stage 2 (Splink) | DuckDB engine in-process | **Splink's DuckDB engine, single-node, in a notebook task** writing scored edges to Delta — serverless compute does not support the JAR Splink's Spark backend needs; at larger scale, move to classic compute or a Spark-native approach |
| Review queue | SQLite (WAL) | Lakebase Postgres (`adapters/databricks_store.LakebaseQueue`) |
| Review app | FastAPI + built React served locally | The same app deployed as a **Databricks App** (React + FastAPI is a documented Apps pattern) |
| Governance demo | API role param + DuckDB views | Unity Catalog PII tags, column masks, lineage; analyst/steward views |
| Self-serve on gold | — | AI/BI dashboard + a Genie space over `gold_profiles` (post-deploy step) |

`core/` (generator, normalization, matching stages, clustering, survivorship, eval) is identical in both modes — that separation is the point.

## Status

**Written to documented APIs, not yet workspace-validated.** Everything in this directory and `adapters/databricks_store.py` requires a workspace run before it is claimed working.

## Day-1 verification checklist (Free Edition)

Run these checks in the target workspace *before* building on the corresponding feature:

1. **Egress**: outbound calls (Anthropic API) are restricted until LinkedIn identity verification — verify a test call from a notebook.
2. **Unity Catalog on the free tier**: column masks and lineage capture — create a masked column and confirm lineage renders for a toy table.
3. **Second principal/group**: the analyst/steward masking demo needs two access levels — confirm group creation works.
4. **Splink single-node**: `pip install splink` in a serverless notebook, load `data/artifacts/splink_model.json`, score a sample, write Delta.
5. **App limits**: max 3 Databricks Apps; apps auto-stop 24h after start (demos should be recorded, not left running); one active pipeline per type; one Lakebase project.

## Deploy sketch

1. Create catalog/schema + a Volume; upload generator CSV exports to `/Volumes/agentic_cdp/main/landing/<source>/`.
2. Create a Lakeflow pipeline from `pipelines/lakeflow_pipeline.py` (bronze/silver).
3. Notebook task: stages 1–4 + clustering + gold via `core/` against Delta (through `adapters/databricks_store`), Splink per the checklist.
4. `apply_governance()` for PII tags + masked views; screenshot lineage for the writeup.
5. Provision Lakebase; point the FastAPI app at `LakebaseQueue`; deploy `app/` as a Databricks App.
6. AI/BI dashboard + Genie space over `gold_profiles`.
