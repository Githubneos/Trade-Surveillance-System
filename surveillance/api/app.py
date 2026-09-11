"""The serving API.

Deliberately a different application from the evaluation explorer. The explorer reads
ground-truth labels so a human can audit what was planted; this one has no access to them
at all. Keeping them as separate apps is what makes that claim structural rather than a
matter of discipline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from surveillance.api.schemas import (
    AccountOut,
    AlertDetail,
    AlertOut,
    SecurityOut,
    StatsOut,
    TradeOut,
)
from surveillance.db.session import session_scope

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

ALERT_SELECT = """
    SELECT a.id, a.alert_type::text, a.severity::text, a.score, a.detection_method,
           a.status::text, a.window_start, a.window_end, a.created_at, a.resolved_at,
           a.details,
           (SELECT count(*) FROM alert_trades t WHERE t.alert_id = a.id) AS n_trades,
           (SELECT count(*) FROM alert_accounts c WHERE c.alert_id = a.id) AS n_accounts,
           (SELECT array_agg(DISTINCT ac.name) FROM alert_accounts c
              JOIN accounts ac ON ac.id = c.account_id WHERE c.alert_id = a.id) AS accounts,
           (SELECT array_agg(DISTINCT s.ticker) FROM alert_trades t
              JOIN trades tr ON tr.id = t.trade_id
              JOIN securities s ON s.id = tr.security_id WHERE t.alert_id = a.id) AS tickers
    FROM alerts a
