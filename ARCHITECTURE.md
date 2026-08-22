# ARCHITECTURE.md — current state

_Living document (rule 1 in CLAUDE.md): updated with every major change. Status: **as built and hardened** (M0–M9 complete). Design rationale and decisions live in PLAN.md._

## System at a glance

```
                       +------------------------------------------------+
  data/artifacts/      |                cdp pipeline (CLI)              |
  ground_truth --------+--> eval                                        |
  enrichment_ref ------+--> gold                                        |
                       |                                                |
  generator --> bronze_* --> silver_records --> Stage 1 deterministic   |
   (seeded)        (DuckDB)      (normalize,       | (domain + token    |
                                  PII tags)        |  containment guard)|
                                                   v                    |
                                          Stage 2 Splink (DuckDB)       |
                                            >0.90 match  0.70-0.90 v    |
                                          Stage 3 agent (live | mock)   |
                                            conf <0.85 ------------> review_queue (SQLite)
                                                   |                    |        ^ |
                                          match_decisions (audit log)   |   FastAPI | approve/
                                                   v                    |        | v reject
                                          clustering (components +      |   React UI (Vite)
                                            min-cut conflict policy)    |
                                                   v                    |
                                          gold_profiles + crosswalk     |
                                                   v                    |
                                          evaluate -> metrics.json      |
                       +------------------------------------------------+
```

- **Stores**: DuckDB file (`data/local/cdp.duckdb`) for all pipeline tables; SQLite WAL (`data/local/queue.db`) for review-queue OLTP. The FastAPI server reads SQLite + `metrics.json` only — never the DuckDB file — so the pipeline can re-run while the server is up.
- **Runtime modes**: `DEV=true` local (implemented); `DEV=false` Databricks (adapters + Lakeflow wrappers in `databricks/`, workspace-validated later). `MOCK_LLM=true` replaces the Anthropic call with a seeded ground-truth oracle (labeled in data).

## Modules

| Path | Responsibility | Status |
|---|---|---|
| `core/config.py` | pydantic-settings config, thresholds, source priorities | built |
| `core/generator.py` | seeded synthetic sources + hard negatives + notes + ground truth | built |
| `core/normalize.py` | canonicalization + PII tagging + token-containment check | built |
| `core/stages/deterministic.py` | Stage 1: domain match + name guard; demotions | built |
| `core/stages/probabilistic.py` | Stage 2: Splink (DuckDB backend, pinned model) + baseline scores | built |
| `core/stages/agent.py` | Stage 3: Anthropic structured-output client + seeded oracle mock | built |
| `core/stages/review.py` | Stage 4: 50-cap queue routing, oracle bulk reviewer, human ingestion | built |
| `core/clustering.py` | connected components + min-cut conflict policy (NetworkXUnbounded => escalate) | built |
| `core/survivorship.py` | gold profiles, anchor-based customer_id + crosswalk, enrichment join | built |
| `core/evaluate.py` | per-stage-prefix precision/recall/F1 + ARI vs ground truth | built |
| `core/pipeline.py` | orchestrator (idempotent upserts, sidecars, metrics) | built |
| `core/cli.py` | `cdp` CLI: generate, pipeline, eval, serve, governance-demo, reset | built |
| `adapters/local_store.py` | DuckDB IO + DDL | built |
| `adapters/queue_store.py` | SQLite queue IO | built |
| `adapters/databricks_store.py` | Delta/UC + Lakebase equivalents | written, workspace-unvalidated |
| `app/server/` | FastAPI: queue API, stats, role masking, static frontend | built |
| `app/frontend/` | Vite + React 19 + TS + Tailwind 4 + shadcn/ui + TanStack Query, one screen | built |
| `databricks/` | Lakeflow definitions wrapping `core/`, deploy notes + verification checklist | written, workspace-unvalidated |
| `tests/` | pytest unit (47) + integration (10); Playwright e2e session (2 specs) | built |

## Key data contracts

- `match_decisions`: ONE row per sorted `(record_a_id, record_b_id)` — the latest decision by precedence (human > oracle_review > deterministic > agent > probabilistic). Stages anti-join before evaluating; only `human` (queue ingestion) and `oracle_review` (overflow/finalize) overwrite. Low-confidence agent rows carry `routed_to_human=true` until review replaces them.
- Accepted cluster edge: `match=true` AND (stage != agent OR conf >= 0.85) AND pair not pending in the queue.
- `customer_id`: minted from cluster anchor record (oldest by loaded_at, then source priority, then id); survival across merge/split via the row-versioned crosswalk; ids never reused.
- Band/routing metrics derive from the decision log (agent + oracle_review + human rows), not per-run increments — stable across re-runs.

## Update log

- 2026-08-22 — initial version (design stage).
- 2026-08-22 — M9 hardening after two adversarial sweeps: regeneration clears derived state; fixture serving refuses real data dirs; fail-closed API roles + validation + atomic decisions; cut-split edges queued for confirmation (priority, outside the cap) and flagged clusters reported; ids never reused; frontend double-submit guard, mobile overflow fix, conflict-policy badge, analyst default; `cdp peek`; whole `dist/` served statically. Queue after a full run: 50 agent-routed + 3 cut confirmations.
- 2026-08-22 — as built: M0–M8 complete. Generator fixes (name-space fallback, franchise city uniqueness, hard-negative name registration); metrics.md protected from test runs; band metrics derived from decision log; sidecar derived from persisted decisions. Full run: 7,515 records / 3,000 entities, precision 1.0 at every stage prefix, recall 0.952 final, FP 0, all 3 injected errors caught by min-cut.
