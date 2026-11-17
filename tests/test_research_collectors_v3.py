"""Only mocked collector ingestion; no research/strategy execution."""
import importlib

import pandas as pd
import pytest

from app.candle_integrity import CandleDataError


@pytest.mark.parametrize("module_name,function,anchors", [
    ("cost_backtest", "collect_historical_candles", "ANCHOR_DATES"),
    ("historical_robustness_test", "collect_early_historical_candles", "HISTORICAL_ANCHORS"),
    ("multi_stock_research", "collect_historical_candles", "ANCHOR_DATES"),
    ("stop_loss_backtest", "collect_historical_candles", "ANCHOR_DATES")])
@pytest.mark.parametrize("field", [None, 1, 2, 3, 4, 5])
@pytest.mark.parametrize("reverse", [False, True])
def test_collectors_are_conflict_safe_in_both_response_orders(monkeypatch, module_name, function, anchors, field, reverse):
    module = importlib.import_module(module_name)
    first = ["2026-09-30T09:15:00+05:30", 100., 102., 98., 100., 1000., 0.]
    second = first.copy()
    if field is not None:
        second[field] += .5
    responses = [[first], [second]]
    if reverse:
        responses.reverse()
    calls = []
    def fetch(*a, **k):
        calls.append(1)
        return {"data": {"candles": responses[len(calls)-1]}}
    monkeypatch.setattr(module, anchors, ["A", "B"])
    monkeypatch.setattr(module, "get_historical_candles", fetch)
    if field is None:
        result = getattr(module, function)("NSE_EQ|TEST")
        assert len(result) == 1
        assert pd.Timestamp(result[0][0]) == pd.Timestamp(first[0])
        assert result[0][1:] == first[1:]
    else:
        with pytest.raises(CandleDataError):
            getattr(module, function)("NSE_EQ|TEST")
    assert len(calls) == 2
