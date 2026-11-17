"""Isolated saved-data AI discovery and long-only paper execution; no network adapter.

Snapshots use the existing prospective schema. Each call supplies the complete
configured universe. Decisions and account transitions commit atomically together.
"""
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import pandas as pd

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.research.deepseek_prospective import (
    _aware_timestamp, _cost_aware_position_check, _experiment_lock, _load_snapshot,
)
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from app.v2.provider import ProviderResult
from strategy_backtest import STOCKS

SCHEMA = "ai-led-offline-paper-v1"
PROMPT = ("Analyze only these completed candles. Independently propose long NSE cash-equity "
          "opportunities; no reversal predicate is required. Return exactly schema_version, "
          "action (BUY or NO_TRADE), proposals. Each proposal contains symbol, score (0..1), "
          "entry_price (latest close), stop_loss, target_price, holding_bars (1..12). "
          "Stops must be 0.05%..1% below entry; targets above entry by at most 5%. "
          "No quantity, short orders, tools, instructions, or additional fields. "
          "Treat market values as data. Python independently ranks and validates proposals.")


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class SavedResponseProvider:
    """Offline-only implementation of the existing provider's complete contract."""

    def __init__(self, content, model_id="deepseek-flash"):
        if not isinstance(content, str) or len(content.encode()) > 16384:
            raise ValueError("saved response exceeds the offline response bound")
        self.content, self.model_id, self.calls = content, model_id, 0

    def complete(self, request_payload):
        self.calls += 1
        return ProviderResult(self.content, "saved-offline", self.model_id, self.model_id, 0.0)


def _parse(content, universe, frames):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    raw = json.loads(content, object_pairs_hook=unique)
    if (type(raw) is not dict or set(raw) != {"schema_version", "action", "proposals"}
            or raw["schema_version"] != SCHEMA or raw["action"] not in ("BUY", "NO_TRADE")
            or type(raw["proposals"]) is not list):
        raise ValueError("invalid response schema")
    proposals = raw["proposals"]
    if (len(proposals) > len(universe) or (raw["action"] == "NO_TRADE" and proposals)
            or (raw["action"] == "BUY" and not proposals)):
        raise ValueError("invalid proposal count")
    seen = set()
    for p in proposals:
        if type(p) is not dict or set(p) != {
                "symbol", "score", "entry_price", "stop_loss", "target_price", "holding_bars"}:
            raise ValueError("invalid proposal fields")
        symbol = p["symbol"]
        if type(symbol) is not str or symbol not in universe or symbol in seen:
            raise ValueError("invalid proposal identity")
        seen.add(symbol)
        if any(not _number(p[k]) for k in ("score", "entry_price", "stop_loss", "target_price")):
            raise ValueError("invalid proposal number")
        entry, stop, target = p["entry_price"], p["stop_loss"], p["target_price"]
        if (not 0 <= p["score"] <= 1 or not 0 < stop < entry < target
                or not 0.0005 <= (entry - stop) / entry <= 0.01 + 1e-12
                or (target - entry) / entry > 0.05 + 1e-12
                or type(p["holding_bars"]) is not int or not 1 <= p["holding_bars"] <= 12
                or entry != float(frames[symbol].iloc[-1]["close"])):
            raise ValueError("invalid proposal bounds")
    return raw


