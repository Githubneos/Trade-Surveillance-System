"""Domain enums. These map to native Postgres enum types so the database itself rejects
invalid states rather than trusting application-level validation."""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    RETAIL = "retail"
    INSTITUTIONAL = "institutional"
    HEDGE_FUND = "hedge_fund"
    MARKET_MAKER = "market_maker"
    PROP_DESK = "prop_desk"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LiquidityTier(StrEnum):
    LIQUID = "liquid"
    MID = "mid"
    ILLIQUID = "illiquid"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AlertType(StrEnum):
    STATISTICAL_OUTLIER = "statistical_outlier"
    WASH_TRADE_RING = "wash_trade_ring"
    COORDINATED_CLUSTER = "coordinated_cluster"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    CLEARED = "cleared"
    ESCALATED = "escalated"


class CommunityEventType(StrEnum):
    COMMUNITY_FORMED = "community_formed"
    COMMUNITY_DISSOLVED = "community_dissolved"
    DENSITY_SPIKE = "density_spike"
    NEW_PAIR = "new_pair"
