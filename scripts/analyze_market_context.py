from pathlib import Path

import numpy as np
import pandas as pd
from app.candle_integrity import merge_candle_sources


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRADES_FILE = PROJECT_ROOT / "backtests" / "validation" / "trades.csv"
CONTEXT_DIR = PROJECT_ROOT / "data" / "research" / "market_context"
OUTPUT_DIR = PROJECT_ROOT / "backtests" / "validation" / "market_context"


CONTEXT_FILES = {
    "nifty50": CONTEXT_DIR / "nifty50_5min.csv",
    "nifty_bank": CONTEXT_DIR / "nifty_bank_5min.csv",
    "nifty_it": CONTEXT_DIR / "nifty_it_5min.csv",
    "sensex": CONTEXT_DIR / "sensex_5min.csv",
    "india_vix": CONTEXT_DIR / "india_vix_5min.csv",
}


def load_context(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df, audit = merge_candle_sources([(str(path), df)], key_columns=[])
    df.attrs["duplicate_coverage"] = audit["duplicate_coverage"]
    df = df[["timestamp", "open", "high", "low", "close"]].sort_values("timestamp", kind="stable").reset_index(drop=True)
    return df


def add_context_features(
    trades: pd.DataFrame,
    context: dict[str, pd.DataFrame],
) -> pd.DataFrame:

    result = trades.copy()

    # Entry timestamps are already stored with +05:30.
    result["entry_timestamp"] = pd.to_datetime(
        result["entry_timestamp"],
        utc=True,
    ).dt.tz_convert("Asia/Kolkata")

    # Use the latest completed context candle at or before entry.
    for name, frame in context.items():

        frame = frame.copy()

        close = frame["close"]

        # Short-term completed-candle returns.
        frame[f"{name}_return_1"] = close.pct_change(1) * 100
        frame[f"{name}_return_3"] = close.pct_change(3) * 100
        frame[f"{name}_return_6"] = close.pct_change(6) * 100

        # Direction of the most recent completed candle.
        frame[f"{name}_direction"] = np.where(
            frame["close"] > frame["open"],
            "up",
            np.where(
                frame["close"] < frame["open"],
                "down",
                "flat",
            ),
        )

        # India VIX level/change is useful directly.
        if name == "india_vix":
            frame[f"{name}_change_1"] = close.pct_change(1) * 100
            frame[f"{name}_change_6"] = close.pct_change(6) * 100

        keep = ["timestamp"]

        for column in frame.columns:
            if column.startswith(f"{name}_return_"):
                keep.append(column)

        keep.append(f"{name}_direction")

        if name == "india_vix":
            keep.extend(
                [
                    "close",
                    f"{name}_change_1",
                    f"{name}_change_6",
                ]
            )

        frame = frame[keep].sort_values("timestamp")

        result = pd.merge_asof(
            result.sort_values("entry_timestamp"),
            frame,
            left_on="entry_timestamp",
            right_on="timestamp",
            direction="backward",
            allow_exact_matches=True,
        )

        result = result.drop(columns=["timestamp"])

    # Cross-index agreement.
    direction_columns = [
        "nifty50_direction",
        "nifty_bank_direction",
        "nifty_it_direction",
        "sensex_direction",
    ]

    result["index_up_count"] = (
        result[direction_columns] == "up"
    ).sum(axis=1)

    result["index_down_count"] = (
        result[direction_columns] == "down"
    ).sum(axis=1)

    result["index_agreement"] = np.select(
        [
            result["index_up_count"] >= 3,
            result["index_down_count"] >= 3,
        ],
        [
            "bullish_alignment",
            "bearish_alignment",
        ],
        default="mixed",
    )

    return result


def print_summary(df: pd.DataFrame) -> None:
    print()
    print("=== STAGE 6.2 MARKET CONTEXT ===")
    print(f"Portfolio trades: {len(df)}")
    print(f"Net P&L: ₹{df['net_pnl'].sum():.2f}")

    print()
    print("=== CONTEXT MATCH COVERAGE ===")

    context_columns = [
        "nifty50_return_1",
        "nifty_bank_return_1",
        "nifty_it_return_1",
        "sensex_return_1",
        "india_vix_return_1",
    ]

    for column in context_columns:
        matched = df[column].notna().sum()
        print(
            f"{column}: "
            f"{matched}/{len(df)} matched"
        )

    print()
    print("=== OUTCOME BY INDEX AGREEMENT ===")

    grouped = (
        df.groupby("index_agreement")
        .agg(
            trades=("net_pnl", "count"),
            net_pnl=("net_pnl", "sum"),
            avg_net_pnl=("net_pnl", "mean"),
            avg_mfe=("mfe_pct", "mean"),
            avg_mae=("mae_pct", "mean"),
        )
        .sort_index()
    )

    print(grouped.to_string())

    print()
    print("=== WIN / LOSS BY INDEX AGREEMENT ===")

    df["outcome"] = np.where(
        df["net_pnl"] > 0,
        "winner",
        "loser",
    )

    outcome = pd.crosstab(
        df["index_agreement"],
        df["outcome"],
    )

    print(outcome.to_string())

    print()
    print("=== INDIA VIX CONTEXT ===")

    vix_summary = (
        df.groupby(
            pd.qcut(
                df["india_vix_return_1"],
                q=3,
                duplicates="drop",
            )
        )["net_pnl"]
        .agg(["count", "sum", "mean"])
    )

    print(vix_summary.to_string())


def main() -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not TRADES_FILE.exists():
        raise FileNotFoundError(TRADES_FILE)

    trades = pd.read_csv(TRADES_FILE)

    # IMPORTANT:
    # Only the frozen portfolio book is used.
    trades = trades[
        trades["book"].eq("portfolio")
    ].copy()

    if len(trades) != 46:
        raise ValueError(
            f"Expected 46 portfolio trades, found {len(trades)}"
        )

    context = {}

    for name, path in CONTEXT_FILES.items():

        if not path.exists():
            raise FileNotFoundError(path)

        context[name] = load_context(path)

        print(
            f"Loaded {name}: "
            f"{len(context[name]):,} candles"
        )

    enriched = add_context_features(
        trades,
        context,
    )

    output_file = (
        OUTPUT_DIR
        / "portfolio_market_context.csv"
    )

    enriched.to_csv(
        output_file,
        index=False,
    )

    print_summary(enriched)

    print()
    print(
        "Saved:",
        output_file,
    )


if __name__ == "__main__":
    main()