"""


class AlertHub:
    """Fan-out for live alerts.

    Each connection gets its own bounded queue rather than sharing one. A slow or stalled
    browser then drops its own messages instead of blocking the publisher and stalling
    every other viewer -- backpressure isolated to the client that caused it.
    """

    def __init__(self, maxsize: int = 100) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    async def connect(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._queues.add(q)
        return q

    def disconnect(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    async def publish(self, payload: dict) -> None:
        for q in list(self._queues):
            # Drop for this subscriber only. A live feed that blocks is worse than one that
            # skips, and the table refetches on reconnect anyway.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(payload)

    @property
    def subscribers(self) -> int:
        return len(self._queues)


def _row_to_alert(row) -> dict:
    d = dict(row._mapping)
    d["score"] = float(d["score"])
    d["accounts"] = list(d.get("accounts") or [])
    d["tickers"] = list(d.get("tickers") or [])
    d["detection_method"] = list(d.get("detection_method") or [])
    details = d.pop("details", {}) or {}
    d["summary"] = details.get("rule", "")
    return d, details


def create_app() -> FastAPI:
    hub = AlertHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = hub
        yield

    app = FastAPI(title="Trade Surveillance API", docs_url="/api/docs", lifespan=lifespan)

    @app.get("/api/config")
    def config() -> dict:
        """Where the frontend should look for the evaluation explorer.

        The explorer is a separate application on its own port, so the dashboard cannot
        assume same-origin. Serving the location rather than hardcoding it keeps the two
        deployable independently -- and keeps the API free of any label-bearing route.
        """
        import os

        return {
            "explorer_url": os.environ.get("SURV_EXPLORER_URL", "http://localhost:8011"),
        }

    @app.get("/api/stats", response_model=StatsOut)
    def stats() -> StatsOut:
        with session_scope() as s:
            counts = s.execute(
                text(
                    "SELECT (SELECT count(*) FROM trades), (SELECT count(*) FROM accounts),"
                    " (SELECT count(*) FROM securities), (SELECT count(*) FROM alerts),"
                    " (SELECT count(*) FROM alerts WHERE status = 'open')"
                )
            ).one()
            by_sev = dict(
                s.execute(
                    text("SELECT severity::text, count(*) FROM alerts GROUP BY 1")
                ).all()
            )
            by_type = dict(
                s.execute(
                    text("SELECT alert_type::text, count(*) FROM alerts GROUP BY 1")
                ).all()
            )
            methods: dict[str, int] = defaultdict(int)
            for (arr,) in s.execute(text("SELECT detection_method FROM alerts")).all():
                for m in arr or []:
                    methods[m] += 1
            window = s.execute(
                text("SELECT min(executed_at), max(executed_at) FROM trades")
            ).one()
        return StatsOut(
            trades=counts[0], accounts=counts[1], securities=counts[2],
            alerts=counts[3], open_alerts=counts[4],
            by_severity=by_sev, by_type=by_type, by_method=dict(methods),
            window=[
                str(window[0])[:10] if window[0] else "",
                str(window[1])[:10] if window[1] else "",
            ],
        )

    @app.get("/api/alerts", response_model=list[AlertOut])
    def alerts(
        severity: str | None = None,
        alert_type: str | None = None,
        status: str | None = None,
        method: str | None = None,
        limit: int = Query(200, le=1000),
        offset: int = 0,
    ) -> list[AlertOut]:
        clauses, params = [], {"limit": limit, "offset": offset}
        if severity:
            clauses.append("a.severity::text = :severity")
            params["severity"] = severity
        if alert_type:
            clauses.append("a.alert_type::text = :alert_type")
            params["alert_type"] = alert_type
        if status:
            clauses.append("a.status::text = :status")
            params["status"] = status
        if method:
            clauses.append(":method = ANY(a.detection_method)")
            params["method"] = method
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"{ALERT_SELECT} {where} ORDER BY a.score DESC, a.id LIMIT :limit OFFSET :offset"
        with session_scope() as s:
            rows = s.execute(text(sql), params).all()
        return [AlertOut(**_row_to_alert(r)[0]) for r in rows]

    @app.get("/api/alerts/{alert_id}", response_model=AlertDetail)
    def alert_detail(alert_id: int) -> AlertDetail:
        with session_scope() as s:
            row = s.execute(
                text(f"{ALERT_SELECT} WHERE a.id = :id"), {"id": alert_id}
            ).one_or_none()
            if row is None:
                raise HTTPException(404, f"no alert {alert_id}")
            base, details = _row_to_alert(row)
            trades = s.execute(
                text(
                    "SELECT tr.external_id, tr.account_id, ac.name AS account_name,"
                    " tr.security_id, se.ticker, tr.side::text, tr.quantity, tr.price,"
                    " tr.notional, tr.executed_at, tr.venue"
                    " FROM alert_trades at JOIN trades tr ON tr.id = at.trade_id"
                    " JOIN accounts ac ON ac.id = tr.account_id"
                    " JOIN securities se ON se.id = tr.security_id"
                    " WHERE at.alert_id = :id ORDER BY tr.executed_at LIMIT 500"
                ),
                {"id": alert_id},
            ).all()
            # For graph alerts, the network neighbourhood: who else these accounts traded
            # alongside in the same instrument and window.
            neighbourhood = s.execute(
                text(
                    "SELECT ac.name, ac.account_type::text AS account_type,"
                    " count(*) AS trades, sum(tr.notional) AS notional,"
                    " sum(CASE WHEN tr.side = 'buy' THEN 1 ELSE -1 END) AS net_side"
                    " FROM alert_accounts aa"
                    " JOIN accounts ac ON ac.id = aa.account_id"
                    " JOIN trades tr ON tr.account_id = aa.account_id"
                    " JOIN alerts al ON al.id = aa.alert_id"
                    " WHERE aa.alert_id = :id"
                    "   AND tr.executed_at BETWEEN al.window_start AND al.window_end"
                    " GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40"
                ),
                {"id": alert_id},
            ).all()
        return AlertDetail(
            **base,
            details=details,
            trades=[TradeOut(**dict(t._mapping)) for t in trades],
            neighbourhood=[
                {k: (float(v) if hasattr(v, "as_integer_ratio") else v)
                 for k, v in dict(n._mapping).items()}
                for n in neighbourhood
            ],
        )

    @app.post("/api/alerts/{alert_id}/status")
    async def set_status(alert_id: int, status: str = Query(...)) -> dict:
        if status not in {"open", "cleared", "escalated"}:
            raise HTTPException(400, "status must be open, cleared or escalated")
        with session_scope() as s:
            updated = s.execute(
                text(
                    "UPDATE alerts SET status = CAST(:st AS alert_status),"
                    " resolved_at = CASE WHEN :st = 'open' THEN NULL ELSE now() END"
                    " WHERE id = :id RETURNING id"
                ),
                {"id": alert_id, "st": status},
            ).one_or_none()
        if updated is None:
            raise HTTPException(404, f"no alert {alert_id}")
        await hub.publish({"event": "status", "alert_id": alert_id, "status": status})
        return {"id": alert_id, "status": status}

    @app.get("/api/accounts/{account_id}", response_model=AccountOut)
    def account(account_id: int) -> AccountOut:
        with session_scope() as s:
            row = s.execute(
                text(
                    "SELECT id, external_ref, name, account_type::text,"
                    " risk_tier::text FROM accounts WHERE id = :id"
                ),
                {"id": account_id},
            ).one_or_none()
        if row is None:
            raise HTTPException(404, f"no account {account_id}")
        return AccountOut(**dict(row._mapping))

    @app.get("/api/accounts/{account_id}/trades", response_model=list[TradeOut])
    def account_trades(account_id: int, limit: int = Query(200, le=1000)) -> list[TradeOut]:
        with session_scope() as s:
            rows = s.execute(
                text(
                    "SELECT tr.external_id, tr.account_id, ac.name AS account_name,"
                    " tr.security_id, se.ticker, tr.side::text, tr.quantity, tr.price,"
                    " tr.notional, tr.executed_at, tr.venue FROM trades tr"
                    " JOIN accounts ac ON ac.id = tr.account_id"
                    " JOIN securities se ON se.id = tr.security_id"
                    " WHERE tr.account_id = :id ORDER BY tr.executed_at DESC LIMIT :limit"
                ),
                {"id": account_id, "limit": limit},
            ).all()
        return [TradeOut(**dict(r._mapping)) for r in rows]

    @app.get("/api/securities", response_model=list[SecurityOut])
    def securities() -> list[SecurityOut]:
        with session_scope() as s:
            rows = s.execute(
                text(
                    "SELECT id, ticker, name, sector, liquidity_tier::text"
                    " FROM securities ORDER BY ticker"
                )
            ).all()
        return [SecurityOut(**dict(r._mapping)) for r in rows]

    @app.websocket("/ws/alerts")
    async def ws_alerts(ws: WebSocket):
        await ws.accept()
        q = await hub.connect()
        try:
            await ws.send_text(json.dumps({"event": "ready"}))
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20.0)
                except TimeoutError:
                    # Keepalive. Idle WebSockets are reaped by proxies within a minute or
                    # two, and a dashboard that silently stops updating is worse than one
                    # that visibly reconnects.
                    await ws.send_text(json.dumps({"event": "ping"}))
                    continue
                await ws.send_text(json.dumps(payload, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(q)

    _mount_frontend(app)
    return app


MISSING_BUILD = (
    "<!doctype html><meta charset=utf-8><title>Build the frontend</title>"
    "<body style='font:15px system-ui;padding:40px'><h1>Frontend not built</h1>"
    "<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>"
    "<p>The API is up regardless: <a href='/api/docs'>/api/docs</a></p>"
)


def _mount_frontend(app: FastAPI) -> None:
    index = FRONTEND_DIST / "index.html"
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index_route():
        if not index.exists():
            return HTMLResponse(MISSING_BUILD, status_code=503)
        return FileResponse(index)

    @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
    def spa(path: str):
        # /explorer/* belongs to the other application. Falling through to index.html
        # would hand HTML to a JSON fetch, turning a wrong-port mistake into a confusing
        # parse error instead of an honest 404.
        if path.startswith(("api/", "ws/", "explorer/")):
            raise HTTPException(404, "not found")
        candidate = FRONTEND_DIST / path
        if candidate.is_file():
            return FileResponse(candidate)
        if not index.exists():
            return HTMLResponse(MISSING_BUILD, status_code=503)
        return FileResponse(index)
