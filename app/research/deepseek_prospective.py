"""Opt-in prospective, paper-only DeepSeek decision-quality research.

This module records point-in-time snapshots, exact prompts, safe decisions and
later shadow outcomes. It has no broker/order interface. Historical provenance
is not used or changed by this prospective workflow.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time

import pandas as pd

from app.cost_model import (
    CostAssumptions, estimate_buy_side_costs, estimate_round_trip_costs,
)
from app.candle_integrity import validate_candles
from app.research.deepseek_eval_v1 import (
    PROMPT_VERSION, _parse_decision, _prepare_quant_candidates,
    build_decision_request, canonical_request_key,
)
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from app.research.prospective_preflight import prospective_preflight, validate_request_snapshot
from strategy_backtest import STOCKS

PROSPECTIVE_SCHEMA = "deepseek-prospective-observation-v1"
OUTCOME_SCHEMA = "deepseek-prospective-outcome-v2"
STOP_PCT = 1.0
HORIZON_BARS = 6
EXPERIMENT_CAPITAL_CONFIG = "prospective_experiment_config.json"
EXPERIMENT_CAPITAL_SCHEMA = "deepseek-prospective-experiment-config-v1"
_SECRET_VALUE = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._~+/-]+=*)")
_SECRET_FIELD = re.compile(
    r"(?i)((?:api[_ -]?key|access[_ -]?token|authorization)\s*[:=]\s*[\"']?)[^\s,\"'}]+"
)
_SAFE_FAILURE_MESSAGES = {
    "authentication": "Upstox authentication failed or access token is not configured",
    "rate_limit": "Upstox rate limit reached",
    "http_error": "Upstox returned an HTTP error",
    "network_error": "Upstox transport request failed",
    "json_decode": "Upstox response was not valid JSON",
    "candle_schema": "Upstox candle response schema is invalid",
    "timestamp_validation": "Candle timestamp validation failed",
    "invalid_ohlcv": "Candle OHLCV validation failed",
    "insufficient_completed_candles": "Fewer than 35 completed candles are available",
    "request_budget_exhausted": "Market-data request budget exhausted",
}
_GENERIC_FAILURE_MESSAGE = "Unexpected error; details redacted"
_SAFE_FAILURE_STAGES = {
    "authentication": {"authentication", "upstox_http"},
    "rate_limit": {"upstox_http"},
    "http_error": {"upstox_http"},
    "network_error": {"upstox_http"},
    "json_decode": {"response_json"},
    "candle_schema": {"response_schema", "candle_validation"},
    "timestamp_validation": {"candle_validation"},
    "invalid_ohlcv": {"candle_validation"},
    "insufficient_completed_candles": {"completed_candle_count"},
    "request_budget_exhausted": {"request_budget"},
}
_SAFE_CANDLE_REASONS = {
    "missing_required_columns", "missing_duplicate_key_columns",
    "timezone_naive_timestamp", "invalid_timestamp", "nonfinite_or_nonnumeric_ohlcv",
    "nonpositive_price", "negative_volume", "invalid_ohlc_relationship",
    "timestamp_not_five_minute_aligned", "duplicate_instrument_timestamp",
}
_SAFE_CANDLE_FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}
_LOCAL_LOCK_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}


@contextmanager
def _experiment_lock(output_dir):
    """Serialize each directory's decision and budget writes across processes."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    key = str(output.resolve())
    with _LOCAL_LOCK_GUARD:
        local_lock = _LOCAL_LOCKS.setdefault(key, threading.RLock())
    with local_lock, (output / ".prospective.lock").open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        deadline = time.monotonic() + 30.0
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("prospective output directory is busy") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _aware_timestamp(value, field: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError(f"{field} must include an explicit timezone offset")
    return stamp.tz_convert("Asia/Kolkata")


def _redact_secrets(value: str, api_key: str | None = None) -> str:
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    value = _SECRET_VALUE.sub("[REDACTED]", value)
    return _SECRET_FIELD.sub(r"\1[REDACTED]", value)


def _safe_market_data_message(category: str, status_code: int | None = None) -> str:
    """Return allowlisted diagnostics; never serialize arbitrary exception text."""
    message = _SAFE_FAILURE_MESSAGES.get(category)
    if message is None:
        return _GENERIC_FAILURE_MESSAGE
    if category == "http_error" and isinstance(status_code, int) and 400 <= status_code <= 599:
        return f"Upstox returned HTTP status {status_code}"
    return message


def _safe_exception_type(exc: Exception) -> str:
    try:
        name = type(exc).__name__
        return name if (type(name) is str and
                        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)) else "Exception"
    except Exception:
        return "Exception"


def _validated_candle_error_details(exc: Exception) -> tuple[str, list[dict]] | None:
    from app.candle_integrity import CandleDataError, CandleValidation

    if not isinstance(exc, CandleDataError):
        return None
    try:
        report = exc.report
        if (type(report) is not CandleValidation or type(report.frame) is not pd.DataFrame
                or type(report.errors) is not list or type(report.warnings) is not list
                or not report.errors):
            return None
        details = []
        reasons = set()
        for item in report.errors:
            if type(item) is not dict:
                return None
            reason = item.get("reason")
            if type(reason) is not str or reason not in _SAFE_CANDLE_REASONS:
                return None
            row = item.get("row")
            if row is not None and (type(row) is not int or row < 0):
                return None
            fields = item.get("fields")
            if fields is not None:
                if (type(fields) is not list or
                        any(type(field) is not str or field not in _SAFE_CANDLE_FIELDS
                            for field in fields)):
                    return None
            reasons.add(reason)
            details.append({"reason": reason, "fields": fields})

        if reasons & {"invalid_timestamp", "timezone_naive_timestamp",
                      "timestamp_not_five_minute_aligned", "duplicate_instrument_timestamp"}:
            category = "timestamp_validation"
        elif reasons & {"nonfinite_or_nonnumeric_ohlcv", "nonpositive_price",
                        "negative_volume", "invalid_ohlc_relationship"}:
            category = "invalid_ohlcv"
        elif reasons & {"missing_required_columns", "missing_duplicate_key_columns"}:
            category = "candle_schema"
        else:
            return None
        return category, details
    except Exception:
        return None


def _exception_message_is(exc: Exception, expected: str) -> bool:
    try:
        return str(exc) == expected
    except Exception:
        return False


def _capture_diagnostic(exc: Exception, stage: str) -> tuple[str, str]:
    from app.upstox_client import UpstoxRequestError
    if isinstance(exc, UpstoxRequestError):
        if (isinstance(exc.category, str) and isinstance(exc.stage, str) and
                exc.stage in _SAFE_FAILURE_STAGES.get(exc.category, set())):
            return exc.stage, exc.category
        return stage, "unexpected_failure"
    if (stage == "request_budget" and isinstance(exc, RuntimeError) and
            _exception_message_is(exc, "prospective provider request budget exhausted")):
        return stage, "request_budget_exhausted"
    from app.candle_integrity import CandleDataError
    if isinstance(exc, CandleDataError):
        details = _validated_candle_error_details(exc)
        if details is not None:
            return "candle_validation", details[0]
        return stage, "unexpected_failure"
    if isinstance(exc, ValueError):
        if (stage == "response_schema" and
                _exception_message_is(exc, "Upstox response contains no candle list")):
            return stage, "candle_schema"
        if (stage == "completed_candle_count" and
                _exception_message_is(exc, "fewer than 35 completed candles are available")):
            return "completed_candle_count", "insufficient_completed_candles"
    return stage, "unexpected_failure"


def _filtered_ohlcv_diagnostics(raw_candles) -> dict | None:
    """Summarize OHLCV rows completed_frame would filter, without changing them."""
    filtered_reasons = {"nonpositive_price", "negative_volume", "invalid_ohlc_relationship"}
    try:
        raw_frame = pd.DataFrame(raw_candles, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "open_interest",
        ])
        report = validate_candles(raw_frame, check_duplicates=False)
        row_reasons: dict[int, set[str]] = {}
        for error in report.errors:
            reason = error.get("reason")
            row = error.get("row")
            if reason in filtered_reasons and isinstance(row, int):
                row_reasons.setdefault(row, set()).add(reason)
        reason_counts = {
            reason: sum(reason in reasons for reasons in row_reasons.values())
            for reason in sorted(filtered_reasons)
            if any(reason in reasons for reasons in row_reasons.values())
        }
        return {"filtered_row_count": len(row_reasons), "reason_counts": reason_counts}
    except Exception:
        # Diagnostics must not become a new data acceptance or rejection path.
        return None


def _safe_decision(raw_response: str | None, api_key: str | None = None) -> dict:
    parsed = _parse_decision(raw_response)
    parsed["reason"] = _redact_secrets(parsed["reason"], api_key)
    return parsed


