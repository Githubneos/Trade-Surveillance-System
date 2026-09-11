# Trade Surveillance System

Real-time detection of market abuse in equity trading, using two independent detection
layers: per-trade statistical anomaly scoring and graph-based network analysis.

Built to a constraint that shapes every design decision: **every number below was measured,
and the failures are reported alongside the successes.**

---

## The problem

Market abuse is mostly not committed through unusual-looking trades. A wash-trading ring
passes a position between controlled accounts in ordinary-sized lots at ordinary prices —
each individual trade is unremarkable, and the manipulation exists only in the *relationship
between* the trades. A detector that asks "is this trade unusual?" cannot answer that
question, no matter how good it is.

This system therefore runs two layers and reports which one caught what.

## Headline results

Measured against 51 deliberately planted abuse cases and 15 planted *look-alikes* hidden in
187,130 synthetic trades across 350 accounts:

| Typology | Cases | Detected | Found by |
|---|---|---|---|
| Wash trade ring | 6 | **100%** | Network |
| Order burst | 8 | **100%** | Per-trade |
| Size spike | 14 | **86%** | Per-trade |
| Coordinated cluster | 5 | **80%** | Network |
| Off-market price | 10 | **70%** | Per-trade |
| Out-of-pattern timing | 8 | **0%** | *nothing — see below* |

**37 of 51 cases (73%)** from **351 alerts on 187,130 trades (0.19%)**, at 20% alert
precision, with 5 of 15 hard negatives firing.

The row that matters most is the first one. Wash rings are planted so that every trade sits
inside the placing account's own normal size distribution — median z of **+0.04** against a
0.24% background rate. The per-trade layer catches **0%** of them, by construction. The
graph layer catches **100%**, without ever reading counterparty identity.

## Where it fails, and why

**Out-of-pattern timing: 0/8.** Not a threshold problem. Accounts with a concentrated
trading schedule legitimately trade off-schedule about 4% of the time, so a single
off-window trade carries almost no information. Measured, a global anomaly score needed a
5% flag rate — over 9,000 alerts — before catching even half of them. The honest conclusion
is that this typology needs *repeated* off-schedule activity to be separable, not better
tuning.

**5 of 15 hard negatives fire.** Three are crowded sessions in mega-caps and one is a
market maker's two-sided book. Each is listed by name in the scorecard rather than averaged
into a precision figure, because a planted look-alike that fires is a diagnosis, not a
rounding error.

**Isolation forest is implemented but is not the primary scorer.** It scored 0.107 average
precision against 0.374 for a rank-based rule. The planted anomalies are single-axis by
construction, and a forest needs several splits to isolate a point extreme on one of six
axes. Shipping the fashionable model over the one that measured better would have been the
wrong call.

## Architecture

```
Synthetic generator ─→ Redis Stream ─→ Consumer ─→ PostgreSQL
                                                       │
                              ┌────────────────────────┴──────────────────┐
                              │                                           │
                   Per-trade rules                            Two inferred graphs
              (size · price · timing · burst)      (reciprocal → Louvain → ring rules)
                              │                    (same-side → components → cluster rules)
                              └────────────────────┬──────────────────────┘
                                                   │
                                        Fusion (severity · attribution)
                                                   │
                                     FastAPI ──WebSocket──→ React dashboard
```

Full diagram and rationale: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/design-rationale.md`](docs/design-rationale.md)

## Running it

```bash
docker compose up --build          # full stack: api :8000, explorer :8011
```

Or locally:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
createdb surveillance && .venv/bin/alembic upgrade head
.venv/bin/python -m surveillance.cli generate   # synthesise + load
.venv/bin/python -m surveillance.cli detect     # detect + scorecard
.venv/bin/python -m surveillance.cli serve      # dashboard on :8000
```

## What makes the evaluation trustworthy

A synthetic dataset can produce any precision figure you like by planting obvious
anomalies. Four structural choices prevent that here:

1. **The evaluation contract was fixed before any detector existed.**
   ([`docs/evaluation-contract.md`](docs/evaluation-contract.md)) A matching rule chosen
   after seeing detector output is a free parameter you can tune until the numbers look
   good. Its one arbitrary constant — account-overlap ≥ 0.5 — is swept in the scorecard:
   the result is 37/51 at *every* threshold from 0.10 to 0.90.

2. **Detectors cannot see the answers.** Ground truth never enters the database.
   `trades.counterparty_account_id` exists for display but no detection module may read it
   — enforced by a test that parses the AST of every module. The graph layer *infers*
   relationships from co-trading, which is what a lit-venue system actually sees.

3. **Hard negatives are planted deliberately**, each targeting one specific way a detector
   cheats: base-rate blindness, keying on bidirectional flow, absolute rather than relative
   size, and confusing an information leak with a public announcement.

4. **Difficulty is measured, not assumed.** The dataset explorer reports each case's
   separability, so "this anomaly is hidden" is a number rather than a claim.

## Engineering

- **Exactly-once ingestion** — at-least-once delivery plus an idempotent write. Verified by
  SIGKILLing a consumer mid-write and asserting no trade is lost or duplicated.
- **84 tests**, including causality tests that fail if a feature reads the future, and a
  test that strips the counterparty column entirely and asserts identical results.
- **Parameters swept, not guessed** — window sizes and Louvain resolution are chosen from
  measured sweeps. One sweep contradicted my own reasoning and changed the default.
- Docker Compose, GitHub Actions CI against real Postgres and Redis, Alembic migrations.

## Honest limitations

Read [`docs/limitations.md`](docs/limitations.md) before drawing conclusions. The largest:
planted abuse is **0.9% of trades against under 0.001% in reality**, so precision here is
optimistic by roughly three orders of magnitude. There is no order-level data, so spoofing
and layering — two of the most important real typologies — are not modelled at all. Prices
are geometric Brownian motion with no jumps or fat tails.
