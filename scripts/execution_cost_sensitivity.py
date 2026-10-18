"""Reprice saved portfolio trades under transparent adverse cost scenarios."""

import argparse
from pathlib import Path

import pandas as pd

from app.cost_model import CostAssumptions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRADES = PROJECT_ROOT / "backtests" / "validation" / "trades.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "backtests" / "execution_cost_review"
BASE_SLIPPAGE_BPS_PER_SIDE = CostAssumptions().slippage_bps_per_side
SCENARIOS = (
    {"name": "existing_model", "slippage_bps_per_side": 5.0, "assumed_full_spread_bps": 0.0,
     "description": "Existing historical fees and 5 bps/side modeled slippage."},
    {"name": "higher_slippage", "slippage_bps_per_side": 10.0, "assumed_full_spread_bps": 0.0,
     "description": "10 bps/side modeled slippage; no spread allowance."},
    {"name": "assumed_10bps_spread", "slippage_bps_per_side": 5.0, "assumed_full_spread_bps": 10.0,
     "description": "Existing slippage plus assumed 10 bps full spread, crossed at half-spread on each side."},
    {"name": "assumed_20bps_spread", "slippage_bps_per_side": 5.0, "assumed_full_spread_bps": 20.0,
     "description": "Existing slippage plus assumed 20 bps full spread, crossed at half-spread on each side."},
    {"name": "combined_adverse", "slippage_bps_per_side": 10.0, "assumed_full_spread_bps": 10.0,
     "description": "10 bps/side slippage plus assumed 10 bps full spread."},
)


def analyze(trades_path=DEFAULT_TRADES):
    trades = pd.read_csv(trades_path)
    required = {"book", "symbol", "signal_timestamp", "entry_timestamp", "entry_price",
                "exit_price", "quantity", "gross_pnl", "net_pnl", "total_cost", "slippage"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"Trade file is missing required columns: {missing}")
    trades = trades.loc[trades.book == "portfolio"].copy()
    if trades.empty:
        raise ValueError("No portfolio trades found; refusing to report standalone rows as a portfolio")
    numeric = ["entry_price", "exit_price", "quantity", "gross_pnl", "net_pnl", "total_cost", "slippage"]
    for column in numeric:
        trades[column] = pd.to_numeric(trades[column], errors="raise")
    if (trades[["entry_price", "exit_price", "quantity"]] <= 0).any().any():
        raise ValueError("Trade prices and quantities must be positive")

    turnover = (trades.entry_price + trades.exit_price) * trades.quantity
    baseline_slippage = float(trades.slippage.sum())
    calculated_baseline_slippage = float(turnover.sum() * BASE_SLIPPAGE_BPS_PER_SIDE / 10_000)
    if abs(baseline_slippage - calculated_baseline_slippage) > 0.01:
        raise ValueError("Saved slippage does not reconcile to the current 5 bps/side model")
    statutory_and_brokerage = float((trades.total_cost - trades.slippage).sum())
    charge_columns = ("brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst")
    missing_charges = sorted(set(charge_columns) - set(trades.columns))
    if missing_charges:
        raise ValueError(f"Trade file is missing itemized cost columns: {missing_charges}")
    for column in charge_columns:
        trades[column] = pd.to_numeric(trades[column], errors="raise")
    charge_totals = {f"{column}_inr": float(trades[column].sum()) for column in charge_columns}
    gross_pnl = float(trades.gross_pnl.sum())
    existing_net = float(trades.net_pnl.sum())
    if abs(existing_net - (gross_pnl - float(trades.total_cost.sum()))) > 0.01:
        raise ValueError("Saved gross, costs, and net P&L do not reconcile")

    scenario_rows, detail_rows = [], []
    for scenario in SCENARIOS:
        slip_rate = scenario["slippage_bps_per_side"]
        spread_rate_each_side = scenario["assumed_full_spread_bps"] / 2
        modeled_slippage = float(turnover.sum() * slip_rate / 10_000)
        spread_allowance = float(turnover.sum() * spread_rate_each_side / 10_000)
        total_costs = statutory_and_brokerage + modeled_slippage + spread_allowance
        net_values = []
        for trade, notional in zip(trades.itertuples(index=False), turnover):
            trade_slippage = float(notional * slip_rate / 10_000)
            trade_spread = float(notional * spread_rate_each_side / 10_000)
            trade_fixed_costs = float(trade.total_cost - trade.slippage)
            trade_costs = trade_fixed_costs + trade_slippage + trade_spread
            net = float(trade.gross_pnl - trade_costs)
            net_values.append(net)
            detail_rows.append({
                "scenario": scenario["name"], "symbol": trade.symbol,
                "signal_timestamp": trade.signal_timestamp,
                "entry_timestamp": trade.entry_timestamp,
                "entry_price_reference": float(trade.entry_price),
                "exit_price_reference": float(trade.exit_price),
                "quantity": int(trade.quantity), "gross_pnl": float(trade.gross_pnl),
                "brokerage_and_statutory_costs": trade_fixed_costs,
                **{column: float(getattr(trade, column)) for column in charge_columns},
                "modeled_slippage": trade_slippage, "assumed_spread_allowance": trade_spread,
                "scenario_total_costs": trade_costs, "scenario_net_pnl": net,
            })
        positive = sum(value for value in net_values if value > 0)
        negative = -sum(value for value in net_values if value < 0)
        scenario_rows.append({
            "scenario": scenario["name"],
            "description": scenario["description"],
            "trades": len(trades),
            "slippage_bps_per_side": slip_rate,
            "assumed_full_spread_bps": scenario["assumed_full_spread_bps"],
            "brokerage_and_statutory_costs_inr": statutory_and_brokerage,
            **charge_totals,
            "modeled_slippage_inr": modeled_slippage,
            "assumed_spread_allowance_inr": spread_allowance,
            "total_costs_inr": total_costs,
            "gross_pnl_inr": gross_pnl,
            "net_pnl_inr": gross_pnl - total_costs,
            "net_win_rate": sum(value > 0 for value in net_values) / len(net_values),
            "profit_factor": positive / negative if negative else (None if not positive else "Infinity"),
        })
    return trades, pd.DataFrame(scenario_rows), pd.DataFrame(detail_rows)