def _load_snapshot(snapshot_path, *, now: datetime, max_age_seconds: int = 900):
    path = Path(snapshot_path)
    raw_bytes = path.read_bytes()
    try:
        snapshot = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != PROSPECTIVE_SCHEMA:
        raise ValueError(f"snapshot schema_version must be {PROSPECTIVE_SCHEMA}")
    symbol = snapshot.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", symbol):
        raise ValueError("snapshot symbol is missing or invalid")
    symbol = symbol.upper()
    expected_instrument = STOCKS.get(symbol)
    instrument_key = snapshot.get("instrument_key")
    if expected_instrument is None:
        raise ValueError("snapshot symbol is not in the configured stock universe")
    if instrument_key != expected_instrument:
        raise ValueError("snapshot instrument_key does not match the configured symbol mapping")
    source = snapshot.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("snapshot source label is required")
    observed_at = _aware_timestamp(snapshot.get("observed_at"), "observed_at")
    decision_time = pd.Timestamp(now)
    if decision_time.tzinfo is None:
        decision_time = decision_time.tz_localize("UTC")
    decision_time = decision_time.tz_convert("Asia/Kolkata")
    if observed_at > decision_time:
        raise ValueError("snapshot observed_at cannot be later than decision time")
    if (decision_time - observed_at).total_seconds() > max_age_seconds:
        raise ValueError("snapshot observed_at is stale for prospective decision capture")
    bars = snapshot.get("candles")
    if not isinstance(bars, list) or len(bars) < 35:
        raise ValueError("snapshot must contain at least 35 candles for indicator warm-up")
    frame = pd.DataFrame(bars)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError("snapshot candle rows are missing required OHLCV fields")
    parsed_times = [_aware_timestamp(item, "candle timestamp") for item in frame["timestamp"]]
    if any(right <= left for left, right in zip(parsed_times, parsed_times[1:])):
        raise ValueError("snapshot candles must be strictly chronological without duplicates")
    if any(stamp > observed_at for stamp in parsed_times):
        raise ValueError("snapshot contains a candle later than its observed_at timestamp")
    frame["stock"] = symbol.upper()
    frame["timestamp"] = parsed_times
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("snapshot contains nonnumeric OHLCV fields")
    integrity = validate_candles(frame, key_columns=["stock"])
    if integrity.errors:
        raise ValueError("snapshot candle integrity validation failed")
    latest_candle_age = (observed_at - frame["timestamp"].iloc[-1]).total_seconds()
    if latest_candle_age < 0 or latest_candle_age > max_age_seconds:
        raise ValueError("latest candle is stale or later than observation for prospective decision capture")
    # Preserve original candle rows in the recorded snapshot; this normalized
    # frame is used only for point-in-time quantitative calculations.
    return snapshot, raw_bytes, frame.reset_index(drop=True), observed_at, decision_time


class AppendOnlyJsonl:
    """Idempotent append-only event file, keyed by a caller-selected field."""

    def __init__(self, path, key_field: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key_field = key_field
        self._records = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    record = json.loads(line)
                    key = record.get(key_field)
                    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
                    if not isinstance(key, str) or key in self._records:
                        raise ValueError(f"{self.path.name} has a missing or duplicate {key_field}")
                    self._records[key] = encoded

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]

    def append(self, record: dict) -> bool:
        key = record.get(self.key_field)
        if not isinstance(key, str) or not key:
            raise ValueError(f"record requires {self.key_field}")
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        prior = self._records.get(key)
        if prior is not None:
            if prior != encoded:
                raise ValueError(f"refusing conflicting duplicate {self.key_field}")
            return False
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
        self._records[key] = encoded
        return True


class PersistentRequestBudget:
    """A cross-run request cap; attempts are reserved before network access."""

    def __init__(self, path, maximum: int):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError("max_total_requests must be a non-negative integer")
        path = Path(path)
        self.ledger = AppendOnlyJsonl(path, "request_id")
        self.config_path = path.with_name(f"{path.stem}_config.json")
        if self.config_path.exists():
            try:
                config = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("request budget configuration is unreadable") from exc
            persisted_maximum = config.get("max_total_requests") if isinstance(config, dict) else None
            if (isinstance(persisted_maximum, bool) or not isinstance(persisted_maximum, int)
                    or persisted_maximum < 0):
                raise ValueError("request budget configuration is invalid")
            if maximum != persisted_maximum:
                raise ValueError("max_total_requests must match the persistent request budget")
        else:
            # Exclusive creation prevents a second process from silently
            # replacing the lifetime cap chosen by the first run.
            try:
                with self.config_path.open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump({"schema_version": "deepseek-request-budget-v1",
                               "max_total_requests": maximum}, stream, sort_keys=True)
                    stream.write("\n")
            except FileExistsError:
                config = json.loads(self.config_path.read_text(encoding="utf-8"))
                if config.get("max_total_requests") != maximum:
                    raise ValueError("max_total_requests must match the persistent request budget")
        self.maximum = maximum

    @property
    def used(self) -> int:
        return len(self.ledger._records)

    def reserve(self, request_id: str) -> bool:
        if request_id in self.ledger._records:
            return False
        if self.used >= self.maximum:
            raise RuntimeError("prospective provider request budget exhausted")
        self.ledger.append({"request_id": request_id, "reserved_at_utc":
                            datetime.now(timezone.utc).isoformat()})
        return True


class ProspectiveCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.hits = 0

    def get(self, key):
        value = self.rows.get(key)
        if value is not None:
            self.hits += 1
        return value

    def put(self, key, value):
        prior = self.rows.get(key)
        if prior is not None:
            if prior != value:
                raise ValueError("refusing to replace a cached provider response")
            return
        next_rows = {**self.rows, key: value}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", dir=self.path.parent, prefix=".response_cache_",
                    suffix=".tmp", encoding="utf-8", newline="\n",
                    delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(next_rows, sort_keys=True, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        self.rows = next_rows


def _quant_baseline(frame: pd.DataFrame, *, capital: float, has_open_paper_trade: bool,
                    trades_today: int = 0, daily_loss: float = 0.0,
                    available_cash: float | None = None) -> dict:
    prepared, candidates = _prepare_quant_candidates(frame)
    signal_index = len(prepared) - 1
    qualifies = signal_index in candidates
    if not qualifies:
        return {"decision": "HOLD", "eligible": False, "reason": "no frozen quantitative candidate"}
    try:
        capital = float(capital)
    except (TypeError, ValueError, OverflowError):
        capital = math.nan
    if not math.isfinite(capital) or capital <= 0:
        return {"decision": "HOLD", "eligible": False,
                "reason": "paper capital must be finite and positive"}
    config = RiskConfig(capital=capital)
    state = RiskState(daily_loss=daily_loss, open_positions=int(has_open_paper_trade),
                      trades_today=trades_today)
    allowed, reason = check_trade_allowed(config, state)
    if not allowed:
        return {"decision": "HOLD", "eligible": False, "reason": reason}
    next_index = signal_index + 1
    if next_index >= len(prepared):
        entry_price = float(prepared.at[signal_index, "close"])
    else:
        entry_price = float(prepared.at[next_index, "open"])
    if not math.isfinite(entry_price) or entry_price <= 0:
        return {"decision": "HOLD", "eligible": False,
                "reason": "invalid entry price for cost-aware sizing"}
    stop = entry_price * (1.0 - STOP_PCT / 100)
    size = calculate_position_size(config, entry_price, stop)
    if size.quantity <= 0:
        return {"decision": "HOLD", "eligible": False, "reason": "risk sizing produced zero shares"}
    entry_timestamp = prepared.at[signal_index, "timestamp"]
    cost_check = _cost_aware_position_check(
        capital=capital, entry_price=entry_price, stop_price=stop,
        quantity=size.quantity, trade_timestamp=entry_timestamp,
        available_cash=available_cash,
    )
    if not cost_check["eligible"]:
        return {"decision": "HOLD", "eligible": False,
                "reason": cost_check["reason"], "cost_eligibility": cost_check}
    return {"decision": "BUY_CANDIDATE", "eligible": True, "reason": reason,
            "entry_reference": entry_price, "stop_reference": stop,
            "quantity": size.quantity, "risk_per_trade_pct": config.max_risk_per_trade * 100,
            "cost_eligibility": cost_check}


def _cost_aware_position_check(*, capital, entry_price, stop_price, quantity,
                               trade_timestamp, available_cash=None) -> dict:
    """Fail-closed entry cash and stop-risk checks for one whole-share size.

    Stop exposure is estimated at the frozen stop plus modeled round-trip costs.
    A stop gap or execution variance can still cause a larger realized loss.
    """
    failure = {"eligible": False, "reason": "invalid or unavailable cost inputs"}
    try:
        capital = float(capital)
        available_cash = capital if available_cash is None else float(available_cash)
        entry_price = float(entry_price)
        stop_price = float(stop_price)
        stamp = pd.Timestamp(trade_timestamp)
        if (not math.isfinite(capital) or capital <= 0
                or not math.isfinite(available_cash) or available_cash < 0
                or available_cash > capital
                or not math.isfinite(entry_price) or entry_price <= 0
                or not math.isfinite(stop_price) or stop_price <= 0
                or stop_price >= entry_price
                or isinstance(quantity, bool) or not isinstance(quantity, int)
                or quantity <= 0 or pd.isna(stamp) or stamp.tzinfo is None):
            return failure
        assumptions = CostAssumptions()
        trade_date = stamp.date()
        buy_costs = estimate_buy_side_costs(
            entry_price, quantity, trade_date, assumptions=assumptions,
        )
        round_trip = estimate_round_trip_costs(
            entry_price, stop_price, quantity, trade_date, assumptions=assumptions,
        )
        required_buy_components = {
            "brokerage", "exchange", "ipft", "sebi", "stamp_duty", "gst",
            "slippage", "total_cost",
        }
        required_round_trip_components = {
            "brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty",
            "gst", "slippage", "total_cost",
        }
        if (not isinstance(buy_costs, dict) or not required_buy_components.issubset(buy_costs)
                or not isinstance(round_trip, dict)
                or not required_round_trip_components.issubset(round_trip)):
            return failure
        all_costs = [buy_costs[key] for key in required_buy_components]
        all_costs.extend(round_trip[key] for key in required_round_trip_components)
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
               for value in all_costs):
            return failure
        notional = entry_price * quantity
        entry_cash_required = notional + float(buy_costs["total_cost"])
        stop_loss = (entry_price - stop_price) * quantity
        cost_inclusive_stop_risk = stop_loss + float(round_trip["total_cost"])
        max_risk = capital * RiskConfig(capital=capital).max_risk_per_trade
        if not all(math.isfinite(value) and value >= 0 for value in
                   (notional, entry_cash_required, stop_loss, cost_inclusive_stop_risk, max_risk)):
            return failure
        details = {
            "eligible": True,
            "reason": "cost-aware eligibility passed",
            "quantity": quantity,
            "entry_notional": round(notional, 6),
            "estimated_buy_side_costs": round(float(buy_costs["total_cost"]), 6),
            "entry_cash_required": round(entry_cash_required, 6),
            "available_cash": round(available_cash, 6),
            "estimated_stop_loss": round(stop_loss, 6),
            "estimated_round_trip_costs": round(float(round_trip["total_cost"]), 6),
            "cost_inclusive_stop_risk": round(cost_inclusive_stop_risk, 6),
            "max_trade_risk": round(max_risk, 6),
            "stop_gap_can_exceed_estimate": True,
        }
        if entry_cash_required > capital:
            details.update(eligible=False, reason="entry notional plus buy-side costs exceeds capital")
        elif entry_cash_required > available_cash:
            details.update(eligible=False, reason="entry notional plus buy-side costs exceeds available account cash")
        elif cost_inclusive_stop_risk > max_risk:
            details.update(eligible=False, reason="cost-inclusive stop risk exceeds the trade-risk limit")
        return details
    except Exception:
        # Provider, date-rate, malformed-data, and cost-model failures all fail closed.
        return failure