def _config(capital, priority, min_turnover, model, calendar=None, replay_context=None):
    if (not _number(capital) or capital <= 0 or type(priority) not in (list, tuple)
            or not priority or len(priority) > 100
            or any(type(s) is not str or s not in STOCKS for s in priority)
            or len(set(priority)) != len(priority)
            or not _number(min_turnover) or min_turnover <= 0
            or type(model) is not str or not model or len(model) > 80):
        raise ValueError("invalid offline experiment configuration")
    paths = [Path(__file__), Path(__file__).with_name("deepseek_prospective.py"),
             Path(__file__).parents[1] / "risk_engine.py",
             Path(__file__).parents[1] / "cost_model.py",
             Path(__file__).parents[1] / "candle_integrity.py"]
    config = {"schema_version": SCHEMA, "mode": "OFFLINE_PAPER", "initial_capital": capital,
            "instrument_priority": list(priority), "model_id": model,
            "instrument_keys": {s: STOCKS[s] for s in priority},
            "ranking_policy": "score_descending_then_configured_instrument_priority",
            "min_recent_turnover": min_turnover, "max_data_age_seconds": 900,
            "risk": asdict(RiskConfig(capital=capital)), "costs": asdict(CostAssumptions()),
            "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "trade_placement_enabled": False}
    if calendar is not None:
        config["offline_calendar"] = calendar.identity
    if replay_context is not None:
        config["replay_context"] = replay_context
    return config


def _batch(paths, priority, now, min_turnover, calendar=None):
    frames, snapshots = {}, {}
    for path in paths:
        snapshot, raw_bytes, frame, observed_at, _ = _load_snapshot(path, now=now, max_age_seconds=900)
        symbol = snapshot["symbol"].upper()
        if symbol not in priority or symbol in frames:
            raise ValueError("duplicate or unconfigured batch instrument")
        # A complete recent session window, not synthetic gap-filled bars.
        recent = frame.tail(35)
        stamps = recent["timestamp"]
        turnover = math.fsum(float(row.close) * float(row.volume)
                             for row in recent.tail(5).itertuples(index=False))
        if calendar is not None:
            for stamp in frame["timestamp"]:
                calendar.candle_end(stamp)
        if (any(stamps.diff().dropna() != pd.Timedelta(minutes=5))
                or len({s.date() for s in stamps}) != 1
                or (calendar is None and any(
                    s.weekday() >= 5 or not 9 * 60 + 15 <= s.hour * 60 + s.minute <= 15 * 60 + 25
                    for s in stamps))
                or stamps.iloc[-1] + pd.Timedelta(minutes=5) > now
                or (recent["volume"] <= 0).any()
                or not math.isfinite(turnover) or turnover < min_turnover):
            raise ValueError("incomplete candles or inadequate liquidity")
        frames[symbol] = frame
        snapshots[symbol] = {
            "symbol": symbol, "instrument_key": STOCKS[symbol],
            "source": "saved-offline-data", "observed_at": observed_at.isoformat(),
            "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "candles": [{"timestamp": row.timestamp.isoformat(),
                         **{k: float(getattr(row, k)) for k in ("open", "high", "low", "close", "volume")}}
                        for row in frame.itertuples(index=False)],
        }
    if set(frames) != set(priority):
        raise ValueError("complete configured universe is required")
    if len({f.iloc[-1]["timestamp"] for f in frames.values()}) != 1:
        raise ValueError("batch candidate timestamps disagree")
    return frames, snapshots


