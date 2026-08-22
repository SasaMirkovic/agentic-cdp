# Agentic CDP

A local-first **Customer Data Platform core**: it ingests company records from four GTM-shaped source systems (CRM, incentive-comp, marketing automation, intent data), resolves which records refer to the same real-world company, and produces governed, auditable **golden customer profiles** — with a layered matching strategy in which an LLM agent handles only the genuinely ambiguous cases and humans adjudicate what the agent itself is unsure about.

The design principle throughout: **use the simplest reliable method for each class of problem**. Exact keys resolve deterministically; fuzzy cases get a probabilistic model; only the ambiguous band reaches an LLM; only the LLM's low-confidence band reaches a person. A wrong merge is worse than a missed one — it corrupts downstream revenue reporting and attribution — so the false-positive rate is the headline metric.

```
  salesforce   xactly    marketo   sixsense        (synthetic, seeded, messy)
      └──────────┴──┬───────┴──────────┘
                 BRONZE  (as-received, append-only)
                    ▼
                 SILVER  (normalized, PII-tagged)
                    ▼
   ┌── Stage 1: deterministic ── exact domain + name-token agreement, guarded
   ├── Stage 2: probabilistic ── Splink (Fellegi-Sunter, DuckDB engine) + blocking
   ├── Stage 3: agent ────────── LLM structured verdict on the 0.70–0.90 band
   └── Stage 4: human ────────── capped review queue (React + FastAPI app)
                    ▼
        match_decisions  (every decision: evidence, confidence, rationale, actor)
                    ▼
        clustering  (connected components + min-cut conflict resolution)
                    ▼
        GOLD  (survivorship, enrichment, stable customer ids)  →  eval vs ground truth
```

## Results

7,515 synthetic records / 3,000 true entities, seeded with hard negatives (franchises sharing a brand, subsidiaries sharing a domain, same-name different-industry pairs). Full table in [docs/metrics.md](docs/metrics.md):

| Stage prefix | Precision | Recall | FP pairs |
|---|---|---|---|
| Deterministic rules only | 1.000 | 0.510 | 0 |
| + Splink auto-matches | 1.000 | 0.894 | 0 |
| + agent decisions | 1.000 | 0.945 | 0 |
| + review | 1.000 | 0.952 | 0 |

Each layer buys recall without giving up precision. Three deliberately injected high-confidence agent errors (see honesty notes below) were all caught and removed by the **min-cut conflict policy** — a confirmed no-match inside a cluster triggers a minimum cut over the contradicted evidence, so even a confidently wrong agent decision doesn't survive contact with the surrounding evidence.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Node 20+. No database servers, no Docker — storage is embedded (DuckDB for the pipeline, SQLite for the review queue).

```powershell
uv sync                                  # Python 3.12 env
cd app/frontend; npm install; npm run build; cd ../..
uv run cdp generate                      # seeded synthetic sources + ground truth
uv run cdp pipeline                      # bronze → silver → matching → gold → metrics
uv run cdp serve                         # review UI at http://localhost:8000
```

The demo loop: review pairs in the UI (approve/reject/skip, keyboard `a`/`r`/`s`), then `uv run cdp pipeline` again — human decisions are ingested with the highest precedence, clusters and metrics update, and the header chip showing "decisions awaiting pipeline re-run" clears. `MOCK_LLM=true` (the default) needs no API key or network; set `MOCK_LLM=false` with an `ANTHROPIC_API_KEY` in `.env` to run the agent stage live.

Other commands: `cdp peek` (one company across all sources), `cdp eval` (metrics snapshot), `cdp governance-demo` (masked vs clear views), `cdp reset`. Tests: `uv run pytest` (57 unit + integration), `cd tests/e2e && npm test` (Playwright end-to-end session).

## What makes the audit log the core

Every decision at every stage lands in one table, `match_decisions`, keyed by the record pair: stage, verdict, confidence, machine-readable evidence, free-text rationale, the deciding actor, and (for agent calls) cost. Nothing merges silently. Clustering consumes only this table, so any golden profile can be explained by walking its pairs' decisions — and the min-cut policy writes back *which* no-match decision cut *which* edge.

Governance is built in rather than bolted on: PII fields are tagged at the silver stage, the API and DuckDB views serve **analyst** (masked) vs **steward** (clear) access levels, and gold profiles carry per-field survivorship notes tracing every value to its source record.

## Honesty notes (read before quoting numbers)

- **The agent stage runs in mock mode by default.** Decisions labeled `decided_by: "mock-oracle"` come from a seeded simulator that reads generator ground truth and emits the same structured verdict shape as the live path. It deliberately injects a small rate (2%) of high-confidence false matches so the false-positive pipeline behavior stays observable; the injected pairs are written to a sidecar for exact test assertions. The live path (Anthropic structured outputs) is implemented and unit-tested against a mocked client.
- **The review "team" beyond the 50-pair human queue is simulated.** Overflow pairs are adjudicated from ground truth by an `oracle-bulk-reviewer`, disclosed in the data and in the metrics footnote. The final metrics row is therefore oracle-assisted.
- **LLM self-reported confidence is a routing signal, not a calibrated probability.** Thresholds are validated by the eval harness against ground truth, not taken on faith.
- Synthetic data means the eval has a perfect ground truth; real-world precision/recall would need labeled data.

## Design decisions

Short answers in [TALKING-POINTS.md](TALKING-POINTS.md); highlights:

- **Splink over a hand-rolled score** for stage 2 (the weighted-score baseline is retained in `stage2_scores` for comparison): Fellegi-Sunter with EM training, native blocking, and the same code runs on DuckDB locally or single-node in a Databricks notebook. EM below ~2k records is refused — the pinned model is committed instead.
- **Pairwise decisions are not clusters.** Transitivity needs connected components, and conflicts need a policy: confirmed no-matches trigger a capacity-weighted min-cut (human/deterministic evidence uncuttable; an all-uncuttable contradiction escalates the whole cluster to human review, because then a *rule* is wrong).
- **Stable customer ids** are minted from a cluster's anchor record and survive merges/splits through a row-versioned crosswalk; ids are never reused.
- **DuckDB + SQLite, deliberately.** OLAP-shaped pipeline tables in DuckDB; the queue's OLTP row updates in SQLite (WAL). The API server never opens the DuckDB file, so the pipeline can re-run mid-demo. The mapping to a lakehouse deployment is one-to-one: DuckDB↔Delta, SQLite↔managed Postgres (see [databricks/](databricks/)).

## Repo layout

```
core/        pipeline logic (generator, normalize, stages, clustering, survivorship, eval)
adapters/    storage: DuckDB + SQLite locally; Delta/Lakebase equivalents for Databricks
app/         FastAPI server + React (Vite/TS/Tailwind/shadcn) review UI
databricks/  Lakeflow pipeline wrappers + deployment notes (workspace-validated separately)
data/        committed artifacts (ground truth, pinned Splink model); local/ is regenerated
tests/       pytest unit + integration; Playwright e2e
```

## References

- Databricks: [Customer 360 reference architecture](https://www.databricks.com/resources/architectures/c360-reference-architecture-for-insurance) · [Curating customer identities with Amperity](https://www.databricks.com/blog/curating-high-quality-customer-identities-databricks-and-amperity) · [Adaptive identity resolution with Hightouch](https://www.databricks.com/blog/adaptive-identity-resolution-databricks-hightouch)
- [Splink](https://github.com/moj-analytical-services/splink) (MoJ) — Fellegi-Sunter probabilistic record linkage
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
