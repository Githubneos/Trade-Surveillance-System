# Design rationale

Why this system is built the way it is, with the measurements that settled each choice.
Every number below came from running the code, not from reasoning about it.

## Why two detection layers

The per-trade statistical layer and the graph layer catch disjoint typologies. That is not
a happy accident — the dataset was constructed to prove it.

Wash rings are planted so that **every trade sits inside the placing account's own normal
size distribution** (median z = +0.04 against a 0.24% background tail rate). Measured, the
per-trade layer catches 0% of them. The graph layer catches 100%.

If a single layer could do both, the second would be decoration. It cannot, and the
measurement is reproducible from `pytest tests/test_statistical.py tests/test_network_detection.py`.

## Why graph-based detection over pure statistical outlier detection

A statistical detector asks "is this trade unusual?". That question has no answer for
market abuse that is executed through *ordinary-looking trades*. A wash ring's individual
legs are unremarkable by design — the manipulation is in the relationship between them,
which is a property of the graph and not of any row in it.

The same applies to coordinated trading: each participant places one moderately-sized
order. Nothing about it is anomalous. What is anomalous is that eleven unconnected accounts
placed theirs in the same thin instrument within forty minutes.

Statistical detection remains necessary — it catches the size spikes and off-market prints
the graph layer is blind to — which is why the system runs both and reports which layer
caught what.

## Why per-typology rules instead of one anomaly score

A single global anomaly score forces unrelated typologies to compete for one threshold, and
they do not compete fairly. Features have different tail weights: a size spike reaches the
99.999th percentile of its own feature while out-of-pattern timing reaches the 99.8th, so
size always wins.

Measured, a global score needed a **5% flag rate — 9,358 alerts — before it caught even
half the timing cases**. Per-rule budgets against each feature's own tail fixed that. It is
also what real venues do: named scenarios with independently tuned parameters, not one
score. A useful side effect is that every alert is natively explainable, because the rule
that fired is the reason it fired.

## Why isolation forest is implemented but not primary

| Scorer | Average precision (per-trade typologies) |
|---|---|
| Rank-based tail rule | **0.374** |
| Isolation forest | 0.107 |

The forest loses, and the reason is structural rather than a tuning failure: the planted
anomalies are **single-axis** — a size spike is extreme in size and ordinary in everything
else. Isolation forest isolates points by random axis-aligned splits, which is efficient
for joint/multivariate anomalies and inefficient for a point extreme on one of six axes.

It is kept in the codebase and reported because being able to say *why* the fashionable
model lost is more useful than quietly not trying it.

## Why two graph constructions, not one

A feasibility probe (`graph-layer-feasibility.md`) run before implementation established
that one graph cannot serve both typologies:

- Wash rings are **reciprocal** — they need directed, opposite-side edges.
- Coordinated clusters are **directionally unanimous** — they produce *zero* opposite-side
  edges and score 0.00 reciprocity. The ring graph cannot see them at all.

One graph with two thresholds would have caught rings, scored zero on clusters, and buried
that inside an aggregate recall number.

## Why raw co-trading counts are not used

Also from the probe: within a window of n trades a security generates O(n²) candidate
pairs, and the pairs with the highest raw counts are simply the pairs of busiest accounts.
Ring members scored **below** random pairs (0.2×–1.7×). Every edge weight is therefore
normalised against a session-aware null model.

The null model matters as much as the graph: a naive uniform-Poisson expectation over the
full multi-day span collapsed every scenario, abusive or not, to a lift of ≈0.8. Using each
security's **actually active** span absorbs overnight gaps and the intraday intensity
profile without modelling either explicitly.

## Window sensitivity

"Why this window, and what happens if you halve it" — measured, not asserted.
Reproduce with `surveillance/eval/sweeps.py`.

### Reciprocal (wash-ring) graph — default 90s at mid liquidity

| scale | window | pairs | alerts | wash-ring recall | precision | hard negs |
|---|---|---|---|---|---|---|
| 0.25× | 22s | 12,206 | 8 | 0.33 | 0.75 | 1 |
| 0.5× | 45s | 18,686 | 13 | 0.67 | 0.62 | 1 |
| **1.0×** | **90s** | **26,971** | **15** | **0.83** | **0.60** | **2** |
| 2.0× | 180s | 36,955 | 20 | 0.83 | 0.45 | 3 |
| 4.0× | 360s | 48,642 | 27 | 0.67 | 0.30 | 1 |

