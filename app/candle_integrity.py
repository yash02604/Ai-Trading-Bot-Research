"""Shared structural validation and conflict-safe merging for candle data.

Timestamp parsing is deliberately separate from exchange-session validation. This
module normalizes aware timestamps to Asia/Kolkata, but does not reject a candle
because its time is unusual for a presumed session.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import re


OHLCV = ("open", "high", "low", "close", "volume")
REQUIRED = ("timestamp", *OHLCV)
TZ = "Asia/Kolkata"


@dataclass
class CandleValidation:
    frame: pd.DataFrame
    errors: list[dict]
    warnings: list[dict]

    @property
    def valid(self) -> bool:
        return not self.errors


class CandleDataError(ValueError):
    """Hard candle-data error with machine-readable row details."""

    def __init__(self, message: str, report: CandleValidation | None = None, details=None):
        super().__init__(message)
        self.report = report
        self.details = details or []


def validate_candles(frame: pd.DataFrame, *, key_columns=None, check_duplicates=True) -> CandleValidation:
    """Normalize timestamps and report hard structural errors and soft warnings."""
    data = frame.copy()
    errors: list[dict] = []
    warnings: list[dict] = []
    missing = sorted(set(REQUIRED).difference(data.columns))
    if missing:
        errors.append({"row": None, "reason": "missing_required_columns", "fields": missing})
        return CandleValidation(data, errors, warnings)
    known = set(REQUIRED) | {"stock", "instrument_key", "oi", "open_interest", "date"}
    extras = sorted(set(data.columns).difference(known))
    if extras:
        warnings.append({"row": None, "reason": "unrecognized_optional_fields_preserved", "fields": extras})

    raw_timestamps = data["timestamp"]
    if isinstance(raw_timestamps.dtype, pd.DatetimeTZDtype):
        aware = pd.Series(True, index=data.index)
    elif pd.api.types.is_datetime64_dtype(raw_timestamps.dtype):
        aware = pd.Series(False, index=data.index)
    else:
        aware = raw_timestamps.astype(str).str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", case=False, regex=True)
    for idx in data.index[~aware.fillna(False)]:
        errors.append({"row": _row_label(data, idx), "reason": "timezone_naive_timestamp"})
    parsed = pd.to_datetime(data["timestamp"], errors="coerce", utc=True)
    invalid_ts = parsed.isna()
    for idx in data.index[invalid_ts]:
        errors.append({"row": _row_label(data, idx), "reason": "invalid_timestamp"})
    data["timestamp"] = parsed.dt.tz_convert(TZ)

    numeric = data.loc[:, OHLCV].apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    finite = np.isfinite(values).all(axis=1) & ~numeric.isna().any(axis=1).to_numpy()
    for idx in data.index[~finite]:
        errors.append({"row": _row_label(data, idx), "reason": "nonfinite_or_nonnumeric_ohlcv"})
    data.loc[:, OHLCV] = numeric

    valid_numeric = pd.Series(finite & ~invalid_ts.to_numpy(), index=data.index)
    positive = (numeric[["open", "high", "low", "close"]] > 0).all(axis=1)
    relationships = (
        (numeric.low <= numeric.open) & (numeric.open <= numeric.high) &
        (numeric.low <= numeric.close) & (numeric.close <= numeric.high) & (numeric.low <= numeric.high)
    )
    for idx in data.index[valid_numeric & ~positive]:
        errors.append({"row": _row_label(data, idx), "reason": "nonpositive_price"})
    for idx in data.index[valid_numeric & (numeric.volume < 0)]:
        errors.append({"row": _row_label(data, idx), "reason": "negative_volume"})
    for idx in data.index[valid_numeric & ~relationships]:
        errors.append({"row": _row_label(data, idx), "reason": "invalid_ohlc_relationship"})
    for idx in data.index[valid_numeric & numeric.volume.eq(0)]:
        warnings.append({"row": _row_label(data, idx), "reason": "zero_volume_retained"})

    stamp = data["timestamp"]
    aligned = stamp.notna() & stamp.dt.minute.mod(5).eq(0) & stamp.dt.second.eq(0) & stamp.dt.microsecond.eq(0)
    for idx in data.index[stamp.notna() & ~aligned]:
        errors.append({"row": _row_label(data, idx), "reason": "timestamp_not_five_minute_aligned"})
    for idx in data.index[stamp.notna() & ((stamp.dt.hour < 9) | (stamp.dt.hour > 15) | ((stamp.dt.hour == 15) & (stamp.dt.minute > 25)))]:
        warnings.append({"row": _row_label(data, idx), "reason": "outside_regular_session_candidate_check_calendar"})
    time_key = list(key_columns if key_columns is not None else (["instrument_key"] if "instrument_key" in data else ["stock"] if "stock" in data else []))
    group_cols = [c for c in time_key if c in data]
    ordered = data.sort_values(group_cols + ["timestamp"], kind="stable") if group_cols else data.sort_values("timestamp", kind="stable")
    groups = ordered.groupby(group_cols, sort=False) if group_cols else [(None, ordered)]
    for _, group in groups:
        delta = group["timestamp"].diff()
        same_date = group["timestamp"].dt.date.eq(group["timestamp"].shift().dt.date)
        for idx in group.index[(delta > pd.Timedelta(minutes=5)) & same_date]:
            warnings.append({"row": _row_label(data, idx), "reason": "intrasession_gap_review_calendar"})

    keys = list(key_columns if key_columns is not None else (["instrument_key"] if "instrument_key" in data else ["stock"] if "stock" in data else []))
    if check_duplicates:
        if keys:
            absent = [key for key in keys if key not in data]
            if absent:
                errors.append({"row": None, "reason": "missing_duplicate_key_columns", "fields": absent})
            else:
                dup = data.duplicated(keys + ["timestamp"], keep=False) & stamp.notna()
                for idx in data.index[dup]:
                    errors.append({"row": _row_label(data, idx), "reason": "duplicate_instrument_timestamp"})
        else:
            dup = data.duplicated(["timestamp"], keep=False) & stamp.notna()
            for idx in data.index[dup]:
                errors.append({"row": _row_label(data, idx), "reason": "duplicate_instrument_timestamp"})

    return CandleValidation(data, errors, warnings)


def require_valid_candles(frame: pd.DataFrame, *, key_columns=None, check_duplicates=True) -> tuple[pd.DataFrame, list[dict]]:
    report = validate_candles(frame, key_columns=key_columns, check_duplicates=check_duplicates)
    if report.errors:
        reasons = sorted({item["reason"] for item in report.errors})
        raise CandleDataError(f"Invalid candle data: {', '.join(reasons)}", report)
    return report.frame, report.warnings


def merge_candle_sources(sources, *, key_columns=None):
    """Merge named (source, DataFrame) inputs; identical overlaps dedupe, conflicts fail."""
    keys = list(key_columns if key_columns is not None else ["instrument_key"])
    frames = []
    for source, frame in sources:
        validated, _ = require_valid_candles(frame, check_duplicates=False)
        absent = [key for key in keys if key not in validated]
        if absent:
            raise CandleDataError(f"{source}: missing merge key columns {absent}")
        part = validated.copy()
        part["__source"] = str(source)
        frames.append(part)
    if not frames:
        return pd.DataFrame(), {"duplicate_coverage": {"count": 0, "source_files": [], "sample_keys": []}, "conflicts": []}
    combined = pd.concat(frames, ignore_index=True)
    identity = keys + ([] if keys == ["timestamp"] else ["timestamp"])
    duplicate_mask = combined.duplicated(identity, keep=False)
    duplicate_rows = combined.loc[duplicate_mask]
    conflicts = []
    duplicate_count = 0
    duplicate_sample = []
    if not duplicate_rows.empty:
        compare_fields = list(OHLCV) + (["stock"] if "stock" in duplicate_rows else [])
        field_variation = duplicate_rows.groupby(identity, dropna=False, sort=False)[compare_fields].nunique(dropna=False)
        conflict_keys = field_variation.index[field_variation.gt(1).any(axis=1)]
        conflict_groups = duplicate_rows.groupby(identity, dropna=False, sort=False)
        for index_key in conflict_keys:
            key_values = index_key if isinstance(index_key, tuple) else (index_key,)
            group = conflict_groups.get_group(key_values)
            fields = field_variation.loc[index_key]
            record = {key: group.iloc[0][key] for key in identity}
            record["sources"] = sorted(group["__source"].unique().tolist())
            conflicts.append({**record, "differing_fields": fields.index[fields.gt(1)].tolist()})
        exact_keys = field_variation.index[field_variation.le(1).all(axis=1)]
        duplicate_count = int(len(exact_keys))
        for index_key in list(exact_keys[:20]):
            key_values = index_key if isinstance(index_key, tuple) else (index_key,)
            duplicate_sample.append(dict(zip(identity, key_values)))
    if conflicts:
        details = "; ".join(
            f"{entry['instrument_key'] if 'instrument_key' in entry else entry.get('timestamp')} "
            f"{entry['timestamp']} fields={entry['differing_fields']} sources={entry['sources']}"
            for entry in conflicts[:5]
        )
        raise CandleDataError(f"Conflicting overlapping candle records: {details}", details=conflicts)
    result = combined.loc[~combined.duplicated(identity, keep="first")].drop(columns="__source")
    result = result.sort_values(identity, kind="stable").reset_index(drop=True)
    coverage = {
        "count": duplicate_count,
        "source_files": sorted(duplicate_rows["__source"].unique().tolist()),
        "sample_keys": duplicate_sample,
    }
    return result, {"duplicate_coverage": coverage, "conflicts": conflicts}


def merge_raw_candle_batches(named_batches, instrument_key):
    """Conflict-check Upstox positional candle batches and return sorted rows."""
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    sources = []
    for name, candles in named_batches:
        frame = pd.DataFrame(candles, columns=columns)
        frame["instrument_key"] = instrument_key
        sources.append((name, frame))
    merged, audit = merge_candle_sources(sources, key_columns=["instrument_key"])
    return merged[columns].values.tolist(), audit


def _row_label(frame: pd.DataFrame, idx):
    return int(frame.index.get_loc(idx)) if idx in frame.index else str(idx)


