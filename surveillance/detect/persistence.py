"""Persist fused alerts to Postgres.

Idempotent by construction: ``alerts.dedup_key`` is UNIQUE and inserts use ON CONFLICT, so
re-running detection over the same data updates scores in place rather than accumulating
duplicate findings. That matters operationally -- detection gets re-run after every
threshold change, and an alert queue that doubles each time is unusable.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from surveillance.detect.fusion import FusedAlert

UPSERT_ALERT = text(
    """
    INSERT INTO alerts
        (dedup_key, alert_type, severity, score, detection_method, status,
         primary_trade_id, window_start, window_end, details, created_at)
    VALUES
        (:dedup_key, CAST(:alert_type AS alert_type), CAST(:severity AS severity),
         :score, :detection_method, CAST('open' AS alert_status),
         (SELECT id FROM trades WHERE external_id = :primary_trade LIMIT 1),
         :window_start, :window_end, CAST(:details AS jsonb), now())
    ON CONFLICT (dedup_key) DO UPDATE SET
        score = EXCLUDED.score,
        severity = EXCLUDED.severity,
        detection_method = EXCLUDED.detection_method,
        details = EXCLUDED.details
    RETURNING id
    """
)


def persist_alerts(session: Session, alerts: list[FusedAlert]) -> dict[str, int]:
    import json

    counts = {"alerts": 0, "alert_trades": 0, "alert_accounts": 0}
    for alert in alerts:
        alert_id = session.execute(
            UPSERT_ALERT,
            {
                "dedup_key": alert.dedup_key,
                "alert_type": alert.alert_type.value,
                "severity": alert.severity.value,
                "score": float(alert.score),
                "detection_method": list(alert.detection_method),
                "primary_trade": alert.primary_trade_external_id,
                "window_start": alert.window_start,
                "window_end": alert.window_end,
                "details": json.dumps(alert.details),
            },
        ).scalar_one()
        counts["alerts"] += 1

        session.execute(
            text("DELETE FROM alert_trades WHERE alert_id = :aid"), {"aid": alert_id}
        )
        session.execute(
            text("DELETE FROM alert_accounts WHERE alert_id = :aid"), {"aid": alert_id}
        )
        if alert.trade_external_ids:
            session.execute(
                text(
                    "INSERT INTO alert_trades (alert_id, trade_id) "
                    "SELECT :aid, id FROM trades WHERE external_id = ANY(:ext) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"aid": alert_id, "ext": list(alert.trade_external_ids)},
            )
            counts["alert_trades"] += len(alert.trade_external_ids)
        for account_id in alert.account_ids:
            session.execute(
                text(
                    "INSERT INTO alert_accounts (alert_id, account_id, role) "
                    "VALUES (:aid, :acct, :role) ON CONFLICT DO NOTHING"
                ),
                {
                    "aid": alert_id,
                    "acct": int(account_id),
                    "role": "ring_member" if len(alert.account_ids) > 1 else "subject",
                },
            )
            counts["alert_accounts"] += 1
    return counts
