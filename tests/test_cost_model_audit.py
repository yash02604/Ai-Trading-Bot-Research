from types import SimpleNamespace

import pytest

from scripts.run_cost_model_audit import reprice_trade


def _trade(timestamp="2025-02-03T09:25:00+05:30"):
    return SimpleNamespace(
        entry_price=1000.0,
        exit_price=900.0,
        quantity=100,
        entry_timestamp=timestamp,
    )


def test_repricing_scenario_uses_date_rates_and_itemizes_each_charge():
    trade = _trade()
    charges, total = reprice_trade(trade, 0.0000325, False, 5)
    assert charges["brokerage"] == pytest.approx(40.0)
    assert charges["exchange"] == pytest.approx(5.643)
    assert charges["ipft"] == pytest.approx(0.19)
    assert charges["sebi"] == pytest.approx(0.19)
    assert charges["stt"] == pytest.approx(22.5)
    assert charges["stamp_duty"] == pytest.approx(3.0)
    assert charges["gst"] == pytest.approx(0.18 * (40 + 5.643 + 0.19))
    assert charges["slippage"] == pytest.approx(95.0)
    assert total == pytest.approx(sum(charges.values()))


def test_repricing_shows_pre_2024_fee_and_gst_uncertainty_as_explicit_inputs():
    trade = _trade("2023-12-07T09:30:00+05:30")
    low, low_total = reprice_trade(trade, 0.0000322, False, 5)
    high, high_total = reprice_trade(trade, 0.0000345, True, 5)
    assert high["exchange"] > low["exchange"]
    assert high["gst"] - low["gst"] == pytest.approx(
        0.18 * ((high["exchange"] - low["exchange"]) + high["sebi"])
    )
    assert high_total > low_total
