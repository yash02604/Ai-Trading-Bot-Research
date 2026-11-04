"""Pure offline eligibility, not fill commitment or provider-depth authentication.

Synthetic evidence is the only supported depth-evidence kind in this version.
The caller supplies an already validated opportunity, an acceptance-time account
snapshot and a loaded offline calendar. No clock, transport or ledger is used.
"""
import hashlib
import json
import math
from datetime import datetime

import pandas as pd

from app.research.deepseek_prospective import _cost_aware_position_check
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from strategy_backtest import STOCKS

POLICY_ID = "prospective-observed-ask-v1"
HISTORICAL_POLICY_ID = "historical-candle-open-simulation"
TIMESTAMPS = {"market_observed_at", "opportunity_at", "provider_request_sent_at",
              "provider_response_received_at", "decision_ready_at", "earliest_entry_at",
              "entry_expires_at", "quote_request_sent_at", "quote_source_at",
              "quote_received_at", "fill_acceptance_at"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _stamp(value):
    if type(value) is str:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, (datetime, pd.Timestamp)):
        raise ValueError()
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError()
    return stamp.tz_convert("Asia/Kolkata")


def _result(code, **details):
    return {"policy_id": POLICY_ID, "eligible": code == "ELIGIBLE_SIMULATED_FILL",
            "reason_code": code, "validation_only": True, "taken": False,
            "simulated": True, "evidence_kind": "SYNTHETIC_FIXTURE",
            "settlement_supported": False, "trade_placement_enabled": False, **details}


def validate_paper_fill(*, policy, timestamps, proposal, quote, depth_evidence, account, calendar):
    """Deterministic eligibility only; never accepts/records an account trade.

    Evidence hashes bind synthetic inputs, not provider authorship. Real depth
    authentication and post-fill settlement are deliberately not implemented.
    """
    try:
        return _validate(policy, timestamps, proposal, quote, depth_evidence, account, calendar)
    except Exception:
        # Never echo arbitrary malformed inputs, credentials or exception text.
        return _result("INVALID_INPUT")


