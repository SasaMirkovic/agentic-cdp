# CLAUDE.md — working rules for this repo

Agentic CDP: a local-first identity-resolution / Customer 360 pipeline (deterministic → probabilistic (Splink) → LLM agent → human review) with a FastAPI + React review UI and an optional Databricks deployment mode.

## Rules (non-negotiable)

1. **PLAN.md and ARCHITECTURE.md are living documents.** Every major change — new module, changed data contract, changed stage semantics, dropped/added scope, new dependency — MUST update both in the same working session: PLAN.md for status/decisions (check off milestones, add to "Recorded deviations"), ARCHITECTURE.md for current-state structure and behavior. If code and these docs disagree, that is a bug; fix the doc or the code before moving on.
2. **`docs/archive/` is historical only.** Never read it to determine current requirements or design — PLAN.md and ARCHITECTURE.md are the only sources of truth. The archive exists for provenance, nothing else.
3. **No authorship claims anywhere.** README, comments, docstrings, and commit messages describe the system in neutral engineering voice. Do not mention how, by whom, or with what tools the code was produced. No AI/assistant references. No Co-Authored-By trailers in commits.
4. **Honesty labels are load-bearing.** Anything simulated (mock-oracle agent decisions, oracle bulk review, simulated costs) must be labeled as simulated in the data (`decided_by`, README disclosures). Never let simulated output masquerade as live-LLM or human output. Do not weaken these labels to make demos look better.
5. **Decision-log invariants** (see PLAN.md §2.2): unique per sorted pair; stages anti-join before evaluating; only `human` (and `oracle_review` under `--finalize-pending`) overwrite. Don't break these for convenience.
6. **The API server never opens the DuckDB file.** Stats come from SQLite + `metrics.json`. This keeps `cdp pipeline` runnable while the server is up.
7. **Determinism everywhere**: generator, oracle (per-pair hashing), Splink u-sampling (explicit seed), tie-breaks (sorted pair ids, never UUIDs). A re-run on the same inputs must produce byte-identical decisions.
8. **Regeneration invalidates everything downstream.** `cdp generate` clears derived tables and the queue; never "optimize" that away — record ids are minted by position, so stale decisions would attach to different companies.
9. **Fixture-backed serving needs an isolated `CDP_DATA_DIR`.** `cdp serve --fixture` refuses a data dir that holds real pipeline data; keep that guard.

## Commands

- Python env: `uv sync` (Python 3.12 pinned). Run anything via `uv run ...`.
- CLI: `uv run cdp generate | pipeline | eval | serve | governance-demo | reset` (see PLAN.md §1 for flags).
- Frontend: `cd app/frontend && npm install && npm run build` (dev: `npm run dev`, proxies `/api`).
- Tests: `uv run pytest` (unit + integration); e2e: `cd tests/e2e && npm test` (builds served app via Playwright webServer).

## Environment

`.env` (copy from `.env.example`): `DEV=true` (local DuckDB/SQLite) — the only fully implemented mode; `MOCK_LLM=true` default (no API key required); `ANTHROPIC_API_KEY` only needed for `MOCK_LLM=false`; `CDP_DATA_DIR` overrides `data/local/`.

Windows notes: PowerShell is the shell; DuckDB is single-writer (pipeline and any other DuckDB reader must not overlap); SQLite runs WAL mode.
