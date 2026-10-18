import json
from pathlib import Path

import pandas as pd
import pytest

from app.nse_session_audit import load_manifest, validate_frame

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_manifest(ROOT / "data/audits/nse_cm_session_manifest_v1.json")


def frame(times, instrument="TEST"):
    return pd.DataFrame({"stock": instrument, "timestamp": times})


def test_ordinary_session_timezone_conversion_and_complete_coverage():
    utc = ["2025-02-03T03:45:00Z", "2025-02-03T03:50:00Z"]
    out = validate_frame(frame(utc), MANIFEST, timestamp_convention="start")
    assert out.rows.classification.tolist() == ["VERIFIED_VALID"] * 2
    assert str(out.rows.timestamp_ist.dt.tz) == "Asia/Kolkata"
    assert out.coverage.iloc[0].missing_labels == 73


def test_verified_holiday_and_weekend_are_invalid():
    out = validate_frame(frame(["2024-01-26T09:15:00+05:30", "2024-01-21T09:15:00+05:30"]), MANIFEST, timestamp_convention="start")
    assert out.rows.reason.tolist() == ["candle_on_verified_holiday", "candle_on_weekend_without_session"]


@pytest.mark.parametrize("day", ["2024-01-20", "2024-03-02", "2024-05-18", "2024-11-01", "2025-02-01", "2025-10-21", "2026-02-01"])
def test_verified_special_sessions(day):
    entry = next(x for x in MANIFEST["sessions"] if x["date"] == day)
    assert entry["session_type"] == "SPECIAL"


def test_amended_and_special_schedule_boundaries():
    for day in ["2024-01-20", "2024-03-02", "2024-05-18"]:
        entry = next(x for x in MANIFEST["sessions"] if x["date"] == day)
        assert entry["verification_status"] == "VERIFIED"
    good = validate_frame(frame(["2024-03-02T11:30:00+05:30"]), MANIFEST, timestamp_convention="start")
    bad = validate_frame(frame(["2024-03-02T10:00:00+05:30"]), MANIFEST, timestamp_convention="start")
    assert good.rows.classification.iloc[0] == "VERIFIED_VALID"
    assert bad.rows.classification.iloc[0] == "VERIFIED_INVALID"


def test_missing_candles_reported_against_expected_labels():
    result = validate_frame(frame(["2025-02-03T09:15:00+05:30", "2025-02-03T09:25:00+05:30"]), MANIFEST, timestamp_convention="start")
    assert result.coverage.iloc[0].missing_labels == 73
    assert "09:20" in result.coverage.iloc[0].missing_timestamps


def test_entire_missing_instrument_session_is_reported():
    sample = pd.DataFrame({"stock": ["A", "B"], "timestamp": ["2025-02-03T09:15:00+05:30", "2025-02-04T09:15:00+05:30"]})
    result = validate_frame(sample, MANIFEST, timestamp_convention="start")
    missing = result.coverage[(result.coverage.instrument == "B") & (result.coverage.date == "2025-02-03")]
    assert len(missing) == 1
    assert missing.iloc[0].missing_labels == 75
def test_duplicate_timestamp_labels_are_invalid():
    result = validate_frame(frame(["2025-02-03T09:15:00+05:30"] * 2), MANIFEST, timestamp_convention="start")
    assert result.rows.classification.tolist() == ["VERIFIED_INVALID", "VERIFIED_INVALID"]
    assert set(result.rows.reason) == {"duplicate_timestamp_label"}


def test_out_of_session_and_unaligned_labels_are_invalid():
    result = validate_frame(frame(["2025-02-03T08:55:00+05:30", "2025-02-03T09:16:00+05:30"]), MANIFEST, timestamp_convention="start")
    assert result.rows.classification.tolist() == ["VERIFIED_INVALID", "VERIFIED_INVALID"]


def test_missing_manifest_and_unknown_manifest_status_fail_closed():
    no_manifest_day = {"schema_version": 1, "exchange": "NSE", "segment": "CM", "sources": ["x"], "sessions": []}
    result = validate_frame(frame(["2025-02-03T09:15:00+05:30"]), no_manifest_day, timestamp_convention="start")
    assert result.rows.classification.iloc[0] == "UNRESOLVED"
    altered = json.loads(json.dumps(MANIFEST))
    next(x for x in altered["sessions"] if x["date"] == "2025-02-03")["verification_status"] = "UNRESOLVED"
    result = validate_frame(frame(["2025-02-03T09:15:00+05:30"]), altered, timestamp_convention="start")
    assert result.rows.classification.iloc[0] == "UNRESOLVED"


def test_unknown_label_convention_fails_closed():
    result = validate_frame(frame(["2025-02-03T09:15:00+05:30"]), MANIFEST, timestamp_convention=None)
    assert result.rows.classification.iloc[0] == "UNRESOLVED"
    assert result.coverage.empty


def test_manifest_source_list_required(tmp_path):
    invalid = {"schema_version": 1, "exchange": "NSE", "segment": "CM", "sessions": []}
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="no primary sources"):
        load_manifest(path)