def _saved_observation_capital(row: dict) -> float:
    """Read an observation's recorded capital without falling back to defaults."""
    if not isinstance(row, dict):
        raise ValueError("existing prospective observation is malformed")
    top_level = row.get("capital")
    configuration = row.get("configuration")
    nested = configuration.get("paper_capital") if isinstance(configuration, dict) else None
    candidates = [value for value in (top_level, nested) if value is not None]
    if not candidates:
        raise ValueError("existing prospective observation has no recorded capital")
    try:
        values = [float(value) for value in candidates]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("existing prospective observation has invalid capital") from exc
    if (any(not math.isfinite(value) or value <= 0 for value in values)
            or any(value != values[0] for value in values[1:])):
        raise ValueError("existing prospective observation has inconsistent capital")
    return values[0]


def _experiment_source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    names = (
        "app/research/deepseek_prospective.py", "app/research/deepseek_eval_v1.py",
        "app/reversal_detector.py", "app/market_analysis.py",
        "app/candle_integrity.py", "app/risk_engine.py", "app/cost_model.py",
        "app/v2/provider.py", "app/upstox_client.py",
        "app/research/prospective_preflight.py", "app/client_attestation.py",
        "app/session_manifest_v3.py", "app/research/offline_session_calendar.py",
    )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in names}


def _experiment_spec(model: str | None = None) -> dict:
    return {
        "model_id": model,
        "prompt_version": PROMPT_VERSION,
        "stop_pct": STOP_PCT,
        "horizon_bars": HORIZON_BARS,
        "frozen_decline_candles": 3,
        "frozen_decline_pct_bounds": [-2.0, -1.0],
        "frozen_rsi_bounds": [20.0, 25.0],
        "cost_assumptions": asdict(CostAssumptions()),
        "source_sha256": _experiment_source_hashes(),
    }


def ensure_prospective_experiment_capital(output_dir, capital: float,
                                          *, model: str | None = None,
                                          request_policy_sha256: str | None = None) -> float:
    """Freeze/check one output directory's initial capital without rewriting it.

    Legacy directories with observations but no manifest remain usable only when
    all recorded observations agree with the requested capital. Their files are
    not rewritten. Other nonempty legacy outputs fail closed because their
    experiment capital cannot be established safely.
    """
    try:
        capital = float(capital)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("paper capital must be finite and positive") from exc
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("paper capital must be finite and positive")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / EXPERIMENT_CAPITAL_CONFIG
    observations_path = output / "observations.jsonl"
    saved_capital = None
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if (not isinstance(config, dict)
                    or config.get("schema_version") != EXPERIMENT_CAPITAL_SCHEMA):
                raise ValueError("experiment capital configuration is invalid")
            saved_capital = float(config["initial_capital"])
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError,
                ValueError, OverflowError) as exc:
            raise ValueError("experiment capital configuration is unreadable or invalid") from exc
        if not math.isfinite(saved_capital) or saved_capital <= 0:
            raise ValueError("experiment capital configuration is unreadable or invalid")
        if request_policy_sha256 is not None and config.get("request_policy_sha256") != request_policy_sha256:
            raise ValueError("request policy differs from saved experiment configuration")
        saved_spec = config.get("experiment_spec")
        if saved_spec is not None:
            current_spec = _experiment_spec(model)
            if not isinstance(saved_spec, dict):
                raise ValueError("experiment specification is invalid")
            for name, expected in current_spec.items():
                if name == "model_id" and (model is None or saved_spec.get(name) is None):
                    continue
                if saved_spec.get(name) != expected:
                    raise ValueError("experiment specification differs from saved configuration")
    elif observations_path.exists() and observations_path.stat().st_size:
        try:
            with observations_path.open(encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream if line.strip()]
            prospective_rows = [row for row in rows
                                if isinstance(row, dict) and
                                row.get("classification") == "PROSPECTIVE_OBSERVATION"]
            if not prospective_rows:
                raise ValueError("existing observations do not identify experiment capital")
            legacy_capitals = [_saved_observation_capital(row) for row in prospective_rows]
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("existing experiment capital cannot be verified") from exc
        if any(value != legacy_capitals[0] for value in legacy_capitals[1:]):
            raise ValueError("existing experiment contains multiple initial capital values")
        saved_capital = legacy_capitals[0]
    elif any(path.name not in {"observations.jsonl", ".prospective.lock"}
             for path in output.iterdir()):
        raise ValueError("existing output has no saved experiment capital; use a new output directory")

    if saved_capital is not None:
        if saved_capital != capital:
            raise ValueError("paper capital differs from this experiment's saved initial capital")
        if observations_path.exists() and observations_path.stat().st_size:
            try:
                with observations_path.open(encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if (isinstance(row, dict)
                                and row.get("classification") == "PROSPECTIVE_OBSERVATION"
                                and _saved_observation_capital(row) != saved_capital):
                            raise ValueError("existing observation capital differs from experiment configuration")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError("existing experiment capital cannot be verified") from exc
        return saved_capital

    config = {"schema_version": EXPERIMENT_CAPITAL_SCHEMA,
              "initial_capital": capital, "currency": "INR",
              "experiment_spec": _experiment_spec(model)}
    if request_policy_sha256 is not None:
        config["request_policy_sha256"] = request_policy_sha256
    try:
        with config_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(config, sort_keys=True, indent=2) + "\n")
    except FileExistsError:
        return ensure_prospective_experiment_capital(
            output, capital, model=model, request_policy_sha256=request_policy_sha256)
    return capital


def capture_upstox_snapshot(symbol: str, *, output_dir, max_market_data_requests: int,
                            allow_market_data_requests: bool = False) -> Path:
    with _experiment_lock(output_dir):
        return _capture_upstox_snapshot_unlocked(
            symbol, output_dir=output_dir,
            max_market_data_requests=max_market_data_requests,
            allow_market_data_requests=allow_market_data_requests,
        )


