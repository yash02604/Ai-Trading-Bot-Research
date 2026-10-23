"""Offline, fixed-spec sharp-reversal research; never connected to execution."""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, time
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from app.candle_integrity import validate_candles
from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from app.v2.registry import StrategyCandidate, StrategyRegistry, default_registry
from app.research.execution_integrity import (
    ExecutionIntegrityError, TimestampSemanticsEvidence,
    validate_simulation_event,
)

BAR = pd.Timedelta(minutes=5)
IST_LAST_START = time(15, 25)
DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ExperimentCandidate:
    strategy_id: str
    version: str
    kind: str
    decline_bars: int
    min_decline_pct: float
    max_decline_pct_exclusive: float
    rsi_min_inclusive: float
    rsi_max_exclusive: float
    require_monotonic_decline: bool
    stop_pct: float
    horizon_bars: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_session_dates(path: Path) -> dict[date, dict]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or manifest.get("segment") != "CM":
        raise ValueError("Expected NSE CM session manifest v2")
    output = {}
    for item in manifest.get("sessions", []):
        day = date.fromisoformat(item["date"])
        if day in output:
            raise ValueError(f"duplicate session date in manifest: {day}")
        output[day] = item
    return output


def load_stock_map(strategy_file: Path) -> dict[str, str]:
    """Parse the literal V1 symbol map without importing modules that load .env."""
    tree = ast.parse(strategy_file.read_text(encoding="utf-8"), filename=str(strategy_file))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "STOCKS" for target in node.targets
        ):
            mapping = ast.literal_eval(node.value)
            if not isinstance(mapping, dict) or not mapping:
                break
            return {str(symbol).upper(): str(key) for symbol, key in mapping.items()}
    raise ValueError("could not parse literal STOCKS mapping from strategy_backtest.py")


def load_candidates(spec: dict) -> tuple[list[ExperimentCandidate], StrategyRegistry]:
    candidates = [ExperimentCandidate(**{key: item[key] for key in ExperimentCandidate.__dataclass_fields__})
                  for item in spec["candidates"]]
    if len({candidate.strategy_id for candidate in candidates}) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    baseline = default_registry()
    for candidate, raw in zip(candidates, spec["candidates"]):
        if candidate.kind == "reference":
            if candidate.strategy_id != "frozen_reversal_v1":
                raise ValueError("only the registered frozen V1 may be the reference")
            registered = baseline.require(candidate.strategy_id)
            if registered.status != "FROZEN_BASELINE" or registered.enabled_for_paper:
                raise ValueError("frozen V1 registry status changed")
            continue
        if raw.get("registry_status") != "RESEARCH_ONLY" or raw.get("enabled_for_paper") is not False:
            raise ValueError("research candidate must remain paper-disabled")
        baseline.register(StrategyCandidate(
            candidate.strategy_id, candidate.version, raw["description"],
            status="RESEARCH_ONLY", enabled_for_paper=False,
        ))
        if candidate.decline_bars < 2 or not (0 < candidate.min_decline_pct < candidate.max_decline_pct_exclusive):
            raise ValueError(f"invalid decline bounds for {candidate.strategy_id}")
        if not (0 <= candidate.rsi_min_inclusive < candidate.rsi_max_exclusive <= 100):
            raise ValueError(f"invalid RSI bounds for {candidate.strategy_id}")
        if not (0 < candidate.stop_pct < 5) or not (1 <= candidate.horizon_bars <= 12):
            raise ValueError(f"invalid exit bounds for {candidate.strategy_id}")
    return candidates, baseline


