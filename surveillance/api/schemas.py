"""Response models for the serving API.

Note what is absent: there is no scenario id, no label, no ground truth of any kind. This
application is the production surveillance API, and the evaluation-side explorer in
``surveillance/eval/explorer.py`` is a separate app precisely so that this one can never
reach for the answers.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    external_ref: str
    name: str
    account_type: str
    risk_tier: str


class SecurityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticker: str
    name: str
    sector: str
    liquidity_tier: str


class TradeOut(BaseModel):
    external_id: str
    account_id: int
    account_name: str | None = None
    security_id: int
    ticker: str | None = None
    side: str
    quantity: float
    price: float
    notional: float
    executed_at: datetime
    venue: str


class AlertOut(BaseModel):
    id: int
    alert_type: str
    severity: str
    score: float
    detection_method: list[str]
    status: str
    window_start: datetime | None
    window_end: datetime | None
    created_at: datetime
    resolved_at: datetime | None = None
    n_trades: int
    n_accounts: int
    accounts: list[str] = []
    tickers: list[str] = []
    summary: str = ""


class AlertDetail(AlertOut):
    details: dict
    trades: list[TradeOut] = []
    neighbourhood: list[dict] = []


class StatsOut(BaseModel):
    trades: int
    accounts: int
    securities: int
    alerts: int
    open_alerts: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    by_method: dict[str, int]
    window: list[str]
