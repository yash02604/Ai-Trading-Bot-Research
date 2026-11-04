"""Reproducible DeepSeek historical research evaluation (v1), paper-only.

Architecture (reuses existing components):
- Quant candidates: app.reversal_detector (frozen 1-2% decline / RSI 20-25 setup).
- Risk controls:   app.risk_engine (RiskConfig/RiskState/check_trade_allowed).
- Costs:           app.cost_model.estimate_round_trip_costs.
- Candle hygiene:  app.candle_integrity.validate_candles (fail-closed filtering).
- Provider:        app.v2.provider.DeepSeekProvider (bounded, no retries).

Safety properties:
- Point-in-time: features for a decision at bar t are computed from bars <= t only.
- AI can only confirm or veto a quantitative candidate; it can never create a
  signal, lift a risk rejection, or size a position. Anything other than an
  explicit BUY is treated as HOLD/veto.
- Responses are cached by sha256(model, prompt_version, request); repeated
  evaluations make zero provider calls.
- A request budget caps live calls; exceeding it fails the run closed.
- Live historical mode is gated on the provenance-integrity audit reporting
  VERIFIED for the dataset; the current dataset is UNRESOLVED, so live
  historical evaluation is blocked rather than fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from app.candle_integrity import validate_candles
from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.historical_validation import _simulate_exit as _historical_simulate_exit
from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates, is_frozen_reversal
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from strategy_backtest import STOCKS

PROMPT_VERSION = "deepseek-research-eval-v1"
DECISION_LEDGER_VERSION = "deepseek-eval-decision-ledger-v1"
STOP_PCT = 1.0
HORIZON = 6
BAR_MINUTES = 5
DEFAULT_INITIAL_CAPITAL = 100_000.0
RISK_1PCT = RiskConfig(capital=DEFAULT_INITIAL_CAPITAL)  # 1%/trade, 2%/day, 1 position, 3 trades/day

_UNRESOLVED = "UNRESOLVED"
_REQUEST_ID_SAFE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------- provenance

def evaluate_provenance_gate(summary_path, candles_path=None) -> tuple[bool, list[str]]:
    """Historical-live evaluation may run only when the provenance audit
    reports the dataset VERIFIED, has no unresolved findings, and binds the
    exact candle bytes supplied to this run."""
    path = Path(summary_path)
    if not path.exists():
        return False, ["provenance summary file is missing"]
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"provenance summary is unreadable: {type(exc).__name__}"]
    reasons = []
    status = summary.get("overall_dataset_status")
    if status != "VERIFIED":
        reasons.append(f"overall_dataset_status is {status!r}, not 'VERIFIED'")
    binding_status = summary.get("dataset_binding_status")
    if binding_status != "VERIFIED":
        reasons.append(f"exact candle byte binding is {binding_status!r}, not 'VERIFIED'")
    for finding in summary.get("findings", []):
        if isinstance(finding, dict) and finding.get("status") == _UNRESOLVED:
            reasons.append(f"unresolved provenance finding: {finding.get('finding_id')}")
    status_counts = summary.get("status_counts", {})
    if isinstance(status_counts, dict):
        unresolved_count = status_counts.get("UNRESOLVED", 0)
        if isinstance(unresolved_count, int) and not isinstance(unresolved_count, bool) and unresolved_count > 0:
            reasons.append(f"provenance audit reports {unresolved_count} unresolved finding(s)")
        invalidated_count = status_counts.get("INVALIDATED", 0)
        if isinstance(invalidated_count, int) and not isinstance(invalidated_count, bool) and invalidated_count > 0:
            reasons.append(f"provenance audit reports {invalidated_count} invalidated finding(s)")
    if summary.get("missing_inputs"):
        reasons.append(f"provenance audit lists missing inputs: {summary['missing_inputs']}")
    if not isinstance(summary.get("findings", []), list):
        reasons.append("provenance findings are malformed")
    if candles_path is not None:
        try:
            actual_hash = _file_sha256(candles_path)
        except OSError:
            reasons.append("candle data file is missing or unreadable")
        else:
            bound_hashes = summary.get("dataset_sha256")
            expected_hash = (bound_hashes.get(Path(candles_path).name)
                             if isinstance(bound_hashes, dict) else bound_hashes)
            if not isinstance(expected_hash, str) or not _REQUEST_ID_SAFE.fullmatch(expected_hash):
                reasons.append("provenance summary does not bind the exact candle file SHA-256")
            elif expected_hash != actual_hash:
                reasons.append("candle file SHA-256 does not match the verified provenance summary")
    return (not reasons), reasons


# ------------------------------------------------------------------- loading

def load_candles(path, allowed_symbols=None) -> dict[str, pd.DataFrame]:
    """Load candles fail-closed (drop only rows flagged by candle_integrity)."""
    frame = pd.read_csv(path)
    required = {"stock", "timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"candle data is missing columns: {sorted(missing)}")
    frame["stock"] = frame["stock"].astype(str).str.upper()
    keys = ["instrument_key"] if "instrument_key" in frame else ["stock"]
    integrity = validate_candles(frame, key_columns=keys)
    row_reasons: dict[int, set[str]] = {}
    for error in integrity.errors:
        if error.get("row") is not None:
            row_reasons.setdefault(error["row"], set()).add(error["reason"])
    if any("duplicate_instrument_timestamp" in v for v in row_reasons.values()):
        raise ValueError("candle data has duplicate instrument/timestamp rows")
    keep = pd.Series([i not in row_reasons for i in range(len(frame))], index=frame.index)
    valid = frame.loc[keep].copy()
    if allowed_symbols is not None:
        valid = valid[valid.stock.isin({s.upper() for s in allowed_symbols})]
    valid["timestamp"] = pd.to_datetime(valid["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    valid = valid.sort_values(["stock", "timestamp"], kind="stable").reset_index(drop=True)
    return {symbol: group.reset_index(drop=True)
            for symbol, group in valid.groupby("stock", sort=True)}


def _point_in_time_view(frame: pd.DataFrame, signal_index: int) -> pd.DataFrame:
    """Rows up to and including the signal bar; nothing later is visible."""
    if signal_index < 0 or signal_index >= len(frame):
        raise ValueError("signal index out of range")
    return frame.iloc[: signal_index + 1]


def _prepare_quant_candidates(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[int]]:
    """Prepare causal indicators and frozen-setup candidates for one symbol.

    Indicators at index i depend only on bars <= i (verified by
    tests/test_historical_validation.py::test_indicators_at_a_timestamp_do_not_change_when_future_bars_are_appended),
    so candidate detection never looks ahead.
    """
    if frame.empty:
        return frame.copy(), []
    indicators = calculate_indicators(frame.copy())
    # calculate_indicators drops warm-up rows and resets its index. Re-align by
    # timestamp so each indicator stays attached to its actual candle.
    indicator_columns = ("ema_9", "ema_20", "rsi_14", "macd", "macd_signal", "volume_ma_20")
    aligned = frame[["timestamp"]].merge(
        indicators[["timestamp", *indicator_columns]], on="timestamp", how="left", sort=False
    )
    merged = frame.copy()
    for name in indicator_columns:
        merged[name] = aligned[name].to_numpy()
    scored = detect_reversal_candidates(merged, decline_candles=3, min_decline_pct=1.0,
                                        rsi_threshold=25.0)
    previous_close = merged["close"].shift(1)
    decline_pct = (merged["close"] - merged["close"].shift(3)) / merged["close"].shift(3) * 100
    merged["decline_pct"] = decline_pct
    candidates = []
    for idx in scored.index:
        row = {"decline_pct": decline_pct.at[idx], "rsi_14": merged.at[idx, "rsi_14"],
               "reversal_condition": bool(merged.at[idx, "close"] > previous_close.at[idx])}
        if is_frozen_reversal(row):
            candidates.append(int(idx))
    return merged, candidates


def _quant_candidates(frame: pd.DataFrame) -> list[int]:
    return _prepare_quant_candidates(frame)[1]


# ---------------------------------------------------------------- AI request

def build_decision_request(symbol: str, view: pd.DataFrame, signal_index: int) -> dict:
    """Request payload containing only information available at the signal bar."""
    row = view.iloc[signal_index]
    window = view.iloc[max(0, signal_index - 12): signal_index + 1]
    indicators = calculate_indicators(view.copy())
    indicator_row = indicators.loc[indicators["timestamp"] == row["timestamp"]]
    if indicator_row.empty:
        raise ValueError("signal candle is before indicator warm-up is complete")
    indicator_row = indicator_row.iloc[0]
    return {
        "prompt_version": PROMPT_VERSION,
        "symbol": symbol,
        "signal_timestamp": row["timestamp"].isoformat(),
        "indicators": {name: round(float(indicator_row[name]), 6) for name in
                       ("ema_9", "ema_20", "rsi_14", "macd", "volume_ma_20")},
        "recent_bars": [
            {"timestamp": bar["timestamp"].isoformat(), "open": float(bar["open"]),
             "high": float(bar["high"]), "low": float(bar["low"]), "close": float(bar["close"]),
             "volume": int(bar["volume"])}
            for _, bar in window.iterrows()
        ],
        "instruction": (
            "Decision support only for an Indian intraday long setup that already passed the "
            "quantitative eligibility and risk gates. You cannot create signals or size positions. "
            "Return only a json object with keys decision (BUY, SELL, or HOLD), confidence (0..1), reason. "
            "When uncertain, HOLD."
        ),
    }


def canonical_request_key(model: str, request: dict) -> str:
    payload = json.dumps({"model": model, "request": request},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_decision(content) -> dict:
    """Fail-safe parse: anything invalid is a HOLD veto."""
    hold = {"decision": "HOLD", "confidence": 0.0, "reason": "invalid or unavailable AI response"}
    if not isinstance(content, str) or not content.strip():
        return hold
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        return hold
    if not isinstance(result, dict):
        return hold
    decision = result.get("decision")
    confidence, reason = result.get("confidence"), result.get("reason")
    if decision not in {"BUY", "SELL", "HOLD"}:
        return hold
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return hold
    if not isinstance(reason, str) or not reason.strip():
        return hold
    return {"decision": decision, "confidence": float(confidence), "reason": reason.strip()}


def _is_valid_cached_decision(value, model: str) -> bool:
    return (
        isinstance(value, dict)
        and value.get("decision") in {"BUY", "SELL", "HOLD"}
        and isinstance(value.get("confidence"), (int, float))
        and not isinstance(value.get("confidence"), bool)
        and 0 <= value["confidence"] <= 1
        and isinstance(value.get("reason"), str)
        and bool(value["reason"].strip())
        and value.get("model") == model
        and isinstance(value.get("provider_failure", False), bool)
    )


class DecisionCache:
    """JSON-backed response cache keyed by canonical request hash."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        self.hits = 0

    def get(self, key: str):
        if not _REQUEST_ID_SAFE.fullmatch(key):
            raise ValueError("cache key must be a sha256 hex digest")
        if key in self._data:
            self.hits += 1
            return self._data[key]
        return None

    def put(self, key: str, value: dict) -> None:
        if not _REQUEST_ID_SAFE.fullmatch(key):
            raise ValueError("cache key must be a sha256 hex digest")
        self._data[key] = value
        self.path.write_text(json.dumps(self._data, sort_keys=True, indent=1),
                             encoding="utf-8", newline="\n")


