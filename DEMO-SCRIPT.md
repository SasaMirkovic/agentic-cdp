# Demo script (3–5 minute walkthrough / recording)

Prep once before recording: `uv run cdp reset` (confirm), `uv run cdp generate`, `uv run cdp pipeline`, frontend built (`app/frontend`: `npm run build`). Have `docs/metrics.md` and this file open.

## 1. The mess (30s)

- `uv run cdp peek` — prints one real company as it appears across the source systems.
- Point at: suffix variants, typos, case differences, missing domains/phones. "Four source systems, 7.5k records, 3k real companies — which records are the same company?"

## 2. The layered pipeline (60s)

- Show the pipeline output table (`uv run cdp pipeline` or `uv run cdp eval`).
- Narrate the ladder from `docs/metrics.md`: deterministic gets precision 1.0 at recall 0.51; Splink lifts recall to 0.89; the agent band adds 5 points; review closes the rest — **false positives stay at zero the whole way**.
- One sentence on the decision log: "every row in `match_decisions` carries evidence, confidence, rationale, and who decided — nothing merges silently."

## 3. The review app (90s)

- `uv run cdp serve` → http://localhost:8000.
- Review 3–4 pairs: read the agent's evidence chips and reasoning out loud for one pair — ideally a shared-domain case ("same domain, but the notes say subsidiary — reject").
- Use keyboard shortcuts (`a`/`r`/`s`) for the second pair; skip one and note it resurfaces later.
- Flip the role select from **steward** to **analyst**: phone/address/notes mask to `•••`. "Two access levels: resolved identities vs raw PII."
- Point at the header chip: "N decisions awaiting pipeline re-run."

## 4. The loop closes (45s)

- In a second terminal: `uv run cdp pipeline` (server keeps running — it never opens the DuckDB file).
- Refresh the app: chip cleared, stats updated. "Human decisions carry the highest precedence — they reshape clusters and get written to the same audit log."

## 5. The defense in depth (30s)

- Show `data/local/oracle_injected_errors.json` (3 pairs) and say plainly: the simulated agent deliberately injects confident wrong matches at 2%.
- Then the punchline from the metrics: all three were **caught by the min-cut conflict policy** — surrounding no-match evidence contradicted them, so the cluster split and the bad edges were cut. "Even a confidently wrong agent decision has to survive the rest of the evidence."

## 6. Close (20s)

- Governance: `uv run cdp governance-demo` (masked vs clear views).
- "Same shape deploys to a lakehouse: DuckDB↔Delta, SQLite↔Lakebase, pipeline↔Lakeflow — the logic in `core/` doesn't change; see `databricks/`."

**Honesty rule for the recording:** if the mock agent comes up, say it plainly — the agent stage is simulated from ground truth in the demo (labeled `mock-oracle` in the UI), and the live Anthropic path is a config flag away.