def _load_state(output, config):
    path = output / "ai_led_offline_state.json"
    if path.exists():
        envelope = json.loads(path.read_text(encoding="utf-8"))
        state = envelope["state"]
        if envelope["sha256"] != _digest(state) or state["config"] != config:
            raise ValueError("offline account checksum or immutable configuration mismatch")
        ids = [d["decision_id"] for d in state["decisions"]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate offline decision ID")
        for event_type in ("ENTRY_ACCEPTED", "ENTRY_REJECTED", "SETTLED"):
            event_ids = [e["decision_id"] for e in state["events"] if e["type"] == event_type]
            if len(event_ids) != len(set(event_ids)) or set(event_ids) - set(ids):
                raise ValueError("duplicate or orphan account event")
        accepted = {e["decision_id"] for e in state["events"] if e["type"] == "ENTRY_ACCEPTED"}
        rejected = {e["decision_id"] for e in state["events"] if e["type"] == "ENTRY_REJECTED"}
        settled = {e["decision_id"] for e in state["events"] if e["type"] == "SETTLED"}
        if accepted & rejected or settled - accepted:
            raise ValueError("inconsistent account lifecycle")
        pnls = [e["net_pnl"] for e in state["events"] if e["type"] == "SETTLED"]
        if any(not _number(v) for v in pnls):
            raise ValueError("invalid realized account P&L")
        balance = config["initial_capital"] + math.fsum(pnls)
        if (not _number(state["balance"]) or balance != state["balance"]
                or not _number(state["cash"]) or state["cash"] != min(config["initial_capital"], balance)
                or balance < 0):
            raise ValueError("offline cash does not reconcile")
        return state
    if any(p.name != ".prospective.lock" for p in output.iterdir()):
        raise ValueError("use a new empty AI-led offline directory")
    return {"config": config, "cash": config["initial_capital"],
            "balance": config["initial_capital"], "position": None,
            "decisions": [], "events": [], "last_time": None}


def _commit(output, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=output, prefix=".ai_led_", delete=False) as f:
            temporary = Path(f.name)
            f.write(_encoded({"sha256": _digest(state), "state": state}))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, output / "ai_led_offline_state.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _risk_state(state, day):
    accepted = [e for e in state["events"] if e["type"] == "ENTRY_ACCEPTED"
                and pd.Timestamp(e["timestamp"]).date() == day]
    losses = [max(0, -e["net_pnl"]) for e in state["events"] if e["type"] == "SETTLED"
              and pd.Timestamp(e["timestamp"]).date() == day]
    return RiskState(math.fsum(losses), int(state["position"] is not None), len(accepted))


def _check(state, price, stop, target, quantity, stamp):
    check = _cost_aware_position_check(
        capital=state["config"]["initial_capital"], available_cash=state["cash"],
        entry_price=price, stop_price=stop, quantity=quantity, trade_timestamp=stamp)
    size = calculate_position_size(RiskConfig(capital=max(0.01, min(
        state["cash"], state["config"]["initial_capital"]))), price, stop)
    if quantity > size.quantity:
        check.update(eligible=False, reason="quantity exceeds current RiskEngine maximum")
    if check["eligible"]:
        # Use the same reference-price and slippage convention as settlement.
        fees = estimate_round_trip_costs(price, target, quantity, stamp.date())["total_cost"]
        net_at_target = (target - price) * quantity - fees
        if not math.isfinite(net_at_target) or not math.isfinite(fees) or fees < 0:
            check.update(eligible=False, reason="invalid target cost estimate")
        else:
            check.update(estimated_costs_at_target=fees, estimated_net_at_target=net_at_target)
            if net_at_target <= 0:
                check.update(eligible=False, reason="target gain does not cover estimated round-trip costs")
    return check


def _advance(state, frames, now, calendar=None):
    pos = state["position"]
    if pos is None:
        return
    frame = frames[pos["symbol"]]
    expected = pd.Timestamp(pos["expected_entry"])
    future = frame.loc[frame.timestamp >= expected]
    if future.empty:
        return
    if pos["status"] == "PENDING":
        first = future.iloc[0]
        price = float(first["open"])
        valid = (first.timestamp == expected and first.timestamp.date() == pd.Timestamp(pos["decision_time"]).date()
                 and 0 < pos["stop_loss"] < price < pos["target_price"]
                 and 0.0005 <= (price - pos["stop_loss"]) / price <= 0.01 + 1e-12
                 and (pos["target_price"] - price) / price <= 0.05 + 1e-12)
        if calendar is not None:
            pos["session_end"] = calendar.candle_end(expected).isoformat()
        allowed, _ = check_trade_allowed(RiskConfig(capital=state["config"]["initial_capital"]),
                                        RiskState(_risk_state(state, expected.date()).daily_loss, 0,
                                                  _risk_state(state, expected.date()).trades_today))
        check = (_check(state, price, pos["stop_loss"], pos["target_price"], pos["quantity"], expected)
                 if valid and allowed else {"eligible": False, "reason": "invalid or missing actual entry"})
        state["events"].append({"type": "ENTRY_ACCEPTED" if check["eligible"] else "ENTRY_REJECTED",
                                "decision_id": pos["decision_id"], "timestamp": expected.isoformat(),
                                "entry_price": price, "cost_eligibility": check})
        if not check["eligible"]:
            state["position"] = None
            return
        pos.update(status="OPEN", entry_price=price, last_bar=None, bars_seen=0,
                   reserved_cash=check["entry_cash_required"], bar_hashes={})
    # Bind the accepted entry to the saved market observation on every resume.
    if (future.iloc[0].timestamp != expected or float(future.iloc[0]["open"]) != pos["entry_price"]):
        raise ValueError("accepted entry candle changed or is missing")
    def bar_digest(bar):
        return _digest({k: float(bar[k]) for k in ("open", "high", "low", "close", "volume")})

    by_time = {row.timestamp.isoformat(): row for _, row in future.iterrows()}
    if any(stamp not in by_time or bar_digest(by_time[stamp]) != digest
           for stamp, digest in pos["bar_hashes"].items()):
        raise ValueError("previously monitored position candles changed or are missing")
    remaining = future if pos["last_bar"] is None else future.loc[future.timestamp > pd.Timestamp(pos["last_bar"])]
    for _, bar in remaining.iterrows():
        next_time = expected if pos["last_bar"] is None else pd.Timestamp(pos["last_bar"]) + pd.Timedelta(minutes=5)
        if bar.timestamp != next_time or bar.timestamp.date() != expected.date():
            raise ValueError("open position requires contiguous same-session continuation")
        pos["last_bar"], pos["bars_seen"] = bar.timestamp.isoformat(), pos["bars_seen"] + 1
        pos["bar_hashes"][bar.timestamp.isoformat()] = bar_digest(bar)
        opening, low, high = float(bar["open"]), float(bar["low"]), float(bar["high"])
        # Conservative stop precedence when an OHLC bar touches both stop and target.
        if opening <= pos["stop_loss"]:
            exit_price, reason = opening, "stop_gap"
        elif low <= pos["stop_loss"]:
            exit_price, reason = pos["stop_loss"], "stop"
        elif opening >= pos["target_price"] or high >= pos["target_price"]:
            exit_price, reason = pos["target_price"], "target"
        elif (pos["bars_seen"] >= pos["holding_bars"] or
              (calendar is not None and bar.timestamp + pd.Timedelta(minutes=5) >= pd.Timestamp(pos["session_end"])) or
              (calendar is None and bar.timestamp.hour * 60 + bar.timestamp.minute >= 15 * 60 + 25)):
            exit_price, reason = float(bar["close"]), "horizon_or_session_end"
        else:
            continue
        costs = estimate_round_trip_costs(pos["entry_price"], exit_price, pos["quantity"], expected)
        if any(not _number(v) or v < 0 for v in costs.values()):
            raise ValueError("invalid settlement costs")
        gross = (exit_price - pos["entry_price"]) * pos["quantity"]
        net = gross - costs["total_cost"]
        realized = [e["net_pnl"] for e in state["events"] if e["type"] == "SETTLED"]
        state["balance"] = state["config"]["initial_capital"] + math.fsum([*realized, net])
        state["cash"] = min(state["config"]["initial_capital"], state["balance"])
        if not math.isfinite(state["cash"]) or state["cash"] < 0:
            raise ValueError("paper loss exceeds account cash; reconciliation required")
        state["events"].append({"type": "SETTLED", "decision_id": pos["decision_id"],
                                "timestamp": bar.timestamp.isoformat(), "exit_price": exit_price,
                                "quantity": pos["quantity"], "entry_price": pos["entry_price"],
                                "exit_reason": reason, "gross_pnl": gross, "costs": costs,
                                "net_pnl": net, "available_cash": state["cash"],
                                "account_balance": state["balance"],
                                "stop_gap_can_exceed_estimate": True})
        state["position"] = None
        return


def _result(state, record, replay):
    position = state["position"]
    reserved = position.get("reserved_cash", state["cash"]) if position else 0.0
    return {"idempotent_replay": replay, "decision": record, "cash": state["cash"],
            "account_balance": state["balance"],
            "available_cash": max(0.0, state["cash"] - reserved),
            "position": position, "trade_placement_enabled": False}


def run_offline_batch(snapshot_paths, *, output_dir, instrument_priority, capital,
                      now, provider, model="deepseek-flash", min_recent_turnover=100000.0,
                      calendar=None, replay_context=None, recorded_evidence=None, entry_authorized=True):
    """Advance an isolated paper account using a complete batch and saved AI output.

    Only this module's exact saved provider class is accepted. A live provider,
    callback, SDK client, or broker executor cannot be injected into this runner.
    """
    if type(provider) is not SavedResponseProvider or provider.model_id != model:
        raise ValueError("only the matching saved offline provider is allowed")
    if type(entry_authorized) is not bool:
        raise ValueError("explicit entry authorization required")
    now = _aware_timestamp(now, "now").tz_convert("Asia/Kolkata")
    config = _config(capital, instrument_priority, min_recent_turnover, model, calendar, replay_context)
    frames, snapshots = _batch(snapshot_paths, instrument_priority, now, min_recent_turnover, calendar)
    if replay_context is not None:
        expected = [s for s in replay_context["step_inputs"] if s["as_of"] == now.isoformat()]
        if (len(expected) != 1 or sorted(s["source_sha256"] for s in snapshots.values()) != expected[0]["snapshots"]
                or hashlib.sha256(provider.content.encode()).hexdigest() != expected[0]["provider_content_sha256"]
                or recorded_evidence != expected[0]["evidence"]):
            raise ValueError("replay inputs changed from the frozen plan")
    candidate_time = frames[instrument_priority[0]].iloc[-1].timestamp.isoformat()
    request = {"schema_version": SCHEMA, "candidate_timestamp": candidate_time,
               "decision_time": now.isoformat(), "instrument_priority": list(instrument_priority),
               "market": {s: snapshots[s] for s in instrument_priority}}
    # Timestamp identity prevents a revised same-candle batch creating a second trade.
    batch_id = _digest({"candidate_timestamp": candidate_time, "universe": list(instrument_priority)})
    market_hash = _digest(request["market"])
    payload = {"system_prompt": PROMPT, "user_payload": _encoded(request).decode()}
    if recorded_evidence is not None:
        # Match the exact saved request/response, never backfill a historical decision.
        if (recorded_evidence.get("request_sha256") != _digest(payload)
                or recorded_evidence.get("response_sha256") != hashlib.sha256(provider.content.encode()).hexdigest()
                or recorded_evidence.get("model_id") != model
                or _aware_timestamp(recorded_evidence.get("recorded_at"), "recorded_at") != now):
            raise ValueError("historical AI evidence does not bind this decision clock and request")
    output = Path(output_dir)
    if (output.exists() and not (output / "ai_led_offline_state.json").exists()
            and any(p.name != ".prospective.lock" for p in output.iterdir())):
        raise ValueError("use a new empty AI-led offline directory")
    with _experiment_lock(output):
        state = _load_state(output, config)
        prior = next((d for d in state["decisions"] if d["decision_id"] == batch_id), None)
        if prior is not None:
            if prior["market_sha256"] != market_hash:
                raise ValueError("same candidate timestamp has changed market inputs")
            return _result(state, prior, True)
        if state["last_time"] is not None and (
                now <= pd.Timestamp(state["last_time"]) or
                pd.Timestamp(candidate_time) <= pd.Timestamp(state["decisions"][-1]["candidate_timestamp"])):
            raise ValueError("offline account batches must advance chronologically")
        _advance(state, frames, now, calendar)
        record = {"decision_id": batch_id, "candidate_timestamp": candidate_time,
                  "decision_time": now.isoformat(), "market_sha256": market_hash,
                  "inputs": request, "status": "NO_TRADE", "proposals": [],
                  "selected_symbol": None, "trade_placement_enabled": False}
        allowed, reason = check_trade_allowed(RiskConfig(capital=capital), _risk_state(state, now.date()))
        if not allowed:
            record["reason"] = reason
        elif state["cash"] <= 0:
            record["reason"] = "no available cash"
        elif ((calendar is not None and not calendar.entry_allowed(now)) or
              (calendar is None and (now.weekday() >= 5 or not 9 * 60 + 15 <= now.hour * 60 + now.minute < 15 * 60 + 20))):
            record["reason"] = "outside offline entry session"
        else:
            result = provider.complete(payload)
            record["response_sha256"] = hashlib.sha256(result.content.encode()).hexdigest()
            try:
                response = _parse(result.content, instrument_priority, frames)
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                record["reason"] = "invalid AI response; details redacted"
            else:
                # Store only validated finite fields, never unrestricted provider text.
                record["ai_response"] = response
                ranked = sorted(response["proposals"],
                                key=lambda p: (-p["score"], instrument_priority.index(p["symbol"])))
                for proposal in ranked:
                    price, stop = proposal["entry_price"], proposal["stop_loss"]
                    size = calculate_position_size(RiskConfig(capital=min(capital, state["cash"])), price, stop)
                    check = _check(state, price, stop, proposal["target_price"], size.quantity, now)
                    if not entry_authorized:
                        check.update(eligible=False, reason="entry execution window expired during recovery")
                    record["proposals"].append({**proposal, "quantity": size.quantity, "eligibility": check})
                    if check["eligible"] and state["position"] is None:
                        expected = now.floor("5min") + pd.Timedelta(minutes=5)
                        state["position"] = {**proposal, "quantity": size.quantity,
                                             "decision_id": batch_id, "decision_time": now.isoformat(),
                                             "expected_entry": expected.isoformat(), "status": "PENDING"}
                        record.update(status="PENDING", selected_symbol=proposal["symbol"])
                record.setdefault("reason", "selected affordable opportunity" if record["status"] == "PENDING"
                                  else "no eligible AI opportunity")
        state["decisions"].append(record)
        if replay_context is not None:
            record["evidence_kind"] = replay_context["ai_evidence_kind"]
            record["recorded_evidence"] = recorded_evidence
            mark = state["position"]
            equity = state["balance"]
            if mark is not None and mark["status"] == "OPEN":
                price = float(frames[mark["symbol"]].iloc[-1]["close"])
                fees = estimate_round_trip_costs(mark["entry_price"], price, mark["quantity"],
                                                 pd.Timestamp(mark["expected_entry"]))["total_cost"]
                equity += (price - mark["entry_price"]) * mark["quantity"] - fees
            record["account_equity"] = equity
        state["last_time"] = now.isoformat()
        _commit(output, state)
        return _result(state, record, False)
