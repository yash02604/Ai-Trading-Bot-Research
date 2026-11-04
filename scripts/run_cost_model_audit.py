"""Reprice the saved validation portfolio under disclosed cost scenarios."""

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.cost_model import nse_ipft_per_lakh, nse_transaction_rate


ROOT = Path(__file__).resolve().parents[1]
TRADES = ROOT / "backtests/validation/trades.csv"
SUMMARY = ROOT / "backtests/validation/summary.json"
DATA = ROOT / "data/research/extended_historical_robustness_candles.csv"
OUTPUT = ROOT / "backtests/cost_model_audit_v2"
UNKNOWN_PRE_APRIL_2024_RATES = {
    # Sensitivity inputs only. The 2016 Upstox tariff and NSE's pre-April-2023
    # member schedule are not proof of this account's 2023 client rate.
    "legacy_model_rate_proxy": 0.0000322,
    "archived_upstox_tariff_proxy": 0.0000325,
    "prior_nse_upper_schedule_proxy": 0.0000345,
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reprice_trade(trade, pre_april_2024_rate, gst_on_sebi, slippage_bps):
    entry, exit_price, quantity = float(trade.entry_price), float(trade.exit_price), int(trade.quantity)
    buy_turnover, sell_turnover = entry * quantity, exit_price * quantity
    turnover = buy_turnover + sell_turnover
    day = pd.Timestamp(trade.entry_timestamp).date()
    exchange_rate = pre_april_2024_rate if day < date(2024, 4, 1) else nse_transaction_rate(day)
    ipft_rate = nse_ipft_per_lakh(day)
    brokerage = min(20.0, buy_turnover * 0.001) + min(20.0, sell_turnover * 0.001)
    exchange = turnover * exchange_rate
    ipft = turnover / 100_000.0 * ipft_rate
    sebi = turnover * 0.000001
    stt = sell_turnover * 0.00025
    stamp_duty = buy_turnover * 0.00003
    gst_base = brokerage + exchange + ipft + (sebi if gst_on_sebi else 0.0)
    gst = gst_base * 0.18
    slippage = turnover * slippage_bps / 10_000.0
    components = {
        "brokerage": brokerage, "exchange": exchange, "ipft": ipft,
        "sebi": sebi, "stt": stt, "stamp_duty": stamp_duty,
        "gst": gst, "slippage": slippage,
    }
    return components, sum(components.values())


def analyze(trades_path=TRADES):
    trades = pd.read_csv(trades_path)
    trades = trades.loc[trades.book == "portfolio"].copy()
    required = {"entry_timestamp", "entry_price", "exit_price", "quantity", "gross_pnl", "total_cost", "net_pnl"}
    missing = required - set(trades.columns)
    if missing or trades.empty:
        raise ValueError(f"Portfolio trade input is invalid; missing={sorted(missing)}, empty={trades.empty}")
    scenarios, details = [], []
    for rate_name, rate in UNKNOWN_PRE_APRIL_2024_RATES.items():
        for gst_on_sebi in (False, True):
            for slippage_bps in (0.0, 5.0, 10.0):
                item_rows, breakdown = [], {}
                for trade in trades.itertuples(index=False):
                    components, cost = reprice_trade(trade, rate, gst_on_sebi, slippage_bps)
                    net = float(trade.gross_pnl) - cost
                    row = {
                        "scenario": f"{rate_name}_gst_sebi_{gst_on_sebi}_slippage_{slippage_bps:g}bps",
                        "symbol": trade.symbol,
                        "entry_timestamp": trade.entry_timestamp,
                        "entry_price": float(trade.entry_price),
                        "exit_price": float(trade.exit_price),
                        "quantity": int(trade.quantity),
                        "gross_pnl": float(trade.gross_pnl),
                        **components,
                        "scenario_total_cost": cost,
                        "scenario_net_pnl": net,
                        "old_saved_cost": float(trade.total_cost),
                        "old_saved_net_pnl": float(trade.net_pnl),
                    }
                    item_rows.append(row)
                    details.append(row)
                    for name, value in components.items():
                        breakdown[name] = breakdown.get(name, 0.0) + value
                totals = pd.DataFrame(item_rows)
                scenarios.append({
                    "scenario": item_rows[0]["scenario"],
                    "pre_april_2024_rate_proxy": rate,
                    "gst_on_sebi_scenario": gst_on_sebi,
                    "slippage_bps_per_side_assumption": slippage_bps,
                    "trades": len(totals),
                    **{f"{name}_inr": value for name, value in breakdown.items()},
                    "scenario_costs_inr": float(totals.scenario_total_cost.sum()),
                    "old_saved_costs_inr": float(totals.old_saved_cost.sum()),
                    "cost_delta_vs_saved_inr": float(totals.scenario_total_cost.sum() - totals.old_saved_cost.sum()),
                    "gross_pnl_inr": float(totals.gross_pnl.sum()),
                    "scenario_net_pnl_inr": float(totals.scenario_net_pnl.sum()),
                    "old_saved_net_pnl_inr": float(totals.old_saved_net_pnl.sum()),
                })
    return trades, pd.DataFrame(scenarios), pd.DataFrame(details)


def write_report(trades_path=TRADES, output_dir=OUTPUT):
    trades_path, output_dir = Path(trades_path).resolve(), Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    trades, summary, details = analyze(trades_path)
    summary.to_csv(output_dir / "scenario_summary.csv", index=False)
    details.to_csv(output_dir / "trade_repricing.csv", index=False)
    input_paths = {
        "saved_validation_trades": trades_path,
        "saved_validation_summary": SUMMARY.resolve(),
        "historical_candle_file": DATA.resolve(),
        "cost_model_source": (ROOT / "app/cost_model.py").resolve(),
        "audit_runner_source": Path(__file__).resolve(),
    }
    hashes = {name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for name, path in input_paths.items()}
    (output_dir / "input_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    baseline = summary.loc[
        summary.scenario == "archived_upstox_tariff_proxy_gst_sebi_False_slippage_5bps"
    ].iloc[0]
    report = f"""# Historical Cost Model Audit v2

Status: **PROVISIONAL COST SENSITIVITY — NOT A VERIFIED HISTORICAL BACKTEST**.

This report reprices the already-saved 46 portfolio trades; it does not rerun the strategy or alter strategy parameters. It is not an independent historical validation. The underlying dataset's provenance remains UNRESOLVED (10 findings), and the saved trades are historical outputs from the prior cost model.

## Evidence and limits

- Instrument and order type assumed from project code: Upstox, NSE cash equities, long-only equity intraday, regular market orders modeled at OHLC references. The exact account tariff and actual fills are not in this repository.
- Verified rate windows used where documented: Upstox NSE intraday transaction rate 0.00322% from 2024-04-01 through 2024-09-30 and 0.00297% from 2024-10-01 through 2026-02-28. From 2026-03-01 the NSE's ₹307/crore total each side is represented as ₹306.99/crore transaction charges plus ₹0.01/crore IPFT to avoid double-counting. Pre-2024-04-01 client pricing is unresolved and appears only as three explicit sensitivity proxies.
- STT: 0.025% of sell turnover. Stamp duty: 0.003% of buy turnover. SEBI: ₹10/crore on both sides. NSE IPFT: ₹10/crore through 2026-02-28 and ₹0.01/crore from 2026-03-01.
- Brokerage uses ₹20 or 0.1% per executed order, whichever is lower, as an Upstox schedule assumption. Account-specific historical tariff/contract-note evidence is missing.
- GST is 18% on brokerage, exchange transaction and IPFT charges in the Upstox schedule. The NSE's 2022 circular also instructs GST on SEBI fees; because applicable client invoicing treatment for this period is not evidenced here, both GST-base scenarios are shown.
- Slippage is shown at 0, 5, and 10 bps per side. These are assumptions, not measured fills. No spread, market impact, auction/impact charges, DP fees, or other account-specific charges are included; equity intraday schedule states no DP fee.

Sources: [Upstox charges](https://upstox.com/brokerage-charges/), [Upstox April 2023 rate notice](https://upstox.com/announcements/updates/nse-transaction-charges-and-stt-from-1st-april-2023/), [NSE Circular FA56129](https://nsearchives.nseindia.com/content/circulars/FA56129.pdf), [NSE Circular FA64232](https://nsearchives.nseindia.com/content/circulars/FA64232.pdf), [NSE Circular FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf), [NSE statutory charge summary](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies), and [NSE GST circular 2/2022](https://archives.nseindia.com/content/circulars/FATAX52993.pdf).

## Saved costs versus scenario repricing

The center scenario is only an illustrative sensitivity: archived-tariff proxy 0.00325% for the unresolved pre-April-2024 period, GST excluding SEBI, and 5 bps/side slippage. It must not be treated as a verified corrected baseline.

- Saved old costs: ₹{baseline.old_saved_costs_inr:,.2f}; saved old net P&L: ₹{baseline.old_saved_net_pnl_inr:,.2f}.
- Center-scenario costs: ₹{baseline.scenario_costs_inr:,.2f}; scenario net P&L: ₹{baseline.scenario_net_pnl_inr:,.2f}; cost delta: ₹{baseline.cost_delta_vs_saved_inr:,.2f}.
- Other combinations and itemized per-trade costs are in `scenario_summary.csv` and `trade_repricing.csv`.

Each scenario changes only modeled cost inputs and resulting net P&L. Gross P&L and trades remain exactly those in the saved trade file. No scenario is designated as the verified historical cost baseline, and none is evidence of profitability.

## Reproduction

`python -m scripts.run_cost_model_audit --trades backtests/validation/trades.csv --output backtests/cost_model_audit_v2`

The run writes dataset, saved-result, cost-model, and runner SHA-256 values to `input_hashes.json`; it refuses to overwrite a non-empty output directory. Existing validation reports are not changed.
"""
    (output_dir / "COST_MODEL_AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    return summary, output_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, default=TRADES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    summary, output = write_report(args.trades, args.output)
    print(f"Wrote {len(summary)} labeled cost scenarios to {output}")


if __name__ == "__main__":
    main()