class RequestBudget:
    def __init__(self, max_requests: int):
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 0:
            raise ValueError("max_requests must be a non-negative integer")
        self.max_requests = max_requests
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.max_requests:
            raise RuntimeError("request budget exhausted")
        self.used += 1


class BudgetExceeded(RuntimeError):
    pass


class DecisionLedger:
    """Append-only JSONL ledger of AI requests/decisions (no credentials)."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, str] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    record = json.loads(line)
                    request_id = record.get("request_id")
                    if not isinstance(request_id, str) or not _REQUEST_ID_SAFE.fullmatch(request_id):
                        raise ValueError("decision ledger contains an invalid request ID")
                    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
                    if request_id in self._records and self._records[request_id] != encoded:
                        raise ValueError("decision ledger contains conflicting duplicate request IDs")
                    self._records[request_id] = encoded

    def append(self, record: dict) -> None:
        request_id = record.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID_SAFE.fullmatch(request_id):
            raise ValueError("decision ledger request ID must be a sha256 hex digest")
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        if request_id in self._records:
            if self._records[request_id] != encoded:
                raise ValueError("decision ledger refuses a conflicting duplicate request ID")
            return
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
        self._records[request_id] = encoded


# ---------------------------------------------------------------- simulation

@dataclass
class EvaluationResult:
    classification: str
    status: str
    blockers: list[str] = field(default_factory=list)
    partitions: dict = field(default_factory=dict)
    request_count: int = 0
    cache_hits: int = 0
    vetoed_by_ai: int = 0
    config: dict = field(default_factory=dict)

    def to_report(self) -> dict:
        return {
            "schema_version": "deepseek-research-eval-v1",
            "results_classification": self.classification,
            "synthetic_inputs_only": self.classification == "SYNTHETIC",
            "results_note": ("Synthetic fixture output only; not historical trading performance."
                             if self.classification == "SYNTHETIC" else None),
            "status": self.status,
            "blockers": self.blockers,
            "request_count": self.request_count,
            "cache_hits": self.cache_hits,
            "ai_vetoed_candidates": self.vetoed_by_ai,
            "config": self.config,
            "partitions": self.partitions,
            "performance_evidence": self.status == "COMPLETED"
                                    and self.classification == "HISTORICAL",
        }


def _trade_metrics(trades: list[dict], initial_capital: float) -> dict:
    metrics = {
        "trade_count": len(trades),
        "gross_pnl": round(sum(t["gross_pnl"] for t in trades), 2),
        "estimated_costs": round(sum(t["total_cost"] for t in trades), 2),
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
        "win_rate": None,
        "profit_factor": None,
        "max_drawdown": None,
    }
    if trades:
        wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
        losses = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
        metrics["win_rate"] = round(len(wins) / len(trades), 4)
        metrics["profit_factor"] = (round(sum(wins) / abs(sum(losses)), 4)
                                    if losses else ("Infinity" if wins else 0.0))
        equity, peak, drawdown, drawdown_pct = initial_capital, initial_capital, 0.0, 0.0
        for trade in sorted(trades, key=lambda t: t["exit_timestamp"]):
            equity += trade["net_pnl"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            if peak > 0:
                drawdown_pct = max(drawdown_pct, (peak - equity) / peak * 100)
        metrics["max_drawdown"] = round(drawdown, 2)
        metrics["max_drawdown_pct"] = round(drawdown_pct, 4)
        metrics["return_pct"] = round(metrics["net_pnl"] / initial_capital * 100, 4)
    else:
        metrics["max_drawdown"] = 0.0
        metrics["max_drawdown_pct"] = 0.0
        metrics["return_pct"] = 0.0
    return metrics


def _summarize(trades: list[dict], initial_capital: float, symbols=()) -> dict:
    metrics = _trade_metrics(trades, initial_capital)
    by_symbol: dict[str, dict] = {}
    for symbol in sorted(set(symbols) | {trade["symbol"] for trade in trades}):
        by_symbol[symbol] = _trade_metrics(
            [trade for trade in trades if trade["symbol"] == symbol], initial_capital)
    metrics["per_symbol"] = by_symbol
    return metrics


def _simulate_exit(frame: pd.DataFrame, entry_index: int, stop: float) -> tuple[float, pd.Timestamp, str]:
    """Use the existing baseline's entry-bar, gap, stop, horizon, and session rules."""
    signal_index = entry_index - 1
    event = {
        "symbol": str(frame.at[entry_index, "stock"]),
        "signal_index": signal_index,
        "entry_index": entry_index,
        "signal_timestamp": frame.at[signal_index, "timestamp"],
        "decline_pct": float(frame.at[signal_index, "decline_pct"]),
        "rsi_14": float(frame.at[signal_index, "rsi_14"]),
    }
    simulated = _historical_simulate_exit(frame, event, STOP_PCT, HORIZON)
    return simulated["exit_price"], simulated["exit_timestamp"], simulated["exit_reason"]


