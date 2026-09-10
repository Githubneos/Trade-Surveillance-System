"""Local dataset explorer -- an EVALUATION tool, not the production API.

This lives under ``surveillance/eval/`` deliberately. It reads ground-truth labels so a
human can inspect what was planted and how well hidden it is, which is exactly the access
the serving path must never have. The Phase 6 API (``surveillance/api/``) is a separate
application with no label access at all.

Run with:  python -m surveillance.cli serve --port 8000
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from surveillance.config import get_settings
from surveillance.db.session import session_scope
from surveillance.eval.dataset_report import Z_THRESHOLD, account_baselines
from surveillance.generator.ground_truth import read_labels

STATIC_DIR = Path(__file__).resolve().parent / "static"


@lru_cache
def _dataset() -> tuple[pd.DataFrame, list]:
    settings = get_settings()
    if not settings.trades_path.exists():
        raise RuntimeError(
            f"{settings.trades_path} not found -- run 'python -m surveillance.cli generate'"
        )
    trades = pd.read_parquet(settings.trades_path)
    trades["notional"] = trades["quantity"] * trades["price"]
    return trades, read_labels(settings.ground_truth_path)


@lru_cache
def _reference() -> dict:
    settings = get_settings()
    data = json.loads(settings.reference_path.read_text())
    return {
        "accounts": {a["id"]: a for a in data["accounts"]},
        "securities": {s["id"]: s for s in data["securities"]},
    }


@lru_cache
def _zframe() -> pd.DataFrame:
    """Every trade with its account-relative log-notional z-score attached."""
    trades, _ = _dataset()
    base = account_baselines(trades)
    j = trades.join(base, on="account_id", how="left")
    j["z"] = (np.log(j["notional"]) - j["log_mu"]) / j["log_sd"]
    return j


def create_app() -> FastAPI:
    app = FastAPI(title="Trade Surveillance -- Dataset Explorer", docs_url="/api/docs")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text()

    @app.get("/api/stats")
    def stats() -> dict:
        trades, labels = _dataset()
        z = _zframe()
        planted = trades["scenario_id"].notna()
        bg_z = z.loc[~planted, "z"].dropna()

        db_rows = None
        try:
            with session_scope() as s:
                db_rows = int(s.execute(text("select count(*) from trades")).scalar())
        except Exception:
            db_rows = None

        return {
            "trades": int(len(trades)),
            "background": int((~planted).sum()),
            "planted": int(planted.sum()),
            "planted_pct": float(planted.mean()),
            "accounts": int(trades["account_id"].nunique()),
            "securities": int(trades["security_id"].nunique()),
            "days": int(pd.to_datetime(trades["executed_at"]).dt.date.nunique()),
            "window": [
                str(trades["executed_at"].min())[:10],
                str(trades["executed_at"].max())[:10],
            ],
            "median_notional": float(trades["notional"].median()),
            "total_notional": float(trades["notional"].sum()),
            "scenarios": len(labels),
            "background_tail_rate": float(np.mean(np.abs(bg_z) > Z_THRESHOLD)),
            "db_trades": db_rows,
        }

    @app.get("/api/scenarios")
    def scenarios() -> list[dict]:
        trades, labels = _dataset()
        z = _zframe()
        planted = trades["scenario_id"].notna()
        bg_rate = float(np.mean(np.abs(z.loc[~planted, "z"].dropna()) > Z_THRESHOLD))

        out = []
        for label in labels:
            zz = z.loc[z["scenario_id"] == label.scenario_id, "z"].dropna()
            rate = float(np.mean(np.abs(zz) > Z_THRESHOLD)) if len(zz) else None
            out.append(
                {
                    "scenario_id": label.scenario_id,
                    "scenario_type": label.scenario_type,
                    "subtype": label.subtype,
                    "label": label.label,
                    "expected_layer": label.expected_layer,
                    "difficulty": label.difficulty,
                    "n_accounts": len(label.account_ids),
                    "n_trades": len(label.trade_external_ids),
                    "median_z": float(np.median(zz)) if len(zz) else None,
                    "size_sep": rate,
                    "background_tail_rate": bg_rate,
                    "window_start": label.window_start.isoformat(),
                    "window_end": label.window_end.isoformat(),
                    "notes": label.notes,
                }
            )
        return out

    @app.get("/api/scenarios/{scenario_id}")
    def scenario_detail(scenario_id: str) -> dict:
        _, labels = _dataset()
        z = _zframe()
        label = next((x for x in labels if x.scenario_id == scenario_id), None)
        if label is None:
            raise HTTPException(404, f"unknown scenario {scenario_id}")

        ref = _reference()
        rows = z[z["scenario_id"] == scenario_id].sort_values("executed_at")
        return {
            "scenario_id": label.scenario_id,
            "scenario_type": label.scenario_type,
            "subtype": label.subtype,
            "label": label.label,
            "expected_layer": label.expected_layer,
            "difficulty": label.difficulty,
            "notes": label.notes,
            "window_start": label.window_start.isoformat(),
            "window_end": label.window_end.isoformat(),
            "accounts": [
                {
                    "id": aid,
                    "external_ref": ref["accounts"].get(aid, {}).get("external_ref"),
                    "account_type": ref["accounts"].get(aid, {}).get("account_type"),
                }
                for aid in label.account_ids
            ],
            "securities": [
                {
                    "id": sid,
                    "ticker": ref["securities"].get(sid, {}).get("ticker"),
                    "liquidity_tier": ref["securities"].get(sid, {}).get("liquidity_tier"),
                }
                for sid in label.security_ids
            ],
            "trades": _serialise(rows.head(400), ref),
        }

    @app.get("/api/trades")
    def trades_endpoint(
        account_id: int | None = None,
        security_id: int | None = None,
        planted_only: bool = False,
        limit: int = Query(100, le=1000),
    ) -> list[dict]:
        z = _zframe()
        m = pd.Series(True, index=z.index)
        if account_id is not None:
            m &= z["account_id"] == account_id
        if security_id is not None:
            m &= z["security_id"] == security_id
        if planted_only:
            m &= z["scenario_id"].notna()
        return _serialise(z[m].head(limit), _reference())

    @app.get("/api/accounts/{account_id}")
    def account_detail(account_id: int) -> dict:
        z = _zframe()
        ref = _reference()
        if account_id not in ref["accounts"]:
            raise HTTPException(404, f"unknown account {account_id}")
        rows = z[z["account_id"] == account_id]
        bg = rows[rows["scenario_id"].isna()]
        return {
            **ref["accounts"][account_id],
            "n_trades": int(len(rows)),
            "n_planted": int(rows["scenario_id"].notna().sum()),
            "median_notional": float(bg["notional"].median()) if len(bg) else None,
            "p99_notional": float(bg["notional"].quantile(0.99)) if len(bg) else None,
            "scenarios": sorted(rows["scenario_id"].dropna().unique().tolist()),
            "recent_trades": _serialise(rows.tail(50), ref),
        }

    return app


def _serialise(rows: pd.DataFrame, ref: dict) -> list[dict]:
    out = []
    for r in rows.itertuples():
        out.append(
            {
                "external_id": r.external_id,
                "account_id": int(r.account_id),
                "account_ref": ref["accounts"].get(int(r.account_id), {}).get("external_ref"),
                "security_id": int(r.security_id),
                "ticker": ref["securities"].get(int(r.security_id), {}).get("ticker"),
                "liquidity_tier": ref["securities"]
                .get(int(r.security_id), {})
                .get("liquidity_tier"),
                "side": r.side,
                "quantity": float(r.quantity),
                "price": float(r.price),
                "notional": float(r.notional),
                "executed_at": str(r.executed_at),
                "venue": r.venue,
                "scenario_id": None if pd.isna(r.scenario_id) else r.scenario_id,
                "z": None if pd.isna(r.z) else round(float(r.z), 2),
            }
        )
    return out
