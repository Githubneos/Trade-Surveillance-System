# Graph layer: feasibility probe (run during Phase 1)

Run before committing to the Phase 4 design, to check the premise that wash rings are
recoverable **without** reading `counterparty_account_id`. Three edge constructions were
tested against the planted rings and, as controls, the three hard negatives most likely to
imitate them.

All numbers below come from inferred edges only: same security, opposite sides, within a
time window. No counterparty column.

## Result 1 — raw co-trading counts do not work at all

Mean edge weight among ring members vs. all other pairs in the same security:

| ring | members | in-ring | other pairs | ratio |
|---|---|---|---|---|
| wash_ring_01 | 4 | 288 | 661 | **0.4x** |
| wash_ring_02 | 5 | 782 | 1709 | **0.5x** |
| wash_ring_03 | 5 | 392 | 405 | 1.0x |
| wash_ring_04 | 3 | 72 | 360 | **0.2x** |
| wash_ring_05 | 4 | 128 | 705 | **0.2x** |
| wash_ring_06 | 4 | 2433 | 1434 | 1.7x |

Ring members are typically **less** connected than random pairs. The reason is mechanical:
within a t-second window a security with n trades produces O(n²) spurious opposite-side
pairs, and the pairs with the highest raw counts are simply the pairs of busiest accounts.
A ring's ~100 trades are drowned by background flow. Any design that thresholds on raw
co-trade counts is dead on arrival.

## Result 2 — a naive activity null model does not rescue it

Normalising by expected co-trades under independent Poisson trading,
`E[obs] = n_a · n_b · (2w/T) · P(opposite side)`, collapsed every scenario to ≈0.8 —
positives and hard negatives alike. The failure is that `T` spans 20 days while trading is
confined to sessions and is U-shaped within them, so the expectation is wildly overstated
and roughly proportional to the observation. **A null model has to respect the session
structure and the intraday intensity profile, not just the total span.** That is a Phase 4
design requirement, not a detail.

## Result 3 — reciprocity separates rings cleanly

`reciprocity(a,b) = min(a→b, b→a) / max(a→b, b→a)` over directed inferred edges, 90s window:

| scenario | kind | median reciprocity, in-group | other pairs |
|---|---|---|---|
| wash_ring_01..05 | POSITIVE | **0.96 – 1.00** | 0.46 – 0.62 |
| wash_ring_06 | POSITIVE | **0.81** | 0.57 |
| hn_mm_two_sided | hard neg | 0.32 – 0.56 | 0.46 – 0.53 |
| hn_liquid_crowding | hard neg | 0.00 – 0.39 | 0.42 – 0.48 |
| hn_event_comovement | hard neg | 0.53 – 0.65 | 0.49 – 0.61 |

This is what theory predicts and it holds empirically: a closed cycle where every member
ends flat produces near-perfectly balanced bidirectional flow, which ordinary trading —
including a market maker's genuinely two-sided book — does not. The market-maker hard
negative sits at 0.32–0.56 precisely because its flow, while bidirectional, is *unbalanced*
per counterparty and it ends the day net directional.

## Result 4 — coordinated clusters are invisible to this graph entirely

Reciprocity for every coordinated-cluster scenario: **0.00**. Obviously so, in hindsight —
those scenarios are directionally unanimous, so an edge definition requiring *opposite*
sides cannot form a single within-group edge.

**Consequence for Phase 4: the two positive typologies need two different graph
constructions, not one graph with two thresholds.**

- Wash rings → directed, opposite-side, tight window; scored on reciprocity, cycle
  structure and per-member net position ≈ 0.
- Coordinated clusters → undirected, **same-side**, synchronous co-occurrence in a thin
  name; scored on group size, time concentration and absence of prior history in the
  security.

Collapsing both into one edge type would have produced a detector that catches one
typology and silently scores zero on the other, and the aggregate recall number would have
hidden it. This is the single most useful thing the probe found.

## Carried into Phase 4

1. Do not threshold raw co-trade counts.
2. The null model must respect session and intraday structure.
3. Reciprocity is the primary wash-ring discriminator; net-position-flat and
   single-security concentration are the confirmatory ones.
4. Build two graphs. Report recall per typology, never only in aggregate.
5. Window sensitivity (30s / 90s / 5min / 4h / 1d) still needs a real sweep — the probe
   used a single 90s window and one arbitrary choice is not a justification.
