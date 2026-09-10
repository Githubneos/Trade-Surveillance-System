"""SQLAlchemy 2.0 typed models.

Design notes worth defending:

* ``trades.external_id`` is a UNIQUE business key supplied by the producer. It is the sole
  basis of the ingestion layer's exactly-once guarantee: delivery is at-least-once, and the
  write is idempotent (``ON CONFLICT (external_id) DO NOTHING``).
* ``trades.counterparty_account_id`` is GROUND-TRUTH METADATA ONLY. Real surveillance rarely
  has clean counterparty attribution on lit venues, and handing it to the graph layer would
  make wash-ring detection trivial. ``tests/test_detection_isolation.py`` enforces that no
  module under ``surveillance/detect/`` reads it.
* Alert<->trade and alert<->account are association tables rather than array columns, so
  "every alert touching account X" is an index scan instead of an array scan.
* Money is NUMERIC throughout. Never float.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from surveillance.db.enums import (
    AccountType,
    AlertStatus,
    AlertType,
    CommunityEventType,
    LiquidityTier,
    RiskTier,
    Severity,
    Side,
)


def pg_enum(python_enum: type, name: str) -> Enum:
    """Native Postgres enum keyed on the enum *value* (lowercase), not the member name."""
    return Enum(
        python_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
    )


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        pg_enum(AccountType, "account_type"), nullable=False
    )
    risk_tier: Mapped[RiskTier] = mapped_column(pg_enum(RiskTier, "risk_tier"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    trades: Mapped[list[Trade]] = relationship(
        back_populates="account", foreign_keys="Trade.account_id"
    )


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sector: Mapped[str] = mapped_column(String(64), nullable=False)
    # Load-bearing for detection: illiquid names host coordinated-cluster scenarios and
    # widen the co-trading time window; liquid names host the hard negatives.
    liquidity_tier: Mapped[LiquidityTier] = mapped_column(
        pg_enum(LiquidityTier, "liquidity_tier"), nullable=False
    )
    adv: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    trades: Mapped[list[Trade]] = relationship(back_populates="security")


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_account_executed", "account_id", "executed_at"),
        Index("ix_trades_security_executed", "security_id", "executed_at"),
        Index("ix_trades_executed_at", "executed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: Producer-supplied business key. Basis of idempotent ingestion.
    external_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    security_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[Side] = mapped_column(pg_enum(Side, "side"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    notional: Mapped[Decimal] = mapped_column(
        Numeric(28, 6), Computed("quantity * price", persisted=True)
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(16), nullable=False, default="XNAS")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="synthetic")
    #: GROUND TRUTH ONLY -- never read by anything under surveillance/detect/.
    counterparty_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    account: Mapped[Account] = relationship(back_populates="trades", foreign_keys=[account_id])
    security: Mapped[Security] = relationship(back_populates="trades")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_alerts_dedup_key"),
        Index("ix_alerts_created_at", "created_at"),
        Index("ix_alerts_type_status", "alert_type", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: Deterministic natural key so re-running detection is idempotent.
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    alert_type: Mapped[AlertType] = mapped_column(pg_enum(AlertType, "alert_type"), nullable=False)
    severity: Mapped[Severity] = mapped_column(pg_enum(Severity, "severity"), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    #: Which layer(s) fired: {'statistical'}, {'graph'}, or both.
    detection_method: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(
        pg_enum(AlertStatus, "alert_status"), nullable=False, default=AlertStatus.OPEN
    )
    primary_trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    trades: Mapped[list[AlertTrade]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )
    accounts: Mapped[list[AlertAccount]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class AlertTrade(Base):
    __tablename__ = "alert_trades"

    alert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True
    )
    trade_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("trades.id", ondelete="CASCADE"), primary_key=True
    )

    alert: Mapped[Alert] = relationship(back_populates="trades")


class AlertAccount(Base):
    __tablename__ = "alert_accounts"

    alert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    #: Role within the alert, e.g. 'ring_member', 'subject'.
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="subject")

    alert: Mapped[Alert] = relationship(back_populates="accounts")


class GraphSnapshot(Base):
    """One rolling-window graph build. Retained so the demo can answer 'how did the
    network change over time' rather than only showing the final state."""

    __tablename__ = "graph_snapshots"
    __table_args__ = (Index("ix_graph_snapshots_window", "window_start", "window_end"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_label: Mapped[str] = mapped_column(String(16), nullable=False)
    num_nodes: Mapped[int] = mapped_column(Integer, nullable=False)
    num_edges: Mapped[int] = mapped_column(Integer, nullable=False)
    num_communities: Mapped[int] = mapped_column(Integer, nullable=False)
    modularity: Mapped[float | None] = mapped_column(Numeric(8, 5), nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    events: Mapped[list[GraphCommunityEvent]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class GraphCommunityEvent(Base):
    __tablename__ = "graph_community_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("graph_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[CommunityEventType] = mapped_column(
        pg_enum(CommunityEventType, "community_event_type"), nullable=False
    )
    community_key: Mapped[str] = mapped_column(String(64), nullable=False)
    member_account_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    snapshot: Mapped[GraphSnapshot] = relationship(back_populates="events")