def _signal_window_is_valid(frame: pd.DataFrame, signal_index: int) -> bool:
    if signal_index < 3:
        return False
    timestamps = frame.iloc[signal_index - 3:signal_index + 1]["timestamp"].tolist()
    return len(timestamps) == 4 and all(
        right.date() != left.date() or right - left == timedelta(minutes=BAR_MINUTES)
        for left, right in zip(timestamps, timestamps[1:])
    )


def _holdout_timestamp(value):
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("Asia/Kolkata")
    return stamp.tz_convert("Asia/Kolkata")


def _eligible_entry(frame: pd.DataFrame, signal_index: int) -> int | None:
    """Next bar only when it is the immediate 5-minute bar in the same session."""
    idx = signal_index + 1
    if idx >= len(frame):
        return None
    signal_time = frame.at[signal_index, "timestamp"]
    entry_time = frame.at[idx, "timestamp"]
    if entry_time.date() != signal_time.date() or entry_time - signal_time != timedelta(minutes=BAR_MINUTES):
        return None
    return idx


def run_evaluation(provider, *, mode: str, candles_path, provenance_summary,
                   output_dir, model: str = "unknown", initial_capital: float = DEFAULT_INITIAL_CAPITAL,
                   cache_path=None, ledger_path=None, max_requests: int = 0,
                   allow_live: bool = False, holdout_start=None) -> EvaluationResult:
    """Run the comparison. mode: OFFLINE_SYNTHETIC or HISTORICAL_LIVE."""
    if mode not in {"OFFLINE_SYNTHETIC", "HISTORICAL_LIVE"}:
        raise ValueError("mode must be OFFLINE_SYNTHETIC or HISTORICAL_LIVE")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = DecisionCache(cache_path or output_dir / "decision_cache.json")
    ledger = DecisionLedger(ledger_path or output_dir / "decision_ledger.jsonl")

    if mode == "HISTORICAL_LIVE":
        gate_ok, blockers = evaluate_provenance_gate(provenance_summary, candles_path)
        if not allow_live:
            blockers.append("live provider calls require explicit --allow-live")
        if provider is None:
            blockers.append("no live provider configured (DEEPSEEK_API_KEY is required)")
        if gate_ok and not blockers:
            result = _execute(provider, mode=mode, candles_path=candles_path, model=model,
                              initial_capital=initial_capital, cache=cache, ledger=ledger,
                              budget=RequestBudget(max_requests), holdout_start=holdout_start)
            counterfactual = _execute(None, mode=mode, candles_path=candles_path, model=model,
                                      initial_capital=initial_capital, cache=cache, ledger=ledger,
                                      budget=RequestBudget(0), holdout_start=holdout_start,
                                      ai_gate=False)
            for name, legs in counterfactual.partitions.items():
                result.partitions.setdefault(name, {}).update(legs)
                result.partitions[name]["existing_baseline"] = {
                    "status": "NOT_COMPARABLE",
                    "reason": ("The archived baseline cannot be verified against this exact "
                               "provenance-bound candle file and chronological split."),
                }
            result.classification = "HISTORICAL"
            return result
        return EvaluationResult(classification="BLOCKED", status="BLOCKED_MISSING_PREREQUISITES",
                                blockers=sorted(set(blockers)),
                                config={"mode": mode, "max_requests": max_requests,
                                        "provenance_summary": str(provenance_summary)})
    if allow_live and provider is not None and not getattr(provider, "is_mock", False):
        return EvaluationResult(classification="BLOCKED", status="BLOCKED_MODE_MISMATCH",
                                blockers=["--allow-live is invalid in OFFLINE_SYNTHETIC mode"],
                                config={"mode": mode})
    result = _execute(provider, mode=mode, candles_path=candles_path, model=model,
                      initial_capital=initial_capital, cache=cache, ledger=ledger,
                      budget=RequestBudget(max_requests), holdout_start=holdout_start)
    counterfactual = _execute(None, mode=mode, candles_path=candles_path, model=model,
                              initial_capital=initial_capital, cache=cache, ledger=ledger,
                              budget=RequestBudget(0), holdout_start=holdout_start,
                              ai_gate=False)
    for name, legs in counterfactual.partitions.items():
        result.partitions.setdefault(name, {}).update(legs)
    return result


