from dataclasses import dataclass
from datetime import datetime
import math

from app.risk_engine import (
    RiskConfig,
    RiskState,
    calculate_position_size,
    check_trade_allowed,
)

from app.trade_logger import log_trade


@dataclass
class PaperPosition:
    symbol: str
    quantity: int
    entry_price: float
    stop_loss: float
    entry_time: datetime
    context: dict | None = None


class PaperTrader:
    def __init__(self, config=None, state=None):
        self.config = config or RiskConfig()
        self.state = state or RiskState()
        self.position = None
        self.trades = []

    def open_position(
        self,
        symbol,
        entry_price,
        stop_loss,
        context=None,
    ):
        allowed, reason = check_trade_allowed(
            self.config,
            self.state,
        )

        if not allowed:
            print("Paper trade rejected:", reason)
            return None

        if self.position is not None:
            print("Paper trade rejected: position already open")
            return None

        try:
            if not math.isfinite(entry_price) or not math.isfinite(stop_loss):
                raise ValueError("Entry and stop-loss must be finite")
            position_size = calculate_position_size(
                self.config,
                entry_price,
                stop_loss,
            )
        except (TypeError, ValueError) as error:
            print("Paper trade rejected: invalid prices:", error)
            return None

        if position_size.quantity <= 0:
            print(
                "Paper trade rejected: insufficient capital "
                "or risk limit prevents a whole-share position"
            )
            return None

        self.position = PaperPosition(
            symbol=symbol,
            quantity=position_size.quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            entry_time=datetime.now(),
            context=context or {},
        )

        self.state.open_positions += 1
        self.state.trades_today += 1

        print()
        print("===== PAPER BUY =====")
        print("Symbol:", symbol)
        print("Quantity:", position_size.quantity)
        print("Entry price:", entry_price)
        print("Stop-loss:", stop_loss)
        print("Planned risk:", round(position_size.planned_risk, 2))
        print("Capital required:", round(position_size.capital_required, 2))

        return self.position

    def close_position(self, exit_price, reason, context=None):
        if self.position is None:
            print("No paper position is currently open")
            return None

        if not math.isfinite(exit_price) or exit_price <= 0:
            raise ValueError("Exit price must be greater than zero")

        position = self.position

        pnl = (
            exit_price - position.entry_price
        ) * position.quantity

        trade = {
            "symbol": position.symbol,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "stop_loss": position.stop_loss,
            "pnl": pnl,
            "reason": reason,
            "entry_time": position.entry_time,
            "exit_time": datetime.now(),
        }

        incoming_context = context or {}
        context = dict(position.context or {})
        context.update({key: value for key, value in incoming_context.items() if value is not None})
        if context:
            trade.update({
                "ai_decision": context.get("ai_decision"),
                "ai_confidence": context.get("ai_confidence"),
                "ai_reason": context.get("ai_reason"),
                "ema_9": context.get("ema_9"),
                "ema_20": context.get("ema_20"),
                "rsi_14": context.get("rsi_14"),
                "macd": context.get("macd"),
                "macd_signal": context.get("macd_signal"),
                "macd_histogram": context.get("macd_histogram"),
                "volume": context.get("volume"),
                "volume_ma_20": context.get("volume_ma_20"),
                "strategy_result": context.get("strategy_result"),
                "strategy_reason": context.get("strategy_reason"),
                "risk_result": context.get("risk_result"),
            })

        self.trades.append(trade)

        log_trade(trade)

        self.state.open_positions -= 1

        if pnl < 0:
            self.state.daily_loss += abs(pnl)
        self.config.capital = max(0.0, self.config.capital + pnl)

        self.position = None

        print()
        print("===== PAPER SELL =====")
        print("Symbol:", trade["symbol"])
        print("Quantity:", trade["quantity"])
        print("Entry price:", trade["entry_price"])
        print("Exit price:", trade["exit_price"])
        print("P&L:", round(trade["pnl"], 2))
        print("Reason:", trade["reason"])

        return trade

    def check_stop_loss(self, current_price, context=None):
        if self.position is None:
            return None

        if current_price <= self.position.stop_loss:
            print()
            print("===== STOP-LOSS TRIGGERED =====")
            print("Symbol:", self.position.symbol)
            print("Current price:", current_price)
            print("Stop-loss:", self.position.stop_loss)

            return self.close_position(
                exit_price=current_price,
                reason="Stop-loss triggered",
                context=context,
            )

        return None
