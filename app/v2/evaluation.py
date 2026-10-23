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

from app.cost_model import CostAssumptions, estimate_round_trip_costs, nse_transaction_rate
from app.research.execution_integrity import ExecutionIntegrityError, TimestampSemanticsEvidence
from app.research.sharp_reversal_experiment import load_session_dates
from .ai_led_research import AIResearchDecision, AIResearchRequest, ResearchAction, safe_no_trade
from .contracts import Action, DecisionRequest, MarketRegime, RankedSymbol, V2Recommendation, parse_timestamp
from .registry import StrategyCandidate, StrategyRegistry
from .safety import RiskLimits, RiskState, SafetyGate

IST = ZoneInfo("Asia/Kolkata")
BAR = timedelta(minutes=5)
EVALUATION_VERSION = "ai-led-paper-evaluation-v1.1"
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


def _split_round_trip_costs(entry_price: float, exit_price: float, quantity: int,
                            trade_date: date, assumptions: CostAssumptions) -> tuple[dict, dict]:
    """Allocate the existing round-trip formula to its actual buy and sell legs."""
    legs = []
    for side, price in (("entry", entry_price), ("exit", exit_price)):
        turnover = price * quantity
        brokerage = min(assumptions.brokerage_cap, turnover * assumptions.brokerage_rate)
        exchange = turnover * nse_transaction_rate(trade_date)
        ipft = turnover / 100_000.0 * assumptions.ipft_per_lakh
        sebi = turnover * assumptions.sebi_rate
        stt = turnover * assumptions.stt_sell_rate if side == "exit" else 0.0
        stamp = turnover * assumptions.stamp_buy_rate if side == "entry" else 0.0
        gst = assumptions.gst_rate * (brokerage + exchange + ipft)
        slippage = turnover * assumptions.slippage_bps_per_side / 10_000.0
        values = {"brokerage": brokerage, "stt": stt, "exchange": exchange,
                  "ipft": ipft, "sebi": sebi, "stamp_duty": stamp, "gst": gst,
                  "slippage": slippage}
        values["total_cost"] = sum(values.values())
        legs.append(values)
    combined = {key: legs[0][key] + legs[1][key] for key in legs[0] if key != "total_cost"}
    combined["total_cost"] = sum(combined.values())
    expected = estimate_round_trip_costs(entry_price, exit_price, quantity, trade_date, assumptions)
    if any(not math.isclose(combined[key], expected[key], rel_tol=1e-12, abs_tol=1e-9) for key in combined):
        raise EvaluationBlocked("buy/sell cost allocation does not reconcile with the existing round-trip model")
    return legs[0], legs[1]


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
                                        "same_session_only": True,
                                        "same_bar_precedence": ["opening_gap_stop", "opening_gap_target", "ai_exit_at_open",
                                                                "intrabar_stop_loss", "intrabar_target",
                                                                "maximum_holding_close", "session_close"],
                                        "stop_first_if_stop_and_target_touch": True},
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
    active_day: date | None = None
    event_context = {"paper_only": True, "execution_enabled": False}
    frames = {symbol: group.reset_index(drop=True)
              for symbol, group in candles.groupby("symbol", sort=True)}
    positions: dict[str, dict] = {}

    def emit(signal_id, stamp, event_type, payload, *, event_time=None, position_id=None):
        events.append({"event_id": f"{run_id}:{len(events)+1}", "run_id": run_id, **event_context,
                      "signal_id": signal_id, "decision_id": signal_id,
                      "recorded_at": (event_time or stamp).isoformat(),
                      "position_id": position_id, "source_hashes": source_hashes,
                      "event_type": event_type, **payload})

    def reject(signal_id, stamp, event_type, payload, *, position_id=None):
        emit(signal_id, stamp, event_type, payload, position_id=position_id)
        emit(signal_id, stamp, "TRADE_REJECTED", payload, position_id=position_id)

    def close_position(position, reference, stamp, reason, *, signal_id, event_stamp=None):
        symbol = position["symbol"]
        quantity = position["quantity"]
        entry_costs, exit_costs = _split_round_trip_costs(position["reference_entry"], reference,
                                                          quantity, stamp.date(), costs)
        breakdown = estimate_round_trip_costs(position["reference_entry"], reference,
                                              quantity, stamp.date(), costs)
        gross = (reference - position["reference_entry"]) * quantity
        net = gross - breakdown["total_cost"]
        trade = {"position_id": position["position_id"], "entry_signal_id": position["entry_signal_id"],
                 "entry_decision_id": position["entry_signal_id"], "exit_signal_id": signal_id,
                 "exit_decision_id": signal_id, "symbol": symbol, "strategy_id": position["strategy_id"],
                 "strategy_version": position["strategy_version"], "market_regime": position["regime"],
                 "side": "LONG", "status": "CLOSED", "stop_loss": position["stop"],
                 "target_price": position["target"], "maximum_holding_bars": position["horizon"],
                 "quantity": quantity, "entry_timestamp": position["entry_timestamp"].isoformat(),
                 "exit_timestamp": stamp.isoformat(), "entry_reference": position["reference_entry"],
                 "exit_reference": reference, "entry_fill": position["entry_fill"],
                 "exit_fill": reference * (1 - costs.slippage_bps_per_side / 10_000),
                 "entry_costs": entry_costs, "exit_costs": exit_costs,
                 "holding_bars": position["holding_bars"], "exit_reason": reason,
                 "gross_pnl": gross, "costs": breakdown, "net_pnl": net,
                 "source_hashes": source_hashes}
        trades.append(trade)
        state.capital += net
        state.daily_loss += max(0.0, -net)
        del state.open_positions[symbol]
        del positions[symbol]
        event_at = event_stamp or stamp + BAR
        if reason in {"STOP_LOSS", "TARGET"}:
            emit(signal_id, stamp, f"{reason}_EXIT", {"reference_price": reference},
                 event_time=event_at, position_id=position["position_id"])
        elif reason == "SESSION_CLOSE":
            emit(signal_id, stamp, "FORCED_SESSION_CLOSE", {"reference_price": reference},
                 event_time=event_at, position_id=position["position_id"])
        elif reason == "AI_EXIT":
            emit(signal_id, stamp, "EXIT_FILL", {"reference_price": reference},
                 event_time=event_at, position_id=position["position_id"])
        emit(signal_id, stamp, "SIMULATED_EXIT", trade, event_time=event_at,
             position_id=position["position_id"])
        emit(signal_id, stamp, "COSTS_RECORDED", {"costs": breakdown, "entry_costs": entry_costs,
             "exit_costs": exit_costs},
             event_time=event_at, position_id=position["position_id"])
        emit(signal_id, stamp, "TRADE_OUTCOME", {"gross_pnl": gross, "net_pnl": net,
             "costs": breakdown, "exit_reason": reason, "source_hashes": source_hashes},
             event_time=event_at, position_id=position["position_id"])

    def advance_positions(until: datetime) -> None:
        """Process only bars whose START-labeled interval has completed by `until`."""
        for symbol in list(positions):
            position = positions.get(symbol)
            if position is None:
                continue
            frame = frames[symbol]
            # A pending order fills at the eligible bar open; no OHLC is inspected until
            # that bar has completed and its label + five minutes is visible.
            if position["status"] == "PENDING_ENTRY":
                if position["entry_timestamp"] > until:
                    continue
                if kill_switch.enabled or not _verified_session(position["entry_timestamp"], session_dates):
                    reject(position["entry_signal_id"], until, "ENTRY_REJECTED",
                         {"reason": "kill switch or session invalid at simulated fill"},
                         position_id=position["position_id"])
                    del state.open_positions[symbol]
                    del positions[symbol]
                    continue
                position["status"] = "OPEN"
                state.open_positions[symbol] = {"quantity": position["quantity"],
                    "entry_price": position["entry_fill"], "allocation": position["quantity"] * position["entry_fill"]}
                state.trades_today += 1
                emit(position["entry_signal_id"], position["entry_timestamp"], "SIMULATED_FILL",
                     {"symbol": symbol, "strategy_id": position["strategy_id"],
                      "quantity": position["quantity"], "bar_timestamp": position["entry_timestamp"].isoformat(),
                      "reference_price": position["reference_entry"], "fill_price": position["entry_fill"],
                      "entry_costs": position["entry_costs"], "source_hashes": source_hashes},
                     event_time=position["entry_timestamp"], position_id=position["position_id"])
            if position.get("pending_exit") is not None:
                exit_request = position["pending_exit"]
            else:
                exit_request = None
            start_index = position["entry_index"]
            next_index = position.get("next_index", start_index)
            while next_index < len(frame):
                bar = frame.iloc[next_index]
                stamp = bar.timestamp
                if stamp + BAR > until:
                    break
                if stamp.date() != position["entry_timestamp"].date() or not _verified_session(stamp, session_dates):
                    break
                if next_index > start_index:
                    prior = frame.iloc[next_index - 1].timestamp
                    if stamp - prior != BAR:
                        raise EvaluationBlocked(f"missing candle while position is open: {symbol} {prior + BAR}")
                opn, low, high, close = map(float, (bar.open, bar.low, bar.high, bar.close))
                held = next_index - start_index + 1
                reason = None
                exit_ref = None
                # Opening gaps are handled first. A stop gap receives the worse open;
                # target gaps are capped at target. Otherwise an AI exit executes at open.
                if opn <= position["stop"]:
                    reason, exit_ref = "STOP_LOSS", opn
                elif opn >= position["target"]:
                    reason, exit_ref = "TARGET", position["target"]
                elif exit_request is not None and stamp > exit_request["recorded_at"]:
                    reason, exit_ref = "AI_EXIT", opn
                elif low <= position["stop"]:
                    reason, exit_ref = "STOP_LOSS", position["stop"]
                elif high >= position["target"]:
                    reason, exit_ref = "TARGET", position["target"]
                elif held >= position["horizon"]:
                    reason, exit_ref = "HORIZON", close
                session = session_dates[stamp.date()]
                intervals = session.get("normal_market_intervals", [])
                interval = next((row for row in intervals
                    if time.fromisoformat(row["start"]) <= stamp.timetz().replace(tzinfo=None)
                    < time.fromisoformat(row["end"])), None)
                session_close = datetime.combine(stamp.date(), time.fromisoformat(interval["end"]), IST) if interval else None
                if reason is None and session_close == stamp + BAR:
                    reason, exit_ref = "SESSION_CLOSE", close
                position["holding_bars"] = held
                next_index += 1
                position["next_index"] = next_index
                if reason:
                    closing_signal = exit_request["signal_id"] if reason == "AI_EXIT" else position["entry_signal_id"]
                    close_position(position, exit_ref, stamp, reason, signal_id=closing_signal)
                    break

    def schedule_entry(signal_id, recorded, decision, candidates, request, risk):
        symbol = decision.symbol
        frame = frames.get(symbol)
        if frame is None:
            return "decision symbol has no candle source"
        cutoff = _iso(request.market.data_cutoff, "data_cutoff")
        if len(frame.index[frame.timestamp.eq(cutoff)]) != 1:
            return "decision cutoff candle missing or ambiguous"
        later = frame.index[frame.timestamp.gt(recorded)].tolist()
        if not later:
            return "no strictly future entry candle"
        index = later[0]
        bar = frame.iloc[index]
        entry_time, entry_ref = bar.timestamp, float(bar.open)
        if entry_time.date() != cutoff.date() or not _verified_session(entry_time, session_dates):
            return "entry is not a future same-session candle"
        intervening = frame[(frame.timestamp >= cutoff) & (frame.timestamp <= entry_time)]
        if list(intervening.timestamp) != list(pd.date_range(cutoff, entry_time, freq="5min", tz=IST)):
            return "missing candle before entry"
        deviation = abs(entry_ref - float(decision.entry_price)) / float(decision.entry_price) * 100
        if deviation > MAX_ENTRY_DEVIATION_PCT:
            return "next-open price exceeds the bounded AI entry deviation"
        fill = entry_ref * (1 + costs.slippage_bps_per_side / 10_000)
        stop, target, horizon = float(decision.stop_loss), float(decision.target_price), decision.holding_bars
        stop_pct = (fill - stop) / fill * 100
        target_pct = (target - fill) / fill * 100
        if not limits.min_stop_distance_pct <= stop_pct <= limits.max_stop_distance_pct or not 0 < target_pct <= limits.max_target_distance_pct:
            return "actual entry fill violates stop/target distance limits"
        risk_qty = int((state.capital * limits.max_risk_per_trade) // (fill - stop))
        used_allocation = sum(float(row.get("allocation", 0.0)) for row in state.open_positions.values())
        allocation_cap = min(state.capital * limits.max_allocation, max(0.0, state.capital - used_allocation))
        quantity = min(risk.quantity, risk_qty, int(allocation_cap // fill))
        if quantity <= 0:
            return "actual fill cannot satisfy risk or capital bounds"
        position_id = f"{run_id}:position:{_json_hash([signal_id, symbol])[:16]}"
        version = next(x.version for x in candidates if x.strategy_id == decision.strategy_id)
        entry_costs, _unused_exit_costs = _split_round_trip_costs(entry_ref, entry_ref, quantity,
                                                                  entry_time.date(), costs)
        positions[symbol] = {"position_id": position_id, "entry_signal_id": signal_id,
            "symbol": symbol, "strategy_id": decision.strategy_id, "strategy_version": version,
            "regime": decision.market_regime.value, "entry_index": index, "next_index": index,
            "entry_timestamp": entry_time, "reference_entry": entry_ref, "entry_fill": fill,
            "entry_costs": entry_costs, "quantity": quantity, "stop": stop, "target": target,
            "horizon": horizon, "holding_bars": 0, "status": "PENDING_ENTRY", "pending_exit": None}
        state.open_positions[symbol] = {"quantity": quantity, "entry_price": fill,
            "allocation": quantity * fill}
        emit(signal_id, recorded, "ENTRY_ACCEPTED", {"symbol": symbol, "quantity": quantity,
            "position_id": position_id, "entry_timestamp": entry_time.isoformat(),
            "stop_loss": stop, "target": target, "maximum_holding_bars": horizon,
            "source_hashes": source_hashes}, position_id=position_id)
        return None

    try:
        for signal_id, recorded_text, body, request, decision, candidates in parsed_records:
            recorded = _iso(recorded_text, "ledger recorded_at")
            advance_positions(recorded)
            if active_day != recorded.date():
                if positions and any(p["entry_timestamp"].date() != recorded.date() for p in positions.values()):
                    raise EvaluationBlocked("position crossed a verified session boundary")
                if not positions:
                    state.daily_loss, state.trades_today = 0.0, 0
                    state.day_start_capital = state.capital
                active_day = recorded.date()
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
            if state.open_positions and recorded.date() != next(iter(positions.values()))["entry_timestamp"].date():
                raise EvaluationBlocked("position crossed a verified session boundary without a close")
            action = decision.action
            if action is ResearchAction.NO_TRADE:
                emit(signal_id, recorded, "NO_TRADE", {"decision_action": action.value,
                    "recorded_final_decision": body.get("final_decision"),
                    "reason": decision.rationale, "source_hashes": source_hashes})
                continue
            if action is ResearchAction.HOLD:
                open_positions = [p for p in positions.values() if p["status"] == "OPEN"]
                if open_positions:
                    for position in open_positions:
                        emit(signal_id, recorded, "POSITION_HELD", {"symbol": position["symbol"],
                            "reason": decision.rationale, "source_hashes": source_hashes},
                            position_id=position["position_id"])
                else:
                    emit(signal_id, recorded, "NO_TRADE", {"decision_action": action.value,
                        "reason": "no filled position to hold", "source_hashes": source_hashes})
                continue
            if action is ResearchAction.BUY and decision.symbol in positions:
                duplicate_position_id = f"{run_id}:position:{_json_hash([signal_id, decision.symbol])[:16]}"
                emit(signal_id, recorded, "TRADE_PROPOSED", {"symbol": decision.symbol,
                    "strategy_id": decision.strategy_id, "decision_action": action.value,
                    "data_cutoff": _iso(request.market.data_cutoff, "data_cutoff").isoformat()},
                    position_id=duplicate_position_id)
                emit(signal_id, recorded, "ENTRY_PROPOSED", {"symbol": decision.symbol,
                    "strategy_id": decision.strategy_id}, position_id=duplicate_position_id)
                reject(signal_id, recorded, "ENTRY_REJECTED", {"reason": "signal duplicates an existing pending or open instrument"},
                       position_id=duplicate_position_id)
                continue
            if action is ResearchAction.EXIT:
                position = positions.get(decision.symbol or "")
                emit(signal_id, recorded, "EXIT_PROPOSED", {"symbol": decision.symbol,
                    "source_hashes": source_hashes}, position_id=position["position_id"] if position else None)
                if position is None or position["status"] != "OPEN":
                    reject(signal_id, recorded, "EXIT_REJECTED", {"reason": "EXIT has no matching filled open position"})
                    continue
                if position.get("pending_exit") is not None:
                    reject(signal_id, recorded, "EXIT_REJECTED", {"reason": "an exit is already pending"},
                         position_id=position["position_id"])
                    continue
                if decision.strategy_id != position["strategy_id"]:
                    reject(signal_id, recorded, "EXIT_REJECTED", {"reason": "EXIT strategy does not match the open position"},
                         position_id=position["position_id"])
                    continue
                if decision.symbol not in request.market.open_symbols:
                    reject(signal_id, recorded, "EXIT_REJECTED", {"reason": "EXIT instrument is absent from recorded open_symbols"},
                         position_id=position["position_id"])
                    continue
                recommendation = V2Recommendation(Action.EXIT, decision.market_regime,
                    tuple(RankedSymbol(x.symbol, x.rank, x.evidence) for x in decision.ranked_symbols),
                    decision.symbol, decision.strategy_id, decision.entry_price, decision.stop_loss,
                    decision.target_price, decision.holding_bars, decision.rationale, decision.key_evidence,
                    decision.confidence)
                risk = gate.approve_research(request.market, recommendation, now=recorded, signal_id=signal_id)
                if not risk.approved:
                    reject(signal_id, recorded, "EXIT_REJECTED", {"reason": risk.reason}, position_id=position["position_id"])
                    continue
                position["pending_exit"] = {"signal_id": signal_id, "recorded_at": recorded}
                emit(signal_id, recorded, "EXIT_ACCEPTED", {"symbol": decision.symbol,
                    "execution": "NEXT_ELIGIBLE_BAR_OPEN"}, position_id=position["position_id"])
                continue
            emit(signal_id, recorded, "TRADE_PROPOSED", {"symbol": decision.symbol,
                "strategy_id": decision.strategy_id, "strategy_version": next(
                    x.version for x in candidates if x.strategy_id == decision.strategy_id),
                "decision_action": action.value, "recorded_final_decision": body.get("final_decision"),
                "entry_condition": decision.entry_condition,
                "ai_estimated_round_trip_cost_pct": decision.estimated_round_trip_cost_pct,
                "data_cutoff": cutoff.isoformat(), "source_hashes": source_hashes},
                position_id=f"{run_id}:position:{_json_hash([signal_id, decision.symbol])[:16]}")
            proposed_position_id = f"{run_id}:position:{_json_hash([signal_id, decision.symbol])[:16]}"
            emit(signal_id, recorded, "ENTRY_PROPOSED", {"symbol": decision.symbol,
                "strategy_id": decision.strategy_id, "data_cutoff": cutoff.isoformat()},
                position_id=proposed_position_id)
            if action is ResearchAction.BUY and decision.entry_condition != SUPPORTED_ENTRY_CONDITION:
                reject(signal_id, recorded, "ENTRY_REJECTED", {"reason": "entry condition is outside the implemented execution contract"},
                       position_id=proposed_position_id)
                continue
            recommendation = V2Recommendation(
                Action.BUY, decision.market_regime,
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
                reject(signal_id, recorded, "ENTRY_REJECTED", {"reason": risk.reason,
                    "risk_quantity": risk.quantity, "source_hashes": source_hashes},
                    position_id=proposed_position_id)
                continue
            reason = schedule_entry(signal_id, recorded, decision, candidates, request, risk)
            if reason:
                reject(signal_id, recorded, "ENTRY_REJECTED", {"reason": reason, "source_hashes": source_hashes},
                       position_id=proposed_position_id)

        if parsed_records:
            latest = max(frame.timestamp.max() + BAR for frame in frames.values())
            advance_positions(latest)
        if positions:
            raise EvaluationBlocked("paper run ended with an unresolved open or pending position")
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