def _validate(policy, timestamps, proposal, quote, depth_evidence, account, calendar):
    if type(policy) is not dict or policy.get("policy_id") != POLICY_ID:
        return _result("UNSUPPORTED_POLICY")
    if (set(policy) != {"policy_id", "max_spread_bps", "max_quote_age_seconds"}
            or not _number(policy["max_spread_bps"]) or policy["max_spread_bps"] < 0
            or not _number(policy["max_quote_age_seconds"])
            or not 0 < policy["max_quote_age_seconds"] <= 5):
        return _result("INVALID_POLICY")
    if type(timestamps) is not dict or set(timestamps) != TIMESTAMPS:
        return _result("INVALID_TIMESTAMPS")
    if timestamps["quote_source_at"] is None:
        return _result("DEPTH_TIMESTAMP_MISSING")
    try:
        t = {key: _stamp(value) for key, value in timestamps.items()}
    except Exception:
        return _result("INVALID_TIMESTAMPS")
    if not (t["market_observed_at"] <= t["opportunity_at"] <= t["provider_request_sent_at"]
            < t["provider_response_received_at"] <= t["decision_ready_at"]
            < t["quote_request_sent_at"] <= t["quote_source_at"]
            <= t["quote_received_at"] <= t["fill_acceptance_at"]):
        return _result("CAUSAL_ORDER_INVALID")
    # Reuse the existing 900s bound; this does not replace candle validation.
    if (t["opportunity_at"] - t["market_observed_at"]).total_seconds() > 900:
        return _result("MARKET_OBSERVATION_STALE")
    if type(calendar) is not OfflineSessionCalendar:
        return _result("INVALID_CALENDAR")
    try:
        expected = t["opportunity_at"].floor("5min") + pd.Timedelta(minutes=5)
        if not calendar.entry_allowed(t["opportunity_at"]):
            return _result("UNSUPPORTED_SESSION")
        end = calendar.candle_end(expected)
        expiry = min(expected + pd.Timedelta(minutes=5), end - pd.Timedelta(minutes=5))
    except Exception:
        return _result("UNSUPPORTED_SESSION")
    if t["earliest_entry_at"] != expected or t["entry_expires_at"] != expiry:
        return _result("ENTRY_WINDOW_INVALID")
    if (expected >= expiry or t["decision_ready_at"] >= expected
            or t["fill_acceptance_at"] >= expiry):
        return _result("ENTRY_WINDOW_EXPIRED")
    if t["quote_request_sent_at"] < expected or t["fill_acceptance_at"] < expected:
        return _result("ENTRY_TOO_EARLY")
    if type(proposal) is not dict or set(proposal) != {
            "symbol", "quantity", "entry_price", "stop_loss", "target_price", "holding_bars"}:
        return _result("INVALID_PROPOSAL")
    symbol, quantity = proposal["symbol"], proposal["quantity"]
    if (type(symbol) is not str or symbol not in STOCKS
            or type(quantity) is not int or quantity <= 0
            or type(proposal["holding_bars"]) is not int or not 1 <= proposal["holding_bars"] <= 12
            or any(not _number(proposal[k]) or proposal[k] <= 0
                   for k in ("entry_price", "stop_loss", "target_price"))):
        return _result("INVALID_PROPOSAL")
    if type(quote) is not dict or set(quote) != {
            "instrument_key", "bid_price", "ask_price", "bid_quantity", "ask_quantity", "source_quote_timestamp"}:
        return _result("INVALID_QUOTE")
    if type(quote["instrument_key"]) is not str or quote["instrument_key"] != STOCKS[symbol]:
        return _result("INSTRUMENT_MISMATCH")
    if quote["source_quote_timestamp"] is None:
        return _result("DEPTH_TIMESTAMP_MISSING")
    if _stamp(quote["source_quote_timestamp"]) != t["quote_source_at"]:
        return _result("DEPTH_TIMESTAMP_AMBIGUOUS")
    if any(not _number(quote[k]) or quote[k] <= 0 for k in ("bid_price", "ask_price")):
        return _result("INVALID_QUOTE_PRICE")
    if (type(depth_evidence) is not dict or set(depth_evidence) != {
            "schema_version", "evidence_kind", "timestamp_semantics", "quote_sha256"}
            or depth_evidence["schema_version"] != "synthetic-depth-evidence-v1"
            or depth_evidence["evidence_kind"] != "SYNTHETIC_FIXTURE"
            or depth_evidence["timestamp_semantics"] != "DEPTH_EVENT_TIME"):
        return _result("DEPTH_EVIDENCE_UNSUPPORTED")
    encoded = json.dumps(quote, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if depth_evidence["quote_sha256"] != hashlib.sha256(encoded).hexdigest():
        return _result("DEPTH_EVIDENCE_MISMATCH")
    if any((t[key] - t["quote_source_at"]).total_seconds() > policy["max_quote_age_seconds"]
           for key in ("quote_received_at", "fill_acceptance_at")):
        return _result("QUOTE_STALE")
    if any(type(quote[k]) is not int or quote[k] <= 0 for k in ("bid_quantity", "ask_quantity")):
        return _result("INVALID_QUOTE_DEPTH")
    bid, ask = quote["bid_price"], quote["ask_price"]
    if bid > ask:
        return _result("CROSSED_MARKET")
    spread = (ask - bid) / (ask / 2 + bid / 2) * 10000
    if not math.isfinite(spread) or spread > policy["max_spread_bps"]:
        return _result("SPREAD_LIMIT_EXCEEDED")
    if quote["ask_quantity"] < quantity:
        return _result("INSUFFICIENT_ASK_DEPTH")
    stop, target = proposal["stop_loss"], proposal["target_price"]
    # Same price/stop/target bounds as the existing AI-led entry validation.
    for price in (proposal["entry_price"], ask):
        if (not stop < price < target or not 0.0005 <= (price - stop) / price <= 0.01 + 1e-12
                or (target - price) / price > 0.05 + 1e-12):
            return _result("PRICE_STOP_TARGET_INVALID")
    if type(account) is not dict or set(account) != {
            "capital", "balance", "available_cash", "daily_loss", "trades_today", "open_positions", "as_of"}:
        return _result("INVALID_ACCOUNT")
    if (any(not _number(account[k]) for k in ("capital", "balance", "available_cash", "daily_loss"))
            or account["capital"] <= 0 or account["balance"] < 0 or account["daily_loss"] < 0
            or not 0 <= account["available_cash"] <= min(account["capital"], account["balance"])
            or any(type(account[k]) is not int or account[k] < 0 for k in ("trades_today", "open_positions"))
            or _stamp(account["as_of"]) != t["fill_acceptance_at"]):
        return _result("INVALID_ACCOUNT")
    config = RiskConfig(capital=account["capital"])
    allowed, reason = check_trade_allowed(config, RiskState(account["daily_loss"], account["open_positions"], account["trades_today"]))
    if not allowed:
        return _result({"Daily loss limit reached": "DAILY_LOSS_LIMIT", "Maximum open positions reached": "OPEN_POSITION_LIMIT",
                        "Maximum trades for today reached": "DAILY_TRADE_LIMIT"}.get(reason, "INVALID_ACCOUNT"))
    if account["available_cash"] <= 0:
        return _result("INSUFFICIENT_CASH")
    size = calculate_position_size(RiskConfig(capital=min(account["capital"], account["available_cash"])), ask, stop)
    if quantity > size.quantity:
        return _result("RISKENGINE_QUANTITY_EXCEEDED")
    costs = _cost_aware_position_check(capital=account["capital"], available_cash=account["available_cash"],
        entry_price=ask, stop_price=stop, quantity=quantity, trade_timestamp=t["fill_acceptance_at"])
    if not costs["eligible"]:
        return _result({"entry notional plus buy-side costs exceeds capital": "INSUFFICIENT_CASH",
                        "entry notional plus buy-side costs exceeds available account cash": "INSUFFICIENT_CASH",
                        "cost-inclusive stop risk exceeds the trade-risk limit": "COST_INCLUSIVE_RISK_EXCEEDED"}
                       .get(costs["reason"], "INVALID_COST_INPUTS"))
    return _result("ELIGIBLE_SIMULATED_FILL", symbol=symbol, instrument_key=STOCKS[symbol],
        quantity=quantity, entry_reference_price=ask, stop_loss=stop, target_price=target,
        holding_bars=proposal["holding_bars"], fill_acceptance_at=t["fill_acceptance_at"].isoformat(),
        spread_bps=spread, cost_eligibility=costs,
        policy_parameters=dict(policy), timestamps={key: value.isoformat() for key, value in t.items()},
        quote_sha256=depth_evidence["quote_sha256"],
        cost_convention="reference_price_plus_slippage_cost")
