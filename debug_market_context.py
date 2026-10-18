import pandas as pd

FILES = {
    "NIFTY50": "data/research/market_context/nifty50_5min.csv",
    "BANK": "data/research/market_context/nifty_bank_5min.csv",
    "IT": "data/research/market_context/nifty_it_5min.csv",
    "SENSEX": "data/research/market_context/sensex_5min.csv",
}

TARGETS = [
    "2025-12-02 09:20:00",
    "2025-12-23 09:25:00",
    "2026-01-02 09:20:00",
    "2026-03-30 09:20:00",
    "2026-09-02 09:20:00",
]

targets = pd.DatetimeIndex(
    TARGETS,
    tz="Asia/Kolkata",
)

print("=== RAW INDEX CANDLES ===")

for name, path in FILES.items():

    df = pd.read_csv(path)

    df["timestamp"] = (
        pd.to_datetime(
            df["timestamp"],
            utc=True,
        )
        .dt.tz_convert("Asia/Kolkata")
    )

    df = df.sort_values("timestamp")

    print()
    print(f"--- {name} ---")

    for target in targets:

        prior = df[
            df["timestamp"] <= target
        ].tail(1)

        if prior.empty:
            print(target, "NO MATCH")
            continue

        row = prior.iloc[0]

        if row["close"] > row["open"]:
            direction = "up"
        elif row["close"] < row["open"]:
            direction = "down"
        else:
            direction = "flat"

        print(
            f"Signal {target} | "
            f"Context candle {row['timestamp']} | "
            f"Open {row['open']} | "
            f"Close {row['close']} | "
            f"Direction {direction}"
        )