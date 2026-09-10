"""Domain enums. These map to native Postgres enum types so the database itself rejects
invalid states rather than trusting application-level validation."""

from __future__ import annotations

import enum


class AccountType(str, enum.Enum):
    RETAIL = "retail"
    INSTITUTIONAL = "institutional"
    HEDGE_FUND = "hedge_fund"
    MARKET_MAKER = "market_maker"
    PROP_DESK = "prop_desk"


class RiskTier(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LiquidityTier(str, enum.Enum):
    LIQUID = "liquid"
    MID = "mid"
    ILLIQUID = "illiquid"


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class AlertType(str, enum.Enum):
    STATISTICAL_OUTLIER = "statistical_outlier"
    WASH_TRADE_RING = "wash_trade_ring"
    COORDINATED_CLUSTER = "coordinated_cluster"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    CLEARED = "cleared"
    ESCALATED = "escalated"


class CommunityEventType(str, enum.Enum):
    COMMUNITY_FORMED = "community_formed"
    COMMUNITY_DISSOLVED = "community_dissolved"
    DENSITY_SPIKE = "density_spike"
    NEW_PAIR = "new_pair"