def _execute(provider, *, mode, candles_path, model, initial_capital, cache, ledger,
             budget: RequestBudget, holdout_start, ai_gate: bool = True) -> EvaluationResult:
    frames = load_candles(candles_path)
    split = _holdout_timestamp(holdout_start)
    if split is not None:
        first_timestamp = min(frame.timestamp.min() for frame in frames.values())
        last_timestamp = max(frame.timestamp.max() for frame in frames.values())
        if not first_timestamp < split <= last_timestamp:
            raise ValueError("holdout_start must leave nonempty chronological train and holdout periods")
    partitions = {"train": [], "holdout": []}
    vetoed = 0
    capital = initial_capital
    daily_trades: dict = {}
    daily_losses: dict = {}
    last_exit = None
    prepared, events = {}, []
    symbol_priority = {symbol: rank for rank, symbol in enumerate(STOCKS)}
    for symbol, source_frame in frames.items():
        frame, candidates = _prepare_quant_candidates(source_frame)
        prepared[symbol] = frame
        for signal_index in candidates:
            if not _signal_window_is_valid(frame, signal_index):
                continue
            entry_index = _eligible_entry(frame, signal_index)
            if entry_index is not None:
                events.append((frame.at[signal_index, "timestamp"], symbol_priority.get(symbol, len(symbol_priority)),
                               symbol, signal_index, entry_index))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    provider_failures = 0
    if ai_gate and provider is None:
        return EvaluationResult(
            classification="SYNTHETIC" if mode == "OFFLINE_SYNTHETIC" else "HISTORICAL",
            status="BLOCKED_NO_PROVIDER", blockers=["AI decisions require a configured provider"],
            config={"mode": mode, "model": model, "max_requests": budget.max_requests})

    for signal_time, _priority, symbol, signal_index, entry_index in events:
        frame = prepared[symbol]
        entry_time = frame.at[entry_index, "timestamp"]
        if last_exit is not None and entry_time <= last_exit:
            continue
        day = entry_time.date()
        if capital <= 0:
            continue
        risk_config = RiskConfig(capital=capital)
        state = RiskState(daily_loss=daily_losses.get(day, 0.0), open_positions=0,
                          trades_today=daily_trades.get(day, 0))
        allowed, _reason = check_trade_allowed(risk_config, state)
        if not allowed:
            continue
        partition = "holdout" if split is not None and signal_time >= split else "train"
        if split is not None and partition == "train":
            if entry_time >= split:
                continue
            final_horizon_index = min(entry_index + HORIZON - 1, len(frame) - 1)
            if frame.at[final_horizon_index, "timestamp"] >= split:
                continue
        ai_confidence = None
        if ai_gate:
            request = build_decision_request(
                symbol, _point_in_time_view(frame, signal_index), signal_index)
            key = canonical_request_key(model, request)
            cached = cache.get(key)
            if cached is not None and not _is_valid_cached_decision(cached, model):
                raise ValueError("cached AI decision does not satisfy the versioned response contract")
            if cached is None:
                try:
                    budget.consume()
                except RuntimeError as exc:
                    return EvaluationResult(
                        classification="SYNTHETIC" if mode == "OFFLINE_SYNTHETIC" else "HISTORICAL",
                        status="BLOCKED_BUDGET_EXHAUSTED", blockers=[str(exc)],
                        request_count=budget.used, cache_hits=cache.hits,
                        vetoed_by_ai=vetoed,
                        config={"mode": mode, "model": model,
                                "max_requests": budget.max_requests})
                try:
                    provider_result = provider.complete({
                        "system_prompt": "You are an intraday decision-support analyst. Use only the provided payload; do not infer later prices. Reply with json only.",
                        "user_payload": json.dumps(request, sort_keys=True, separators=(",", ":")),
                    })
                    requested_model = getattr(provider_result, "requested_model_id", model)
                    returned_model = getattr(provider_result, "model_id", model)
                    if requested_model != model or returned_model != model:
                        raise ValueError("provider requested-model identity mismatch")
                    cached = _parse_decision(provider_result.content)
                    cached["model"] = model
                    cached["provider_failure"] = False
                except Exception as exc:
                    cached = {"decision": "HOLD", "confidence": 0.0,
                              "reason": f"provider failure ({type(exc).__name__})",
                              "model": model, "provider_failure": True}
                cache.put(key, cached)
            ledger.append({"schema_version": DECISION_LEDGER_VERSION, "request_id": key,
                           "symbol": symbol, "signal_timestamp": signal_time.isoformat(),
                           "partition": partition, "decision": cached["decision"],
                           "confidence": cached["confidence"], "reason": cached["reason"]})
            if cached.get("provider_failure"):
                provider_failures += 1
            if cached["decision"] != "BUY":
                vetoed += 1
                continue
            ai_confidence = cached["confidence"]
        entry_price = float(frame.at[entry_index, "open"])
        stop = entry_price * (1.0 - STOP_PCT / 100.0)
        size = calculate_position_size(risk_config, entry_price, stop)
        if size.quantity <= 0:
            continue
        exit_price, exit_time, exit_reason = _simulate_exit(frame, entry_index, stop)
        gross = (exit_price - entry_price) * size.quantity
        costs = estimate_round_trip_costs(entry_price, exit_price, size.quantity,
                                          entry_time, assumptions=CostAssumptions())
        net = gross - costs["total_cost"]
        capital += net
        daily_losses[day] = daily_losses.get(day, 0.0) + (abs(net) if net < 0 else 0.0)
        daily_trades[day] = daily_trades.get(day, 0) + 1
        last_exit = exit_time
        partitions[partition].append({
            "symbol": symbol, "signal_timestamp": signal_time.isoformat(),
            "entry_timestamp": entry_time.isoformat(), "exit_timestamp": exit_time.isoformat(),
            "entry_price": entry_price, "exit_price": exit_price, "exit_reason": exit_reason,
            "quantity": size.quantity, "gross_pnl": round(gross, 2),
            "total_cost": round(costs["total_cost"], 2), "net_pnl": round(net, 2),
            "ai_confidence": ai_confidence,
        })
    quant_partitions: dict[str, dict] = {}
    for name, trades in partitions.items():
        if ai_gate:
            quant_partitions[name] = {
                "ai_assisted": _summarize(trades, initial_capital, frames),
            }
        else:
            quant_partitions[name] = {
                "quantitative_only": _summarize(trades, initial_capital, frames),
            }
    classification = "SYNTHETIC" if mode == "OFFLINE_SYNTHETIC" else "HISTORICAL"
    return EvaluationResult(
        classification=classification,
        status="COMPLETED_WITH_FAILURES" if provider_failures else "COMPLETED",
        blockers=([f"{provider_failures} provider request(s) failed and were treated as HOLD."]
                  if provider_failures else []),
        partitions=quant_partitions,
        request_count=budget.used,
        cache_hits=cache.hits,
        vetoed_by_ai=vetoed,
        config={"mode": mode, "model": model, "stop_pct": STOP_PCT, "horizon_bars": HORIZON,
                "initial_capital": initial_capital, "holdout_start":
                    str(holdout_start) if holdout_start else None,
                "candles_sha256": _file_sha256(candles_path),
                "provider_failures": provider_failures},
    )


def _file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
