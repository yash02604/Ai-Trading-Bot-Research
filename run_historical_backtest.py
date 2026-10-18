"""Run the frozen reversal backtest over locally cached research candles."""

import argparse
from pathlib import Path

import pandas as pd

from app.risk_engine import RiskConfig
from backtester import Backtester

DATA = Path(__file__).resolve().parent / "data" / "research" / "extended_historical_robustness_candles.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Symbol present in the cached historical dataset")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--stop-pct", type=float, default=1.0)
    parser.add_argument("--horizon", type=int, default=6)
    args = parser.parse_args()
    if not DATA.exists():
        raise SystemExit(f"Historical candle data not found: {DATA}")
    frame = pd.read_csv(DATA, usecols=["stock", "timestamp", "open", "high", "low", "close", "volume", "oi"])
    frame = frame[frame.stock.str.upper() == args.symbol.upper()]
    if frame.empty:
        raise SystemExit(f"No cached candles found for {args.symbol}")
    candles = frame[["timestamp", "open", "high", "low", "close", "volume", "oi"]].values.tolist()
    tester = Backtester(candles)
    report = tester.run_reversal_backtest(
        RiskConfig(capital=args.capital), stop_pct=args.stop_pct, horizon=args.horizon
    )
    print(f"Symbol: {args.symbol.upper()}")
    print(f"Input candles: {len(tester.df)} ({tester.df.timestamp.iloc[0]} to {tester.df.timestamp.iloc[-1]})")
    for key in (
        "total_trades", "winning_trades", "losing_trades", "win_rate",
        "gross_profit", "gross_loss", "net_pnl", "ending_capital",
        "max_drawdown", "average_trade", "average_r_multiple", "profit_factor",
    ):
        print(f"{key}: {report[key]}")
    if not report["trades"].empty:
        print("Exit reasons:")
        print(report["trades"].exit_reason.value_counts().to_string())


if __name__ == "__main__":
    main()
