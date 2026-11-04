"""Chronological offline replay orchestration and evidence-labeled metrics."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from app.research.ai_led_offline import (
    SCHEMA, SavedResponseProvider, _aware_timestamp, _digest, _load_state,
    run_offline_batch,
)
from app.research.offline_session_calendar import OfflineSessionCalendar
from strategy_backtest import STOCKS

REPLAY_SCHEMA = "ai-led-offline-replay-v1"
ROOT = Path(__file__).resolve().parents[2]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _path(base, value):
    if not isinstance(value, str) or not value:
        raise ValueError("saved replay path must be explicit")
    return (base / value).resolve()


def _plan(path):
    path = Path(path).resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (set(raw) != {"schema_version", "capital", "instrument_priority", "model_id",
                    "calendar", "calendar_evidence_kind", "ai_evidence_kind", "market_data_kind",
                    "candle_timestamp_convention", "steps"}
            or raw["schema_version"] != REPLAY_SCHEMA
            or raw["ai_evidence_kind"] not in {"SYNTHETIC_MOCK", "RECORDED_HISTORICAL_ASSERTED"}
            or raw["market_data_kind"] not in {"SYNTHETIC_FIXTURE", "SAVED_UNVERIFIED"}
            or raw["candle_timestamp_convention"] != "START"
            or (raw["ai_evidence_kind"] == "RECORDED_HISTORICAL_ASSERTED" and raw["market_data_kind"] == "SYNTHETIC_FIXTURE")
            or not isinstance(raw["steps"], list) or not raw["steps"]):
        raise ValueError("invalid replay plan or evidence classification")
    calendar = OfflineSessionCalendar(_path(path.parent, raw["calendar"]), raw["calendar_evidence_kind"])
    steps, identities = [], []
    previous = None
    for step in raw["steps"]:
        if type(step) is not dict or set(step) != {"as_of", "snapshots", "response", "record_artifact"}:
            raise ValueError("invalid replay step")
        now = _aware_timestamp(step["as_of"], "as_of").tz_convert("Asia/Kolkata")
        if previous is not None and now <= previous:
            raise ValueError("replay plan steps must be strictly chronological; no implicit sorting")
        previous = now
        if type(step["snapshots"]) is not list or not step["snapshots"]:
            raise ValueError("replay step requires a complete snapshot batch")
        paths = [_path(path.parent, p) for p in step["snapshots"]]
        response_path = _path(path.parent, step["response"])
        provider = SavedResponseProvider(response_path.read_text(encoding="utf-8"), raw["model_id"])
        evidence = None
        if raw["ai_evidence_kind"] == "RECORDED_HISTORICAL_ASSERTED":
            artifact = _path(path.parent, step["record_artifact"])
            record = json.loads(artifact.read_text(encoding="utf-8"))
            if set(record) != {"recorded_at", "request_sha256", "response_sha256", "model_id"}:
                raise ValueError("historical record artifact must bind exact request and response hashes")
            evidence = {**record, "artifact_sha256": _sha(artifact),
                        "timestamp_authentication": "NOT_INDEPENDENTLY_AUTHENTICATED"}
        elif step["record_artifact"] is not None:
            raise ValueError("mock replay must not claim historical AI evidence")
        steps.append((now, paths, provider, evidence))
        identities.append({"as_of": now.isoformat(), "snapshots": sorted(_sha(p) for p in paths),
                           "response": _sha(response_path), "evidence": evidence,
                           "provider_content_sha256": hashlib.sha256(provider.content.encode()).hexdigest()})
    context = {"replay_schema": REPLAY_SCHEMA, "ai_evidence_kind": raw["ai_evidence_kind"],
               "candle_timestamp_convention": raw["candle_timestamp_convention"],
               "step_inputs": identities,
               "plan_sha256": _digest({"steps": identities, "calendar": calendar.identity,
                                       "capital": raw["capital"], "priority": raw["instrument_priority"],
                                       "model": raw["model_id"]}),
               "implementation_sha256": _sha(__file__),
               "calendar_implementation_sha256": _sha(Path(__file__).with_name("offline_session_calendar.py")),
               "provider_request_budget": {"limit": 0, "used": 0},
               "data_quality": "SYNTHETIC_FIXTURE" if raw["market_data_kind"] == "SYNTHETIC_FIXTURE"
                               else "SAVED_SNAPSHOTS; provenance not independently established"}
    return raw, calendar, steps, context


def summarize(state):
    """Pure summary of validated account state; no writes, provider calls, or tuning."""
    events = state["events"]
    completed = [e for e in events if e["type"] == "SETTLED"]
    rejected = [e for e in events if e["type"] == "ENTRY_REJECTED"]
    pnls = [e["net_pnl"] for e in completed]
    gains, losses = math.fsum(max(0, x) for x in pnls), math.fsum(max(0, -x) for x in pnls)
    initial = state["config"]["initial_capital"]
    peak, max_drawdown, max_drawdown_pct = initial, 0.0, 0.0
    for row in state["decisions"]:
        equity = row["account_equity"]
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        max_drawdown_pct = max(max_drawdown_pct, 100 * (peak - equity) / peak)
    pos = state["position"]
    reserved = pos.get("reserved_cash", state["cash"]) if pos else 0
    reasons = Counter(e["cost_eligibility"]["reason"] for e in rejected)
    proposal_reasons = Counter(p["eligibility"]["reason"] for d in state["decisions"]
                               for p in d["proposals"] if not p["eligibility"]["eligible"])
    warnings = ["Execution validation only; no profitability inference.",
                "Sample is insufficient for AI predictive-performance conclusions.",
                "Drawdown uses sampled decision-clock equity including estimated liquidation costs; intrabar drawdown is unknown.",
                "Stop gaps and execution uncertainty can exceed planned risk and daily loss limits."]
    context = state["config"]["replay_context"]
    if context["ai_evidence_kind"] == "RECORDED_HISTORICAL_ASSERTED":
        warnings.append("Historical request/response lineage checked; timestamp authenticity is not independently authenticated.")
    if pos:
        warnings.append("Unresolved position: completed-trade metrics exclude it; ending equity is estimated.")
    return {"schema_version": REPLAY_SCHEMA, "evidence_kind": context["ai_evidence_kind"],
            "data_quality": context["data_quality"], "calendar": state["config"]["offline_calendar"],
            "initial_capital": initial, "decision_count": len(state["decisions"]),
            "total_accepted_trades": sum(e["type"] == "ENTRY_ACCEPTED" for e in events),
            "completed_trades": len(completed), "pending_entry_count": int(bool(pos and pos["status"] == "PENDING")),
            "open_position_count": int(bool(pos and pos["status"] == "OPEN")),
            "win_rate_pct": 100 * sum(x > 0 for x in pnls) / len(pnls) if pnls else None,
            "gross_pnl": math.fsum(e["gross_pnl"] for e in completed),
            "transaction_costs": math.fsum(e["costs"]["total_cost"] for e in completed),
            "net_pnl": math.fsum(pnls), "average_net_expectancy": math.fsum(pnls) / len(pnls) if pnls else None,
            "profit_factor": gains / losses if losses else None,
            "profit_factor_unavailable_reason": None if losses else "no completed losing trades",
            "maximum_drawdown": max_drawdown, "maximum_drawdown_pct": max_drawdown_pct,
            "rejected_actual_entries": len(rejected), "actual_entry_rejection_reasons": dict(sorted(reasons.items())),
            "rejected_proposals": sum(proposal_reasons.values()),
            "proposal_rejection_reasons": dict(sorted(proposal_reasons.items())),
            "no_trade_reasons": dict(sorted(Counter(d["reason"] for d in state["decisions"]
                                                     if d["status"] == "NO_TRADE").items())),
            "ending_available_cash": max(0, state["cash"] - reserved),
            "ending_usable_funding": state["cash"], "ending_account_balance": state["balance"],
            "ending_account_equity": state["decisions"][-1]["account_equity"],
            "provider_requests": 0, "trade_placement_enabled": False, "warnings": warnings}


def replay(plan_path, *, output_dir):
    """Replay in declared order; resuming an unchanged plan is idempotent."""
    plan, calendar, steps, context = _plan(plan_path)
    for now, paths, provider, evidence in steps:
        run_offline_batch(paths, output_dir=output_dir, instrument_priority=plan["instrument_priority"],
                          capital=plan["capital"], now=now, provider=provider, model=plan["model_id"],
                          calendar=calendar, replay_context=context, recorded_evidence=evidence)
    # Read under the same lock to avoid summarizing a partial concurrent write.
    from app.research.ai_led_offline import _config, _experiment_lock
    with _experiment_lock(output_dir):
        config = _config(plan["capital"], plan["instrument_priority"], 100000.0,
                         plan["model_id"], calendar, context)
        return summarize(_load_state(Path(output_dir), config))


def create_synthetic_demo(directory, capital):
    """Predeclared three-trade execution fixture, never historical AI performance.

    Artificial prices scale with capital solely to exercise the same one-share
    account lifecycle at several funding levels. Outcomes do not tune proposals.
    """
    directory = Path(directory)
    if type(capital) not in (int, float) or not math.isfinite(capital) or capital <= 0:
        raise ValueError("synthetic replay capital must be positive and finite")
    directory.mkdir(parents=True, exist_ok=False)
    price = capital * 0.6
    symbols = ["RELIANCE", "HDFCBANK"]
    times = pd.date_range("2026-09-28T09:15:00+05:30", periods=44, freq="5min")
    scenario = {}
    for symbol in symbols:
        bars = [{"timestamp": stamp.isoformat(), "open": price, "high": price * 1.0005,
                 "low": price * 0.9995, "close": price, "volume": 1000000} for stamp in times]
        if symbol == "RELIANCE":
            bars[37]["low"] = price * 0.989
            bars[43]["high"] = price * 1.021
        else:
            bars[40]["high"] = price * 1.021
        scenario[symbol] = bars
    steps = []
    for index, count in enumerate((35, 38, 41, 44)):
        now = times[count - 1] + pd.Timedelta(minutes=5)
        paths = []
        for symbol in symbols:
            filename = f"SYNTHETIC-{symbol}-{count}.json"
            body = {"schema_version": "deepseek-prospective-observation-v1",
                    "symbol": symbol, "instrument_key": STOCKS[symbol], "source": "SYNTHETIC_EXECUTION_FIXTURE",
                    "observed_at": now.isoformat(), "candles": scenario[symbol][:count]}
            (directory / filename).write_text(json.dumps(body), encoding="utf-8")
            paths.append(filename)
        proposals = [{"symbol": symbol, "score": 0.9 if index == 1 and symbol == "HDFCBANK" else 0.8,
                      "entry_price": price, "stop_loss": price * 0.99,
                      "target_price": price * 1.02, "holding_bars": 6} for symbol in symbols]
        response = f"MOCK-response-{index}.json"
        (directory / response).write_text(json.dumps({"schema_version": SCHEMA, "action": "BUY",
                                                      "proposals": proposals}), encoding="utf-8")
        steps.append({"as_of": now.isoformat(), "snapshots": paths, "response": response, "record_artifact": None})
    plan = {"schema_version": REPLAY_SCHEMA, "capital": capital, "instrument_priority": symbols,
            "model_id": "deepseek-flash", "calendar": str(ROOT / "data/audits/nse_cm_session_manifest_v2.json"),
            "calendar_evidence_kind": "OFFLINE_NSE_REFERENCES", "ai_evidence_kind": "SYNTHETIC_MOCK",
            "market_data_kind": "SYNTHETIC_FIXTURE", "candle_timestamp_convention": "START", "steps": steps}
    path = directory / "SYNTHETIC-replay-plan.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path
