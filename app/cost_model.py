"""Transparent NSE cash-equity intraday cost estimates for historical tests."""

from dataclasses import dataclass
from datetime import date, datetime
import math


@dataclass(frozen=True)
class CostAssumptions:
    brokerage_rate: float = 0.001       # ₹20/order or 0.1% of order value, lower
    brokerage_cap: float = 20.0
    stt_sell_rate: float = 0.00025
    stamp_buy_rate: float = 0.00003
    sebi_rate: float = 0.000001         # ₹10/crore on buy and sell turnover
    gst_rate: float = 0.18
    slippage_bps_per_side: float = 5.0   # explicit conservative modeling assumption
    # None selects the published date-dependent NSE IPFT schedule.
    ipft_per_lakh: float | None = None
    gst_on_sebi: bool = False


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value.date()


def nse_transaction_rate(trade_date):
    """Return Upstox NSE equity intraday transaction rate, per side, by date.

    Pre-April 2024 Upstox client pricing is not established by the available
    dated schedule, so this function fails closed for that interval.
    """
    day = _as_date(trade_date)
    if day >= date(2026, 3, 1):
        return 0.000030699  # ₹306.99/crore; IPFT is separately ₹0.01/crore.
    if day >= date(2024, 10, 1):
        return 0.0000297
    if day >= date(2024, 4, 1):
        return 0.0000322
    raise ValueError("Upstox NSE equity intraday transaction rate is unresolved before 2024-04-01")


def nse_ipft_per_lakh(trade_date):
    """Return NSE cash-equity IPFT rupees per lakh of traded value by date."""
    day = _as_date(trade_date)
    if day >= date(2026, 3, 1):
        return 0.0001  # ₹0.01 per crore
    if day >= date(2023, 4, 1):
        return 0.10  # ₹10 per crore
    raise ValueError("NSE cash-equity IPFT schedule is unresolved before 2023-04-01")


def estimate_round_trip_costs(entry_price, exit_price, quantity, trade_date, assumptions=None):
    """Return itemized INR costs; prices are reference fills before slippage."""
    assumptions = assumptions or CostAssumptions()
    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        raise ValueError("Prices and quantity must be positive")
    buy_turnover = float(entry_price) * int(quantity)
    sell_turnover = float(exit_price) * int(quantity)
    turnover = buy_turnover + sell_turnover
    brokerage = min(assumptions.brokerage_cap, buy_turnover * assumptions.brokerage_rate)
    brokerage += min(assumptions.brokerage_cap, sell_turnover * assumptions.brokerage_rate)
    exchange = turnover * nse_transaction_rate(trade_date)
    ipft_rate = assumptions.ipft_per_lakh
    if ipft_rate is None:
        ipft_rate = nse_ipft_per_lakh(trade_date)
    ipft = turnover / 100_000.0 * ipft_rate
    sebi = turnover * assumptions.sebi_rate
    stt = sell_turnover * assumptions.stt_sell_rate
    stamp = buy_turnover * assumptions.stamp_buy_rate
    gst_base = brokerage + exchange + ipft
    if assumptions.gst_on_sebi:
        gst_base += sebi
    gst = assumptions.gst_rate * gst_base
    slippage = turnover * assumptions.slippage_bps_per_side / 10_000.0
    components = {
        "brokerage": brokerage,
        "stt": stt,
        "exchange": exchange,
        "ipft": ipft,
        "sebi": sebi,
        "stamp_duty": stamp,
        "gst": gst,
        "slippage": slippage,
    }
    components["total_cost"] = sum(components.values())
    return components


def estimate_buy_side_costs(entry_price, quantity, trade_date, assumptions=None):
    """Estimate entry-side charges and slippage using the round-trip model.

    This is an affordability estimate, not a guarantee of the cash required to
    execute. It fails closed when any required input or dated rate is invalid.
    """
    assumptions = assumptions or CostAssumptions()
    try:
        price = float(entry_price)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("entry price is invalid") from exc
    if not math.isfinite(price) or price <= 0:
        raise ValueError("entry price is invalid")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity is invalid")
    if not isinstance(trade_date, (date, datetime)):
        raise ValueError("trade date is invalid")

    numeric_assumptions = (
        assumptions.brokerage_rate, assumptions.brokerage_cap,
        assumptions.stamp_buy_rate, assumptions.sebi_rate,
        assumptions.gst_rate, assumptions.slippage_bps_per_side,
    )
    if assumptions.ipft_per_lakh is not None:
        numeric_assumptions += (assumptions.ipft_per_lakh,)
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in numeric_assumptions):
        raise ValueError("cost assumptions are invalid")

    turnover = price * quantity
    brokerage = min(assumptions.brokerage_cap, turnover * assumptions.brokerage_rate)
    exchange = turnover * nse_transaction_rate(trade_date)
    ipft_rate = assumptions.ipft_per_lakh
    if ipft_rate is None:
        ipft_rate = nse_ipft_per_lakh(trade_date)
    ipft = turnover / 100_000.0 * ipft_rate
    sebi = turnover * assumptions.sebi_rate
    stamp = turnover * assumptions.stamp_buy_rate
    gst_base = brokerage + exchange + ipft
    if assumptions.gst_on_sebi:
        gst_base += sebi
    gst = assumptions.gst_rate * gst_base
    slippage = turnover * assumptions.slippage_bps_per_side / 10_000.0
    components = {
        "brokerage": brokerage,
        "exchange": exchange,
        "ipft": ipft,
        "sebi": sebi,
        "stamp_duty": stamp,
        "gst": gst,
        "slippage": slippage,
    }
    if any(not math.isfinite(value) or value < 0 for value in components.values()):
        raise ValueError("buy-side cost estimate is invalid")
    components["total_cost"] = sum(components.values())
    if not math.isfinite(components["total_cost"]):
        raise ValueError("buy-side cost estimate is invalid")
    return components
