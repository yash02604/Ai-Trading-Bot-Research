import math
from dataclasses import dataclass


@dataclass
class RiskConfig:
    capital: float = 1000.0
    max_risk_per_trade: float = 0.01
    max_daily_loss: float = 0.02
    max_positions: int = 1
    max_trades_per_day: int = 3

    def __post_init__(self):
        if not math.isfinite(self.capital) or self.capital <= 0:
            raise ValueError("Capital must be a positive finite value")
        if not math.isfinite(self.max_risk_per_trade) or not 0 < self.max_risk_per_trade <= 1:
            raise ValueError("max_risk_per_trade must be in (0, 1]")
        if not math.isfinite(self.max_daily_loss) or not 0 < self.max_daily_loss <= 1:
            raise ValueError("max_daily_loss must be in (0, 1]")
        if self.max_positions < 1 or self.max_trades_per_day < 1:
            raise ValueError("Position and daily trade limits must be positive")


@dataclass
class RiskState:
    daily_loss: float = 0.0
    open_positions: int = 0
    trades_today: int = 0


@dataclass
class PositionSize:
    quantity: int
    risk_per_share: float
    planned_risk: float
    capital_required: float


def calculate_max_trade_risk(config):
    return config.capital * config.max_risk_per_trade


def calculate_daily_loss_limit(config):
    return config.capital * config.max_daily_loss


def check_trade_allowed(config, state):
    if state.daily_loss < 0 or not math.isfinite(state.daily_loss):
        return False, "Invalid daily loss state"
    if state.open_positions < 0 or state.trades_today < 0:
        return False, "Invalid trade state"
    max_trade_risk = calculate_max_trade_risk(config)
    daily_loss_limit = calculate_daily_loss_limit(config)

    if state.daily_loss >= daily_loss_limit:
        return False, "Daily loss limit reached"

    if state.open_positions >= config.max_positions:
        return False, "Maximum open positions reached"

    if state.trades_today >= config.max_trades_per_day:
        return False, "Maximum trades for today reached"

    return True, (
        f"Trade allowed. Maximum planned risk: "
        f"₹{max_trade_risk:.2f}"
    )


def calculate_position_size(config, entry_price, stop_loss_price):
    """
    Calculate whole-share position size for a long trade.

    The position is limited by both:
    1. Maximum allowed risk per trade.
    2. Available capital.

    No broker order is placed.
    """

    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("Entry price must be greater than zero")

    if not math.isfinite(stop_loss_price) or stop_loss_price <= 0:
        raise ValueError("Stop-loss price must be greater than zero")

    if stop_loss_price >= entry_price:
        raise ValueError(
            "For a long position, stop-loss must be below entry price"
        )

    risk_per_share = entry_price - stop_loss_price
    max_trade_risk = calculate_max_trade_risk(config)

    risk_based_quantity = math.floor(
        max_trade_risk / risk_per_share
    )

    capital_based_quantity = math.floor(
        config.capital / entry_price
    )

    quantity = min(
        risk_based_quantity,
        capital_based_quantity
    )

    planned_risk = quantity * risk_per_share
    capital_required = quantity * entry_price

    return PositionSize(
        quantity=quantity,
        risk_per_share=risk_per_share,
        planned_risk=planned_risk,
        capital_required=capital_required,
    )
