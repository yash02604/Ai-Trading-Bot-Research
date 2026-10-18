"""Paper simulator. This module has no broker client or order-placement method."""
from datetime import datetime
import math

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from .contracts import Action
from .safety import GateResult, RiskLimits, RiskState


class PaperOnlyExecutor:
    def __init__(self, *, audit_ledger, risk_state: RiskState,
                 limits: RiskLimits | None = None,
                 cost_assumptions: CostAssumptions | None = None):
        self.audit = audit_ledger
        self.state = risk_state
        self.limits = limits or RiskLimits()
        self.costs = cost_assumptions or CostAssumptions()
        if self.costs.slippage_bps_per_side != self.limits.entry_slippage_bps:
            raise ValueError("paper slippage must match the independently approved risk assumption")
        self.open_positions: dict[str, dict] = {}

    def open_position(self, *, signal_id: str, decision, gate_result: GateResult,
                      recorded_at: datetime) -> dict:
        if not gate_result.approved or decision.action is not Action.BUY:
            raise ValueError("paper fill requires a Python-approved BUY")
        if signal_id in self.open_positions:
            raise ValueError("duplicate paper signal")
        reference = float(decision.entry_price)
        fill = reference * (1 + self.costs.slippage_bps_per_side / 10_000)
        stop = float(decision.stop_loss)
        stop_pct = (fill - stop) / fill * 100 if fill > stop else math.inf
        if not self.limits.min_stop_distance_pct <= stop_pct <= self.limits.max_stop_distance_pct:
            raise ValueError("slippage-adjusted stop is outside configured bounds")
        used_allocation = sum(
            float(position.get("allocation", position.get("quantity", 0) * position.get("entry_price", 0)))
            for position in self.state.open_positions.values()
        )
        available_capital = max(0.0, self.state.capital - used_allocation)
        allocation_cap = min(self.state.capital * self.limits.max_allocation, available_capital)
        risk_qty = math.floor(self.state.capital * self.limits.max_risk_per_trade / (fill - stop)) if fill > stop else 0
        allocation_qty = math.floor(allocation_cap / fill)
        quantity = min(gate_result.quantity, risk_qty, allocation_qty)
        if quantity < 1:
            raise ValueError("slippage-adjusted paper fill violates risk/allocation limits")
        position = {
            "paper_only": True, "signal_id": signal_id, "symbol": decision.symbol,
            "strategy_id": decision.strategy_id, "quantity": quantity,
            "reference_entry": reference, "entry_fill": fill,
            "entry_slippage": fill - reference, "stop_loss": stop,
            "target_price": float(decision.target_price), "holding_bars": decision.holding_bars,
            "entry_time": recorded_at.isoformat(), "exit_time": None, "exit_fill": None,
            "gross_pnl": None, "estimated_costs": None, "net_pnl": None,
        }
        self.open_positions[signal_id] = position
        self.state.open_positions[decision.symbol] = {
            "signal_id": signal_id, "quantity": quantity, "entry_price": fill,
            "allocation": quantity * fill,
        }
        self.state.trades_today += 1
        self.audit.append_event(signal_id, "PAPER_FILL", recorded_at.isoformat(), position)
        return dict(position)

    def close_position(self, *, position_signal_id: str, exit_signal_id: str,
                       reference_exit: float, recorded_at: datetime,
                       exit_reason: str) -> dict:
        if position_signal_id not in self.open_positions:
            raise ValueError("no matching open paper position")
        if not math.isfinite(reference_exit) or reference_exit <= 0:
            raise ValueError("exit reference price must be positive and finite")
        position = self.open_positions.pop(position_signal_id)
        exit_fill = reference_exit * (1 - self.costs.slippage_bps_per_side / 10_000)
        gross = (reference_exit - position["reference_entry"]) * position["quantity"]
        fees = estimate_round_trip_costs(
            position["entry_fill"], reference_exit, position["quantity"],
            recorded_at.date(), self.costs,
        )
        total_costs = fees["total_cost"]
        trade = {**position, "exit_time": recorded_at.isoformat(),
                 "reference_exit": reference_exit, "exit_fill": exit_fill,
                 "exit_slippage": reference_exit - exit_fill,
                 "gross_pnl": gross, "estimated_costs": total_costs,
                 "cost_breakdown": fees, "net_pnl": gross - total_costs,
                 "exit_reason": exit_reason, "exit_signal_id": exit_signal_id}
        self.state.open_positions.pop(position["symbol"], None)
        self.state.daily_loss += max(0.0, -trade["net_pnl"])
        self.state.capital += trade["net_pnl"]
        self.audit.append_event(position_signal_id, "PAPER_OUTCOME", recorded_at.isoformat(), trade)
        return trade

