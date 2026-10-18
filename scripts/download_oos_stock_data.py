from pathlib import Path
from datetime import date, timedelta
import time
import pandas as pd
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app.upstox_client import get_historical_candles
from strategy_backtest import STOCKS
from app.candle_integrity import merge_candle_sources


START_DATE = date(2025, 12, 2)
END_DATE = date(2026, 10, 1)

OUTPUT = (
    Path(__file__).resolve().parent
    / "data"
    / "research"
    / "extended_historical_robustness_oos_candles.csv"
)

COLUMNS = [
    "stock",
    "instrument_key",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
]


def main():
    rows = []

    current = START_DATE

    total_days = (END_DATE - START_DATE).days + 1
    day_number = 0

    for stock, instrument_key in STOCKS.items():
        print(f"\nLoading {stock}...")

        current = START_DATE

        while current <= END_DATE:
            day_number += 1

            try:
                response = get_historical_candles(
                    instrument_key,
                    current.isoformat(),
                    5,
                )

                candles = response.get("data", {}).get("candles", [])

                for candle in candles:
                    rows.append(
                        {
                            "stock": stock,
                            "instrument_key": instrument_key,
                            "timestamp": candle[0],
                            "open": candle[1],
                            "high": candle[2],
                            "low": candle[3],
                            "close": candle[4],
                            "volume": candle[5],
                            "oi": candle[6],
                        }
                    )

                print(
                    f"  {current}: {len(candles)} candles"
                )

            except Exception as exc:
                print(
                    f"  {current}: ERROR: {exc}"
                )

            current += timedelta(days=1)
            time.sleep(0.05)

    if not rows:
        raise RuntimeError("No OOS candles were downloaded.")

    incoming = pd.DataFrame(rows, columns=COLUMNS)
    incoming["timestamp"] = pd.to_datetime(incoming["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    sources = [("new Upstox download", incoming)]
    if OUTPUT.exists():
        existing = pd.read_csv(OUTPUT)
        sources.insert(0, (str(OUTPUT), existing))
    df, audit = merge_candle_sources(sources, key_columns=["instrument_key"])
    if audit["duplicate_coverage"]["count"]:
        print(f"Identical overlapping candles: {audit['duplicate_coverage']['count']}; retained once")

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    df.to_csv(OUTPUT, index=False)

    print("\n=== OOS DOWNLOAD COMPLETE ===")
    print(f"Rows: {len(df):,}")
    print(f"First: {df['timestamp'].min()}")
    print(f"Last:  {df['timestamp'].max()}")
    print(f"Stocks: {df['stock'].nunique()}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    main()
