# Evaluation contract

**Fixed in Phase 1, before any detector existed.** That timing is the point. Alerts are
window- and entity-level while ground truth is scenario-level, so "did this alert catch
that scenario" needs a matching rule — and a matching rule chosen *after* seeing detector
output is a free parameter that can be tuned until the numbers look good. Writing it down
first is what makes the headline metrics a measurement rather than a claim.

## Matching rule

A planted scenario is **detected** when at least one alert matches it under the rule for
its family:

| Scenario family | Match condition |
|---|---|
| `statistical_outlier` (all subtypes) | The alert references ≥ 1 trade whose `external_id` is in the scenario's `trade_external_ids`. |
| `wash_ring`, `coordinated_cluster` | Jaccard similarity between the alert's account set and the scenario's `account_ids` ≥ **0.5**, **and** the alert window overlaps `[window_start, window_end]`. |

Rationale for Jaccard ≥ 0.5 rather than "any overlap": a single shared account between a
6-account alert and a 12-account scenario is not a detection, it is a coincidence — and
accepting it would let one sprawling alert claim credit for several distinct scenarios.
0.5 requires the alert to have recovered the majority of the group. It is a threshold, not
a law; the sensitivity of the final numbers to it is reported in the Phase 5 write-up.

Positive scenarios are constructed with **disjoint account sets** (enforced by
`test_positive_scenarios_do_not_share_accounts`), so no alert can be ambiguously credited
to two scenarios.

## Metrics reported

- **Recall, per scenario type and subtype.** Never only in aggregate: the four
  `statistical_outlier` subtypes differ enormously in difficulty, and one headline recall
  number over them is close to meaningless.
- **Precision**, over all alerts raised.
- **Hard-negative false-positive rate**, reported **separately and by name.** A hard
  negative that fires is listed individually with the scenario id and what it was testing.
  It is never folded into an averaged precision figure, because the whole reason it exists
  is to be looked at.
- **Attribution by layer**: which of `{statistical, graph}` fired for each detected
  scenario, compared against the `expected_layer` declared on the label when the scenario
  was written. Declaring the expectation up front prevents the Phase 5 write-up from
  rationalising whatever the detectors happen to do.

## What counts as a false positive

Any alert that matches no positive scenario. This includes alerts on background activity,
which is the honest treatment: a compliance team's cost is total alert volume, not just
alerts on the interesting cases.

## Anti-leakage rules

1. Ground-truth labels live only in `data/ground_truth.jsonl`. They are never loaded into
   Postgres, so nothing in the serving path can read them
   (`test_no_ground_truth_column_reaches_the_database`).
2. `trades.counterparty_account_id` is ground-truth metadata. Nothing under
   `surveillance/detect/` may reference it; the graph layer must *infer* account-to-account
   relationships from co-trading behaviour. Enforced by AST inspection in
   `test_detection_isolation.py`.
3. `external_id` is assigned after a global sort by execution time, so the primary key
   carries no information about scenario membership
   (`test_external_ids_do_not_leak_labels`).
