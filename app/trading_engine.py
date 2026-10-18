"""Guarded quantitative -> risk -> AI -> paper execution pipeline."""

import logging

from app.ai_engine import analyze_market
from app.paper_trader import PaperTrader
from app.risk_engine import calculate_position_size, check_trade_allowed
from app.trade_logger import log_decision

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, paper_trader=None, ai_analyzer=None):
        self.paper_trader = paper_trader or PaperTrader()
        self.ai_analyzer = ai_analyzer or analyze_market

    def _context(self, market_data, ai_result=None, strategy_result=None, risk_result=None):
        context = dict(market_data)
        context["strategy_result"] = strategy_result
        context["strategy_reason"] = strategy_result
        context["risk_result"] = risk_result
        if ai_result:
            context.update({
                "ai_decision": ai_result.get("decision"),
                "ai_confidence": ai_result.get("confidence"),
                "ai_reason": ai_result.get("reason"),
            })
        return context

    def process_market(self, market_data, strategy_signal=False, strategy_reason="No reversal candidate"):
        """Evaluate one completed candle. This class never places broker orders."""
        def finish(result):
            try:
                log_decision(market_data, result)
            except OSError as error:
                logger.warning("Could not persist decision log (%s)", type(error).__name__)
            return result

        price = market_data.get("close")
        if self.paper_trader.position is not None:
            stopped = self.paper_trader.check_stop_loss(
                current_price=price,
                context=self._context(market_data),
            )
            if stopped:
                return finish({"decision": "STOP_LOSS", "reason": stopped["reason"]})

        if not strategy_signal:
            return finish({"decision": "HOLD", "reason": strategy_reason, "strategy": "NO_SIGNAL"})

        config, state = self.paper_trader.config, self.paper_trader.state
        allowed, reason = check_trade_allowed(config, state)
        if not allowed:
            return finish({"decision": "REJECTED", "reason": reason, "strategy": "BUY", "risk": reason})
        try:
            size = calculate_position_size(
                config, float(price), float(market_data["stop_loss"])
            )
        except (KeyError, TypeError, ValueError) as error:
            return finish({"decision": "REJECTED", "reason": f"Invalid trade: {error}", "strategy": "BUY"})
        if size.quantity <= 0:
            return finish({"decision": "REJECTED", "reason": "Risk/capital limits prevent a whole-share position", "strategy": "BUY"})

        try:
            ai_result = self.ai_analyzer(market_data)
        except Exception as error:
            logger.warning("AI context analysis failed (%s)", type(error).__name__)
            ai_result = {"decision": "HOLD", "confidence": 0.0, "reason": "AI unavailable; fail-safe HOLD"}
        if not isinstance(ai_result, dict) or ai_result.get("decision") not in {"BUY", "SELL", "HOLD"}:
            ai_result = {"decision": "HOLD", "confidence": 0.0, "reason": "Invalid AI result; fail-safe HOLD"}
        if ai_result["decision"] != "BUY":
            return finish({
                "decision": "HOLD", "reason": "AI context did not confirm the quantitative BUY",
                "strategy": "BUY", "risk": reason, "ai": ai_result,
            })
        position = self.paper_trader.open_position(
            symbol=market_data["symbol"], entry_price=float(price),
            stop_loss=float(market_data["stop_loss"]),
            context=self._context(
                market_data, ai_result=ai_result,
                strategy_result=strategy_reason, risk_result=reason,
            ),
        )
        if position is None:
            return finish({"decision": "REJECTED", "reason": "Paper trader rejected entry", "strategy": "BUY", "risk": reason, "ai": ai_result})
        return finish({"decision": "BUY", "reason": strategy_reason, "strategy": "BUY", "risk": reason, "ai": ai_result})
