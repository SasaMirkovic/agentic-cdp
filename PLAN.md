# PLAN.md — Agentic CDP Implementation Plan

_Living document. Updated with every major change (rule in CLAUDE.md). Status markers: [ ] todo, [~] in progress, [x] done._

Derived from the agreed spec + improvements (archived in `docs/archive/`). This plan is the buildable truth; the archive is history.

---

## 0. Ground rules & constraints

- **Local-first, embedded-only storage**: DuckDB for pipeline tables, SQLite (WAL) for the review queue. No database servers, no Docker.
- **Runtime modes** via `.env`: `DEV=true` (local DuckDB/SQLite — the only mode implemented end-to-end here) vs `DEV=false` (Delta/Unity Catalog + Lakebase; adapters + Lakeflow definitions written, validated later in a workspace). `MOCK_LLM=true` replays/generates recorded agent decisions so demos never need network.
- **Toolchain**: `uv` with pinned Python 3.12 (Splink/library compatibility; system 3.14 is too new to bet on), npm + Vite for the frontend, single repo.
- **No live Anthropic key in the build environment**: the live agent path is implemented and covered by unit tests with a mocked client; the committed decision-log artifact is produced by the **oracle simulator** (reads generator ground truth, injects a small configured error rate, labeled `decided_by: "mock-oracle"`). Running one real pass later requires only `ANTHROPIC_API_KEY` + `MOCK_LLM=false`.
- **Honesty labels**: every simulated decision (oracle agent, oracle bulk-reviewer) is marked as simulated in the data and in the README. Nothing simulated masquerades as human or live-LLM output.
- **Authorship-neutral docs**: README and code comments describe the system in neutral engineering voice; no authorship claims of any kind.

---

## 1. Repo layout

```
agentic-cdp/
  CLAUDE.md                  # working rules for coding sessions
  PLAN.md                    # this file (living)
  ARCHITECTURE.md            # current-state architecture (living)
  README.md                  # public-facing writeup (written last, milestone M8)
  pyproject.toml             # uv-managed; deps + [project.scripts] cdp CLI
  .env.example               # DEV, MOCK_LLM, ANTHROPIC_API_KEY, CDP_DATA_DIR
  core/                      # pure logic — engine-agnostic, no IO beyond dataframes
    config.py                #   env/config loading, thresholds, source priorities
    generator/               #   synthetic data generator (fixed seed)
    normalize.py             #   name/domain/phone/address canonicalization + PII tags
    stages/
      deterministic.py       #   stage 1
      probabilistic.py       #   stage 2: Splink config + weighted baseline
      agent.py               #   stage 3: Anthropic structured-output client + oracle mock
      review.py              #   stage 4: queue routing policy (cap + oracle bulk reviewer)
    clustering.py            #   connected components + min-cut conflict policy
    survivorship.py          #   golden-profile builder + enrichment join + crosswalk IDs
    feedback.py              #   alias/suffix promotion from confirmed decisions (stretch)
    evaluate.py              #   precision/recall/FP-rate per stage vs ground truth
  adapters/
    local_store.py           #   DuckDB table IO (bronze/silver/decisions/gold)
    queue_store.py           #   SQLite review queue (WAL)
    databricks_store.py      #   Delta/UC + Lakebase equivalents (written, not locally testable)
  app/
    server/                  # FastAPI: /api/* + serves frontend build
    frontend/                # Vite + React 19 + TS + Tailwind v4 + shadcn/ui + TanStack Query
  databricks/                # Lakeflow definitions wrapping core/, deploy notes
  data/
    artifacts/               # committed: ground_truth.parquet, mock_agent_decisions.parquet, enrichment reference
    local/                   # gitignored: cdp.duckdb, queue.db, generated CSVs
  scripts/                   # thin entrypoints (also exposed as `cdp` CLI subcommands)
  tests/
    unit/                    # pytest: core logic
    integration/             # pytest: full pipeline on small seed
    e2e/                     # Playwright: full user session against served app
  docs/archive/              # original spec + improvements doc + conversation (historical)
```

