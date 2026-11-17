"""Spreadsheet-safe legacy export without modifying trading values."""
import copy
import csv
import json

import pytest

from app import trade_logger


TEXT_FIELDS = tuple(trade_logger.FIELDNAMES)
FORMULAS = ("=1+1", "+SUM(A1:A2)", "-1+2", "@SUM(A1:A2)")


def read_export():
    with open(trade_logger.LOG_FILE, newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("field", TEXT_FIELDS)
@pytest.mark.parametrize("value", FORMULAS)
def test_untrusted_text_is_neutralized_only_in_csv(field, value):
    trade = {"quantity": 2, "pnl": -1.25, "entry_price": 100.5, field: value}
    before = copy.deepcopy(trade)
    trade_logger.log_trade(trade)
    actual = read_export()[0]
    assert actual[field] == "'" + value
    assert trade == before
    for numeric_field, expected in (("quantity", "2"), ("pnl", "-1.25"), ("entry_price", "100.5")):
        if field != numeric_field:
            assert actual[numeric_field] == expected


@pytest.mark.parametrize("prefix", [" ", "\t", "\r", "\n", "\v\f", "\x00", "\x1f", "\u00a0", "\ufeff", "\u200b", " \t\r\n\ufeff"])
@pytest.mark.parametrize("formula", FORMULAS)
def test_formula_after_whitespace_or_control_prefix_is_literal(prefix, formula):
    value = prefix + formula
    trade_logger.log_trade({"ai_reason": value})
    assert read_export()[0]["ai_reason"] == "'" + value


@pytest.mark.parametrize("value", ["normal text", "RELIANCE", "NSE", "trade completed", "", "'quoted'", 'He said "hold", then wait', "line one\nline two", "नमस्ते ₹", " space", "text =1+1", "'=1+1"])
def test_benign_text_round_trips_unchanged(value):
    trade_logger.log_trade({"ai_reason": value})
    assert read_export()[0]["ai_reason"] == value


def test_append_header_numeric_types_and_authoritative_decision_json_are_preserved():
    trade = {"ai_reason": "=1+1", "quantity": 2, "pnl": -3.5, "macd": -0.1, "ai_confidence": 0.8}
    before = copy.deepcopy(trade)
    trade_logger.log_trade(trade)
    trade_logger.log_trade(trade)
    actual = read_export()
    assert len(actual) == 2
    assert actual[0] == actual[1]
    for field in ("quantity", "pnl", "macd", "ai_confidence"):
        assert float(actual[0][field]) == trade[field]
        assert not actual[0][field].startswith("'")
    assert trade == before
    trade_logger.log_decision({}, {"ai": {"reason": trade["ai_reason"]}})
    with open(trade_logger.DECISION_LOG_FILE, encoding="utf-8") as stream:
        assert json.loads(stream.readline())["ai_result"]["reason"] == "=1+1"


@pytest.mark.parametrize("value", [None, 0, -1, -1.25, 0.8, True])
def test_csv_sanitizer_preserves_nontext_values_and_types(value):
    actual = trade_logger.sanitize_csv_cell_for_spreadsheet(value)
    assert actual == value
    assert type(actual) is type(value)


def test_formula_text_with_csv_delimiters_remains_one_literal_cell():
    value = '=1+1,\n"note"'
    trade_logger.log_trade({"ai_reason": value, "symbol": "RELIANCE"})
    actual = read_export()
    assert len(actual) == 1
    assert actual[0]["ai_reason"] == "'" + value
    assert actual[0]["symbol"] == "RELIANCE"