def prepare_data(root: Path, spec: dict) -> tuple[pd.DataFrame, dict]:
    paths = [root / item for item in spec["data_files"]]
    frames = []
    validation_reasons: dict[int, set[str]] = defaultdict(set)
    warning_counts = Counter()
    row_offset = 0
    for path in paths:
        raw = pd.read_csv(path)
        keys = ["instrument_key"] if "instrument_key" in raw.columns else ["stock"]
        validation = validate_candles(raw, key_columns=keys)
        global_errors = [item for item in validation.errors if item.get("row") is None]
        if global_errors:
            raise ValueError(f"candle validation could not run for {path}: {global_errors}")
        for error in validation.errors:
            validation_reasons[row_offset + int(error["row"])].add(error["reason"])
        warning_counts.update(item["reason"] for item in validation.warnings)
        frames.append(validation.frame)
        row_offset += len(raw)
    data = pd.concat(frames, ignore_index=True)
    required = {"stock", "timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"historical data missing columns: {sorted(missing)}")
    data["stock"] = data.stock.astype(str).str.upper()
    stock_map = load_stock_map(root / "strategy_backtest.py")
    structural_invalid = pd.Series(False, index=data.index)
    for row_index in validation_reasons:
        structural_invalid.at[row_index] = True
    # Catch cross-file duplicates as both files have already been normalized.
    key_columns = ["instrument_key"] if "instrument_key" in data.columns else ["stock"]
    duplicate = data.duplicated(key_columns + ["timestamp"], keep=False)
    structural_invalid |= duplicate
    for row_index in data.index[duplicate]:
        validation_reasons[int(row_index)].add("duplicate_instrument_timestamp")
    expected_keys = data.stock.map(stock_map)
    if "instrument_key" in data:
        key_mismatch = expected_keys.notna() & data.instrument_key.ne(expected_keys)
    else:
        key_mismatch = pd.Series(False, index=data.index)
    unknown_symbol = expected_keys.isna()
    session_dates = load_session_dates(root / spec["session_manifest"])
    dates = data.timestamp.dt.date
    timestamp_invalid = data.index.to_series().map(
        lambda index: bool(validation_reasons.get(int(index), set()) & {"invalid_timestamp", "timezone_naive_timestamp"})
    )
    temporal_valid = ~timestamp_invalid
    verified_regular_dates = {
        day for day, item in session_dates.items()
        if item.get("verification_status") == "VERIFIED"
        and item.get("session_type") == "REGULAR"
        and not item.get("special_session")
    }
    explicit_exclusions = {
        (item["symbol"], date.fromisoformat(item["date"])): item["reason"]
        for item in spec["data_policy"]["known_date_exclusions"]
    }
    hour, minute = data.timestamp.dt.hour, data.timestamp.dt.minute
    in_regular_hours = ((hour > 9) | ((hour == 9) & (minute >= 15))) & ((hour < 15) | ((hour == 15) & (minute <= 25)))
    aligned = minute.mod(5).eq(0) & data.timestamp.dt.second.eq(0) & data.timestamp.dt.microsecond.eq(0)
    masks: dict[str, pd.Series] = {
        "invalid_candle_structure": structural_invalid,
        "instrument_key_symbol_mismatch": key_mismatch,
        "symbol_not_in_fixed_basket": unknown_symbol,
        "zero_volume_candle_unresolved": pd.to_numeric(data.volume, errors="coerce").eq(0),
        "session_missing_or_unverified": temporal_valid & ~dates.isin(session_dates),
        "non_regular_session_excluded": temporal_valid & dates.isin(session_dates) & ~dates.isin(verified_regular_dates),
        "outside_regular_session_label": temporal_valid & ~in_regular_hours,
        "five_minute_alignment_invalid": temporal_valid & ~aligned,
    }
    for (symbol_key, day), _reason in explicit_exclusions.items():
        selected = dates.eq(day) if symbol_key == "ALL" else data.stock.eq(symbol_key) & dates.eq(day)
        masks["policy_exclusion:" + _reason] = selected
    accepted = pd.Series(True, index=data.index)
    reasons = Counter()
    excluded_samples = []
    for reason, mask in masks.items():
        mask = mask.fillna(True).astype(bool)
        masks[reason] = mask
        accepted &= ~mask
        if mask.any():
            reasons[reason] = int(mask.sum())
    valid_rows = data.loc[accepted, ["stock", "timestamp"]].copy()
    valid_rows["_day"] = dates.loc[accepted]
    coverage = valid_rows.groupby(["stock", "_day"], sort=True).timestamp.agg(
        count="size", first="min", last="max")
    valid_sessions = set(coverage.index[
        coverage["count"].eq(75)
        & coverage["first"].dt.time.eq(time(9, 15))
        & coverage["last"].dt.time.eq(IST_LAST_START)
    ])
    row_sessions = pd.MultiIndex.from_arrays([data.stock, dates])
    session_complete = pd.Series(row_sessions.isin(valid_sessions), index=data.index)
    incomplete_session = accepted & ~session_complete
    if incomplete_session.any():
        masks["incomplete_regular_session_coverage"] = incomplete_session
        accepted &= ~incomplete_session
        reasons["incomplete_regular_session_coverage"] = int(incomplete_session.sum())
    excluded_indices = data.index[~accepted][:100]
    for i in excluded_indices:
        row_reasons = [reason for reason, mask in masks.items() if bool(mask.at[i])]
        row_reasons.extend(validation_reasons.get(int(i), set()))
        excluded_samples.append({"row": int(i), "symbol": data.at[i, "stock"],
                                 "timestamp": str(data.at[i, "timestamp"]), "reasons": sorted(set(row_reasons))})
    filtered = data.loc[accepted].copy()
    filtered = filtered.sort_values(["stock", "timestamp"], kind="stable").reset_index(drop=True)
    if filtered.duplicated(["stock", "timestamp"]).any():
        raise ValueError("duplicate stock/timestamp rows remain after exclusion")
    gaps = 0
    for _, group in filtered.groupby("stock", sort=False):
        delta = group.timestamp.diff()
        same_day = group.timestamp.dt.date.eq(group.timestamp.shift().dt.date)
        gaps += int(((delta > BAR) & same_day).sum())
    coverage_by_partition = {}
    for partition in spec["partitions"]:
        start, end = date.fromisoformat(partition["start"]), date.fromisoformat(partition["end"])
        subset = filtered.loc[filtered.timestamp.dt.date.between(start, end)]
        retained_dates = set(subset.timestamp.dt.date.unique())
        expected_dates = {day for day in verified_regular_dates if start <= day <= end}
        missing_dates = sorted(expected_dates - retained_dates)
        coverage_by_partition[partition["partition_id"]] = {
            "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "retained_start": subset.timestamp.min().date().isoformat() if not subset.empty else None,
            "retained_end": subset.timestamp.max().date().isoformat() if not subset.empty else None,
            "retained_rows": int(len(subset)), "retained_regular_dates": len(retained_dates),
            "expected_regular_dates": len(expected_dates),
            "missing_expected_regular_dates": [day.isoformat() for day in missing_dates],
        }
    audit = {
        "input_hashes": {str(path.relative_to(root)): sha256_file(path) for path in paths},
        "raw_rows": int(len(data)), "retained_rows": int(len(filtered)),
        "excluded_rows": int((~accepted).sum()), "exclusion_reason_counts": dict(sorted(reasons.items())),
        "integrity_error_reason_counts": dict(sorted(Counter(reason for values in validation_reasons.values() for reason in values).items())),
        "integrity_warning_counts": dict(sorted(warning_counts.items())),
        "excluded_examples_first_100": excluded_samples,
        "zero_volume_rows_retained": int(filtered.volume.eq(0).sum()),
        "complete_symbol_sessions_retained": int(filtered.groupby(["stock", filtered.timestamp.dt.date]).ngroups),
        "incomplete_symbol_sessions_excluded": int(len({(data.at[i, "stock"], dates.at[i]) for i in data.index[incomplete_session]})),
        "same_day_gaps_after_row_exclusions": gaps,
        "retained_date_coverage_by_partition": coverage_by_partition,
        "symbols": sorted(filtered.stock.unique().tolist()),
        "start": filtered.timestamp.min().isoformat(), "end": filtered.timestamp.max().isoformat(),
        "timestamp_semantics": "provisional START-label assumption; dataset-specific semantics unresolved",
    }
    return filtered, audit


def build_market_regimes(data: pd.DataFrame) -> pd.DataFrame:
    daily = data.copy()
    daily["_day"] = daily.timestamp.dt.date
    daily = daily.sort_values("timestamp").groupby(["stock", "_day"], sort=True).tail(1)
    closes = daily.pivot(index="_day", columns="stock", values="close").sort_index()
    basket_return = closes.pct_change(fill_method=None).mean(axis=1, skipna=True)
    trend = (1.0 + basket_return).rolling(60, min_periods=40).apply(np.prod, raw=True).sub(1).shift(1)
    volatility = basket_return.rolling(20, min_periods=15).std().mul(np.sqrt(252)).shift(1)
    rows = []
    for day in closes.index:
        t, v = trend.get(day), volatility.get(day)
        rows.append({
            "date": day,
            "trend_regime": "warmup" if pd.isna(t) else ("bullish" if t > 0.05 else "bearish" if t < -0.05 else "sideways"),
            "volatility_regime": "warmup" if pd.isna(v) else ("high_volatility" if v >= 0.20 else "low_volatility"),
        })
    return pd.DataFrame(rows)


def prepare_symbol_frames(data: pd.DataFrame, regular_session_dates: set[date] | None = None) -> dict[str, pd.DataFrame]:
    output = {}
    if regular_session_dates is None and not data.empty:
        regular_session_dates = set(data.timestamp.dt.date.unique())
    calendar_dates = sorted(regular_session_dates or set())
    previous_regular = {calendar_dates[index]: calendar_dates[index - 1]
                        for index in range(1, len(calendar_dates))}
    for symbol, group in data.groupby("stock", sort=True):
        frame = group.sort_values("timestamp", kind="stable").reset_index(drop=True)
        if "open_interest" not in frame:
            frame["open_interest"] = (pd.to_numeric(frame["oi"], errors="coerce").fillna(0)
                                       if "oi" in frame else 0)
        delta = frame.timestamp.diff()
        same_day = frame.timestamp.dt.date.eq(frame.timestamp.shift().dt.date)
        day = frame.timestamp.dt.date
        previous_observed = day.shift()
        expected_previous = day.map(previous_regular)
        cross_day = previous_observed.notna() & day.ne(previous_observed)
        missing_regular_session = cross_day & expected_previous.notna() & previous_observed.ne(expected_previous)
        breaks = (same_day & delta.ne(BAR)) | missing_regular_session
        segment_id = breaks.cumsum()
        valid_segments = []
        for _, segment in frame.groupby(segment_id, sort=True):
            if len(segment) >= 35:
                # Reset causal indicators after a missing in-session bar so RSI/EMA
                # cannot silently bridge an unexplained data gap.
                valid_segments.append(calculate_indicators(segment.reset_index(drop=True)))
        if valid_segments:
            output[symbol] = pd.concat(valid_segments, ignore_index=True).sort_values(
                "timestamp", kind="stable").reset_index(drop=True)
    return output


def _same_session_contiguous(times: list[pd.Timestamp]) -> bool:
    for left, right in zip(times, times[1:]):
        if left.date() == right.date() and right - left != BAR:
            return False
    return True


def build_events(frames: dict[str, pd.DataFrame], candidate: ExperimentCandidate) -> tuple[list[dict], Counter, Counter]:
    events, signals, rejects = [], Counter(), Counter()
    for symbol, frame in frames.items():
        if candidate.kind == "reference":
            detected = detect_reversal_candidates(frame, decline_candles=3, min_decline_pct=1.0, rsi_threshold=25.0)
            decline_pct = detected["decline_pct"]
            mask = (decline_pct.gt(-2.0) & decline_pct.le(-1.0)
                    & detected.rsi_14.ge(20.0) & detected.rsi_14.lt(25.0)
                    & detected.reversal_condition)
        else:
            n = candidate.decline_bars
            prior = pd.concat([frame.close.shift(shift) for shift in range(n, 0, -1)], axis=1)
            decline_pct = (prior.iloc[:, -1] / prior.iloc[:, 0] - 1.0) * 100.0
            monotonic = pd.Series(np.all(prior.iloc[:, :-1].to_numpy() > prior.iloc[:, 1:].to_numpy(), axis=1), index=frame.index)
            mask = (decline_pct.le(-candidate.min_decline_pct)
                    & decline_pct.gt(-candidate.max_decline_pct_exclusive)
                    & frame.rsi_14.ge(candidate.rsi_min_inclusive)
                    & frame.rsi_14.lt(candidate.rsi_max_exclusive)
                    & frame.close.gt(frame.close.shift(1)))
            if candidate.require_monotonic_decline:
                mask &= monotonic
        for index in frame.index[mask]:
            signals[symbol] += 1
            i = int(index)
            feature_start = max(0, i - max(14, candidate.decline_bars))
            feature_times = frame.timestamp.iloc[feature_start:i + 1].tolist()
            if len(feature_times) < max(15, candidate.decline_bars + 1) or not _same_session_contiguous(feature_times):
                rejects["feature_window_gap_or_warmup"] += 1
                continue
            entry_index = i + 1
            if entry_index >= len(frame):
                rejects["no_next_candle"] += 1
                continue
            signal_time = frame.timestamp.iloc[i]
            entry_time = frame.timestamp.iloc[entry_index]
            if entry_time.date() != signal_time.date() or entry_time - signal_time != BAR:
                rejects["entry_crosses_session_or_gap"] += 1
                continue
            day_end = entry_index
            while day_end + 1 < len(frame) and frame.timestamp.iloc[day_end + 1].date() == signal_time.date():
                day_end += 1
            last_index = min(entry_index + candidate.horizon_bars - 1, day_end)
            session_truncated = entry_index + candidate.horizon_bars - 1 > day_end
            holding_times = frame.timestamp.iloc[entry_index:last_index + 1].tolist()
            if not holding_times or not _same_session_contiguous(holding_times):
                rejects["holding_window_gap"] += 1
                continue
            if last_index == day_end and frame.timestamp.iloc[day_end].time() != IST_LAST_START:
                rejects["incomplete_session_tail"] += 1
                continue
            events.append({"symbol": symbol, "frame": frame, "signal_index": i,
                           "entry_index": entry_index, "last_index": last_index,
                           "session_truncated": session_truncated,
                           "signal_timestamp": signal_time, "entry_timestamp": entry_time,
                           "decline_pct": float(frame.close.iloc[i] / frame.close.iloc[i - 3] * 100 - 100) if candidate.kind == "reference" else float(((frame.close.iloc[i - 1] / frame.close.iloc[i - candidate.decline_bars]) - 1) * 100),
                           "rsi_14": float(frame.rsi_14.iloc[i])})
    events.sort(key=lambda e: (e["entry_timestamp"], e["symbol"]))
    return events, signals, rejects


def simulate(events: list[dict], candidate: ExperimentCandidate, initial_capital: float,
             costs: CostAssumptions | None = None,
             timestamp_evidence: TimestampSemanticsEvidence | None = None,
             verified_regular_dates: set[date] | None = None) -> tuple[list[dict], dict]:
    if timestamp_evidence is None:
        raise ExecutionIntegrityError("verified timestamp evidence is required before simulated execution")
    timestamp_evidence.require_verified_start_labels()
    if not verified_regular_dates:
        raise ExecutionIntegrityError("verified regular-session dates are required before simulated execution")
    if (type(candidate.horizon_bars) is not int or not 1 <= candidate.horizon_bars <= 12
            or type(candidate.stop_pct) not in (int, float) or not np.isfinite(candidate.stop_pct)
            or not 0 < candidate.stop_pct < 5):
        raise ExecutionIntegrityError("candidate exit parameters are outside registered research bounds")
    costs = costs or CostAssumptions()
    trades, rejected = [], Counter()
    try:
        capital = float(initial_capital)
    except (TypeError, ValueError) as exc:
        raise ExecutionIntegrityError("starting capital must be a positive finite number") from exc
    if not np.isfinite(capital) or capital <= 0:
        raise ExecutionIntegrityError("starting capital must be a positive finite number")
    day_start_capital: dict[date, float] = {}
    day_loss, day_trades = Counter(), Counter()
    open_exit_labels = []
    previous_entry_time = None
    for event in events:
        try:
            validate_simulation_event(event, timestamp_evidence)
            if event["last_index"] - event["entry_index"] >= candidate.horizon_bars:
                raise ExecutionIntegrityError("event exceeds the registered holding horizon")
        except ExecutionIntegrityError:
            rejected["execution_integrity_rejected"] += 1
            continue
        entry_time = event["entry_timestamp"]
        if entry_time.date() not in verified_regular_dates:
            rejected["execution_integrity_rejected"] += 1
            continue
        if previous_entry_time is not None and entry_time < previous_entry_time:
            rejected["execution_integrity_rejected"] += 1
            continue
        previous_entry_time = entry_time
        day = entry_time.date()
        day_start_capital.setdefault(day, capital)
        open_exit_labels = [exit_label for exit_label in open_exit_labels if exit_label >= entry_time]
        if capital <= 0:
            rejected["insufficient_capital"] += 1
            continue
        daily_config = RiskConfig(capital=day_start_capital[day], max_risk_per_trade=0.01,
                                  max_daily_loss=0.02, max_positions=1, max_trades_per_day=3)
        allowed, reason = check_trade_allowed(daily_config, RiskState(
            daily_loss=day_loss[day], open_positions=len(open_exit_labels),
            trades_today=day_trades[day]))
        if not allowed:
            rejected[reason] += 1
            continue
        frame = event["frame"]
        entry_index = event["entry_index"]
        entry = float(frame.open.iloc[entry_index])
        stop = entry * (1 - candidate.stop_pct / 100.0)
        active_config = RiskConfig(capital=capital, max_risk_per_trade=0.01,
                                   max_daily_loss=0.02, max_positions=1, max_trades_per_day=3)
        size = calculate_position_size(active_config, entry, stop)
        if size.quantity <= 0:
            rejected["insufficient_capital_for_one_share"] += 1
            continue
        exit_index, exit_price, reason = event["last_index"], None, "horizon_exit"
        for idx in range(entry_index, event["last_index"] + 1):
            bar = frame.iloc[idx]
            if float(bar.low) <= stop:
                exit_index = idx
                exit_price = min(float(bar.open), stop)
                reason = "gap_exit" if float(bar.open) < stop else "stop_exit"
                break
        if exit_price is None:
            exit_price = float(frame.close.iloc[exit_index])
            if event.get("session_truncated"):
                reason = "session_exit"
        gross = (exit_price - entry) * size.quantity
        itemized = estimate_round_trip_costs(entry, exit_price, size.quantity, entry_time, assumptions=costs)
        net = gross - itemized["total_cost"]
        regime = event.get("regime", {})
        trade = {"strategy_id": candidate.strategy_id, "symbol": event["symbol"],
                 "signal_timestamp": event["signal_timestamp"].isoformat(),
                 "entry_timestamp": entry_time.isoformat(),
                 "exit_timestamp": frame.timestamp.iloc[exit_index].isoformat(),
                 "decline_pct": event["decline_pct"], "rsi_14": event["rsi_14"],
                 "entry_price": entry, "exit_price": exit_price, "stop_loss": stop,
                 "quantity": size.quantity, "planned_risk": size.planned_risk,
                 "capital_exposure": size.capital_required, "gross_pnl": gross,
                 **itemized, "net_pnl": net, "exit_reason": reason,
                 "trend_regime": regime.get("trend_regime", "unclassified"),
                 "volatility_regime": regime.get("volatility_regime", "unclassified")}
        trades.append(trade)
        capital += net
        if net < 0:
            day_loss[day] += abs(net)
        day_trades[day] += 1
        open_exit_labels.append(frame.timestamp.iloc[exit_index])
    return trades, {"risk_rejections": dict(sorted(rejected.items())), "ending_capital": capital}


def metrics(trades: list[dict], initial_capital: float) -> dict:
    net_values = [float(row["net_pnl"]) for row in trades]
    gross_values = [float(row["gross_pnl"]) for row in trades]
    winners = sum(value > 0 for value in net_values)
    gross_wins = sum(value for value in net_values if value > 0)
    gross_losses = -sum(value for value in net_values if value < 0)
    equity = [initial_capital]
    for value in net_values:
        equity.append(equity[-1] + value)
    peaks = np.maximum.accumulate(np.asarray(equity, dtype=float))
    drawdown = float(np.min(np.asarray(equity) / peaks - 1.0) * 100) if equity else 0.0
    return {
        "trade_count": len(trades), "gross_pnl_inr": float(sum(gross_values)),
        "estimated_costs_inr": float(sum(float(row["total_cost"]) for row in trades)),
        "net_pnl_inr": float(sum(net_values)),
        "win_rate_pct": float(winners / len(trades) * 100) if trades else 0.0,
        "net_profit_factor": float(gross_wins / gross_losses) if gross_losses else (None if not gross_wins else "Infinity"),
        "max_closed_equity_drawdown_pct": drawdown,
        "average_net_trade_inr": float(np.mean(net_values)) if net_values else 0.0,
        "turnover_inr": float(sum((row["entry_price"] + row["exit_price"]) * row["quantity"] for row in trades)),
    }


def write_experiment(root: Path, spec_path: Path, output: Path,
                     timestamp_evidence: TimestampSemanticsEvidence | None = None) -> dict:
    """Run every preregistered candidate and save deterministic, new artifacts."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite experiment output: {output}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    input_paths = [root / item for item in spec["data_files"]] + [root / spec["session_manifest"], spec_path]
    input_hashes = {str(path.relative_to(root)): sha256_file(path) for path in input_paths}
    if timestamp_evidence is None:
        raise ExecutionIntegrityError("no verified timestamp/acquisition evidence; historical execution is blocked")
    if not timestamp_evidence.evidence_file_sha256:
        raise ExecutionIntegrityError("timestamp evidence must be loaded from a hashed evidence file")
    timestamp_evidence.require_verified_start_labels({
        relative: digest for relative, digest in input_hashes.items() if relative in spec["data_files"]
    })
    data, audit = prepare_data(root, spec)
    session_manifest = load_session_dates(root / spec["session_manifest"])
    regular_dates = {day for day, item in session_manifest.items()
                     if item.get("verification_status") == "VERIFIED" and item.get("session_type") == "REGULAR" and not item.get("special_session")}
    frames = prepare_symbol_frames(data, regular_dates)
    regime_map = build_market_regimes(data).set_index("date").to_dict("index")
    candidates, registry = load_candidates(spec)
    all_metrics, all_trades, all_symbols, all_regimes, candidate_summaries = [], [], [], [], []
    windows = [{"partition_id": "all_observed", "start": spec["partitions"][0]["start"],
                "end": spec["partitions"][-1]["end"], "status": "descriptive full span; all periods previously examined"}, *spec["partitions"]]
    for candidate in candidates:
        events, signal_counts, data_rejects = build_events(frames, candidate)
        for event in events:
            event["regime"] = regime_map.get(event["entry_timestamp"].date(), {})
        candidate_trades = []
        for window in windows:
            start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
            selected = [event for event in events if start <= event["signal_timestamp"].date() <= end]
            trades, sim_meta = simulate(selected, candidate, spec["starting_capital_inr"],
                                        timestamp_evidence=timestamp_evidence,
                                        verified_regular_dates=regular_dates)
            coverage = audit["retained_date_coverage_by_partition"].get(window["partition_id"], {})
            coverage_note = window["status"]
            if coverage.get("missing_expected_regular_dates"):
                coverage_note += (f"; partial retained coverage {coverage['retained_start']}–{coverage['retained_end']}; "
                                  f"{len(coverage['missing_expected_regular_dates'])} expected regular dates have no retained candles")
            row = {"strategy_id": candidate.strategy_id, "partition_id": window["partition_id"],
                   "partition_status": coverage_note,
                   "retained_start": coverage.get("retained_start"), "retained_end": coverage.get("retained_end"),
                   "missing_expected_regular_date_count": len(coverage.get("missing_expected_regular_dates", [])),
                   **metrics(trades, spec["starting_capital_inr"]),
                   "candidate_signals_full_dataset": int(sum(signal_counts.values())),
                   "data_rejections_full_dataset": json.dumps(dict(sorted(data_rejects.items())), sort_keys=True),
                   "risk_rejections": json.dumps(sim_meta["risk_rejections"], sort_keys=True)}
            all_metrics.append(row)
            if window["partition_id"] == "all_observed":
                candidate_trades = trades
        for trade in candidate_trades:
            all_trades.append(trade)
        status = registry.require(candidate.strategy_id)
        candidate_summaries.append({"strategy_id": candidate.strategy_id, "status": status.status,
                                    "enabled_for_paper": status.enabled_for_paper,
                                    "signals": int(sum(signal_counts.values())), "accepted_trades": len(candidate_trades),
                                    "data_rejections": dict(sorted(data_rejects.items()))})
        for symbol in sorted({trade["symbol"] for trade in candidate_trades}):
            part = [trade for trade in candidate_trades if trade["symbol"] == symbol]
            all_symbols.append({"strategy_id": candidate.strategy_id, "symbol": symbol,
                                **metrics(part, spec["starting_capital_inr"])})
        for dimension in ("trend_regime", "volatility_regime"):
            for regime in sorted({trade[dimension] for trade in candidate_trades}):
                part = [trade for trade in candidate_trades if trade[dimension] == regime]
                all_regimes.append({"strategy_id": candidate.strategy_id, "regime_dimension": dimension,
                                    "regime": regime, **metrics(part, spec["starting_capital_inr"])})
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    code_paths = ["app/research/sharp_reversal_experiment.py", "app/research/execution_integrity.py",
                  "scripts/run_sharp_reversal_experiment.py",
                  "app/candle_integrity.py", "app/cost_model.py", "app/market_analysis.py",
                  "app/reversal_detector.py", "app/risk_engine.py", "app/v2/registry.py", "strategy_backtest.py"]
    import platform
    import ta
    code_hashes = {relative: sha256_file(root / relative) for relative in code_paths}
    manifest = {"experiment_id": spec["experiment_id"], "source_commit_before_experiment": commit,
                "input_hashes_sha256": input_hashes, "code_hashes_sha256": code_hashes,
                "timestamp_evidence_sha256": timestamp_evidence.evidence_file_sha256,
                "timestamp_semantics": timestamp_evidence.label_semantics,
                "timestamp_semantics_verification": timestamp_evidence.verification_status,
                "acquisition_request_ids": list(timestamp_evidence.acquisition_request_ids),
                "runtime": {"python": platform.python_version(), "pandas": pd.__version__,
                            "numpy": np.__version__, "ta": getattr(ta, "__version__", "version_not_exposed")},
                "data_audit": audit,
                "strategy_registry": candidate_summaries,
                "important_limits": ["all observed periods were previously examined", "no untouched holdout",
                                     "historical provenance, adjustment, and survivorship unresolved",
                                     "market regime is a lagged equal-weight basket proxy, not an official index",
                                     "timestamp labels provisionally treated as candle starts"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (output / "experiment_spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (output / "data_exclusions.json").write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    pd.DataFrame(all_metrics).to_csv(output / "metrics_by_partition.csv", index=False)
    pd.DataFrame(all_trades).to_csv(output / "trades.csv", index=False, float_format="%.10f")
    pd.DataFrame(all_symbols).to_csv(output / "metrics_by_symbol.csv", index=False)
    pd.DataFrame(all_regimes).to_csv(output / "metrics_by_regime.csv", index=False)
    report = render_report(spec, manifest, all_metrics, all_symbols, all_regimes, candidate_summaries)
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    return manifest


def render_report(spec: dict, manifest: dict, metrics_rows: list[dict], symbol_rows: list[dict], regime_rows: list[dict], candidates: list[dict]) -> str:
    lines = ["# Sharp-reversal exploratory experiment v1", "",
             "**Status: EXPLORATORY ONLY. This is not independent validation, profitability evidence, or paper/live approval.**", "",
             "## Method and limitations", "",
             "The parameter set was frozen in the saved experiment specification before this run. The cached CSVs have unresolved acquisition provenance, adjustment status, point-in-time universe membership, and dataset-specific candle-label semantics. Source labels are provisionally treated as candle starts. Every date partition below has appeared in prior project research; the 2025-12-02–2026-10-01 segment was previously evaluated and is not a fresh holdout.", "",
             "Only dates marked VERIFIED and REGULAR in the checked-in NSE CM manifest v2 are retained. Special, holiday, absent, or unresolved session dates, out-of-session rows, known anomaly dates, and structurally invalid rows are excluded. Same-session gaps invalidate affected feature/entry/holding windows; no bar is filled. The regime is a lagged 14-stock equal-weight basket proxy, not an official index regime.", "",
             "A symbol-session is included only when it has all 75 expected regular five-minute labels; any incomplete or zero-volume symbol-session is excluded. Same-session gaps invalidate the affected session and indicators restart after missing regular sessions. Entry is next same-session five-minute candle open after the completed signal. Long-only, no target, stop checked from the entry candle onward; gap-through stop fills at open. Exits occur by stop, horizon, or session end. No trade crosses a date. Position sizing uses the existing whole-share risk engine: 1% current equity risk, 100% capital cap; one open position; 2% day-start-equity loss cap; three trades/day. Daily loss accumulates the absolute net losses without offset from winning trades. Costs reuse the existing cost model, including estimated taxes/fees and 5 bps per-side slippage. These are estimates, not measured execution costs.", "",
             "## Overall results", "", "| Candidate | Trades | Gross P&L (₹) | Costs (₹) | Net P&L (₹) | Win rate | Net PF | Max DD | Avg net/trade (₹) |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in metrics_rows:
        if row["partition_id"] != "all_observed":
            continue
        lines.append(f"| {row['strategy_id']} | {row['trade_count']} | {row['gross_pnl_inr']:.2f} | {row['estimated_costs_inr']:.2f} | {row['net_pnl_inr']:.2f} | {row['win_rate_pct']:.1f}% | {row['net_profit_factor']} | {row['max_closed_equity_drawdown_pct']:.2f}% | {row['average_net_trade_inr']:.2f} |")
    lines += ["", "## Chronological partitions", "", "Partition labels describe prior reuse, not fresh holdout status.", "",
              "| Candidate | Partition | Status | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in metrics_rows:
        if row["partition_id"] == "all_observed":
            continue
        lines.append(f"| {row['strategy_id']} | {row['partition_id']} | {row['partition_status']} | {row['trade_count']} | {row['gross_pnl_inr']:.2f} | {row['estimated_costs_inr']:.2f} | {row['net_pnl_inr']:.2f} | {row['win_rate_pct']:.1f}% | {row['net_profit_factor']} | {row['max_closed_equity_drawdown_pct']:.2f}% | {row['average_net_trade_inr']:.2f} |")
    lines += ["", "## By stock", "", "| Candidate | Stock | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in symbol_rows:
        lines.append(f"| {row['strategy_id']} | {row['symbol']} | {row['trade_count']} | {row['gross_pnl_inr']:.2f} | {row['estimated_costs_inr']:.2f} | {row['net_pnl_inr']:.2f} | {row['win_rate_pct']:.1f}% | {row['net_profit_factor']} | {row['max_closed_equity_drawdown_pct']:.2f}% | {row['average_net_trade_inr']:.2f} |")
    lines += ["", "Stock and regime drawdowns are calculated on each subgroup's isolated trade sequence with the same starting capital; they are descriptive and not additive contributions to the portfolio drawdown.", "",
              "## By market-regime proxy", "", "| Candidate | Dimension | Regime | Trades | Gross ₹ | Costs ₹ | Net ₹ | Win rate | PF | DD | Avg net ₹ |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in regime_rows:
        lines.append(f"| {row['strategy_id']} | {row['regime_dimension']} | {row['regime']} | {row['trade_count']} | {row['gross_pnl_inr']:.2f} | {row['estimated_costs_inr']:.2f} | {row['net_pnl_inr']:.2f} | {row['win_rate_pct']:.1f}% | {row['net_profit_factor']} | {row['max_closed_equity_drawdown_pct']:.2f}% | {row['average_net_trade_inr']:.2f} |")
    lines += ["", "## Data and reproducibility", "", f"- Retained rows: {manifest['data_audit']['retained_rows']}; excluded rows: {manifest['data_audit']['excluded_rows']}; residual same-day gaps: {manifest['data_audit']['same_day_gaps_after_row_exclusions']}.",
              f"- Complete symbol-sessions retained: {manifest['data_audit']['complete_symbol_sessions_retained']}; incomplete symbol-sessions excluded: {manifest['data_audit']['incomplete_symbol_sessions_excluded']}; zero-volume rows retained: {manifest['data_audit']['zero_volume_rows_retained']}.",
              f"- Requested OOS partition retained coverage: {manifest['data_audit']['retained_date_coverage_by_partition']['previous_oos_reused']['retained_start']} through {manifest['data_audit']['retained_date_coverage_by_partition']['previous_oos_reused']['retained_end']}; {len(manifest['data_audit']['retained_date_coverage_by_partition']['previous_oos_reused']['missing_expected_regular_dates'])} expected regular dates have no retained candles. Missing dates are listed in `data_exclusions.json`.",
              f"- Input and code SHA-256 values, runtime versions, and source commit `{manifest['source_commit_before_experiment']}` are in `run_manifest.json`.",
              "- Exclusions and per-reason counts are in `data_exclusions.json`; trade-level simulated fills and costs are in `trades.csv`.",
              "- No AI historical decisions were fabricated or replayed. No provider, broker, or acquisition-pilot call was made.",
              "- All candidates remain `RESEARCH_ONLY`; none is approved or enabled for V2 paper execution.", "",
              "## Conclusion", ""]
    exploratory_candidates = [item for item in spec["candidates"] if item["kind"] == "research_candidate"]
    screen_passes = []
    for candidate in exploratory_candidates:
        overall = next(row for row in metrics_rows if row["strategy_id"] == candidate["strategy_id"] and row["partition_id"] == "all_observed")
        partition_results = [row for row in metrics_rows if row["strategy_id"] == candidate["strategy_id"] and row["partition_id"] != "all_observed"]
        positive_partitions = sum(row["net_pnl_inr"] > 0 for row in partition_results)
        if overall["trade_count"] >= 30 and overall["net_pnl_inr"] > 0 and positive_partitions >= 2:
            screen_passes.append(candidate["strategy_id"])
    if screen_passes:
        lines.append("Candidates clearing the predeclared exploratory screen and deserving further investigation: " + ", ".join(screen_passes) + ". This is not validation or paper approval.")
    else:
        lines.append("No research candidate clears the predeclared exploratory screen. No candidate shows sufficient evidence to recommend further investigation after modeled costs; low trade counts, where applicable, make results inconclusive rather than proof that no edge exists.")
    lines += ["All results remain exploratory because the source data and periods are reused and provenance remains unresolved.", ""]
    return "\n".join(lines)