**Halving it costs 16 points of recall; quartering it costs 50.** Doubling buys no recall
and costs 15 points of precision plus an extra false positive. 90s sits on the knee. The
mechanism is physical: ring legs are placed 30s–5min apart, so a window much below a minute
simply misses the pairing, and one much above it admits unrelated flow.

### Same-side (coordinated-cluster) graph — default 600s at mid liquidity

| scale | window | pairs | alerts | coordinated recall | precision |
|---|---|---|---|---|---|
| 0.25× | 150s | 31,191 | 15 | 0.80 | 0.60 |
| 0.5× | 300s | 41,407 | 15 | 0.80 | 0.60 |
| **1.0×** | **600s** | **52,878** | **15** | **0.80** | **0.60** |
| 2.0× | 1200s | 64,773 | 15 | 0.80 | 0.60 |
| 4.0× | 2400s | 76,398 | 11 | 0.00 | 0.45 |

**Flat across a 16× range, then a cliff.** This is the more reassuring of the two results:
the parameter is not finely tuned, so the reported performance does not depend on having
guessed it well. Past 2400s the window exceeds the span of the planted clusters themselves
and the structure dissolves.

## Louvain resolution — where measurement contradicted me

| resolution | alerts | wash-ring recall | precision |
|---|---|---|---|
| 0.6 | 17 | **1.00** | 0.59 |
| 0.8 | 17 | **1.00** | 0.59 |
| **1.0** | **17** | **1.00** | **0.59** |
| 1.2 | 16 | 0.83 | 0.56 |
| 1.4 | 15 | 0.83 | 0.60 |
| 1.8 | 11 | 0.17 | 0.45 |
| 2.4 | 9 | 0.17 | 0.56 |

I originally set 1.4, reasoning that resolution above 1.0 favours smaller communities and a
three-account ring is a small community. **The sweep says the opposite.** In hindsight the
reasoning ignored that the graph is *already filtered*: there is no surrounding noise left
for a higher resolution to sharpen against, so raising it only fragments the ring itself
until the pieces fall below the minimum member count.

The default is now 1.0. This is exactly the case for sweeping parameters rather than
arguing about them.

## Why Louvain for rings and connected components for clusters

Different structures need different primitives.

A **ring hides inside** a security's ordinary trading — its members also trade that name
with everyone else — so the filtered graph is still a connected mass and the task is to
find tight sub-structure within it. That is what modularity optimisation is for.

A **cluster is already isolated** by the lift filter: a co-trade three orders of magnitude
above chance, in a name neither account otherwise touches, is not ordinary flow. The
surviving edges *are* the cluster. Running Louvain over them actively harmed results —
it shattered four of five real clusters below the minimum member count.

## Louvain vs alternatives

| Algorithm | Why not |
|---|---|
| **Louvain** (chosen) | No need to specify community count; tunable resolution; fast enough to sweep |
| Leiden | Strictly better — fixes Louvain's internally-disconnected-community flaw. Requires `leidenalg`, a native dependency. Mitigated here by re-checking connectivity and splitting any disconnected community |
| Spectral clustering | Requires the number of communities up front. Nobody knows how many rings are running this week |
| Label propagation | Fast but non-deterministic and unstable on sparse graphs; no resolution control |
| Raw connected components | Used for clusters, where filtering has done the separation. Insufficient for rings, which are embedded in connected ordinary flow |

## Thresholds that were wrong and were corrected by measurement

- **Security concentration ≥ 0.15** rejected five of six real rings. A controlled account
  still does its ordinary trading elsewhere, so a hundred-trade ring is a few percent of
  its owner's activity. Now 0.015, and a scoring input rather than a gate.
- **Worst-member net position** let a single bystander veto a genuine ring (wash_ring_01:
  worst member 0.54, every real member below 0.17). Community detection routinely attaches
  one extra account. Now judged on the share of members who are flat.
- **Directional unanimity measured within the candidate** was vacuous: same-side edges only
  connect accounts trading the same direction, so every component is unanimous by
  construction. It let all three news-co-movement hard negatives through. Now measured
  across everyone trading the name in the window, which is the actual discriminator between
  a leak and an announcement.