CLI (`cdp`, via `[project.scripts]`): `cdp generate`, `cdp pipeline` (bronze→gold; flags `--record-mock`, `--finalize-pending`), `cdp eval`, `cdp serve` (flags `--port`, `--fixture`), `cdp governance-demo`, `cdp reset`. Each subcommand idempotent and re-runnable.

---

## 2. Data design

### 2.1 Synthetic generator (`core/generator`)
- **Scale**: 3,000 base companies; each appears in 1–4 sources (weighted: salesforce 90%, sixsense 60%, marketo 55%, xactly 45%) → ~7,500–9,000 records.
- **Mess model** (applied per-record with configured probabilities): legal-suffix variants (Inc/Corp/GmbH/…); typos (1–2 char edits); domain missing / subdomain / alternate TLD; phone formats; address abbreviation variants; missing fields.
- **Hard negatives (deliberate near-duplicates that are NOT the same company)**:
  1. Franchises sharing a brand name, different domains/cities.
  2. Subsidiaries/sister companies **sharing a domain** (defeats naive domain-match).
  3. Same-name-different-industry pairs ("Acme Software" / "Acme Logistics").
- **Unstructured notes**: template-generated CRM notes attached to ~40% of salesforce records; some notes embed resolution evidence ("formerly known as…", "acquired by…", "not related to X in Denver") that only the agent stage can use.
- **Ground truth**: `data/artifacts/ground_truth.parquet` — record_id → true_entity_id. First-class committed artifact; the eval and oracle both read it. Generator is seeded (`--seed 42` default) → identical output on every machine.
- **Enrichment reference**: `data/artifacts/enrichment_reference.parquet` — one row per true entity domain with firmographics (industry code, employee band, tech tags).

### 2.2 Tables (DuckDB, names identical in Delta mode)
- `bronze_<source>`: source_system, source_record_id, company_name, domain, address, phone, industry, employees, revenue, notes (nullable), loaded_at, raw_payload (JSON).
- `silver_records`: bronze cols + normalized_name, normalized_domain, normalized_phone, normalized_address, name_tokens, pii_flags (list), record_id (source_system:source_record_id).
- `match_decisions` (the audit core): match_id, record_a_id, record_b_id, stage (deterministic|probabilistic|agent|human|oracle_review), match (bool), confidence, evidence (JSON), reason, decided_by, decided_at, cost_usd (agent only; in mock mode a simulated estimate labeled as such).
  - **Composition invariant**: unique natural key `(record_a_id, record_b_id)` (pair ids sorted) — each stage only evaluates pairs with **no prior decision** (anti-join against this table). Under this invariant only `human` (and `oracle_review` via `--finalize-pending`) can ever overwrite an existing row; the full trust order human > oracle_review > deterministic > agent > probabilistic governs those overwrites, clustering edge capacities, and nothing else. Idempotent re-runs upsert on the natural key, never append. Extra column: `routed_to_human` (bool) on agent rows below the confidence threshold.
  - Stage-2 scores below the auto-match band are **not decisions**: sub-0.70 pairs are simply non-candidates, recorded in `stage2_scores` (all scored pairs incl. weighted-baseline score, for eval/comparison only — the baseline never emits decisions).
- `entity_clusters`: record_id → cluster_id, run_id.
- `gold_profiles`: customer_id, canonical_* fields, employees, revenue, industry, enrichment fields, source_record_ids (list), survivorship_notes (JSON), last_updated.
- `customer_id_crosswalk`: customer_id, anchor_record_id, member_set_hash (sha256 of sorted member record_ids — the cluster fingerprint), status (active|superseded), superseded_by_customer_id (nullable), run_id. Row-versioned: each membership change appends a row (same customer_id, new hash); latest row per id is current; a merge marks the losing id `superseded` with `superseded_by_customer_id` set. Ids are never reused.
- SQLite `review_queue`: pair payload snapshot, agent decision snapshot, status (pending|approved|rejected|skipped), reviewed_by, reviewed_at.