def _inr(value):
    value = float(value)
    return f"-₹{abs(value):,.2f}" if value < 0 else f"₹{value:,.2f}"


def write_report(trades, summary, details, trades_path, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "scenario_summary.csv", index=False)
    details.to_csv(output_dir / "trade_sensitivity.csv", index=False)
    dates = pd.to_datetime(trades.entry_timestamp, errors="coerce")
    start = dates.min().isoformat()
    end = dates.max().isoformat()
    baseline = summary.iloc[0]
    lines = [
        "# Execution Cost Sensitivity Review", "",
        f"Source trade file: `{Path(trades_path)}`.",
        f"Portfolio trades: {len(trades)}; entry period: {start} to {end}.",
        "Observed quote sample: 0. Historical trades contain no bid/ask snapshots, so no spread was measured.",
        "All spread scenarios below are explicit assumptions, not observed costs or executable-fill estimates.",
        "The historical entry/exit references are OHLC values and the existing model does not include spread; "
        "therefore each assumed full spread is charged as half-spread on entry and half-spread on exit. "
        "No spread is added to a price already adjusted by an observed quote.", "",
        "Existing modeled costs separate saved brokerage/statutory charges from the saved 5 bps/side slippage. "
        "Adverse scenarios change only slippage and/or add the disclosed spread allowance. "
        "The base cost model and historical outputs are unchanged.", "",
        f"Existing modeled gross P&L: {_inr(baseline.gross_pnl_inr)}; "
        f"costs: {_inr(baseline.total_costs_inr)}; net P&L: {_inr(baseline.net_pnl_inr)}; "
        f"net profit factor: {baseline.profit_factor}.", "",
        "| Scenario | Slippage / side | Full spread | Brokerage | STT | Exchange | IPFT | SEBI | Stamp | GST | Slippage | Spread | Total costs | Net P&L | Net win rate | PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {row.slippage_bps_per_side:g} bps | {row.assumed_full_spread_bps:g} bps | "
            f"{_inr(row.brokerage_inr)} | {_inr(row.stt_inr)} | {_inr(row.exchange_inr)} | "
            f"{_inr(row.ipft_inr)} | {_inr(row.sebi_inr)} | {_inr(row.stamp_duty_inr)} | {_inr(row.gst_inr)} | "
            f"{_inr(row.modeled_slippage_inr)} | {_inr(row.assumed_spread_allowance_inr)} | "
            f"{_inr(row.total_costs_inr)} | {_inr(row.net_pnl_inr)} | "
            f"{row.net_win_rate:.2%} | {row.profit_factor} |"
        )
    lines.extend([
        "", "## Interpretation", "",
        "These are sensitivity calculations on the existing 46-trade portfolio. They do not establish actual "
        "spread, quote freshness, fill probability, market impact, or profitability. Live quote collection is "
        "required for those measurements; the paper runner was not started for this report.", "",
        "Detailed per-trade scenario values are in `trade_sensitivity.csv`; complete scenario values are in "
        "`scenario_summary.csv`.",
    ])
    (output_dir / "execution_cost_sensitivity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    trades, summary, details = analyze(args.trades)
    write_report(trades, summary, details, args.trades, args.output)
    print(f"Wrote {len(summary)} scenarios for {len(trades)} portfolio trades to {args.output}")


if __name__ == "__main__":
    main()