def _capture_upstox_snapshot_unlocked(symbol: str, *, output_dir,
                                      max_market_data_requests: int,
                                      allow_market_data_requests: bool = False) -> Path:
    """Capture completed candles through the existing Upstox read-only V3 client."""
    if not allow_market_data_requests:
        raise ValueError("Upstox requests require explicit market-data opt-in")
    normalized_symbol = str(symbol).upper()
    instrument_key = STOCKS.get(normalized_symbol)
    if instrument_key is None:
        raise ValueError("symbol is not in the configured Upstox research universe")
    from app.config import get_settings
    settings = get_settings()
    requested_at = datetime.now(timezone.utc)
    preflight = prospective_preflight(
        output_dir=output_dir, symbol=normalized_symbol, now=requested_at,
        kind="market_data", maximum=max_market_data_requests)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    failures = AppendOnlyJsonl(output / "market_data_failures.jsonl", "failure_id")
    if not settings.upstox_access_token:
        failure_id = hashlib.sha256(
            f"upstox-missing-token|{instrument_key}|{requested_at.isoformat()}".encode()
        ).hexdigest()
        failures.append({"failure_id": failure_id, "symbol": normalized_symbol,
                         "instrument_key": instrument_key,
                         "attempted_at_utc": requested_at.isoformat(),
                         "error_type": "MissingUpstoxAccessToken",
                         "stage": "authentication",
                         "category": "authentication",
                         "message": "Upstox access token is not configured",
                         "classification": "PROSPECTIVE_MARKET_DATA_FAILURE"})
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is not configured; failure recorded")

    budget = PersistentRequestBudget(output / "market_data_request_budget.jsonl",
                                     max_market_data_requests)
    request_id = hashlib.sha256(
        f"upstox-v3-intraday|{instrument_key}|{requested_at.isoformat()}".encode()
    ).hexdigest()
    events = AppendOnlyJsonl(output / "market_data_events.jsonl", "request_id")
    stage = "request_budget"
    ohlcv_filter_diagnostics = None
    try:
        reserved = budget.reserve(request_id)
        if not reserved:
            raise RuntimeError("market-data request already reserved")
        stage = "upstox_http"
        from app.upstox_client import get_intraday_candles
        from app.paper_session import completed_frame
        response = get_intraday_candles(instrument_key, interval=5)
        received_at = datetime.now(timezone.utc)
        stage = "response_schema"
        raw_candles = response.get("data", {}).get("candles") if isinstance(response, dict) else None
        if not isinstance(raw_candles, list) or not raw_candles:
            raise ValueError("Upstox response contains no candle list")
        ohlcv_filter_diagnostics = _filtered_ohlcv_diagnostics(raw_candles)
        stage = "candle_validation"
        frame = completed_frame(raw_candles, received_at)
        if len(frame) < 35:
            stage = "completed_candle_count"
            raise ValueError("fewer than 35 completed candles are available")
        snapshot = {
            "schema_version": PROSPECTIVE_SCHEMA,
            "source": "upstox-v3-intraday-readonly",
            "symbol": normalized_symbol,
            "instrument_key": instrument_key,
            "observed_at": received_at.isoformat(),
            "candles": [
                {"timestamp": row.timestamp.isoformat(), "open": float(row.open),
                 "high": float(row.high), "low": float(row.low),
                 "close": float(row.close), "volume": int(row.volume)}
                for row in frame.itertuples(index=False)
            ],
        }
        # The API attempt is already reserved. A rejected response must never
        # refund that reservation or become evidence for a provider request.
        validate_request_snapshot(preflight, snapshot, frame, received_at, received_at)
        snapshots = output / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        stamp = received_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = snapshots / f"{normalized_symbol}_{stamp}.json"
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
        events.append({"request_id": request_id, "symbol": normalized_symbol,
                       "instrument_key": instrument_key,
                       "requested_at_utc": requested_at.isoformat(),
                       "received_at_utc": received_at.isoformat(),
                       "snapshot_path": str(path), "snapshot_sha256": hashlib.sha256(
                           encoded.encode("utf-8")).hexdigest(),
                       "completed_candles": len(frame),
                       "ohlcv_filter_diagnostics": ohlcv_filter_diagnostics,
                       "classification": "PROSPECTIVE_MARKET_DATA_CAPTURE"})
        return path
    except Exception as exc:
        failure_stage, category = _capture_diagnostic(exc, stage)
        validation_details = None
        validated_details = _validated_candle_error_details(exc)
        if validated_details is not None and validated_details[0] == category:
            validation_details = validated_details[1]
        from app.upstox_client import UpstoxRequestError
        raw_status_code = (vars(exc).get("status_code")
                           if isinstance(exc, UpstoxRequestError) else None)
        status_code = (raw_status_code if category in {"authentication", "rate_limit", "http_error"}
                       and type(raw_status_code) is int and 100 <= raw_status_code <= 599 else None)
        failures.append({"failure_id": request_id, "request_id": request_id,
                         "symbol": normalized_symbol, "instrument_key": instrument_key,
                         "attempted_at_utc": requested_at.isoformat(),
                         "error_type": _safe_exception_type(exc),
                         "stage": failure_stage,
                         "category": category,
                         "message": _safe_market_data_message(category, status_code),
                         **({"http_status": status_code} if status_code is not None else {}),
                         **({"validation_errors": validation_details}
                            if validation_details is not None else {}),
                         **({"ohlcv_filter_diagnostics": ohlcv_filter_diagnostics}
                            if ohlcv_filter_diagnostics is not None else {}),
                         "classification": "PROSPECTIVE_MARKET_DATA_FAILURE"})
        raise RuntimeError(f"Upstox snapshot capture failed ({type(exc).__name__}); failure recorded") from None


