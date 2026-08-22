# Talking points — "why this and not X"

One-sentence-defensible answers for every component. Longer context in README and ARCHITECTURE.

**Why not route everything through the LLM?**
Exact-domain matches need no inference; the LLM sees only the 0.70–0.90 ambiguous band (~8% of scored pairs here). Cheaper, faster, more auditable — and each layer's precision is measured separately, so the LLM has to *earn* its recall contribution (it added ~5 points of recall at zero FP cost).

**Why Splink and not the hand-rolled weighted score?**
The weighted score is kept as a baseline in `stage2_scores` — it's explainable in 30 seconds. Splink adds what a heuristic can't: Fellegi-Sunter m/u estimation instead of guessed weights, native blocking rules, and per-comparison match weights you can inspect. The judgment call is knowing when the heuristic stops being enough.

**Why Splink and not Zingg?**
Zingg (featured in Databricks' entity-resolution solution accelerator) expects labeled training pairs and classic Spark compute; Splink trains unsupervised (EM), has richer model diagnostics, and its DuckDB engine gives a fast local dev loop. On serverless-only environments Splink's Spark backend can't load its UDF JAR — so it runs single-node on the DuckDB engine writing results back to Delta, which is an honest, documented tradeoff.

**Why does EM training refuse small datasets?**
Fellegi-Sunter parameter estimation needs volume; EM on a few hundred records yields unstable m/λ. Below ~2k records the pipeline loads the committed pinned model instead of training a statistically indefensible one.

**Why a separate clustering step — aren't pairwise matches enough?**
No: transitivity. A↔B and B↔C match while A↔C was never compared. Connected components form entities; and once you have components you need a conflict policy for confirmed no-matches inside them — here, a capacity-weighted min-cut in which human/deterministic evidence is uncuttable, and an all-uncuttable contradiction escalates the whole cluster (a rule is wrong, not an edge).

**Why is the same-domain rule not sufficient for deterministic matching?**
Subsidiaries and sister companies share domains. The guard requires one-to-one name-token agreement ("Acme" vs "Acme Logistics" has a leftover token → demoted to the agent). Token logic, not a scalar similarity threshold, because scalar ratios flip on brand-name *length*.

**Why an LLM at all, if the mock can fake it?**
The mock exists to keep the demo free, offline, and honest (it's labeled). The stage's value is the *contract*: a structured verdict — match, confidence, evidence, reason — that is stored and auditable. Notes attached to records ("subsidiary of X", "franchise location") are evidence string similarity can't use; that's the case for an agent rather than a bigger threshold.

**Why not trust the agent's confidence as a probability?**
LLM self-reported confidence isn't calibrated. It's used only as a routing signal (below 0.85 → human), and the eval harness validates the thresholds against ground truth.

**Why a capped human queue with a simulated bulk reviewer?**
At 10k+ records even a small ambiguous band outruns one reviewer. Capping the genuine queue at 50 and disclosing an oracle for the overflow keeps the metrics computable without pretending a human read 500 pairs — the disclosure *is* the design point.

**Why SQLite for the queue when DuckDB is right there?**
The queue is OLTP (small concurrent row updates from a UI); DuckDB is single-writer OLAP. Splitting them also means the API server never opens the DuckDB file, so the pipeline can re-run while the server is up. It's the same split as Delta vs Lakebase Postgres in the Databricks deployment.

**Why are customer ids stable, and how?**
Downstream systems key on customer_id, so re-runs must not reshuffle ids. Ids are minted from the cluster's anchor record (oldest by load time, then source priority) and survive merges/splits via a row-versioned crosswalk; on a merge the older anchor's id wins and the loser is marked superseded, never reused.

**Why is the false-positive rate the headline metric?**
A wrong merge silently corrupts revenue rollups, attribution, and forecasts, and unmerging is expensive; a missed match is just work left on the table. Accuracy over match-rate.

**Why generate synthetic data instead of using a public dataset?**
Control over the failure modes: seeded hard negatives (shared-domain subsidiaries, franchises, same-name pairs) target exactly the cases each pipeline layer must handle, and a perfect ground truth makes precision/recall exact rather than sampled.

**Why the feedback loop isn't fully automated?**
Repeated human confirmations *propose* new deterministic rules (e.g., an alias table); promotion stays a human decision — auto-promoting rules from a handful of confirmations is itself a governance smell.
