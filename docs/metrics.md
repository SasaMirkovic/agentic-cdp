# Pipeline metrics

_Snapshot state: post-oracle, pre-human unless the queue was drained._
_True-match pair universe: 6854 pairs (all within-entity pairs from ground truth)._

| Stage prefix | Precision | Recall | F1 | FP pairs | ARI | Clusters |
|---|---|---|---|---|---|---|
| Deterministic rules only | 1.0 | 0.5101 | 0.6756 | **0** | 0.6755 | 4920 |
| + Splink auto-matches | 1.0 | 0.8938 | 0.9439 | **0** | 0.9439 | 3421 |
| + agent decisions | 1.0 | 0.9453 | 0.9719 | **0** | 0.9719 | 3226 |
| + review (oracle-assisted, simulated review team) | 1.0 | 0.9519 | 0.9753 | **0** | 0.9753 | 3195 |

- Agent band size (pre-cap, the honest routing denominator): 589
- Routed to human review: 169 (28.7% of band)
- Deliberately injected simulated-agent errors: 3 (see README: error injection keeps the false-positive story observable)
- Pairs still pending human review: 53

_The final stage row is oracle-assisted: overflow review is adjudicated from generator ground truth by a disclosed simulated review team._