def _arm_available_cash(initial_capital: float, outcomes: list[dict],
                        arm: str, as_of: pd.Timestamp) -> float:
    """Keep each shadow book within its original funding after realized P&L."""
    realized = []
    for outcome in outcomes:
        if outcome.get("classification") != "PROSPECTIVE_PAPER_OBSERVATION":
            continue
        trade = outcome.get(arm)
        if not isinstance(trade, dict) or type(trade.get("taken")) is not bool:
            raise ValueError("prior paper outcome has invalid arm state")
        if not trade["taken"]:
            continue
        exit_time = _aware_timestamp(outcome.get("exit_timestamp"), "exit_timestamp")
        if exit_time > as_of:
            raise ValueError("prior paper outcome is later than this decision")
        try:
            net_pnl = float(trade["default_net_pnl"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("prior paper outcome has invalid net P&L") from exc
        if not math.isfinite(net_pnl):
            raise ValueError("prior paper outcome has invalid net P&L")
        realized.append(net_pnl)
    balance = initial_capital + math.fsum(realized)
    if not math.isfinite(balance):
        raise ValueError("prior paper outcomes produce invalid account cash")
    return max(0.0, min(initial_capital, balance))


def _arm_trades_today(decisions: list[dict], outcomes: dict[str, dict],
                      entries: dict[str, dict], arm: str, day) -> int:
    count = 0
    for row in decisions:
        if _aware_timestamp(row["decision_time"], "decision_time").date() != day:
            continue
        decision_id = row["decision_id"]
        if decision_id in outcomes:
            count += outcomes[decision_id].get(arm, {}).get("taken") is True
        elif decision_id in entries:
            count += entries[decision_id][arm]["accepted"] is True
        else:
            # Reserve capacity while the entry candle has not yet arrived.
            count += 1
    return count


def _arm_has_open_position(decisions: list[dict], outcomes: dict[str, dict],
                           entries: dict[str, dict], arm: str) -> bool:
    return any(row["decision_id"] not in outcomes and
               (row["decision_id"] not in entries or
                entries[row["decision_id"]][arm]["accepted"] is True)
               for row in decisions)


def _entry_validation_for(decision: dict, frame: pd.DataFrame) -> dict | None:
    decision_time = _aware_timestamp(decision["decision_time"], "decision_time")
    future = frame.loc[frame["timestamp"] > decision_time].reset_index(drop=True)
    if future.empty:
        return None
    expected = decision_time.floor("5min") + pd.Timedelta(minutes=5)
    first = future.iloc[0]
    entry_time = first["timestamp"]
    quantity = int(decision["quantitative_baseline"].get("quantity", 0))
    initial_capital = float(decision["capital"])
    rejected = {"accepted": False, "cost_eligibility": {
        "eligible": False, "reason": "next available entry candle is missing"}}
    event = {
        "entry_event_id": decision["decision_id"],
        "decision_id": decision["decision_id"],
        "classification": "PROSPECTIVE_ENTRY_VALIDATION",
        "expected_entry_timestamp": expected.isoformat(),
        "first_future_timestamp": entry_time.isoformat(),
        "entry_timestamp": expected.isoformat(),
        "entry_price": None,
        "stop_reference": None,
        "proposed_quantity": quantity,
        "quantitative_baseline_trade": rejected,
        "deepseek_assisted_trade": {**rejected},
        "trade_placement_enabled": False,
    }
    if entry_time != expected or entry_time.date() != decision_time.date():
        return event
    entry_price = float(first["open"])
    if not math.isfinite(entry_price) or entry_price <= 0:
        event["quantitative_baseline_trade"] = {"accepted": False,
            "cost_eligibility": {"eligible": False, "reason": "invalid actual entry price"}}
        event["deepseek_assisted_trade"] = {**event["quantitative_baseline_trade"]}
        return event
    stop = entry_price * (1.0 - STOP_PCT / 100)
    event.update(entry_price=entry_price, stop_reference=stop)
    size = calculate_position_size(RiskConfig(capital=initial_capital), entry_price, stop)
    if quantity <= 0:
        baseline_check = {"eligible": False, "reason": "saved quantity is zero",
                          "risk_engine_quantity_at_entry": size.quantity}
    else:
        baseline_check = _cost_aware_position_check(
            capital=initial_capital, entry_price=entry_price, stop_price=stop,
            quantity=quantity, trade_timestamp=entry_time,
            available_cash=decision.get("baseline_available_cash", initial_capital),
        )
        if quantity > size.quantity:
            baseline_check["eligible"] = False
            baseline_check["reason"] = "saved quantity exceeds sizing allowed at actual entry"
            baseline_check["risk_engine_quantity_at_entry"] = size.quantity
    event["quantitative_baseline_trade"] = {
        "accepted": baseline_check["eligible"], "cost_eligibility": baseline_check}
    if baseline_check["eligible"] and decision.get("ai_assisted_eligible") is True:
        ai_check = _cost_aware_position_check(
            capital=initial_capital, entry_price=entry_price, stop_price=stop,
            quantity=quantity, trade_timestamp=entry_time,
            available_cash=decision.get("ai_available_cash", initial_capital),
        )
    else:
        ai_check = {"eligible": False, "reason": (
            "baseline actual-entry eligibility failed" if not baseline_check["eligible"]
            else "AI-assisted arm did not confirm candidate")}
    event["deepseek_assisted_trade"] = {
        "accepted": ai_check["eligible"], "cost_eligibility": ai_check}
    return event


def _rejected_entry_outcome(decision: dict, decision_id: str, *, entry_time,
                            entry_price: float | None, proposed_quantity: int,
                            stop_price: float | None, eligibility: dict,
                            ai_eligibility: dict | None = None) -> dict:
    """Persist an actual-entry rejection immediately, without simulating a trade."""
    reason = eligibility.get("reason", "actual entry failed cost-aware eligibility")
    rejected_trade = {
        "taken": False,
        "quantity": 0,
        "gross_pnl": 0.0,
        "cost_scenarios": {},
        "default_estimated_costs": 0.0,
        "default_net_pnl": 0.0,
        "rejection_reason": reason,
    }
    return {
        "schema_version": OUTCOME_SCHEMA,
        "outcome_id": decision_id,
        "decision_id": decision_id,
        "classification": "PROSPECTIVE_ENTRY_REJECTED",
        "entry_timestamp": entry_time.isoformat(),
        "entry_price": entry_price,
        "stop_reference": stop_price,
        "exit_timestamp": None,
        "exit_price": None,
        "exit_reason": "entry_rejected",
        "quantity": 0,
        "proposed_quantity": proposed_quantity,
        "gross_pnl": 0.0,
        "estimated_costs": 0.0,
        "cost_breakdown": {},
        "net_pnl": 0.0,
        "quantitative_baseline_trade": rejected_trade,
        "deepseek_assisted_trade": {**rejected_trade},
        "entry_cost_eligibility": eligibility,
        "ai_entry_cost_eligibility": ai_eligibility,
        "rejection_reason": reason,
        "outcome_observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_note": "Actual entry rejected before an accepted paper position; no exit horizon was simulated.",
    }


def _outcome_for(decision: dict, frame: pd.DataFrame, decision_id: str,
                 entry_event: dict):
    decision_time = _aware_timestamp(decision["decision_time"], "decision_time")
    future = frame.loc[frame["timestamp"] > decision_time].reset_index(drop=True)
    entry_time = _aware_timestamp(entry_event["entry_timestamp"], "entry_timestamp")
    entry_price = entry_event["entry_price"]
    stop = entry_event["stop_reference"]
    quantity = entry_event["proposed_quantity"]
    entry_cost_check = entry_event["quantitative_baseline_trade"]["cost_eligibility"]
    ai_cost_check = entry_event["deepseek_assisted_trade"]["cost_eligibility"]
    if entry_event["quantitative_baseline_trade"]["accepted"] is not True:
        return _rejected_entry_outcome(
            decision, decision_id, entry_time=entry_time, entry_price=entry_price,
            proposed_quantity=quantity, stop_price=stop, eligibility=entry_cost_check,
            ai_eligibility=ai_cost_check,
        )
    if (future.empty or future.iloc[0]["timestamp"] != entry_time or
            float(future.iloc[0]["open"]) != entry_price):
        raise ValueError("saved actual-entry candle is unavailable or changed")
    trade_accepted = True
    accepted_quantity = quantity
    observed = []
    previous = None
    for _, bar in future.iterrows():
        stamp = bar["timestamp"]
        if stamp.date() != entry_time.date():
            break
        if previous is not None and stamp - previous != pd.Timedelta(minutes=5):
            break
        observed.append(bar)
        previous = stamp
        if len(observed) >= HORIZON_BARS:
            break
    if not observed:
        return None
    exit_bar = None
    exit_reason = None
    exit_price = None
    for bar in observed:
        opening, low = float(bar["open"]), float(bar["low"])
        if opening <= stop:
            exit_bar, exit_price, exit_reason = bar, opening, "stop_gap"
            break
        if low <= stop:
            exit_bar, exit_price, exit_reason = bar, stop, "stop"
            break
    if exit_bar is None and len(observed) < HORIZON_BARS:
        return None
    if exit_bar is None:
        exit_bar = observed[-1]
        exit_price, exit_reason = float(exit_bar["close"]), "horizon"
    gross = (exit_price - entry_price) * accepted_quantity
    cost_scenarios = {}
    for gst_on_sebi in (False, True):
        gst_label = "gst_ex_sebi" if not gst_on_sebi else "gst_incl_sebi"
        for slippage_bps in (0.0, 5.0, 10.0):
            scenario_id = f"{gst_label}_slippage_{slippage_bps:g}bps"
            if accepted_quantity:
                costs = estimate_round_trip_costs(
                    entry_price, exit_price, accepted_quantity, entry_time,
                    assumptions=CostAssumptions(slippage_bps_per_side=slippage_bps,
                                                gst_on_sebi=gst_on_sebi),
                )
                cost_breakdown = costs
                estimated_costs = round(costs["total_cost"], 2)
                net_pnl = round(gross - costs["total_cost"], 2)
            else:
                cost_breakdown = {}
                estimated_costs = net_pnl = 0.0
            cost_scenarios[scenario_id] = {
                "cost_breakdown": cost_breakdown,
                "estimated_costs": estimated_costs,
                "net_pnl": net_pnl,
                "assumptions": {"gst_on_sebi": gst_on_sebi,
                                "slippage_bps_per_side": slippage_bps},
            }
    default_scenario = cost_scenarios["gst_ex_sebi_slippage_5bps"]
    baseline_trade = {
        "taken": trade_accepted, "quantity": accepted_quantity,
        "gross_pnl": round(gross, 2), "cost_scenarios": cost_scenarios,
        "default_estimated_costs": default_scenario["estimated_costs"],
        "default_net_pnl": default_scenario["net_pnl"],
    }
    ai_taken = entry_event["deepseek_assisted_trade"]["accepted"] is True
    ai_trade = ({**baseline_trade, "taken": True} if ai_taken and trade_accepted else {
        "taken": False, "quantity": 0, "gross_pnl": 0.0,
        "cost_scenarios": {name: {"cost_breakdown": {}, "estimated_costs": 0.0,
                                   "net_pnl": 0.0, "assumptions": values["assumptions"]}
                            for name, values in cost_scenarios.items()},
        "default_estimated_costs": 0.0, "default_net_pnl": 0.0,
    })
    directional_outcome = ("BUY" if exit_price > entry_price else
                            "SELL" if exit_price < entry_price else "HOLD")
    return {
        "schema_version": OUTCOME_SCHEMA, "outcome_id": decision_id,
        "decision_id": decision_id, "classification": "PROSPECTIVE_PAPER_OBSERVATION",
        "entry_timestamp": entry_time.isoformat(), "entry_price": entry_price,
        "exit_timestamp": exit_bar["timestamp"].isoformat(), "exit_price": exit_price,
        "exit_reason": exit_reason, "quantity": accepted_quantity,
        "gross_pnl": round(gross, 2),
        "estimated_costs": default_scenario["estimated_costs"],
        "cost_breakdown": default_scenario["cost_breakdown"],
        "net_pnl": default_scenario["net_pnl"],
        "future_direction_label": directional_outcome,
        "baseline_prediction": "BUY",
        "deepseek_prediction": decision.get("decision", {}).get("decision", "HOLD"),
        "quantitative_baseline_trade": baseline_trade,
        "deepseek_assisted_trade": ai_trade,
        "cost_scenario_ids": sorted(cost_scenarios),
        "entry_cost_eligibility": entry_cost_check,
        "ai_entry_cost_eligibility": ai_cost_check,
        "outcome_observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_note": ("Paired prospective shadow outcomes on the same decision timestamp; not a broker fill or backtest result. "
                          "Stop-loss exposure is estimated at the reference stop plus modeled costs; gaps and execution variance can exceed it."),
    }


def settle_available_outcomes(frame: pd.DataFrame, decision_ledger, outcome_ledger, *,
                              symbol: str | None = None,
                              instrument_key: str | None = None,
                              entry_ledger=None) -> int:
    existing = {row["decision_id"] for row in outcome_ledger.records()}
    entries = ({row["decision_id"]: row for row in entry_ledger.records()}
               if entry_ledger is not None else {})
    current_symbols = set(frame["stock"].astype(str).str.upper().unique()) if "stock" in frame else set()
    current_symbol = (symbol or (next(iter(current_symbols)) if len(current_symbols) == 1 else None))
    settled = 0
    for decision in decision_ledger.records():
        baseline = decision.get("quantitative_baseline", {})
        if not baseline.get("eligible") or decision["decision_id"] in existing:
            continue
        if current_symbol is None or decision.get("symbol", "").upper() != current_symbol.upper():
            continue
        prior_instrument_key = decision.get("instrument_key")
        if prior_instrument_key is not None and instrument_key != prior_instrument_key:
            continue
        entry_event = entries.get(decision["decision_id"])
        if entry_event is None:
            entry_event = _entry_validation_for(decision, frame)
            if entry_event is None:
                continue
            if entry_ledger is not None:
                entry_ledger.append(entry_event)
            entries[decision["decision_id"]] = entry_event
        outcome = _outcome_for(decision, frame, decision["decision_id"], entry_event)
        if outcome is not None and outcome_ledger.append(outcome):
            settled += 1
            existing.add(decision["decision_id"])
    return settled


def run_provider_smoke_test(snapshot_path, *, output_dir, provider, model: str,
                            api_key: str | None = None, max_total_requests: int = 1,
                            now: datetime | None = None,
                            allow_provider_requests: bool = False) -> dict:
    with _experiment_lock(output_dir):
        return _run_provider_smoke_test_unlocked(
            snapshot_path, output_dir=output_dir, provider=provider, model=model,
            api_key=api_key, max_total_requests=max_total_requests, now=now,
            allow_provider_requests=allow_provider_requests,
        )


def _run_provider_smoke_test_unlocked(snapshot_path, *, output_dir, provider,
                                      model: str, api_key: str | None = None,
                                      max_total_requests: int = 1,
                                      now: datetime | None = None,
                                      allow_provider_requests: bool = False) -> dict:
    """Make an explicitly authorized, non-trading provider/JSON smoke request.

    This path validates a user-supplied current snapshot but never evaluates the
    quantitative candidate, creates a trade decision, or writes strategy ledgers.
    Its response contract is only {"status":"ok"}; trading-shaped responses
    are marked invalid and are never interpreted as signals.
    """
    if not allow_provider_requests:
        raise ValueError("provider smoke requests require explicit opt-in")
    if provider is None:
        raise ValueError("provider smoke test requires a configured provider")
    now = now or datetime.now(timezone.utc)
    snapshot, raw_bytes, frame, observed_at, decision_time = _load_snapshot(
        snapshot_path, now=now, max_age_seconds=900)
    preflight = prospective_preflight(
        output_dir=output_dir, symbol=snapshot["symbol"].upper(), now=now,
        kind="smoke", maximum=max_total_requests, model=model)
    validate_request_snapshot(preflight, snapshot, frame, observed_at, now)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    budget = PersistentRequestBudget(output / "provider_smoke_request_budget.jsonl",
                                     max_total_requests)
    recent_bars = [
        {"timestamp": row.timestamp.isoformat(), "open": float(row.open),
         "high": float(row.high), "low": float(row.low),
         "close": float(row.close), "volume": int(row.volume)}
        for row in frame.tail(3).itertuples(index=False)
    ]
    system_prompt = (
        "This is a provider connectivity and JSON-format test only. Do not analyze "
        "the market, recommend a trade, or return BUY/SELL/HOLD. Return exactly "
        'the JSON object {"status":"ok"} and nothing else.'
    )
    user_payload = json.dumps({
        "test_type": "NON_TRADING_PROVIDER_CONNECTIVITY_TEST",
        "symbol": snapshot["symbol"].upper(),
        "instrument_key": snapshot["instrument_key"],
        "observed_at": observed_at.isoformat(),
        "recent_completed_candles": recent_bars,
    }, sort_keys=True, separators=(",", ":"))
    snapshot_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    request_id = canonical_request_key(model, {
        "system_prompt": system_prompt, "user_payload": user_payload,
        "test_type": "provider-connectivity-smoke-v1",
    })
    report = {
        "schema_version": "deepseek-provider-smoke-v1",
        "classification": "PROVIDER_CONNECTIVITY_TEST",
        "status": "BLOCKED", "provider_configuration": {
            "configured": True, "model_id": model,
            "provider_id": getattr(provider, "provider_id", None),
            "timeout_seconds": getattr(provider, "timeout_seconds", None),
            "retries": 0,
        },
        "provider_request_attempted": False, "provider_skip_reason": None,
        "provider_error_type": None, "provider_response_redacted": None,
        "response_parse_status": "NOT_EVALUATED", "provider_metadata": None,
        "provider_request": {"system_prompt": system_prompt, "user_payload": user_payload},
        "request_id": request_id, "request_budget_used": budget.used,
        "snapshot_sha256": snapshot_sha256, "snapshot_source": snapshot["source"],
        "symbol": snapshot["symbol"].upper(), "instrument_key": snapshot["instrument_key"],
        "snapshot_observed_at": observed_at.isoformat(),
        "decision_time": decision_time.isoformat(), "candle_count": len(frame),
        "prospective_observation": False, "synthetic": False,
        "historical_backtest": False, "strategy_performance_evidence": False,
        "trading_decision_created": False, "paper_trade_simulated": False,
        "trade_placement_enabled": False,
        "evidence_note": "Provider connectivity/JSON acknowledgement only; not a trading decision, strategy observation, backtest, or performance evidence.",
    }
    try:
        preflight = prospective_preflight(
            output_dir=output_dir, symbol=snapshot["symbol"].upper(), now=now,
            kind="smoke", maximum=max_total_requests, model=model)
        validate_request_snapshot(preflight, snapshot, frame, observed_at, now)
        reserved = budget.reserve(request_id)
    except RuntimeError:
        report["provider_skip_reason"] = "provider_request_budget_exhausted"
        report["request_budget_used"] = budget.used
        return report
    if not reserved:
        report["provider_skip_reason"] = "request_already_reserved"
        report["request_budget_used"] = budget.used
        return report

    report["provider_request_attempted"] = True
    try:
        result = provider.complete({"system_prompt": system_prompt,
                                    "user_payload": user_payload})
        report["provider_metadata"] = {
            "requested_model_id": getattr(result, "requested_model_id", model),
            "returned_model_id": getattr(result, "model_id", model),
            "latency_ms": getattr(result, "latency_ms", None),
            "prompt_tokens": getattr(result, "prompt_tokens", None),
            "completion_tokens": getattr(result, "completion_tokens", None),
            "response_bytes": getattr(result, "response_bytes", None),
        }
        raw = _redact_secrets(result.content, api_key)
        report["provider_response_redacted"] = raw
        if (getattr(result, "model_id", model) != model or
                getattr(result, "requested_model_id", model) != model):
            raise ValueError("provider model identity mismatch")
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        valid_ack = isinstance(parsed, dict) and parsed == {"status": "ok"}
        report["response_parse_status"] = "VALID_ACK" if valid_ack else "INVALID_RESPONSE"
        report["status"] = "COMPLETED" if valid_ack else "INVALID_RESPONSE"
    except Exception as exc:
        report["provider_error_type"] = type(exc).__name__
        report["response_parse_status"] = "NOT_EVALUATED"
        report["status"] = "PROVIDER_ERROR"

    report["request_budget_used"] = budget.used
    events = AppendOnlyJsonl(output / "provider_smoke_events.jsonl", "request_id")
    events.append(report)
    return report


def record_prospective_snapshot(snapshot_path, *, output_dir, provider=None, model: str,
                                api_key: str | None = None, max_total_requests: int = 20,
                                paper_capital: float = 1_000.0, now: datetime | None = None,
                                max_snapshot_age_seconds: int = 900,
                                allow_provider_requests: bool = False) -> dict:
    with _experiment_lock(output_dir):
        return _record_prospective_snapshot_unlocked(
            snapshot_path, output_dir=output_dir, provider=provider, model=model,
            api_key=api_key, max_total_requests=max_total_requests,
            paper_capital=paper_capital, now=now,
            max_snapshot_age_seconds=max_snapshot_age_seconds,
            allow_provider_requests=allow_provider_requests,
        )


def _record_prospective_snapshot_unlocked(snapshot_path, *, output_dir,
                                          provider=None, model: str,
                                          api_key: str | None = None,
                                          max_total_requests: int = 20,
                                          paper_capital: float = 1_000.0,
                                          now: datetime | None = None,
                                          max_snapshot_age_seconds: int = 900,
                                          allow_provider_requests: bool = False) -> dict:
    """Record one observed snapshot; provider use requires caller opt-in."""
    if provider is not None and not allow_provider_requests:
        raise ValueError("provider requests require explicit allow_provider_requests opt-in")
    try:
        paper_capital = float(paper_capital)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("paper capital must be finite and positive") from exc
    if not math.isfinite(paper_capital) or paper_capital <= 0:
        raise ValueError("paper capital must be finite and positive")
    now = now or datetime.now(timezone.utc)
    snapshot, raw_bytes, frame, observed_at, decision_time = _load_snapshot(
        snapshot_path, now=now, max_age_seconds=max_snapshot_age_seconds)
    if provider is not None:
        preflight = prospective_preflight(
            output_dir=output_dir, symbol=snapshot["symbol"].upper(), now=now,
            kind="provider", maximum=max_total_requests, model=model, capital=paper_capital)
        validate_request_snapshot(preflight, snapshot, frame, observed_at, now)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ensure_prospective_experiment_capital(output, paper_capital, model=model)
    decisions = AppendOnlyJsonl(output / "observations.jsonl", "decision_id")
    outcomes = AppendOnlyJsonl(output / "outcomes.jsonl", "outcome_id")
    entries = AppendOnlyJsonl(output / "entry_events.jsonl", "entry_event_id")
    budget = PersistentRequestBudget(output / "request_budget.jsonl", max_total_requests)
    cache = ProspectiveCache(output / "response_cache.json")

    snapshot_digest = hashlib.sha256(raw_bytes).hexdigest()
    previous_decisions = decisions.records()
    is_replay = any(row.get("snapshot_sha256") == snapshot_digest and
                    row.get("symbol", "").upper() == snapshot["symbol"].upper()
                    for row in previous_decisions)
    if not is_replay and any(
            decision_time < _aware_timestamp(row["decision_time"], "decision_time")
            for row in previous_decisions):
        raise ValueError("new prospective observations must advance the shared account clock")

    settled_before = settle_available_outcomes(frame, decisions, outcomes,
                                               symbol=snapshot["symbol"].upper(),
                                               instrument_key=snapshot.get("instrument_key"),
                                               entry_ledger=entries)
    signal_timestamp = frame["timestamp"].iloc[-1].isoformat()
    duplicate_snapshot = next((row for row in decisions.records()
                               if row.get("snapshot_sha256") == snapshot_digest
                               and row.get("symbol", "").upper() == snapshot["symbol"].upper()
                               and row.get("quantitative_baseline", {}).get("signal_timestamp",
                                   row.get("signal_timestamp", signal_timestamp)) == signal_timestamp), None)
    if duplicate_snapshot is not None:
        settled = settle_available_outcomes(frame, decisions, outcomes,
                                            symbol=snapshot["symbol"].upper(),
                                            instrument_key=snapshot.get("instrument_key"),
                                            entry_ledger=entries)
        return {"status": "COMPLETED", "classification": "PROSPECTIVE_OBSERVATION",
                "decision_id": duplicate_snapshot["decision_id"], "idempotent_replay": True,
                "request_attempted": False, "request_budget_used": budget.used,
                "provider_request_attempted": False,
                "provider_skip_reason": "idempotent_replay",
                "provider_response_redacted": duplicate_snapshot.get("provider_response_redacted",
                    duplicate_snapshot.get("raw_decision_response_redacted")),
                "cache_hits": cache.hits, "settled_outcomes": settled_before + settled,
                "trade_placement_enabled": False,
                "observation_path": str(decisions.path), "outcome_path": str(outcomes.path)}
    source_frame = frame.copy()
    prepared, candidates = _prepare_quant_candidates(source_frame)
    signal_index = len(prepared) - 1
    prior_outcomes = outcomes.records()
    all_decisions = decisions.records()
    baseline_decisions = [row for row in all_decisions
                          if row.get("quantitative_baseline", {}).get("eligible")]
    paper_decisions = [row for row in all_decisions if row.get("paper_action") == "PENDING_BUY"]
    decision_day = prepared.at[signal_index, "timestamp"].date()
    outcome_by_id = {item["decision_id"]: item for item in prior_outcomes}
    entry_by_id = {item["decision_id"]: item for item in entries.records()}
    baseline_trades_today = _arm_trades_today(
        baseline_decisions, outcome_by_id, entry_by_id,
        "quantitative_baseline_trade", decision_day)
    baseline_daily_loss = sum(-float(item["quantitative_baseline_trade"]["default_net_pnl"])
                              for item in prior_outcomes
                              if item["quantitative_baseline_trade"]["default_net_pnl"] < 0 and
                              _aware_timestamp(item["entry_timestamp"], "entry_timestamp").date() == decision_day)
    baseline_open = _arm_has_open_position(
        baseline_decisions, outcome_by_id, entry_by_id, "quantitative_baseline_trade")
    daily_loss = sum(-float(item["deepseek_assisted_trade"]["default_net_pnl"])
                     for item in prior_outcomes
                     if item["deepseek_assisted_trade"]["default_net_pnl"] < 0 and
                     _aware_timestamp(item["entry_timestamp"], "entry_timestamp").date() == decision_day)
    baseline_cash = _arm_available_cash(
        paper_capital, prior_outcomes, "quantitative_baseline_trade", decision_time)
    ai_cash = _arm_available_cash(
        paper_capital, prior_outcomes, "deepseek_assisted_trade", decision_time)
    baseline = _quant_baseline(prepared, capital=paper_capital,
                               has_open_paper_trade=baseline_open,
                               trades_today=baseline_trades_today, daily_loss=baseline_daily_loss,
                               available_cash=baseline_cash)
    signal_timestamp = prepared.at[signal_index, "timestamp"].isoformat()

    # The baseline and AI-assisted shadow books share a timestamp, but track
    # their own open positions, daily counts, and realized losses.
    ai_open = _arm_has_open_position(
        paper_decisions, outcome_by_id, entry_by_id, "deepseek_assisted_trade")
    ai_trades_today = _arm_trades_today(
        paper_decisions, outcome_by_id, entry_by_id, "deepseek_assisted_trade", decision_day)
    ai_config = RiskConfig(capital=paper_capital)
    ai_state = RiskState(daily_loss=daily_loss, open_positions=int(ai_open),
                         trades_today=ai_trades_today)
    ai_allowed, ai_risk_reason = check_trade_allowed(ai_config, ai_state)
    shared_cost_eligible = bool(baseline.get("cost_eligibility", {}).get("eligible"))
    ai_cost_check = (_cost_aware_position_check(
        capital=paper_capital, entry_price=baseline["entry_reference"],
        stop_price=baseline["stop_reference"], quantity=baseline["quantity"],
        trade_timestamp=prepared.at[signal_index, "timestamp"],
        available_cash=ai_cash,
    ) if baseline["eligible"] else None)
    provider_eligible = bool(baseline["eligible"] and ai_allowed
                             and ai_cost_check and ai_cost_check["eligible"])

    user_payload = None
    system_prompt = (
        "Use only the timestamped market data supplied. Do not infer later prices or outcomes. "
        "The quantitative baseline controls eligibility; you may only confirm or veto it. "
        "Return JSON with decision (BUY, SELL, or HOLD), confidence (0..1), and reason."
    )
    decision = {"decision": "HOLD", "confidence": 0.0,
                "reason": "no eligible quantitative candidate or provider request not enabled"}
    raw_response = None
    provider_error = None
    provider_requested = False
    provider_skip_reason = None
    if not baseline["eligible"]:
        provider_skip_reason = "quantitative_baseline_ineligible"
        decision["reason"] = baseline.get("reason", provider_skip_reason)
    elif not provider_eligible:
        provider_skip_reason = "ai_account_risk_or_cost_ineligible"
    elif provider is None:
        provider_skip_reason = "provider_not_configured"
    request_id = None
    provider_metadata = None
    if baseline["eligible"] and provider is not None:
        request = build_decision_request(snapshot["symbol"].upper(), prepared, signal_index)
        user_payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
        request_id = canonical_request_key(model, {"system_prompt": system_prompt,
                                                   "user_payload": user_payload})
        decision_id = hashlib.sha256(json.dumps({
            "request_id": request_id, "snapshot_sha256": snapshot_digest,
            "symbol": snapshot["symbol"].upper(),
            "signal_timestamp": signal_timestamp,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    else:
        decision_id = hashlib.sha256(json.dumps({
            "snapshot_sha256": snapshot_digest, "symbol": snapshot["symbol"].upper(),
            "signal_timestamp": signal_timestamp, "model": model,
            "prompt_version": PROMPT_VERSION,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    existing = next((row for row in decisions.records() if row["decision_id"] == decision_id), None)
    if existing is not None:
        settled = settle_available_outcomes(frame, decisions, outcomes,
                                            symbol=snapshot["symbol"].upper(),
                                            instrument_key=snapshot.get("instrument_key"),
                                            entry_ledger=entries)
        return {"status": "COMPLETED", "classification": "PROSPECTIVE_OBSERVATION",
                "decision_id": decision_id, "idempotent_replay": True,
                "request_attempted": False, "request_budget_used": budget.used,
                "cache_hits": cache.hits, "settled_outcomes": settled,
                "trade_placement_enabled": False,
                "observation_path": str(decisions.path), "outcome_path": str(outcomes.path)}

    if provider_eligible and provider is not None:
        cached = cache.get(request_id)
        if cached is not None:
            raw_response, decision = cached["raw_response"], cached["decision"]
            provider_metadata = cached.get("provider_metadata")
            provider_skip_reason = "cached_response_reused"
        else:
            try:
                preflight = prospective_preflight(
                    output_dir=output_dir, symbol=snapshot["symbol"].upper(), now=now,
                    kind="provider", maximum=max_total_requests, model=model, capital=paper_capital)
                validate_request_snapshot(preflight, snapshot, frame, observed_at, now)
                reserved = budget.reserve(request_id)
            except RuntimeError:
                reserved = False
                provider_error = "request_budget_exhausted"
                provider_skip_reason = "provider_request_budget_exhausted"
            if reserved:
                provider_requested = True
                try:
                    result = provider.complete({"system_prompt": system_prompt,
                                                "user_payload": user_payload})
                    returned_model = getattr(result, "model_id", model)
                    requested_model = getattr(result, "requested_model_id", model)
                    if returned_model != model or requested_model != model:
                        raise ValueError("provider model identity mismatch")
                    provider_metadata = {
                        "provider_id": getattr(result, "provider_id", None),
                        "requested_model_id": requested_model,
                        "returned_model_id": returned_model,
                        "latency_ms": getattr(result, "latency_ms", None),
                        "prompt_tokens": getattr(result, "prompt_tokens", None),
                        "completion_tokens": getattr(result, "completion_tokens", None),
                        "response_bytes": getattr(result, "response_bytes", None),
                    }
                    raw_response = _redact_secrets(result.content, api_key)
                    decision = _safe_decision(raw_response, api_key)
                except Exception as exc:
                    provider_error = type(exc).__name__
                    raw_response = None
                    decision = {"decision": "HOLD", "confidence": 0.0,
                                "reason": f"provider failure ({provider_error})"}
                cache.put(request_id, {"raw_response": raw_response, "decision": decision,
                                       "provider_metadata": provider_metadata})
            elif provider_error is None:
                provider_error = "request_already_reserved_without_cached_response"
                provider_skip_reason = "request_already_reserved_without_cached_response"

    ai_assisted_eligible = bool(
        baseline["eligible"] and shared_cost_eligible
        and ai_cost_check["eligible"] and decision["decision"] == "BUY" and ai_allowed
    )
    # AI can veto a baseline setup, but cannot create one, size a trade, or route
    # an order. This label refers only to a shadow-paper decision.
    paper_action = "PENDING_BUY" if ai_assisted_eligible else "NO_TRADE"
    observation = {
        "schema_version": PROSPECTIVE_SCHEMA,
        "decision_id": decision_id,
        "classification": "PROSPECTIVE_OBSERVATION",
        "snapshot_sha256": snapshot_digest,
        "snapshot_source": snapshot["source"],
        "symbol": snapshot["symbol"].upper(),
        "instrument_key": snapshot.get("instrument_key"),
        "snapshot_observed_at": observed_at.isoformat(),
        "decision_time": decision_time.isoformat(),
        "snapshot": {**snapshot, "candles": [bar for bar in snapshot["candles"]
                                                  if _aware_timestamp(bar["timestamp"], "candle timestamp") <= decision_time]},
        "quantitative_baseline": baseline,
        "position_cost_eligibility": baseline.get("cost_eligibility"),
        "ai_position_cost_eligibility": ai_cost_check,
        "ai_assisted_eligible": ai_assisted_eligible,
        "ai_assisted_risk_reason": ai_risk_reason,
        "capital": paper_capital,
        "baseline_available_cash": baseline_cash,
        "ai_available_cash": ai_cash,
        "provider_call_enabled": provider is not None,
        "provider_configuration": {
            "configured": provider is not None,
            "model_id": model if provider is not None else None,
            "provider_id": getattr(provider, "provider_id", None),
            "timeout_seconds": getattr(provider, "timeout_seconds", None),
            "max_completion_tokens": getattr(provider, "max_completion_tokens", None),
            "max_response_bytes": getattr(provider, "max_response_bytes", None),
            "retries": 0,
        },
        "provider_request_attempted": provider_requested,
        "provider_skip_reason": provider_skip_reason,
        "provider_request_id": request_id,
        "model_id": model,
        "prompt_version": PROMPT_VERSION,
        "configuration": {"max_total_requests": max_total_requests,
                          "request_budget_used": budget.used,
                          "paper_capital": paper_capital,
                          "experiment_initial_capital": paper_capital,
                          "stop_pct": STOP_PCT, "horizon_bars": HORIZON_BARS,
                          "cost_aware_position_gate": {
                              "buy_side_costs_in_entry_cap": True,
                              "round_trip_costs_in_stop_risk": True,
                              "stop_gap_can_exceed_estimate": True,
                              "assumptions": "app.cost_model.CostAssumptions defaults",
                          },
                          "provider_timeout_seconds": getattr(provider, "timeout_seconds", None),
                          "provider_max_completion_tokens": getattr(provider, "max_completion_tokens", None),
                          "provider_max_response_bytes": getattr(provider, "max_response_bytes", None),
                          "provider_retries": 0,
                          "estimated_cost_model": "app.cost_model.CostAssumptions",
                          "prospective_cost_scenarios": [
                              "gst_ex_sebi_slippage_0bps", "gst_ex_sebi_slippage_5bps",
                              "gst_ex_sebi_slippage_10bps", "gst_incl_sebi_slippage_0bps",
                              "gst_incl_sebi_slippage_5bps", "gst_incl_sebi_slippage_10bps"]},
        "provider_request": ({"system_prompt": system_prompt, "user_payload": user_payload}
                             if user_payload is not None else None),
        "raw_decision_response_redacted": raw_response,
        "provider_response_redacted": raw_response,
        "provider_error_type": provider_error,
        "provider_metadata": provider_metadata,
        "decision": decision,
        "paper_action": paper_action,
        "trade_placement_enabled": False,
        "evidence_note": "Prospective observation only; not a historical backtest or broker execution.",
    }
    decisions.append(observation)
    # Outcomes are processed only from candles observed by this invocation and
    # are appended separately; they never modify the decision-time record.
    settled_after = settle_available_outcomes(frame, decisions, outcomes,
                                              symbol=snapshot["symbol"].upper(),
                                              instrument_key=snapshot.get("instrument_key"),
                                              entry_ledger=entries)
    return {
        "status": "COMPLETED", "classification": "PROSPECTIVE_OBSERVATION",
        "decision_id": decision_id, "request_attempted": provider_requested,
        "provider_request_attempted": provider_requested,
        "provider_skip_reason": provider_skip_reason,
        "provider_configuration": {
            "configured": provider is not None,
            "model_id": model if provider is not None else None,
            "provider_id": getattr(provider, "provider_id", None),
            "timeout_seconds": getattr(provider, "timeout_seconds", None),
            "retries": 0,
        },
        "provider_response_redacted": raw_response,
        "request_budget_used": budget.used, "cache_hits": cache.hits,
        "settled_outcomes": settled_before + settled_after,
        "trade_placement_enabled": False,
        "observation_path": str(decisions.path), "outcome_path": str(outcomes.path),
    }


def offline_synthetic_check() -> dict:
    """Exercise decision handling with fixtures; this function cannot call a provider."""
    cases = {
        "BUY": '{"decision":"BUY","confidence":0.8,"reason":"fixture"}',
        "SELL": '{"decision":"SELL","confidence":0.8,"reason":"fixture"}',
        "HOLD": '{"decision":"HOLD","confidence":0.8,"reason":"fixture"}',
        "malformed": '{"decision":"BUY","confidence":"high","reason":"bad"}',
        "timeout": None,
    }
    rows = []
    for case, raw in cases.items():
        parsed = ({"decision": "HOLD", "confidence": 0.0,
                   "reason": "provider failure (TimeoutError)"} if case == "timeout"
                  else _safe_decision(raw))
        fallback_applied = case in {"malformed", "timeout"}
        rows.append({"case": case, "classification": "SYNTHETIC",
                     "evidence_type": "SYNTHETIC_FIXTURE",
                     "decision": parsed["decision"],
                     "safe_fallback": fallback_applied and parsed["decision"] == "HOLD"})
    return {"schema_version": "deepseek-offline-synthetic-check-v1",
            "status": "COMPLETED", "classification": "SYNTHETIC",
            "synthetic_inputs_only": True, "api_requests": 0,
            "trade_placement_enabled": False, "cases": rows,
            "note": "Deterministic synthetic test results; not prospective observations or historical performance."}
