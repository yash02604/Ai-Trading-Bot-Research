"""Independent Python safety gate for AI recommendations; fails closed."""
from dataclasses import dataclass, field
from datetime import datetime, time
import math
from typing import Callable
from zoneinfo import ZoneInfo

from .contracts import Action, DecisionRequest, V2Recommendation, parse_timestamp, safe_hold
from .registry import StrategyRegistry

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade: float = 0.01
    max_allocation: float = 0.25
    max_daily_loss: float = 0.02
    max_open_positions: int = 1
    max_trades_per_day: int = 3
    min_stop_distance_pct: float = 0.05
    max_stop_distance_pct: float = 1.0
    max_target_distance_pct: float = 5.0
    min_holding_bars: int = 1
    max_holding_bars: int = 12
    max_data_age_seconds: int = 300

    def __post_init__(self):
        for field_name in ("max_risk_per_trade", "max_allocation", "max_daily_loss"):
            value = getattr(self, field_name)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{field_name} must be in (0, 1]")
        if self.max_open_positions < 1 or self.max_trades_per_day < 1:
            raise ValueError("position and daily trade limits must be positive")
        if not 0 < self.min_stop_distance_pct <= self.max_stop_distance_pct:
            raise ValueError("stop distance limits are invalid")
        if self.max_target_distance_pct <= 0 or not 1 <= self.min_holding_bars <= self.max_holding_bars:
            raise ValueError("target or holding limits are invalid")
        if self.max_data_age_seconds <= 0:
            raise ValueError("max_data_age_seconds must be positive")


@dataclass
class RiskState:
    capital: float
    day_start_capital: float
    daily_loss: float = 0.0
    trades_today: int = 0
    open_positions: dict[str, dict] = field(default_factory=dict)
    seen_signal_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class GateResult:
    approved: bool
    reason: str
    quantity: int = 0
    allocation: float = 0.0
    planned_risk: float = 0.0


class FileKillSwitch:
    """Presence of a configured file disables trading; absent path is invalid."""
    def __init__(self, path):
        self.path = path

    @property
    def enabled(self) -> bool:
        from pathlib import Path
        return bool(self.path) and Path(self.path).is_file()


class SafetyGate:
    def __init__(self, *, registry: StrategyRegistry, limits: RiskLimits,
                 session_validator: Callable[[datetime], bool], kill_switch,
                 state: RiskState):
        if session_validator is None or kill_switch is None:
            raise ValueError("session validator and kill switch are required")
        self.registry, self.limits = registry, limits
        self.session_validator, self.kill_switch, self.state = session_validator, kill_switch, state

    def approve(self, request: DecisionRequest, decision: V2Recommendation,
                *, now: datetime, signal_id: str) -> GateResult:
        try:
            request.validate(now=now, max_data_age_seconds=self.limits.max_data_age_seconds)
            current = parse_timestamp(now, "now")
            signal = parse_timestamp(request.signal_timestamp, "signal_timestamp")
            if current.astimezone(IST).date() != signal.astimezone(IST).date():
                return GateResult(False, "signal is not from the current market day")
            if not self.session_validator(signal):
                return GateResult(False, "market session is not validated")
            if self.kill_switch.enabled:
                return GateResult(False, "emergency kill switch is active")
            if not signal_id or signal_id in self.state.seen_signal_ids:
                return GateResult(False, "duplicate or missing signal ID")
            if not all(math.isfinite(v) and v >= 0 for v in
                       (self.state.capital, self.state.day_start_capital, self.state.daily_loss)):
                return GateResult(False, "account risk state is invalid")
            if decision.action is Action.HOLD:
                self.state.seen_signal_ids.add(signal_id)
                return GateResult(True, "HOLD recorded; no order parameters")
            if decision.symbol not in request.eligible_symbols:
                return GateResult(False, "symbol is outside the eligible universe")
            candidate = self.registry.require(decision.strategy_id or "")
            if decision.strategy_id not in request.strategy_ids:
                return GateResult(False, "strategy was not in the request")
            if not candidate.enabled_for_paper:
                return GateResult(False, "strategy is registered but not approved for paper execution")
            if decision.action is Action.EXIT:
                if decision.symbol not in self.state.open_positions:
                    return GateResult(False, "EXIT does not match an open paper position")
                if decision.entry_price is None or not math.isfinite(decision.entry_price) or decision.entry_price <= 0:
                    return GateResult(False, "EXIT requires a finite positive current reference price")
                self.state.seen_signal_ids.add(signal_id)
                return GateResult(True, "EXIT approved for the matching paper position")
            return self._approve_buy(decision, signal_id)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            return GateResult(False, f"safety validation failed: {str(error)[:180]}")

    def _approve_buy(self, decision: V2Recommendation, signal_id: str) -> GateResult:
        entry, stop, target = decision.entry_price, decision.stop_loss, decision.target_price
        if None in (entry, stop, target) or not all(math.isfinite(v) for v in (entry, stop, target)):
            return GateResult(False, "BUY requires finite entry, stop, and target")
        if entry <= 0 or not 0 < stop < entry or target <= entry:
            return GateResult(False, "BUY has invalid stop/target direction")
        stop_pct = (entry - stop) / entry * 100
        target_pct = (target - entry) / entry * 100
        if not self.limits.min_stop_distance_pct <= stop_pct <= self.limits.max_stop_distance_pct:
            return GateResult(False, "stop distance is outside configured bounds")
        if target_pct > self.limits.max_target_distance_pct:
            return GateResult(False, "target distance is outside configured bounds")
        if decision.holding_bars is None or not self.limits.min_holding_bars <= decision.holding_bars <= self.limits.max_holding_bars:
            return GateResult(False, "holding horizon is outside configured bounds")
        if self.state.daily_loss >= self.state.day_start_capital * self.limits.max_daily_loss:
            return GateResult(False, "daily loss limit reached")
        if self.state.trades_today >= self.limits.max_trades_per_day:
            return GateResult(False, "maximum trades per day reached")
        if len(self.state.open_positions) >= self.limits.max_open_positions:
            return GateResult(False, "maximum open positions reached")
        risk_budget = self.state.capital * self.limits.max_risk_per_trade
        allocation_cap = self.state.capital * self.limits.max_allocation
        risk_qty = math.floor(risk_budget / (entry - stop))
        allocation_qty = math.floor(allocation_cap / entry)
        quantity = min(risk_qty, allocation_qty)
        if quantity < 1:
            return GateResult(False, "risk or allocation limits do not permit one share")
        allocation = quantity * entry
        planned_risk = quantity * (entry - stop)
        self.state.seen_signal_ids.add(signal_id)
        return GateResult(True, "BUY approved by independent Python risk gate", quantity, allocation, planned_risk)


def regular_weekday_session(timestamp: datetime) -> bool:
    """Conservative clock rule only; production must supply an authoritative holiday/session calendar."""
    local = timestamp.astimezone(IST)
    return local.weekday() < 5 and time(9, 15) <= local.time() <= time(15, 30)
