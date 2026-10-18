import pandas as pd
import pytest
import hashlib

from app.candle_integrity import CandleDataError, merge_candle_sources, require_valid_candles, validate_candles
from app.market_analysis import calculate_indicators
from backtester import Backtester
from app.historical_validation import load_historical_data
from strategy_backtest import STOCKS
from scripts.analyze_market_context import load_context
from extended_historical_robustness_test import upsert_research_csv


def _frame(count=1, *, start="2025-02-03T09:15:00+05:30"):
    times = pd.date_range(start, periods=count, freq="5min")
    return pd.DataFrame({
        "instrument_key": "NSE_EQ|TEST",
        "timestamp": times,
        "open": [100.0 + i for i in range(count)],
        "high": [101.0 + i for i in range(count)],
        "low": [99.0 + i for i in range(count)],
        "close": [100.5 + i for i in range(count)],
        "volume": [10.0] * count,
    })


def test_structural_ohlc_nonfinite_and_negative_volume_fail_closed():
    for field, value in (("high", 98), ("close", float("inf")), ("volume", -1)):
        frame = _frame()
        frame.loc[0, field] = value
        result = validate_candles(frame)
        assert not result.valid
        assert result.errors


def test_valid_zero_volume_is_retained_as_warning():
    frame = _frame()
    frame.loc[0, "volume"] = 0
    normalized, warnings = require_valid_candles(frame)
    assert len(normalized) == 1
    assert normalized.loc[0, "volume"] == 0
    assert {warning["reason"] for warning in warnings} == {"zero_volume_retained"}


def test_exact_duplicate_coverage_is_reported_and_deduplicated_idempotently():
    first, second = _frame(), _frame()
    once, audit = merge_candle_sources([("a.csv", first), ("b.csv", second)], key_columns=["instrument_key"])
    twice, audit_again = merge_candle_sources([("a.csv", once), ("b.csv", second)], key_columns=["instrument_key"])
    assert len(once) == len(twice) == 1
    assert audit["duplicate_coverage"]["count"] == 1
    assert audit["duplicate_coverage"]["source_files"] == ["a.csv", "b.csv"]
    assert audit["duplicate_coverage"]["sample_keys"] == [{"instrument_key": "NSE_EQ|TEST", "timestamp": pd.Timestamp("2025-02-03T09:15:00+05:30")}]
    pd.testing.assert_frame_equal(once, twice)
    assert audit_again["duplicate_coverage"]["count"] == 1


def test_conflicting_overlap_reports_instrument_timestamp_sources_and_fields():
    first, second = _frame(), _frame()
    second.loc[0, "close"] = 100.75
    with pytest.raises(CandleDataError, match="Conflicting overlapping") as exc:
        merge_candle_sources([("first.csv", first), ("second.csv", second)], key_columns=["instrument_key"])
    item = exc.value.details[0]
    assert item["instrument_key"] == "NSE_EQ|TEST"
    assert item["timestamp"] == pd.Timestamp("2025-02-03T09:15:00+05:30")
    assert item["sources"] == ["first.csv", "second.csv"]
    assert item["differing_fields"] == ["close"]


def test_naive_and_unaligned_timestamps_are_rejected_but_utc_normalizes():
    naive = _frame()
    naive["timestamp"] = pd.to_datetime(naive.timestamp).dt.tz_localize(None)
    assert "timezone_naive_timestamp" in {e["reason"] for e in validate_candles(naive).errors}
    unaligned = _frame()
    unaligned.loc[0, "timestamp"] += pd.Timedelta(minutes=1)
    assert "timestamp_not_five_minute_aligned" in {e["reason"] for e in validate_candles(unaligned).errors}
    utc = _frame()
    utc["timestamp"] = utc.timestamp.dt.tz_convert("UTC")
    normalized, _ = require_valid_candles(utc)
    assert str(normalized.timestamp.dt.tz) == "Asia/Kolkata"
    assert normalized.timestamp.iloc[0] == pd.Timestamp("2025-02-03T09:15:00+05:30")


