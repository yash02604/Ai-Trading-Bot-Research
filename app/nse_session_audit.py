"""NSE CM session and candle-coverage validation, independent of strategy code."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

CLASSIFICATIONS = {"VERIFIED_VALID", "VERIFIED_INVALID", "UNRESOLVED"}
IST = "Asia/Kolkata"


@dataclass
class AuditResult:
    rows: pd.DataFrame
    coverage: pd.DataFrame


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("exchange") != "NSE" or manifest.get("segment") != "CM":
        raise ValueError("Unsupported or untrusted NSE CM manifest")
    if not manifest.get("sources"):
        raise ValueError("Manifest has no primary sources")
    return manifest


def _labels(interval: dict, convention: str, minutes: int) -> set[datetime]:
    start = datetime.combine(date(2000, 1, 1), time.fromisoformat(interval["start"]))
    end = datetime.combine(date(2000, 1, 1), time.fromisoformat(interval["end"]))
    if convention == "start":
        last = end - timedelta(minutes=minutes)
        return {start + timedelta(minutes=i) for i in range(0, int((last-start).total_seconds()//60)+1, minutes)}
    if convention == "end":
        first = start + timedelta(minutes=minutes)
        return {first + timedelta(minutes=i) for i in range(0, int((end-first).total_seconds()//60)+1, minutes)}
    raise ValueError("timestamp_convention must be 'start', 'end', or None")


def _session_has_trusted_sources(day: dict[str, Any], manifest: dict[str, Any]) -> bool:
    sources = manifest.get("sources", [])
    source_map = {item.get("source_id"): item for item in sources if isinstance(item, dict) and item.get("source_id")}
    source_ids = day.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids or any(source_id not in source_map for source_id in source_ids):
        return False
    session_date = day.get("date", "")
    for source_id in source_ids:
        source = source_map[source_id]
        bounds = source.get("effective_dates", "").split("/")
        if not source.get("url") or len(bounds) not in (1, 2) or (session_date != bounds[0] if len(bounds) == 1 else not (bounds[0] <= session_date <= bounds[1])):
            return False
    return True

def validate_frame(frame: pd.DataFrame, manifest: dict[str, Any], *, instrument: str | None = None,
                   timestamp_convention: str | None = None, interval_minutes: int = 5) -> AuditResult:
    """Classify input rows and expected coverage, without authenticating feed values."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    if "timestamp" not in frame:
        raise ValueError("timestamp column is required")
    records = {x["date"]: x for x in manifest.get("sessions", [])}
    rows = frame.copy().reset_index(drop=False).rename(columns={"index": "source_row"})
    parsed = pd.to_datetime(rows["timestamp"], errors="coerce", utc=True)
    raw_text = rows["timestamp"].astype(str)
    aware = raw_text.str.endswith("Z") | raw_text.str.contains(r"[+-][0-9]{2}:?[0-9]{2}$", regex=True)
    rows["timestamp_ist"] = parsed.dt.tz_convert(IST)
    rows["date_ist"] = rows["timestamp_ist"].dt.strftime("%Y-%m-%d")
    key = instrument if instrument and instrument in rows.columns else "instrument" if "instrument" in rows.columns else "stock" if "stock" in rows.columns else None
    rows["audit_instrument"] = rows[key].astype(str) if key else "UNSPECIFIED"
    classifications = pd.Series("UNRESOLVED", index=rows.index, dtype=object)
    reasons = pd.Series("timestamp_or_manifest_unverified", index=rows.index, dtype=object)
    valid_clock = rows.timestamp_ist.notna() & aware
    for day_value, indices in rows.groupby("date_ist", dropna=False).groups.items():
        if not isinstance(day_value, str) or day_value not in records:
            continue
        day = records[day_value]
        idx = list(indices)
        if day.get("verification_status") != "VERIFIED" or day.get("session_type") == "UNRESOLVED" or not _session_has_trusted_sources(day, manifest):
            continue
        mask = valid_clock.loc[idx]
        eligible = [i for i in idx if mask.loc[i]]
        if not eligible:
            continue
        if day["session_type"] == "HOLIDAY":
            classifications.loc[eligible] = "VERIFIED_INVALID"; reasons.loc[eligible] = "candle_on_verified_holiday"
            continue
        if day["session_type"] == "WEEKEND_CLOSED":
            classifications.loc[eligible] = "VERIFIED_INVALID"; reasons.loc[eligible] = "candle_on_weekend_without_session"
            continue
        if timestamp_convention not in ("start", "end"):
            reasons.loc[eligible] = "candle_label_convention_unresolved"
            continue
        expected = set()
        for interval in day.get("normal_market_intervals", []):
            expected |= {x.strftime("%H:%M") for x in _labels(interval, timestamp_convention, interval_minutes)}
        clock = rows.loc[eligible, "timestamp_ist"].dt.strftime("%H:%M")
        accepted = clock.isin(expected)
        good_idx = clock.index[accepted]
        bad_idx = clock.index[~accepted]
        classifications.loc[good_idx] = "VERIFIED_VALID"; reasons.loc[good_idx] = "verified_calendar_and_session_label"
        classifications.loc[bad_idx] = "VERIFIED_INVALID"; reasons.loc[bad_idx] = "out_of_session_or_unaligned_label"
    rows["classification"] = classifications
    rows["reason"] = reasons
    rows["duplicate_label"] = rows.duplicated(["audit_instrument", "timestamp_ist"], keep=False) & rows.timestamp_ist.notna()
    rows.loc[rows.duplicate_label, ["classification", "reason"]] = ["VERIFIED_INVALID", "duplicate_timestamp_label"]

    coverage_records = []
    if timestamp_convention in ("start", "end"):
        dated = rows.dropna(subset=["date_ist"])
        if not dated.empty:
            first_day, last_day = dated.date_ist.min(), dated.date_ist.max()
            instruments = sorted(dated.audit_instrument.unique())
            observed_groups = {key: group for key, group in dated.groupby(["audit_instrument", "date_ist"], sort=False)}
            for day_value, day in records.items():
                if day_value < first_day or day_value > last_day:
                    continue
                if day.get("verification_status") != "VERIFIED" or day.get("session_type") in ("HOLIDAY", "WEEKEND_CLOSED", "UNRESOLVED") or not _session_has_trusted_sources(day, manifest):
                    continue
                expected = set()
                for interval in day.get("normal_market_intervals", []):
                    expected |= {x.strftime("%H:%M") for x in _labels(interval, timestamp_convention, interval_minutes)}
                for name in instruments:
                    subset = observed_groups.get((name, day_value))
                    actual = set() if subset is None else set(subset.timestamp_ist.dt.strftime("%H:%M").dropna())
                    missing = sorted(expected - actual)
                    coverage_records.append({"instrument": name, "date": day_value, "session_type": day["session_type"],
                        "expected_labels": len(expected), "observed_labels": len(actual), "missing_labels": len(missing),
                        "missing_timestamps": "|".join(missing),
                        "classification": "VERIFIED_INVALID" if missing else "VERIFIED_VALID",
                        "reason": "missing_expected_candles" if missing else "complete_expected_label_coverage"})
    return AuditResult(rows=rows, coverage=pd.DataFrame(coverage_records))
