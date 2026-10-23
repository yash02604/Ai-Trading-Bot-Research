"""Offline replay of immutable AI V2 ledger decisions; no broker or provider path.

Evaluation inputs must bind the exact candle bytes and reviewed START-label/session
evidence. The existing historical cache does not currently meet that prerequisite.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.research.execution_integrity import ExecutionIntegrityError, TimestampSemanticsEvidence
from app.research.sharp_reversal_experiment import load_session_dates
from .ai_led_research import AIResearchDecision, AIResearchRequest, ResearchAction, safe_no_trade
from .contracts import Action, DecisionRequest, MarketRegime, RankedSymbol, V2Recommendation, parse_timestamp
from .registry import StrategyCandidate, StrategyRegistry
from .safety import RiskLimits, RiskState, SafetyGate

IST = ZoneInfo("Asia/Kolkata")
BAR = timedelta(minutes=5)
EVALUATION_VERSION = "ai-led-paper-evaluation-v1.0"
SUPPORTED_ENTRY_CONDITION = "NEXT_ELIGIBLE_BAR_OPEN"
MAX_ENTRY_DEVIATION_PCT = 0.25


class EvaluationBlocked(ValueError):
    """Required data lineage, session evidence, or immutable record is missing."""


@dataclass(frozen=True)
class EvaluationResult:
    run_id: str
    report_path: str
    ledger_path: str
    decision_count: int
    fill_count: int
    comparison_status: str
    comparison_blocker: str


def _json_hash(value) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _iso(value, field: str) -> datetime:
    try:
        result = parse_timestamp(value, field)
    except (TypeError, ValueError) as exc:
        raise EvaluationBlocked(f"{field} is missing or timezone-ambiguous") from exc
    return result.astimezone(IST)


def _read_ledger(path: Path) -> list[tuple[str, str, dict]]:
    if not path.is_file():
        raise EvaluationBlocked("recorded AI decision ledger does not exist")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            rows = db.execute("SELECT signal_id,recorded_at,payload FROM decisions ORDER BY recorded_at,signal_id").fetchall()
    except sqlite3.Error as exc:
        raise EvaluationBlocked("AI decision ledger could not be read in read-only mode") from exc
    output = []
    for signal_id, recorded_at, payload in rows:
        try:
            body = json.loads(payload)
            if not isinstance(body, dict) or not isinstance(body.get("envelope"), dict):
                raise ValueError
            output.append((signal_id, recorded_at, body))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationBlocked(f"malformed immutable decision record: {signal_id}") from exc
    try:
        output.sort(key=lambda row: (_iso(row[1], "ledger recorded_at").timestamp(), row[0]))
    except EvaluationBlocked:
        raise
    return output


def _parse_envelope(body: dict) -> tuple[AIResearchRequest, AIResearchDecision, tuple[StrategyCandidate, ...]]:
    try:
        envelope = body["envelope"]
        if (envelope["contract_version"] != "ai-led-research-decision-v1.0"
                or envelope["prompt_version"] != "ai-led-research-prompt-v1.0"):
            raise ValueError("unsupported decision/prompt version")
        request_raw = envelope["request"]
        market_raw = dict(request_raw["market"])
        for key in ("eligible_symbols", "strategy_ids", "open_symbols"):
            market_raw[key] = tuple(market_raw.get(key, ()))
        market = DecisionRequest(**market_raw)
        research_request = AIResearchRequest(
            market=market, input_data_sha256=request_raw["input_data_sha256"],
            source_provenance_status=request_raw.get("source_provenance_status", "UNRESOLVED"),
            data_quality_issues=tuple(request_raw.get("data_quality_issues", ())),
            contract_version=envelope["contract_version"], prompt_version=envelope["prompt_version"],
            config_version=envelope["config_version"],
        )
        candidates = tuple(StrategyCandidate(**item) for item in envelope["registered_strategy_catalog"])
        if len({item.strategy_id for item in candidates}) != len(candidates):
            raise ValueError("duplicate strategy catalog ID")
        if envelope.get("failure_type") is None and set(market.strategy_ids) - {item.strategy_id for item in candidates}:
            raise ValueError("request strategies are absent from recorded catalog")
        if envelope.get("failure_type") is not None:
            decision = safe_no_trade(f"recorded provider/validation failure: {envelope['failure_type']}")
        else:
            decision_raw = dict(envelope["recommendation"])
            decision_raw["schema_version"] = envelope["contract_version"]
            decision = AIResearchDecision.from_mapping(decision_raw, research_request)
        if body.get("execution_enabled") is not False:
            raise ValueError("record does not prove execution was disabled")
        return research_request, decision, candidates
    except EvaluationBlocked:
        raise
    except Exception as exc:
        raise EvaluationBlocked("recorded AI decision does not satisfy its immutable versioned contract") from exc


def _load_sources(source_files: Mapping[str, Path]) -> tuple[pd.DataFrame, dict[str, str]]:
    if not source_files:
        raise EvaluationBlocked("source candle files are required")
    frames, hashes = [], {}
    for name, path in sorted(source_files.items()):
        path = Path(path)
        sensitive_parts = {part.lower() for part in path.parts}
        lowered_name = path.name.lower()
        if (not path.is_file() or lowered_name.startswith(".env")
                or any(any(term in part for term in ("credential", "secret", "token"))
                       for part in sensitive_parts)):
            raise EvaluationBlocked("source is missing or has a prohibited sensitive-looking path")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in hashes:
            raise EvaluationBlocked("duplicate source identity")
        hashes[name] = digest
        if path.suffix.lower() != ".csv":
            continue  # Context/source bytes are hash-bound; only equity candles drive fills.
        try:
            frame = pd.read_csv(path)
        except Exception as exc:
            raise EvaluationBlocked(f"cannot parse source candles: {name}") from exc
        needed = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
        if needed - set(frame.columns):
            continue  # E.g. a hash-bound index/context file; never used for equity fills.
        normalized = []
        for raw in frame.timestamp:
            if not isinstance(raw, str):
                raise EvaluationBlocked("candle timestamps must retain their explicit source strings")
            normalized.append(_iso(raw, "candle timestamp"))
        frame = frame.copy()
        frame["timestamp"] = normalized
        frames.append(frame)
    if not frames:
        raise EvaluationBlocked("no source file contains the required equity candle schema")
    candles = pd.concat(frames, ignore_index=True)
    candles["symbol"] = candles.symbol.astype(str).str.upper()
    if candles.duplicated(["symbol", "timestamp"], keep=False).any():
        raise EvaluationBlocked("duplicate symbol/timestamp candle keys are not eligible")
    for name in ("open", "high", "low", "close"):
        candles[name] = pd.to_numeric(candles[name], errors="coerce")
    candles["volume"] = pd.to_numeric(candles["volume"], errors="coerce")
    if candles.volume.isna().any() or (candles.volume <= 0).any():
        raise EvaluationBlocked("nonpositive or unresolved equity volume exists in the supplied candles")
    prices = candles[["open", "high", "low", "close"]]
    if prices.isna().any().any() or not (prices > 0).all().all():
        raise EvaluationBlocked("malformed or nonpositive OHLC candle in supplied evaluation source")
    if ((prices.low > prices[["open", "close"]].min(axis=1))
            | (prices.high < prices[["open", "close"]].max(axis=1))
            | (prices.low > prices.high)).any():
        raise EvaluationBlocked("invalid OHLC relationship in supplied evaluation source")
    for stamp in candles.timestamp:
        if stamp.minute % 5 or stamp.second or stamp.microsecond:
            raise EvaluationBlocked("source contains a misaligned five-minute candle label")
    return candles.sort_values(["symbol", "timestamp"], kind="stable").reset_index(drop=True), hashes


def _verified_session(stamp: datetime, manifest: dict) -> bool:
    entry = manifest.get(stamp.date())
    if not entry or entry.get("verification_status") != "VERIFIED" or entry.get("segment") != "CM":
        return False
    if entry.get("session_type") != "REGULAR" or entry.get("special_session") is not False:
        return False
    local_time = stamp.timetz().replace(tzinfo=None)
    for interval in entry.get("normal_market_intervals", []):
        try:
            if time.fromisoformat(interval["start"]) <= local_time < time.fromisoformat(interval["end"]):
                return True
        except (KeyError, TypeError, ValueError):
            return False
    return False


def _validate_request_lineage(request: AIResearchRequest, recorded_at: datetime,
                              data_hashes: Mapping[str, str], evidence: TimestampSemanticsEvidence,
                              session_dates: dict, *, actionable: bool) -> None:
    signal = _iso(request.market.signal_timestamp, "signal_timestamp")
    cutoff = _iso(request.market.data_cutoff, "data_cutoff")
    # START-labeled OHLC is not known until the interval ends. Requiring a post-close
    # recording time and a strictly later entry prevents same-bar execution.
    if recorded_at < cutoff + BAR:
        raise EvaluationBlocked("decision record predates completion of its START-labeled data cutoff candle")
    try:
        request.validate(now=recorded_at, max_data_age_seconds=300)
    except (TypeError, ValueError) as exc:
        raise EvaluationBlocked(f"recorded decision data is stale or temporally invalid: {exc}") from exc
    if signal.date() != cutoff.date() or not _verified_session(cutoff, session_dates):
        raise EvaluationBlocked("decision cutoff has no verified regular same-day session")
    if actionable and (request.source_provenance_status != "VERIFIED" or request.data_quality_issues):
        raise EvaluationBlocked("actionable decision has unresolved provenance or data-quality issues")
    if not request.input_data_sha256 or dict(request.input_data_sha256) != dict(data_hashes):
        raise EvaluationBlocked("exact recorded decision input hashes do not match supplied source bytes")
    evidence.require_verified_start_labels(data_hashes)


def _metrics(trades: list[dict], events: list[dict], starting_capital: float) -> dict:
    net = [float(item["net_pnl"]) for item in trades]
    gross = [float(item["gross_pnl"]) for item in trades]
    positive = sum(value for value in net if value > 0)
    negative = -sum(value for value in net if value < 0)
    equity, peak, max_dd = starting_capital, starting_capital, 0.0
    for value in net:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    component_totals = defaultdict(float)
    for trade in trades:
        for key, value in trade["costs"].items():
            if key != "total_cost":
                component_totals[key] += float(value)
    gross_total, net_total = sum(gross), sum(net)
    return {
        "decision_count": len(events), "proposed_count": sum(x["event_type"] == "TRADE_PROPOSED" for x in events),
        "rejected_count": sum(x["event_type"] == "TRADE_REJECTED" for x in events),
        "no_trade_count": sum(x["event_type"] == "NO_TRADE" for x in events),
        "trade_count": len(trades), "gross_pnl": gross_total, "net_pnl": net_total,
        "costs_by_component": dict(sorted(component_totals.items())),
        "gross_to_net_deterioration": gross_total - net_total,
        "win_rate": sum(value > 0 for value in net) / len(net) if net else 0.0,
        "profit_factor": positive / negative if negative else (None if not positive else "infinite"),
        "max_drawdown": max_dd, "average_net_per_trade": net_total / len(net) if net else 0.0,
        "exposure_bar_minutes": sum(int(item["holding_bars"]) * 5 for item in trades),
        "stock": _group_metrics(trades, "symbol"), "market_regime": _group_metrics(trades, "market_regime"),
    }


def _group_metrics(trades: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for trade in trades:
        groups[trade[key]].append(trade)
    return {name: {"trade_count": len(rows), "gross_pnl": sum(row["gross_pnl"] for row in rows),
                   "net_pnl": sum(row["net_pnl"] for row in rows),
                   "costs": sum(row["costs"]["total_cost"] for row in rows)}
            for name, rows in sorted(groups.items())}


def evaluate_recorded_decisions(*, decision_ledger: Path, source_files: Mapping[str, Path],
                                timestamp_evidence_path: Path, session_manifest_path: Path,
                                output_dir: Path, starting_capital: float = 100_000.0,
                                limits: RiskLimits | None = None,
                                costs: CostAssumptions | None = None,
                                kill_switch,
                                allow_model_substitution: bool = False) -> EvaluationResult:
    """Replay append-only decision rows into a new exclusive JSONL paper-evaluation run.

    No AI/provider calls are made. The original ledger and all source artifacts are opened
    read-only. New results are never written over an existing output directory.
    """
    decision_ledger, output_dir = Path(decision_ledger), Path(output_dir)
    limits, costs = limits or RiskLimits(), costs or CostAssumptions()
    if kill_switch is None or not hasattr(kill_switch, "enabled"):
        raise EvaluationBlocked("an explicit kill-switch state is required for offline simulation")
    if costs.slippage_bps_per_side != limits.entry_slippage_bps:
        raise EvaluationBlocked("risk gate and cost model slippage assumptions must match")
    if (isinstance(starting_capital, bool) or not isinstance(starting_capital, (int, float))
            or not math.isfinite(float(starting_capital)) or starting_capital <= 0):
        raise EvaluationBlocked("starting capital must be positive and finite")
    evidence = TimestampSemanticsEvidence.from_file(Path(timestamp_evidence_path))
    session_file = Path(session_manifest_path)
    session_dates = load_session_dates(session_file)
    session_hash = hashlib.sha256(session_file.read_bytes()).hexdigest()
    candles, data_hashes = _load_sources(source_files)
    records = _read_ledger(decision_ledger)
    parsed_records = []
    catalog_by_id: dict[str, StrategyCandidate] = {}
    for signal_id, recorded_text, body in records:
        request, decision, candidates = _parse_envelope(body)
        for candidate in candidates:
            existing = catalog_by_id.get(candidate.strategy_id)
            if existing is not None and existing != candidate:
                raise EvaluationBlocked(f"immutable strategy version conflict: {candidate.strategy_id}")
            catalog_by_id[candidate.strategy_id] = candidate
        parsed_records.append((signal_id, recorded_text, body, request, decision, candidates))
    project_root = Path(__file__).resolve().parents[2]
    source_hashes = {"decision_ledger": hashlib.sha256(decision_ledger.read_bytes()).hexdigest(),
                     "timestamp_evidence": evidence.evidence_file_sha256,
                     "session_manifest": session_hash,
                     "evaluator_code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "risk_gate_code": hashlib.sha256((project_root / "app/v2/safety.py").read_bytes()).hexdigest(),
                     "cost_model_code": hashlib.sha256((project_root / "app/cost_model.py").read_bytes()).hexdigest(),
                     "execution_integrity_code": hashlib.sha256(
                         (project_root / "app/research/execution_integrity.py").read_bytes()).hexdigest(),
                     "frozen_v1_strategy_code": hashlib.sha256(
                         (project_root / "strategy_backtest.py").read_bytes()).hexdigest(),
                     "frozen_v1_backtester_code": hashlib.sha256(
                         (project_root / "backtester.py").read_bytes()).hexdigest(),
                     **{f"data:{k}": v for k, v in data_hashes.items()}}
    # An immutable source/config fingerprint makes the run reproducible and its ID stable.
    config = {"version": EVALUATION_VERSION, "risk_limits": asdict(limits),
              "cost_assumptions": asdict(costs), "allow_model_substitution": allow_model_substitution,
              "execution_assumptions": {"entry_condition": SUPPORTED_ENTRY_CONDITION,
                                        "max_entry_deviation_pct": MAX_ENTRY_DEVIATION_PCT,
                                        "same_session_only": True, "stop_first_if_stop_and_target_touch": True},
              "session_manifest_sha256": session_hash, "source_hashes": source_hashes}
    run_id = "ai-v2-paper-" + _json_hash({"config": config, "decision_ids": [r[0] for r in records]})[:20]
    if output_dir.exists():
        raise EvaluationBlocked("output directory already exists; prior evaluation artifacts are immutable")
    output_dir.mkdir(parents=True, exist_ok=False)
    events, trades = [], []
    state = RiskState(float(starting_capital), float(starting_capital))
    registry = StrategyRegistry(catalog_by_id[key] for key in sorted(catalog_by_id))
    def session_validator(stamp):
        return _verified_session(stamp.astimezone(IST), session_dates)
    session_validator.authoritative = True
    gate = SafetyGate(registry=registry, limits=limits, session_validator=session_validator,
                      kill_switch=kill_switch, state=state)
    seen_market_decisions: set[tuple] = set()
    last_date = None
    portfolio_available_at: datetime | None = None
    event_context = {"paper_only": True, "execution_enabled": False}

    def emit(signal_id, stamp, event_type, payload, *, event_time=None):
        events.append({"event_id": f"{run_id}:{len(events)+1}", "run_id": run_id, **event_context,
                      "signal_id": signal_id, "recorded_at": (event_time or stamp).isoformat(),
                      "event_type": event_type, **payload})

    try:
        for signal_id, recorded_text, body, request, decision, candidates in parsed_records:
            recorded = _iso(recorded_text, "ledger recorded_at")
            envelope = body["envelope"]
            event_context = {
                "paper_only": True, "execution_enabled": False,
                "decision_contract_version": envelope["contract_version"],
                "prompt_version": envelope["prompt_version"], "config_version": envelope["config_version"],
                "provider_id": envelope.get("provider_id"),
                "requested_model_id": envelope.get("requested_model_id"),
                "returned_model_id": envelope.get("returned_model_id"),
                "model_substituted": bool(envelope.get("model_substituted")),
                "prompt_sha256": envelope.get("prompt_sha256"),
                "provider_input_sha256": envelope.get("provider_input_sha256"),
                "input_data_sha256": request.input_data_sha256,
                "rationale": decision.rationale, "uncertainty": decision.uncertainty,
                "key_evidence": list(decision.key_evidence),
            }
            if decision.strategy_id:
                chosen = next((item for item in candidates if item.strategy_id == decision.strategy_id), None)
                if chosen is None or chosen.strategy_id not in request.market.strategy_ids:
                    raise EvaluationBlocked(f"unregistered strategy in decision: {signal_id}")
            if body["envelope"].get("model_substituted") and not allow_model_substitution:
                emit(signal_id, recorded, "NO_TRADE", {"reason": "model substitution is disallowed by evaluation configuration"})
                continue
            _validate_request_lineage(request, recorded, data_hashes, evidence, session_dates,
                                      actionable=decision.action in {ResearchAction.BUY, ResearchAction.EXIT})
            cutoff = _iso(request.market.data_cutoff, "data_cutoff")
            market_key = (request.market.request_id, request.market.signal_timestamp,
                          decision.symbol, decision.strategy_id)
            if market_key in seen_market_decisions:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "duplicate decision key"})
                continue
            seen_market_decisions.add(market_key)
            if last_date != recorded.date():
                if state.open_positions:
                    emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "open position crossed session boundary"})
                    raise EvaluationBlocked("position state unexpectedly crossed a session boundary")
                state.daily_loss, state.trades_today = 0.0, 0
                state.day_start_capital = state.capital
                last_date = recorded.date()
            if decision.action in {ResearchAction.BUY, ResearchAction.EXIT} and state.open_positions:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "position already open at decision time"})
                continue
            if (decision.action in {ResearchAction.BUY, ResearchAction.EXIT}
                    and portfolio_available_at is not None and recorded < portfolio_available_at):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "signal overlaps an earlier simulated holding window"})
                continue
            action = decision.action
            if action in {ResearchAction.HOLD, ResearchAction.NO_TRADE}:
                emit(signal_id, recorded, "NO_TRADE", {"decision_action": action.value,
                    "recorded_final_decision": body.get("final_decision"),
                    "reason": decision.rationale, "source_hashes": source_hashes})
                continue
            emit(signal_id, recorded, "TRADE_PROPOSED", {"symbol": decision.symbol,
                "strategy_id": decision.strategy_id, "strategy_version": next(
                    x.version for x in candidates if x.strategy_id == decision.strategy_id),
                "decision_action": action.value, "recorded_final_decision": body.get("final_decision"),
                "entry_condition": decision.entry_condition,
                "ai_estimated_round_trip_cost_pct": decision.estimated_round_trip_cost_pct,
                "data_cutoff": cutoff.isoformat(), "source_hashes": source_hashes})
            if action is ResearchAction.BUY and decision.entry_condition != SUPPORTED_ENTRY_CONDITION:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "entry condition is outside the implemented execution contract"})
                continue
            recommendation = V2Recommendation(
                Action.BUY if action is ResearchAction.BUY else Action.EXIT, decision.market_regime,
                tuple(RankedSymbol(x.symbol, x.rank, x.evidence) for x in decision.ranked_symbols),
                decision.symbol, decision.strategy_id, decision.entry_price, decision.stop_loss,
                decision.target_price, decision.holding_bars, decision.rationale, decision.key_evidence,
                decision.confidence,
            )
            # This re-applies the Python risk policy to the historical recommendation.
            # It bypasses only paper-enabled registry status and returns no executor permit.
            risk = gate.approve_research(request.market, recommendation, now=recorded, signal_id=signal_id)
            event_context["evaluation_risk_result"] = {
                "approved": risk.approved, "reason": risk.reason, "quantity": risk.quantity,
                "allocation": risk.allocation, "planned_risk": risk.planned_risk,
            }
            event_context["recorded_risk_result"] = body.get("risk_result")
            if not risk.approved:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": risk.reason,
                    "risk_quantity": risk.quantity, "source_hashes": source_hashes})
                continue
            symbol = decision.symbol
            frame = candles[candles.symbol.eq(symbol)].reset_index(drop=True)
            cutoff_rows = frame.index[frame.timestamp.eq(cutoff)].tolist()
            if len(cutoff_rows) != 1:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "decision cutoff candle missing or ambiguous"})
                continue
            if not _verified_session(cutoff, session_dates):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "cutoff session invalid or unresolved"})
                continue
            candidates_after = frame.index[frame.timestamp.gt(recorded)].tolist()
            if not candidates_after:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "no strictly future entry candle"})
                continue
            entry_index = candidates_after[0]
            entry_bar = frame.iloc[entry_index]
            entry_time = entry_bar.timestamp
            if (entry_time.date() != cutoff.date() or entry_time - cutoff < BAR
                    or not _verified_session(entry_time, session_dates)):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "entry is not a future same-session candle"})
                continue
            # No silent gap bridging from cutoff completion to a modeled fill.
            before_entry = frame[(frame.timestamp >= cutoff) & (frame.timestamp <= entry_time)]
            expected = pd.date_range(cutoff, entry_time, freq="5min", tz=IST)
            if list(before_entry.timestamp) != list(expected):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "missing candle before entry"})
                continue
            entry_ref = float(entry_bar.open)
            entry_deviation_pct = abs(entry_ref - float(decision.entry_price)) / float(decision.entry_price) * 100
            if entry_deviation_pct > MAX_ENTRY_DEVIATION_PCT:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "next-open price exceeds the bounded AI entry deviation",
                    "entry_deviation_pct": entry_deviation_pct,
                    "max_entry_deviation_pct": MAX_ENTRY_DEVIATION_PCT})
                continue
            fill = entry_ref * (1 + costs.slippage_bps_per_side / 10_000)
            stop, target, horizon = float(decision.stop_loss), float(decision.target_price), decision.holding_bars
            actual_stop_pct = (fill - stop) / fill * 100
            actual_target_pct = (target - fill) / fill * 100
            if (not limits.min_stop_distance_pct <= actual_stop_pct <= limits.max_stop_distance_pct
                    or actual_target_pct <= 0 or actual_target_pct > limits.max_target_distance_pct):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "actual entry fill violates stop/target distance limits"})
                continue
            if kill_switch.enabled or not _verified_session(entry_time, session_dates):
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "kill switch or session invalid at simulated fill"})
                continue
            # Re-size at the actual next-bar open using unchanged independent limits.
            risk_qty = int((state.capital * limits.max_risk_per_trade) // (fill - stop)) if fill > stop else 0
            used = sum(float(x["allocation"]) for x in state.open_positions.values())
            allocation_cap = min(state.capital * limits.max_allocation, max(0.0, state.capital - used))
            alloc_qty = int(allocation_cap // fill) if fill > 0 else 0
            quantity = min(risk.quantity, risk_qty, alloc_qty)
            if quantity <= 0:
                emit(signal_id, recorded, "TRADE_REJECTED", {"reason": "actual fill cannot satisfy risk or capital bounds"})
                continue
            position = {"symbol": symbol, "entry_index": entry_index, "entry_timestamp": entry_time,
                        "reference_entry": entry_ref, "entry_fill": fill, "quantity": quantity,
                        "stop": stop, "target": target, "horizon": horizon,
                        "regime": decision.market_regime.value, "strategy_id": decision.strategy_id}
            state.open_positions[symbol] = {"quantity": quantity, "entry_price": fill,
                                             "allocation": quantity * fill}
            state.trades_today += 1
            emit(signal_id, recorded, "SIMULATED_FILL", {"symbol": symbol, "strategy_id": decision.strategy_id,
                "quantity": quantity, "bar_timestamp": entry_time.isoformat(), "reference_price": entry_ref,
                "fill_price": fill, "slippage_bps_per_side": costs.slippage_bps_per_side,
                "source_hashes": source_hashes}, event_time=entry_time)
            exit_info = None
            session_entry = session_dates[entry_time.date()]
            interval = next(item for item in session_entry["normal_market_intervals"]
                            if time.fromisoformat(item["start"]) <= entry_time.timetz().replace(tzinfo=None)
                            < time.fromisoformat(item["end"]))
            session_close = datetime.combine(entry_time.date(), time.fromisoformat(interval["end"]), IST)
            for offset in range(horizon):
                idx = entry_index + offset
                if idx >= len(frame):
                    break
                bar = frame.iloc[idx]
                stamp = bar.timestamp
                if stamp.date() != entry_time.date() or not _verified_session(stamp, session_dates):
                    break
                if idx > entry_index and stamp - frame.iloc[idx - 1].timestamp != BAR:
                    break
                low, high, opn = float(bar.low), float(bar.high), float(bar.open)
                if low <= stop:  # Conservative when stop and target touch in one candle.
                    exit_info = (min(opn, stop) if opn < stop else stop, stamp, "STOP_LOSS", offset + 1)
                    break
                if high >= target:
                    exit_info = (target, stamp, "TARGET", offset + 1)
                    break
                if offset == horizon - 1:
                    exit_info = (float(bar.close), stamp, "HORIZON", offset + 1)
                    break
                if stamp + BAR == session_close:
                    exit_info = (float(bar.close), stamp, "SESSION_CLOSE", offset + 1)
                    break
            if exit_info is None:
                # No fabricated exit or overnight carry. The proposal is rejected from
                # completed-trade metrics because this candle series cannot close it.
                raise EvaluationBlocked("simulated fill has no complete same-session exit window; run is incomplete")
            exit_ref, exit_time, exit_reason, held_bars = exit_info
            gross = (exit_ref - entry_ref) * quantity
            breakdown = estimate_round_trip_costs(entry_ref, exit_ref, quantity, exit_time.date(), costs)
            net = gross - breakdown["total_cost"]
            trade = {"signal_id": signal_id, "symbol": symbol, "strategy_id": decision.strategy_id,
                     "strategy_version": next(x.version for x in candidates if x.strategy_id == decision.strategy_id),
                     "market_regime": position["regime"],
                     "quantity": quantity, "entry_timestamp": entry_time.isoformat(),
                     "exit_timestamp": exit_time.isoformat(), "entry_reference": entry_ref,
                     "exit_reference": exit_ref, "entry_fill": fill,
                     "exit_fill": exit_ref * (1 - costs.slippage_bps_per_side / 10_000),
                     "holding_bars": held_bars, "exit_reason": exit_reason, "gross_pnl": gross,
                     "costs": breakdown, "net_pnl": net, "source_hashes": source_hashes}
            trades.append(trade)
            state.capital += net
            state.daily_loss += max(0.0, -net)
            del state.open_positions[symbol]
            emit(signal_id, exit_time, "SIMULATED_EXIT", trade, event_time=exit_time + BAR)
            emit(signal_id, exit_time, "TRADE_OUTCOME", {"gross_pnl": gross, "net_pnl": net,
                "costs": breakdown, "exit_reason": exit_reason, "source_hashes": source_hashes},
                event_time=exit_time + BAR)
            # Reserve the slot through the decision's maximum horizon (or the
            # manifest's session close). This cutoff is known at entry time and does
            # not depend on the future stop/target path found by the simulator.
            portfolio_available_at = min(entry_time + horizon * BAR, session_close)
    except Exception:
        # The unique output folder preserves diagnostic append-only events if replay
        # aborts midway; caller can inspect it but must not treat it as complete.
        _write_output(output_dir, run_id, config, source_hashes, events, trades, complete=False)
        raise
    report = _metrics(trades, events, float(starting_capital))
    report.update({"evaluation_version": EVALUATION_VERSION, "run_id": run_id,
                   "source_hashes": source_hashes, "config": config,
                   "comparison_status": "BLOCKED",
                   "comparison_blocker": "No frozen V1 action ledger generated on the same verified inputs and execution assumptions; no fair AI-versus-V1 result is available.",
                   "limitations": ["Exploratory paper simulation only; no profitability or independent validation claim.",
                                   "Underlying cached data provenance, corporate-action adjustment, and universe coverage remain unresolved.",
                                   "AI responses must have been recorded point-in-time; this evaluator cannot fabricate historical AI decisions."]})
    _write_output(output_dir, run_id, config, source_hashes, events, trades, complete=True, report=report)
    return EvaluationResult(run_id, str(output_dir / "report.json"), str(output_dir / "events.jsonl"),
                            len(records), len(trades), "BLOCKED", report["comparison_blocker"])


def _write_output(output_dir: Path, run_id: str, config: dict, source_hashes: dict,
                  events: list[dict], trades: list[dict], *, complete: bool,
                  report: dict | None = None) -> None:
    event_path = output_dir / "events.jsonl"
    with event_path.open("x", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        stream.flush()
    event_hash = hashlib.sha256(event_path.read_bytes()).hexdigest()
    payload = report or {"evaluation_version": EVALUATION_VERSION, "run_id": run_id,
                         "complete": complete, "source_hashes": source_hashes, "config": config,
                         "events_written": len(events), "trades_written": len(trades),
                         "status": "INCOMPLETE"}
    payload["events_sha256"] = event_hash
    payload["trades"] = trades
    if report is not None:
        payload["complete"] = complete
    (output_dir / "report.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