def test_valid_large_price_move_and_volume_are_not_rejected():
    frame = _frame(2)
    frame.loc[1, ["open", "high", "low", "close", "volume"]] = [200, 250, 190, 240, 10**9]
    normalized, _ = require_valid_candles(frame)
    assert normalized.close.iloc[1] == 240
    assert normalized.volume.iloc[1] == 10**9


def test_missing_fields_fail_with_actionable_report():
    result = validate_candles(_frame().drop(columns="volume"))
    assert not result.valid
    assert result.errors[0]["reason"] == "missing_required_columns"
    assert result.errors[0]["fields"] == ["volume"]


def test_indicator_and_legacy_backtest_entry_points_reject_invalid_ohlc():
    frame = _frame(40).drop(columns="instrument_key")
    frame.loc[0, "high"] = 98
    with pytest.raises(CandleDataError):
        calculate_indicators(frame)
    candles = frame[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    candles["open_interest"] = 0
    with pytest.raises(CandleDataError):
        Backtester(candles.values.tolist())


def test_historical_loader_audits_invalid_rows_and_retains_zero_volume(tmp_path):
    frame = _frame(2).drop(columns="instrument_key")
    frame["stock"] = "RELIANCE"
    frame["instrument_key"] = STOCKS["RELIANCE"]
    frame.loc[0, "volume"] = 0
    frame.loc[1, "high"] = 98
    path = tmp_path / "candles.csv"
    frame.to_csv(path, index=False)
    valid, audit = load_historical_data(path)
    assert len(valid) == 1
    assert valid.volume.iloc[0] == 0
    assert audit["invalid_rows_excluded"] == 1
    assert "invalid_ohlc_relationship" in audit["invalid_row_details"][0]["reason"]


def test_historical_loader_rejects_duplicate_keys_instead_of_selecting_a_row(tmp_path):
    frame = _frame(1).drop(columns="instrument_key")
    frame["stock"] = "RELIANCE"
    frame["instrument_key"] = STOCKS["RELIANCE"]
    path = tmp_path / "duplicate.csv"
    pd.concat([frame, frame], ignore_index=True).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate instrument/timestamp"):
        load_historical_data(path)


def test_context_loader_reports_identical_duplicate_coverage_and_never_rewrites(tmp_path):
    frame = _frame().drop(columns="instrument_key")
    path = tmp_path / "index.csv"
    pd.concat([frame, frame], ignore_index=True).to_csv(path, index=False)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = load_context(path)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(loaded) == 1
    assert loaded.attrs["duplicate_coverage"]["count"] == 1
    assert before == after


def test_context_loader_rejects_conflicting_duplicate_ohlcv(tmp_path):
    frame = _frame().drop(columns="instrument_key")
    conflict = frame.copy()
    conflict.loc[0, "close"] = 100.75
    path = tmp_path / "index_conflict.csv"
    pd.concat([frame, conflict], ignore_index=True).to_csv(path, index=False)
    with pytest.raises(CandleDataError, match="Conflicting overlapping"):
        load_context(path)


def test_research_candle_upsert_is_idempotent_and_conflicts_do_not_rewrite(tmp_path):
    frame = _frame().assign(stock="RELIANCE", oi=0)
    frame = frame[["stock", "instrument_key", "timestamp", "open", "high", "low", "close", "volume", "oi"]]
    path = tmp_path / "research.csv"
    key = ["stock", "instrument_key", "timestamp"]
    upsert_research_csv(frame, path, key, ["stock", "timestamp"])
    upsert_research_csv(frame, path, key, ["stock", "timestamp"])
    assert len(pd.read_csv(path)) == 1
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    conflict = frame.copy()
    conflict.loc[0, "close"] = 100.75
    with pytest.raises(CandleDataError, match="Conflicting overlapping"):
        upsert_research_csv(conflict, path, key, ["stock", "timestamp"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

