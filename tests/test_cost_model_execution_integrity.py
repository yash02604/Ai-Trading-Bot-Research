from datetime import date

import pytest

from app.cost_model import CostAssumptions, estimate_round_trip_costs


def test_itemized_round_trip_cost_formulas_at_small_turnover():
    costs = estimate_round_trip_costs(100, 101, 10, date(2025, 2, 3), CostAssumptions())
    expected = {
        "brokerage": 2.01,
        "stt": 0.2525,
        "exchange": 0.064722,
        "ipft": 0.00201,
        "sebi": 0.000201,
        "stamp_duty": 0.03,
        "gst": 0.18 * (2.01 + 0.064722 + 0.00201),
        "slippage": 1.005,
    }
    for name, value in expected.items():
        assert costs[name] == pytest.approx(value)
    assert costs["total_cost"] == pytest.approx(sum(expected.values()))


def test_brokerage_caps_each_leg_and_slippage_is_per_side():
    costs = estimate_round_trip_costs(1000, 1000, 100, date(2025, 2, 3), CostAssumptions())
    assert costs["brokerage"] == pytest.approx(40.0)
    assert costs["slippage"] == pytest.approx(100.0)
    assert costs["stt"] == pytest.approx(25.0)
    assert costs["stamp_duty"] == pytest.approx(3.0)


@pytest.mark.parametrize("entry,exit_price,quantity", [(0, 10, 1), (10, -1, 1), (10, 11, 0), (10, 11, -1)])
def test_cost_model_rejects_nonpositive_fills_and_quantities(entry, exit_price, quantity):
    with pytest.raises(ValueError):
        estimate_round_trip_costs(entry, exit_price, quantity, date(2025, 2, 3))
