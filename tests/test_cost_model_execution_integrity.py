from datetime import date

import pytest

from app.cost_model import (
    CostAssumptions,
    estimate_round_trip_costs,
    nse_ipft_per_lakh,
    nse_transaction_rate,
)


def test_itemized_round_trip_cost_formulas_at_small_turnover():
    costs = estimate_round_trip_costs(100, 101, 10, date(2025, 2, 3), CostAssumptions())
    expected = {
        "brokerage": 2.01,
        "stt": 0.2525,
        "exchange": 0.059697,
        "ipft": 0.00201,
        "sebi": 0.00201,
        "stamp_duty": 0.03,
        "gst": 0.18 * (2.01 + 0.059697 + 0.00201),
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


@pytest.mark.parametrize(
    "trade_date,expected",
    [
        (date(2024, 4, 1), 0.0000322),
        (date(2024, 9, 30), 0.0000322),
        (date(2024, 10, 1), 0.0000297),
        (date(2026, 2, 28), 0.0000297),
        (date(2026, 3, 1), 0.000030699),
    ],
)
def test_nse_cash_transaction_rate_uses_published_date_schedule(trade_date, expected):
    assert nse_transaction_rate(trade_date) == pytest.approx(expected)


def test_unverified_pre_april_2024_transaction_rate_fails_closed():
    with pytest.raises(ValueError, match="unresolved before 2024-04-01"):
        nse_transaction_rate(date(2024, 3, 31))
    with pytest.raises(ValueError, match="unresolved before 2024-04-01"):
        estimate_round_trip_costs(100, 101, 10, date(2023, 12, 1))


def test_nse_ipft_date_schedule_and_explicit_override():
    assert nse_ipft_per_lakh(date(2025, 1, 1)) == pytest.approx(0.10)
    assert nse_ipft_per_lakh(date(2026, 3, 1)) == pytest.approx(0.0001)
    with pytest.raises(ValueError, match="unresolved before 2023-04-01"):
        nse_ipft_per_lakh(date(2023, 3, 31))
    costs = estimate_round_trip_costs(
        100, 101, 10, date(2025, 2, 3), CostAssumptions(ipft_per_lakh=0.25)
    )
    assert costs["ipft"] == pytest.approx(0.005025)


def test_long_round_trip_charges_buy_and_sell_side_separately():
    costs = estimate_round_trip_costs(1000, 900, 100, date(2025, 2, 3))
    assert costs["stt"] == pytest.approx(22.5)  # sell side only
    assert costs["stamp_duty"] == pytest.approx(3.0)  # buy side only
    assert costs["sebi"] == pytest.approx(0.19)  # both sides, ₹10/crore
    assert costs["brokerage"] == pytest.approx(40.0)  # ₹20 cap on each order
    assert costs["slippage"] == pytest.approx(95.0)  # 5 bps on each leg


def test_gst_schedule_base_can_be_repriced_with_or_without_sebi_fee():
    ex_sebi = estimate_round_trip_costs(1000, 900, 100, date(2025, 2, 3))
    incl_sebi = estimate_round_trip_costs(
        1000, 900, 100, date(2025, 2, 3), CostAssumptions(gst_on_sebi=True)
    )
    assert ex_sebi["gst"] == pytest.approx(0.18 * (40 + 5.643 + 0.19))
    assert incl_sebi["gst"] == pytest.approx(0.18 * (40 + 5.643 + 0.19 + 0.19))
    assert incl_sebi["gst"] - ex_sebi["gst"] == pytest.approx(0.18 * 0.19)


@pytest.mark.parametrize("entry,exit_price,quantity", [(0, 10, 1), (10, -1, 1), (10, 11, 0), (10, 11, -1)])
def test_cost_model_rejects_nonpositive_fills_and_quantities(entry, exit_price, quantity):
    with pytest.raises(ValueError):
        estimate_round_trip_costs(entry, exit_price, quantity, date(2025, 2, 3))
