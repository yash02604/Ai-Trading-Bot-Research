"""Deterministic row isolation regressions, including the unchanged audit cases."""
import copy
from itertools import permutations

import pytest

from test_offline_market_data_v1 import candle, MANIFEST, validate_candle_records


@pytest.mark.parametrize("bad_first", [True, False])
def test_each_row_keeps_its_own_validation_reasons(bad_first):
    bad = candle("2025-02-03T09:15:00+05:30", low=103.0)
    good = candle("2025-02-03T09:20:00+05:30")
    rows = [bad, good] if bad_first else [good, bad]
    actual, _ = validate_candle_records(rows, MANIFEST)
    expected = ["VERIFIED_INVALID", "VERIFIED_VALID"] if bad_first else ["VERIFIED_VALID", "VERIFIED_INVALID"]
    assert [r["validation_classification"] for r in actual] == expected


def mixed_rows():
    return [
        candle("2025-02-03T09:15:00+05:30", low=103.0),
        candle("2025-02-03T09:20:00+05:30"),
        candle("2025-02-03T09:25:00+05:30", provider_volume=-1),
        candle("2025-02-03T09:30:00+05:30"),
    ]


@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_mixed_rows_have_order_independent_classifications_and_exact_reasons(order):
    rows = mixed_rows()
    actual, _ = validate_candle_records([rows[i] for i in order], MANIFEST)
    expected = {
        rows[0]["timestamp_original"]: ("VERIFIED_INVALID", ["invalid_ohlc_relationship"]),
        rows[1]["timestamp_original"]: ("VERIFIED_VALID", ["all_offline_checks_passed"]),
        rows[2]["timestamp_original"]: ("VERIFIED_INVALID", ["negative_volume"]),
        rows[3]["timestamp_original"]: ("VERIFIED_VALID", ["all_offline_checks_passed"]),
    }
    assert {r["timestamp_original"]: (r["validation_classification"], r["validation_reasons"]) for r in actual} == expected
    # Each row agrees with its standalone classification; duplicates are tested separately.
    for row in actual:
        single, _ = validate_candle_records([next(r for r in rows if r["timestamp_original"] == row["timestamp_original"])], MANIFEST)
        assert row == single[0]


@pytest.mark.parametrize("indexes", [(0, 1, 2), (1, 0, 3), (1, 3), (0, 2)])
def test_alternating_all_valid_and_all_invalid(indexes):
    source = mixed_rows()
    actual, _ = validate_candle_records([source[i] for i in indexes], MANIFEST)
    assert [r["validation_classification"] for r in actual] == [
        "VERIFIED_INVALID" if i in (0, 2) else "VERIFIED_VALID" for i in indexes
    ]


def test_repeat_calls_preserve_inputs_and_do_not_share_output_reasons():
    source = mixed_rows()
    before = copy.deepcopy(source)
    first = validate_candle_records(source, MANIFEST)
    second = validate_candle_records(source, MANIFEST)
    assert first == second
    assert source == before
    first[0][0]["validation_reasons"].append("test_only_mutation")
    assert "test_only_mutation" not in first[0][2]["validation_reasons"]
    assert validate_candle_records(source, MANIFEST) == second
    valid, _ = validate_candle_records([source[1]], MANIFEST)
    assert valid[0]["validation_classification"] == "VERIFIED_VALID"


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_duplicate_errors_affect_only_duplicate_rows_without_mutating_inputs(order):
    rows = [candle(), candle(), candle("2025-02-03T09:20:00+05:30")]
    before = copy.deepcopy(rows)
    actual, _ = validate_candle_records([rows[i] for i in order], MANIFEST)
    for idx, result in zip(order, actual):
        assert result["validation_reasons"] == (["duplicate_instrument_timestamp"] if idx < 2 else ["all_offline_checks_passed"])
        assert result["validation_classification"] == ("VERIFIED_INVALID" if idx < 2 else "VERIFIED_VALID")
    assert rows == before


@pytest.mark.parametrize("changes,reason", [
    ({"timestamp_original": "2025-02-03 09:15:00"}, "timestamp_timezone_unresolved"),
    ({"interval_minutes": 0}, "schema_or_timestamp_invalid:"),
    ({"timestamp_original": "2025-02-03T09:16:00+05:30"}, "timestamp_not_interval_aligned"),
])
def test_schema_timezone_and_interval_reasons_are_local(changes, reason):
    # Alignment must use a consistently normalized timestamp to reach that rule.
    bad = candle(changes["timestamp_original"]) if reason == "timestamp_not_interval_aligned" else candle(**changes)
    good = candle("2025-02-03T09:20:00+05:30")
    for rows in ([bad, good], [good, bad]):
        actual, _ = validate_candle_records(rows, MANIFEST)
        by_stamp = {r["timestamp_original"]: r for r in actual}
        assert any(r.startswith(reason) for r in by_stamp[bad["timestamp_original"]]["validation_reasons"])
        assert by_stamp[good["timestamp_original"]]["validation_reasons"] == ["all_offline_checks_passed"]
