# Architecture

## System

```mermaid
flowchart TB
    subgraph gen["Data generation (offline)"]
        G["Synthetic generator<br/>personas · GBM prices · planted scenarios"]
        GT[("ground_truth.jsonl<br/>labels")]
        PQ[("trades.parquet")]
        G --> PQ
        G --> GT
    end

    subgraph ingest["Ingestion"]
        P["Producer<br/>replays in simulated time"]
        R[("Redis Stream<br/>consumer group")]
        C["Consumer<br/>XREADGROUP → COMMIT → XACK"]
        P --> R --> C
    end

    subgraph detect["Detection"]
        F["Feature engineering<br/>account-relative · causal"]
        S["Per-typology rules<br/>size · price · timing · burst"]
        GB["Graph builder<br/>inferred co-trading"]
        RG["Reciprocal graph<br/>→ Louvain → ring rules"]
        SG["Same-side graph<br/>→ components → cluster rules"]
        FU["Fusion<br/>severity · attribution · dedup"]
        F --> S --> FU
        GB --> RG --> FU
        GB --> SG --> FU
    end

    DB[("PostgreSQL<br/>trades · alerts · graph snapshots")]
    API["FastAPI serving API<br/>REST + WebSocket"]
    UI["React dashboard<br/>alert queue · detail drawer"]
    EXP["Evaluation explorer<br/>SEPARATE app"]
    EV["Scorecard<br/>precision · recall · attribution"]

    PQ --> P
    C --> DB
    DB --> F
    DB --> GB
    FU --> DB
    DB --> API --> UI
    GT -.-> EV
    DB --> EV
    GT -.-> EXP

    classDef truth fill:#fde,stroke:#c69,color:#333
    class GT,EXP truth
```

**The dotted lines matter.** Ground truth reaches only the evaluation harness and the
explorer — never the detectors, never the serving API. That boundary is enforced by
`tests/test_detection_isolation.py`, which parses the AST of every detection module, and
by `test_serving_api_exposes_no_ground_truth`.

## Why the explorer is a separate application

The explorer reads labels so a human can audit what was planted. The serving API must not.
Running them as one process would make that separation a matter of discipline; running them
as two applications makes it architectural. In Docker Compose they are separate services;
in development the Vite proxy routes the two path prefixes to different ports.

## Ingestion: how exactly-once actually works

```mermaid
sequenceDiagram
    participant R as Redis Stream
    participant C as Consumer
    participant P as Postgres

    C->>R: XREADGROUP (+ XAUTOCLAIM for orphans)
    R-->>C: batch
    C->>P: INSERT ... ON CONFLICT (external_id) DO NOTHING
    P-->>C: COMMIT
    C->>R: XACK

    Note over C,P: crash before COMMIT → stays pending → XAUTOCLAIM recovers it
    Note over C,R: crash after COMMIT, before XACK → redelivered → ON CONFLICT absorbs it
```

Redis Streams give **at-least-once** delivery. Combined with an **idempotent write** that
is enough, and it is the achievable version of the property — true exactly-once across two
systems needs a distributed transaction. Acking before committing would invert the
guarantee and silently lose a batch on every crash.

## Two detection layers, and why neither is redundant

| Typology | Per-trade layer | Graph layer |
|---|---|---|
| Size spike | **86%** | — |
| Off-market price | **70%** | — |
| Order burst | **100%** | — |
| Out-of-pattern timing | 0% | — |
| Wash ring | **0%** | **100%** |
| Coordinated cluster | **0%** | **80%** |

Wash rings are planted so every trade is ordinary for the account that placed it. The
per-trade layer cannot see them by construction, and the graph layer finds all of them
without ever reading counterparty identity. That contrast is the system's reason to exist.

## Services

| Service | Port | Purpose |
|---|---|---|
| `api` | 8000 | Serving API + dashboard. No ground-truth access |
| `explorer` | 8011 → 8001 | Evaluation-side dataset explorer. Reads labels, served under `/explorer/*` |
| `worker` | — | Redis Streams consumer → Postgres |
| `bootstrap` | — | One-shot: migrate, generate, load, detect |
| `postgres` | 55432 → 5432 | Trades, alerts, graph snapshots |
| `redis` | 56379 → 6379 | Trade stream |

Bring-up order is enforced with health checks and `depends_on: condition:` rather than
sleeps, so a fresh `docker compose up` lands on a populated system instead of an empty
dashboard that looks broken.