---

## 3. Pipeline stages — precise behavior

### Stage 1 — Deterministic (`stages/deterministic.py`)
- Match on exact `normalized_domain` **with guards**: domain not null, not in shared/freemail denylist, **and per-pair name agreement via token containment** (NOT a scalar ratio threshold — `token_sort_ratio ≥ 60` was tried and fails: "northwind" vs "northwind labs" scores 78 because a long shared prefix dominates the ratio; the guard's behavior must not flip on brand-name length). Rule: after legal-suffix stripping and tokenization, the pair auto-matches only if the two token sets match one-to-one (each token in A fuzzy-matches a token in B, `ratio ≥ 85` per token for typo tolerance, and vice versa — no leftover distinguishing tokens on either side). "Acme" vs "Acme Logistics" has a leftover token → demoted. Demoted pairs are **marked `stage1_demoted`** and that *pair* (not the whole domain) is force-routed to Stage 3 (see Stage 2/3) — never auto-matched by any automated stage.
- Generator makes subsidiary names **share the brand token on purpose** (mixed short and long brand names) so the guard is tested against the real hard case; unit test asserts demotion for both name lengths.
- Confidence 1.0, evidence `{rule: "exact_domain_name_agree", domain, token_match_detail}`. All decisions logged.

### Stage 2 — Probabilistic (`stages/probabilistic.py`)
- **Baseline**: the weighted score from the original spec (name/address/phone/industry via rapidfuzz), kept for eval comparison.
- **Primary**: Splink (DuckDB backend). Blocking rules: same normalized_domain; same first name-token; same phone. Candidate pairs are **anti-joined against `match_decisions`** first (composition invariant) — Stage 2 never re-scores a pair Stage 1 already decided. Comparisons: name (JaroWinkler levels), address (token), phone (exact), industry (exact). Training: `estimate_probability_two_random_records_match` (deterministic rules for λ) + `estimate_u_using_random_sampling` + EM sessions under blocking; m/u sanity-checked against generator parameters (we control the truth — say so in README).
- Bands on Splink `match_probability`: > 0.90 auto-match (logged as decisions); 0.70–0.90 → Stage 3; < 0.70 → **non-candidate** (recorded in `stage2_scores`, not a no-match decision — see §2.2). Bands are config, revisited after eval.
- **`stage1_demoted` pairs are capped at the agent band**: whatever Splink scores them, they route to Stage 3, never auto-match — otherwise the same-domain blocking rule would silently re-merge the exact subsidiary pairs Stage 1 demoted (brand-token names score high on JaroWinkler). Integration test asserts: **no shared-domain hard-negative pair is auto-matched by any automated stage.**
- Determinism: `estimate_u_using_random_sampling` gets an explicit `seed` **and** `max_pairs` large enough to be exhaustive at this scale — unseeded u-sampling would change m/u across machines and break artifact replay. For the small integration-test seed (~150 companies), EM is skipped entirely: a pinned model-parameter config is loaded instead (EM on ~400 records is the unstable regime the spec warns about).

### Stage 3 — Agent (`stages/agent.py`)
- **Contract** (both live and mock produce exactly this):
  `{match: bool, confidence: float, evidence: [str], reason: str}`
- **Live path**: Anthropic SDK, structured outputs (`client.messages.parse()` / `output_config.format`; verify current param names against docs at implementation time), small-model default (haiku-class), Batch API for the full band, per-decision cost logged. Prompt includes both records' full evidence **plus attached notes** and instructs: lower confidence rather than guess.
- **Mock path** (`MOCK_LLM=true`): oracle simulator reads ground truth; emits the same schema; evidence/reason templated from actual field comparisons (so the UI demo looks real); `decided_by: "mock-oracle"`. Determinism + error semantics:
  - **Per-pair determinism**: a pair's mock decision derives from `hash(seed, sorted_pair_ids)` — never from a sequential RNG draw — so a pair's decision is stable regardless of band composition (one ingested human decision must not reshuffle every other pair's mock output).
  - Configured 2% error rate, **match-direction only** (errors are false *matches*; injected false no-matches would fire the min-cut trigger inside true clusters and pollute the recall/routing story rather than the FP metric). Injected errors get confidence ≥ 0.85 so they bypass the human queue and surface as FPs (that's the point). The oracle emits a sidecar list of injected-error pair ids.
  - **Assertion levels**: exact-count assertions run at the **decision-edge level, pre-clustering** ("agent decisions disagreeing with ground truth == sidecar list, exactly"); at the implied-pair/cluster level post-min-cut only bounds are asserted (transitive closure and cuts legitimately change counts).
  - Replay vs generate: if the current pair-set hash matches the committed `mock_agent_decisions.parquet`, replay it; otherwise generate (per-pair hashing makes the two paths agree wherever pairs overlap). Artifact produced once by `cdp pipeline --record-mock`.
- Agent confidence < 0.85 → Stage 4 queue.

### Stage 4 — Human review (`stages/review.py`)
- Queue is **capped at 50 pairs** (lowest-confidence first) for genuine human review through the UI; the overflow is adjudicated by the **oracle bulk reviewer** (ground truth, labeled `oracle_review`), disclosed in README as a simulated review team.
- **Pending pairs are undecided** (no edge) in the run that created them; gold/metrics for that run reflect post-oracle, pre-human state — and the committed metrics snapshot is labeled exactly that.
- Human decisions from the SQLite queue are ingested into `match_decisions` on the next `cdp pipeline` run (stage `human` outranks all per §2.2 precedence). **Pending and skipped pairs survive re-runs indefinitely by default** — the review→re-run→refresh demo loop must work any number of times, and the idempotency test asserts re-runs create no new decisions. Oracle adjudication of aged pending pairs happens **only** under an explicit `cdp pipeline --finalize-pending` flag (used once when producing the committed metrics snapshot).
- **Demo flow (goes in README):** review in UI → `cdp pipeline` (safe while server runs — see §4 server/DuckDB rule) → refresh. The UI stats include `decisions_pending_ingestion` so the state is never silently stale.

### Clustering (`clustering.py`)
- **Accepted-edge predicate (explicit, spans both stores):** an edge exists for a pair iff its `match_decisions` row has `match=true` AND (stage ≠ agent OR confidence ≥ 0.85) AND NOT still-pending-in-queue. Low-confidence agent decisions ARE written to `match_decisions` (flag `routed_to_human=true`) — they are audit rows and re-run skip markers, not edges; queue status (SQLite) is authoritative for pending-ness and is read by the pipeline at the ingestion step.
- networkx connected components over accepted edges.
- **Conflict policy — precisely:**
  - *Trigger*: a **confirmed no-match** inside a component. Confirmed no-match = a `human`/`oracle_review` no-match decision, or an `agent` no-match with confidence ≥ 0.85. Stage-2 sub-threshold pairs are non-candidates, never triggers.
  - *Mechanism*: `nx.minimum_cut(component, a, b, capacity=...)` (max-flow). Capacities are **rank-dominant**, consistent with §2.2 precedence: `human`/`oracle_review`/`deterministic` = `math.inf` (uncuttable); `agent` = `2000 + confidence`; `probabilistic` = `1000 + confidence` — so between single edges the cut always removes the lower-ranked one. (Additive min-cut can't express strict lexicographic rank across *multiple* edges — e.g. three probabilistic edges outweigh one agent edge; that tradeoff is accepted and documented in code.)
  - *Uncuttable case*: if **any** all-infinite-capacity path connects the pair, networkx raises `NetworkXUnbounded` — catch it and flag the whole cluster `needs_human_review` (a rule or human, not an edge, is wrong). That exception IS the policy's detection mechanism; do not pre-filter infinite edges.
  - *Multiple triggers in one component*: process trigger pairs in sorted order; re-derive components after each cut before processing the next.
  - *Determinism*: ties broken by sorted `(record_a_id, record_b_id)` — never by UUID; identical graphs cut identically across runs.
  - After cutting: removed edges logged (`evidence: {cut_by: match_id_of_nomatch}`), fragments re-clustered, split routed to human review for confirmation.

### Gold (`survivorship.py`)
- Survivorship per field: source priority (salesforce > xactly > marketo > sixsense) then non-null then most recent; every choice recorded in survivorship_notes.
- **`customer_id` — single authority, the anchor record:**
  - *Minting*: on a cluster's first appearance, `customer_id` derives from its **anchor** = oldest record by (loaded_at, source-priority, source_record_id lexicographic). Persisted in the crosswalk with the member_set_hash fingerprint.
  - *Split*: the fragment **containing the anchor** keeps the id (this supersedes the earlier "largest fragment" wording — anchor-based is deterministic and consistent with minting); other fragments mint fresh ids.
  - *Merge*: the id whose anchor is oldest survives; the other is marked `superseded_by` in the crosswalk (never reused).
- Enrichment: left join on canonical_domain against enrichment_reference; enriched fields flagged as such in survivorship_notes.

### Eval (`evaluate.py`)
- **Definitions (fixed, so metrics can't be silently inflated):**
  - *Universe*: all true-match pairs = within-entity record pairs from ground truth (computable at this scale). Recall denominator is this universe — never "pairs the pipeline saw."
  - *Per-stage-prefix metrics*: for each prefix (S1; S1+2; S1+2+3; S1..4), rebuild clusters from that prefix's accepted edges — **including running the min-cut conflict policy with whatever trigger decisions exist within that prefix** (so S1+2+3 applies agent no-match cuts; prefixes are evaluated by the same rules as the full pipeline) — and score **implied pairs** (transitive closure), so pair metrics and cluster metrics tell one story. Cluster-level ARI reported alongside.
  - *% routed to human* = **pre-cap band size** (the honest number), with the 50-cap noted separately.
  - *Stage-4 row footnoted*: oracle-assisted (the bulk reviewer reads ground truth), so its near-perfect numbers are labeled as simulated-review, not model skill.
  - FP assertions in tests use the oracle's injected-error sidecar for **exact** expected counts (seeded).
- Outputs `data/local/metrics.json` + printed table + `docs/metrics.md` snapshot (post-oracle, pre-human — labeled) for the README. **False-positive count highlighted.**
- MLflow (optional dep group `[eval]`): if installed, log runs/params/metrics locally. Agent-call *tracing* deferred until a live key exists (recorded as a deviation from the spec doc). Not required for core flow.

---

## 4. App

### FastAPI (`app/server`)
- `GET /api/queue?status=pending` (paged), `GET /api/queue/next`, `POST /api/queue/{id}/decision {decision: approve|reject|skip}`, `GET /api/stats` (pending/approved/rejected counts, `decisions_pending_ingestion`, pipeline metrics summary), `GET /api/health`.
- **The server never opens the DuckDB file.** Stats come from SQLite + `metrics.json` only — so `cdp pipeline` can safely re-run while the server is up (DuckDB is single-writer; this rule is what makes the review→re-run→refresh demo loop work).
- Serves `app/frontend/dist` statically at `/`. Single process, SQLite WAL, Pydantic models shared with core schemas. Config via `pydantic-settings` (+.env), shared by CLI and server.
- Test/demo affordance: `cdp serve --port N --fixture <file>` resets and seeds `queue.db` before binding (used by Playwright's `webServer`).
- **Governance demo (local):** a `role` toggle — `GET /api/queue?role=analyst` returns PII fields masked (per `pii_flags`), `role=steward` returns them clear; plus a `cdp governance-demo` command creating the equivalent masked/clear DuckDB views. Small, but it keeps the two-access-levels story demonstrable locally, not just in the Databricks checklist.

### Frontend (`app/frontend`)
- Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui (button, card, badge, table, **sonner** for toasts — the shadcn `toast` component is deprecated) + TanStack Query. **No router — one screen.**
- Scaffold prerequisites before `shadcn init` (known Vite gotchas): `@tailwindcss/vite` plugin installed, `@` path alias configured in **both** `tsconfig` and `vite.config.ts`.
- Dev loop: Vite `server.proxy` → `/api` to the FastAPI port (no CORS middleware needed; prod is same-origin).
- Layout: stats header (pending count, session approved/rejected, pipeline precision) · main card: side-by-side record fields with per-field agreement highlighting · agent panel (confidence bar, evidence chips, reason, attached notes) · action row (Approve / Reject / Skip, keyboard a/r/s) · empty state ("queue clear") · error toast on failed mutation.
- TanStack Query: queue fetch + optimistic decision mutation + stats invalidation.

---

## 5. Databricks mode (written, workspace-validated later)

- `adapters/databricks_store.py`: same interface as local_store, Delta tables via Unity Catalog paths; queue backed by Lakebase Postgres (psycopg).
- `databricks/pipelines/`: Lakeflow Declarative Pipeline definitions as thin wrappers importing `core/` transforms; Splink runs via its DuckDB engine in a single-node notebook step writing a Delta table (serverless has no JAR support — documented tradeoff).
- `databricks/README.md`: deploy steps + the day-1 verification checklist (UC masks/lineage on free tier, egress after LinkedIn verification, second principal for the PII-masking demo, app limits).
- **Not testable in this environment** — code written to documented APIs, marked as requiring workspace validation.

---

## 6. Testing strategy

- **Unit (pytest)**: normalization edge cases; deterministic guards (freemail, multi-entity domain); weighted baseline math; band routing; agent JSON contract (live path with mocked Anthropic client — asserts request shape + schema enforcement + fallback when confidence low); oracle honesty labels; clustering incl. min-cut scenarios (simple split, all-deterministic-path escalation, tie-break determinism); survivorship + crosswalk id stability across re-runs (merge and split cases); eval math on a hand-computed miniature.
- **Integration (pytest)**: `cdp generate --seed 42 --companies 150` → full pipeline → assertions on metrics floor (stage-1 precision 1.0, FP count ≤ known-injected), idempotent re-run (no new decisions, ids stable).
- **E2E (Playwright, TS, located in `tests/e2e/` with its own package.json — single authoritative location)**: `playwright.config.ts` sets `webServer.cwd` to the **repo root** (webServer cwd defaults to the config dir, which would break relative paths) and `webServer.command: "uv run cdp serve --port 8765 --fixture tests/e2e/queue_fixture.json"`, with `CDP_DATA_DIR` pointed at a temp dir so e2e never touches dev data; the stats endpoint renders "n/a" gracefully when `metrics.json` is absent in the fixture context (asserted). Build frontend first. One scripted **full user session**: load page → stats visible → review pair (fields + agent panel render) → approve (optimistic update + stats change) → reject next → keyboard-shortcut a decision → skip → drain to empty state → reload page → decisions persisted → API-error path via `page.route()` interception of the decision POST (500 → toast, no state corruption; no server-side failure flag needed).
- **Two post-implementation adversarial test sweeps** (independent agents): sweep A attacks backend/pipeline correctness, sweep B attacks the app/E2E surface; all confirmed bugs fixed and regression-tested.

---

## 7. Milestones

- [x] **M0 scaffold**: uv project (py 3.12), pyproject, env config, CLI skeleton, adapters interfaces, gitignore; frontend scaffold (Vite/TS/Tailwind/shadcn); repo docs (CLAUDE.md, ARCHITECTURE.md, archive move).
- [x] **M1 generator**: mess model, hard negatives, notes, ground truth + enrichment artifacts committed. (Build fixes: three-word fallback names; distinct franchise cities; composed hard-negative names registered against collisions.)
- [x] **M2 bronze/silver**: ingestion + normalization + PII tags (+ unit tests).
- [x] **M3 stage 1 + stage 2**: deterministic with guards; Splink + baseline; decision log (+ tests). Splink model trained at full scale and pinned/committed.
- [x] **M4 stage 3 + 4**: agent live+mock paths; queue routing + oracle bulk reviewer; recorded mock artifact (+ tests).
- [x] **M5 clustering + gold**: components, min-cut policy, survivorship, crosswalk, enrichment (+ tests). All 3 injected agent errors are caught by min-cut in the full run.
- [x] **M6 eval**: metrics engine, metrics.md snapshot, integration test floor (stage-1 precision 1.0 holds; FP = 0 at every stage).
- [x] **M7 app**: FastAPI + SQLite queue + React screen; human-decision ingestion; Playwright session green.
- [x] **M8 docs & deliverables**: README, DEMO-SCRIPT.md, TALKING-POINTS.md, databricks/ port files + checklist.
- [x] **M9 hardening**: two adversarial test sweeps (A: backend, B: frontend/full-system), all confirmed bugs fixed with regression tests; final suite: 57 pytest + Playwright session 3/3 green.

Stretch (only after M9): feedback-loop promotion (`core/feedback.py`), insights-agent CLI demo, MLflow logging group, notes-extraction step.

**Recorded deviations from the spec docs** (deliberate, don't "fix"):
- npm instead of pnpm (pnpm not installed in the build environment; lockfile discipline unchanged).
- Notes-extraction pipeline (spec-improvements 3a use #2) demoted to stretch: without a live key its LLM path can't produce honest committed artifacts; notes remain first-class **agent evidence**. README's enrichment story = reference-data join (+ notes-as-evidence), stated plainly.
- MLflow agent-call tracing deferred until a live key exists; experiment logging stays optional.
- "Largest fragment keeps the id" (spec-improvements 3e) superseded by anchor-based id survival (see Gold §3) — deterministic and consistent with minting.
- Stage-2 sub-0.70 pairs are non-candidates, not logged "no match" decisions (original spec §6 said "no match, stop") — decisions-only audit log keeps the min-cut trigger set sane; scores preserved in `stage2_scores`.
- Genie space / AI-BI dashboard: workspace-only, listed in `databricks/README.md` as a post-deploy step; cannot exist in this repo.
- Incremental *new-record* candidate generation (spec-improvements 5.3 second half) reduced to idempotent re-run semantics; true incremental ingestion is stretch.
- Frontend unit tests (Vitest) dropped — one screen, fully covered by the Playwright session + pytest on the API; recorded here so it isn't "fixed" back in.
- Scale: 3,000 companies (~7.5–9k records) — the spec's lower bound (its "10–20k records" phrasing assumed more sources per company); EM validity and the blocking story hold; README quotes the real numbers.
- README must include the **LLM-confidence-calibration honesty line** (spec-improvements §4: self-reported confidence is a routing signal, not a calibrated probability; thresholds validated by the eval harness).

**Build-time deviations (added during implementation; deliberate):**
- Live agent path calls the Messages API sequentially; the Batch API swap is documented in code but unexercised (no key in the build environment).
- Mock "replay vs generate": per-pair hash derivation makes generation byte-identical everywhere, so the pipeline always generates; the committed `mock_agent_decisions.parquet` is provenance, not a replay dependency.
- The decision log holds ONE row per pair (latest by precedence): overflow `oracle_review` overwrites its pair's routed agent row. Band/routing metrics are therefore derived from the whole log (agent + oracle_review + human rows), not from `routed_to_human` flags alone.
- `docs/metrics.md` is written only by default-artifacts runs; isolated (test) runs write only their own `metrics.json` — tests must not clobber the committed snapshot.
- e2e reality: `record-a`/`record-b` testids carry the column headers; company names are asserted via `field-row-company_name`.

**Post-sweep-A fixes (hardening, M9):**
- `cdp generate` clears all derived tables, queue.db, metrics and sidecars — regenerating without reset previously left stale decisions attached to re-minted record ids (precision collapsed to 0.006 in repro). The pipeline also drops orphan decisions whose records are absent from silver.
- `cdp serve --fixture` refuses to run against a data dir holding `cdp.duckdb`; human ingestion skips pairs whose records are unknown to silver.
- Stage-1 demotion is recomputed every run (only the *decision* is skipped for decided pairs) so `stage2_scores.stage1_demoted` is re-run-stable.
- API fails closed: `role` is a Literal (422 on anything else) defaulting to `analyst` (masked); `status`/`limit`/`item_id` validated; decisions are atomic (409 when already decided); fixture reads are UTF-8 and pair-ordered.
- Cut-split edges are queued for human confirmation (priority items outside the 50 cap, `decided_by: conflict-policy:min-cut`); flagged clusters are reported in metrics (`flagged_clusters`) and gold (`needs_human_review`).
- customer ids are never reused: minting consults every id ever issued and salts re-mints by generation.
- Survivorship order is source priority → non-null → record id (not "most recent": batch loads share timestamps within a source, so recency adds nothing). Supersedes the §3 Gold wording.

**Post-sweep-B fixes (frontend / full system):**
- Double-submit guard: synchronous in-flight ref + a 350 ms settle window (buttons disabled) after a new pair renders — a rapid second click could previously decide the *next*, unreviewed pair; 409 is now treated as "already decided, advancing" rather than an error.
- Narrow viewports: the comparison grid scrolls inside the card (`overflow-x-auto`, min width) instead of clipping Record B; record ids break; action bar wraps.
- UI defaults to the **analyst** (masked) role, matching the API's fail-closed default.
- Conflict-policy queue items show a distinct "Link cut — confirm split?" badge instead of a misleading green "Match".
- Masking detection compares to the exact `•••` token; the source-system row is neutral (always cross-source); progress bar forwards `value` (aria); mutations use `networkMode: "always"` so offline clicks fail loudly instead of replaying silently.
- Static serving mounts the whole `dist/` (favicon/icons no longer 404); metrics `pending_pairs` counts the queue after cut confirmations are enqueued.
- `cdp peek` replaces the shell-fragile DEMO-SCRIPT one-liner; `tests/e2e/.tmp-data/` gitignored; the masking e2e test seeds its own approval.
- Accepted, not changed: analyst view still shows evidence chips like `phone_match` — they reveal a match *fact*, not a value. The working-rules file is named `CLAUDE.md` by explicit request.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Splink API drift / Py 3.12 wheels | Pin splink >=4 <5; smoke-test import in M0; fallback = weighted baseline as primary with Splink behind a flag (eval story survives). |
| Structured-outputs param names shifted again | Isolated in one client module; unit tests assert our request shape; verify against live docs at M4. |
| Tailwind v4 + shadcn init friction on Windows | Pin shadcn CLI version; if init misbehaves, vendor components manually (they're just files). |
| Playwright on Windows (browser install) | `npx playwright install chromium` in M0; test against built frontend (no dev-server flakiness). |
| DuckDB/SQLite file locks on Windows | Single-writer discipline: pipeline and server never run concurrently in tests; WAL for SQLite. |
| Oracle simulation misread as real | Honesty labels in data + README disclosure (non-negotiable). |
