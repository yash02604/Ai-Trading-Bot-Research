"""Transparent NSE cash-equity intraday cost estimates for historical tests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostAssumptions:
    brokerage_rate: float = 0.001       # ₹20/order or 0.1% of order value, lower
    brokerage_cap: float = 20.0
    stt_sell_rate: float = 0.00025
    stamp_buy_rate: float = 0.00003
    sebi_rate: float = 0.0000001        # ₹10/crore on buy and sell turnover
    gst_rate: float = 0.18
    slippage_bps_per_side: float = 5.0   # explicit conservative modeling assumption
    ipft_per_lakh: float = 0.10


def nse_transaction_rate(trade_date):
    """Conservative fixed rate: highest 2024–26 Upstox schedule shown, per leg."""
    return 0.0000322  # 0.00322%; applies uniformly across this backtest.


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
    ipft = turnover / 100_000.0 * assumptions.ipft_per_lakh
    sebi = turnover * assumptions.sebi_rate
    stt = sell_turnover * assumptions.stt_sell_rate
    stamp = buy_turnover * assumptions.stamp_buy_rate
    gst = assumptions.gst_rate * (brokerage + exchange + ipft)
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
