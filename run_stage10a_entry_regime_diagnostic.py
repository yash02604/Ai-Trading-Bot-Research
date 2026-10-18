from pathlib import Path

import pandas as pd

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
)


# ============================================================
# STAGE 10A — ENTRY & REGIME DIAGNOSTIC
#
# PURPOSE:
#   Diagnostic research only.
#
# IMPORTANT:
#   - No strategy parameters are changed.
#   - No new entry filter is introduced.
#   - No optimization is performed.
#   - No trades are simulated.
#   - Uses the already validated Stage 9 trade results.
#   - AI replay is not used.
#
# The purpose is to understand:
#   1. Which stocks behave differently
#   2. Which times of day behave differently
#   3. Decline strength
#   4. RSI position
#   5. Volume strength
#   6. EMA distance
#   7. Signal candle structure
#   8. Market-context regime
#   9. Winner vs loser separation
#
# This stage is descriptive only.
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

SOURCE = Path(
    "scripts/data/research/"
    "extended_historical_robustness_oos_candles.csv"
)

STAGE9_TRADES = Path(
    "backtests/stage9_candidate/trades.csv"
)

CONTEXT_DIR = Path(
    "data/research/market_context"
)

CONTEXT_FILES = {
    "NIFTY50": CONTEXT_DIR / "nifty50_5min.csv",
    "NIFTY_BANK": CONTEXT_DIR / "nifty_bank_5min.csv",
    "NIFTY_IT": CONTEXT_DIR / "nifty_it_5min.csv",
    "SENSEX": CONTEXT_DIR / "sensex_5min.csv",
}

OUTPUT_DIR = Path(
    "backtests/stage10a_entry_regime"
)

OOS_START = pd.Timestamp(
    "2025-12-02 00:00:00",
    tz="Asia/Kolkata",
)

OOS_END = pd.Timestamp(
    "2026-10-01 23:59:59",
    tz="Asia/Kolkata",
)


# ============================================================
# HELPERS
# ============================================================

def pct_return(base, value):
    """Percentage return from base to value."""
    if base == 0:
        return 0.0

    return (
        (float(value) / float(base)) - 1.0
    ) * 100.0


def normalize_timestamp(series):
    """
    Normalize timestamps to Asia/Kolkata.
    """
    ts = pd.to_datetime(
        series,
        errors="coerce",
        utc=True,
    )

    return ts.dt.tz_convert(
        "Asia/Kolkata"
    )


def find_column(frame, candidates):
    """
    Find the first available column from a list.
    """
    for column in candidates:
        if column in frame.columns:
            return column

    return None


def safe_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def bucket_decline(value):
    """
    Decline is stored as a negative percentage.
    Example:
        -1.8% decline -> 1.8
    """
    if value is None:
        return "unknown"

    decline = abs(float(value))

    if decline < 1.25:
        return "1.00-1.25%"

    if decline < 1.50:
        return "1.25-1.50%"

    if decline < 1.75:
        return "1.50-1.75%"

    return "1.75-2.00%"


def bucket_rsi(value):
    if value is None:
        return "unknown"

    value = float(value)

    if value < 21:
        return "20-21"

    if value < 22:
        return "21-22"

    if value < 23:
        return "22-23"

    if value < 24:
        return "23-24"

    return "24-25"


def bucket_time(timestamp):
    """
    Diagnostic time buckets.
    These do NOT represent strategy rules.
    """
    hour = timestamp.hour
    minute = timestamp.minute

    total_minutes = hour * 60 + minute

    if total_minutes < 570:
        return "09:15-09:30"

    if total_minutes < 600:
        return "09:30-10:00"

    if total_minutes < 660:
        return "10:00-11:00"

    if total_minutes < 780:
        return "11:00-13:00"

    if total_minutes < 900:
        return "13:00-15:00"

    return "15:00-15:30"


def bucket_volume(value):
    if value is None:
        return "unknown"

    value = float(value)

    if value < 0.75:
        return "<0.75x"

    if value < 1.00:
        return "0.75-1.00x"

    if value < 1.25:
        return "1.00-1.25x"

    if value < 1.50:
        return "1.25-1.50x"

    return ">=1.50x"


# ============================================================
# MARKET CONTEXT
# ============================================================

def load_context(path):
    print(
        f"Loading context: {path}"
    )

    df = pd.read_csv(path)

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    ]

    missing = [
        column
        for column in required
        if column not in df.columns
    ]

    if missing:
        raise KeyError(
            f"{path} missing columns: {missing}"
        )

    df = df[
        required
    ].copy()

    df["timestamp"] = normalize_timestamp(
        df["timestamp"]
    )

    df = df.dropna(
        subset=["timestamp"]
    )

    df = df.drop_duplicates(
        subset=["timestamp"],
        keep="last",
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    df["is_down"] = (
        df["close"] < df["open"]
    )

    return df


def build_context_lookup(context):
    """
    Prepare one direction dataframe per index.
    """
    result = {}

    for name, df in context.items():

        result[name] = df[
            [
                "timestamp",
                "is_down",
            ]
        ].rename(
            columns={
                "is_down":
                    f"{name}_direction_down"
            }
        )

    return result


def attach_market_context(events):
    """
    Attach the latest completed index candle at or before
    signal_timestamp.

    This is descriptive and follows the Stage 9 context timing:
        signal_timestamp
    """
    context = {}

    for name, path in CONTEXT_FILES.items():
        context[name] = load_context(path)

    lookups = build_context_lookup(
        context
    )

    df = pd.DataFrame(events)

    if df.empty:
        return df

    df["signal_timestamp"] = normalize_timestamp(
        df["signal_timestamp"]
    )

    df = df.sort_values(
        "signal_timestamp"
    ).reset_index(drop=True)

    for name in [
        "NIFTY50",
        "NIFTY_BANK",
        "NIFTY_IT",
        "SENSEX",
    ]:

        lookup = lookups[name]

        df = pd.merge_asof(
            df.sort_values(
                "signal_timestamp"
            ),
            lookup.sort_values(
                "timestamp"
            ),
            left_on="signal_timestamp",
            right_on="timestamp",
            direction="backward",
            allow_exact_matches=True,
        )

        df = df.drop(
            columns=["timestamp"],
            errors="ignore",
        )

    direction_columns = [
        "NIFTY50_direction_down",
        "NIFTY_BANK_direction_down",
        "NIFTY_IT_direction_down",
        "SENSEX_direction_down",
    ]

    missing_context = df[
        direction_columns
    ].isna().any(axis=1)

    if missing_context.any():
        missing_count = int(
            missing_context.sum()
        )

        raise RuntimeError(
            f"{missing_count} OOS signals have "
            "missing market context."
        )

    df["down_count"] = df[
        direction_columns
    ].sum(axis=1)

    df["context_veto"] = (
        df["down_count"] >= 3
    )

    df["context_regime"] = (
        df["down_count"]
        .map(
            {
                0: "0_DOWN",
                1: "1_DOWN",
                2: "2_DOWN",
                3: "3_DOWN",
                4: "4_DOWN",
            }
        )
    )

    return df


# ============================================================
# BUILD SIGNAL FEATURES
# ============================================================

def build_signal_diagnostics(frames):
    """
    Build one row per frozen OOS event.

    This uses the same _make_events() mechanism as the
    frozen strategy.
    """
    events, raw_signals, data_rejects = _make_events(
        frames
    )

    events = [
        event
        for event in events
        if OOS_START
        <= event["signal_timestamp"]
        <= OOS_END
    ]

    print(
        f"\nFrozen OOS events: {len(events)}"
    )

    rows = []

    for event in events:

        symbol = event["symbol"]

        frame = frames[symbol]

        signal_timestamp = pd.Timestamp(
            event["signal_timestamp"]
        )

        signal_index = None

        # _make_events provides signal_position in the
        # current historical-validation implementation.
        if "signal_position" in event:
            signal_index = int(
                event["signal_position"]
            )

        # Fallback: locate by timestamp.
        if signal_index is None:
            matches = frame.index[
                frame["timestamp"]
                == signal_timestamp
            ]

            if len(matches) == 0:
                continue

            signal_index = int(
                matches[0]
            )

        if signal_index < 1:
            continue

        signal_row = frame.iloc[
            signal_index
        ]

        entry_index = signal_index + 1

        if entry_index >= len(frame):
            continue

        entry_row = frame.iloc[
            entry_index
        ]

        # ----------------------------------------------------
        # Price columns
        # ----------------------------------------------------

        signal_open = safe_float(
            signal_row["open"]
        )

        signal_high = safe_float(
            signal_row["high"]
        )

        signal_low = safe_float(
            signal_row["low"]
        )

        signal_close = safe_float(
            signal_row["close"]
        )

        entry_open = safe_float(
            entry_row["open"]
        )

        if (
            signal_open is None
            or signal_high is None
            or signal_low is None
            or signal_close is None
            or entry_open is None
        ):
            continue

        signal_range = (
            signal_high
            - signal_low
        )

        if signal_range != 0:
            close_location = (
                signal_close
                - signal_low
            ) / signal_range
        else:
            close_location = None

        body_pct = (
            abs(
                signal_close
                - signal_open
            )
            / signal_open
            * 100.0
            if signal_open
            else None
        )

        upper_wick_pct = (
            (
                signal_high
                - max(
                    signal_open,
                    signal_close,
                )
            )
            / signal_open
            * 100.0
            if signal_open
            else None
        )

        lower_wick_pct = (
            (
                min(
                    signal_open,
                    signal_close,
                )
                - signal_low
            )
            / signal_open
            * 100.0
            if signal_open
            else None
        )

        candle_return_pct = pct_return(
            signal_open,
            signal_close,
        )

        entry_gap_pct = pct_return(
            signal_close,
            entry_open,
        )

        # ----------------------------------------------------
        # Indicator columns
        # ----------------------------------------------------

        rsi_column = find_column(
            frame,
            [
                "rsi_14",
                "RSI_14",
                "rsi",
            ],
        )

        ema9_column = find_column(
            frame,
            [
                "ema_9",
                "EMA_9",
                "ema9",
            ],
        )

        ema20_column = find_column(
            frame,
            [
                "ema_20",
                "EMA_20",
                "ema20",
            ],
        )

        volume_column = find_column(
            frame,
            [
                "volume",
                "Volume",
            ],
        )

        volume_ma_column = find_column(
            frame,
            [
                "volume_ma_20",
                "Volume_MA_20",
                "volume_ma20",
                "vol_ma_20",
            ],
        )

        macd_column = find_column(
            frame,
            [
                "macd",
                "MACD",
            ],
        )

        macd_signal_column = find_column(
            frame,
            [
                "macd_signal",
                "MACD_signal",
                "macd_signal_9",
            ],
        )

        macd_hist_column = find_column(
            frame,
            [
                "macd_histogram",
                "MACD_histogram",
                "macd_hist",
                "MACD_hist",
            ],
        )

        rsi_value = (
            safe_float(
                signal_row[rsi_column]
            )
            if rsi_column
            else safe_float(
                event.get("rsi_14")
            )
        )

        ema9_value = (
            safe_float(
                signal_row[ema9_column]
            )
            if ema9_column
            else None
        )

        ema20_value = (
            safe_float(
                signal_row[ema20_column]
            )
            if ema20_column
            else None
        )

        volume_value = (
            safe_float(
                signal_row[volume_column]
            )
            if volume_column
            else None
        )

        volume_ma_value = (
            safe_float(
                signal_row[volume_ma_column]
            )
            if volume_ma_column
            else None
        )

        volume_ratio = None

        if (
            volume_value is not None
            and volume_ma_value is not None
            and volume_ma_value != 0
        ):
            volume_ratio = (
                volume_value
                / volume_ma_value
            )

        ema9_distance_pct = None
        ema20_distance_pct = None

        if ema9_value not in (
            None,
            0,
        ):
            ema9_distance_pct = pct_return(
                ema9_value,
                signal_close,
            )

        if ema20_value not in (
            None,
            0,
        ):
            ema20_distance_pct = pct_return(
                ema20_value,
                signal_close,
            )

        macd_value = (
            safe_float(
                signal_row[macd_column]
            )
            if macd_column
            else None
        )

        macd_signal_value = (
            safe_float(
                signal_row[macd_signal_column]
            )
            if macd_signal_column
            else None
        )

        macd_hist_value = (
            safe_float(
                signal_row[macd_hist_column]
            )
            if macd_hist_column
            else None
        )

        # ----------------------------------------------------
        # Previous candle
        # ----------------------------------------------------

        previous_row = frame.iloc[
            signal_index - 1
        ]

        previous_close = safe_float(
            previous_row["close"]
        )

        previous_open = safe_float(
            previous_row["open"]
        )

        previous_return_pct = None

        if (
            previous_open is not None
            and previous_close is not None
        ):
            previous_return_pct = pct_return(
                previous_open,
                previous_close,
            )

        # ----------------------------------------------------
        # Build row
        # ----------------------------------------------------

        rows.append(
            {
                "symbol": symbol,
                "signal_timestamp":
                    signal_timestamp,
                "entry_timestamp":
                    pd.Timestamp(
                        entry_row["timestamp"]
                    ),
                "hour":
                    signal_timestamp.hour,
                "minute":
                    signal_timestamp.minute,
                "time_bucket":
                    bucket_time(
                        signal_timestamp
                    ),
                "decline_pct":
                    safe_float(
                        event.get(
                            "decline_pct"
                        )
                    ),
                "decline_abs_pct":
                    abs(
                        safe_float(
                            event.get(
                                "decline_pct"
                            )
                        )
                    )
                    if event.get(
                        "decline_pct"
                    )
                    is not None
                    else None,
                "decline_bucket":
                    bucket_decline(
                        event.get(
                            "decline_pct"
                        )
                    ),
                "rsi_14":
                    rsi_value,
                "rsi_bucket":
                    bucket_rsi(
                        rsi_value
                    ),
                "volume":
                    volume_value,
                "volume_ma_20":
                    volume_ma_value,
                "volume_ratio":
                    volume_ratio,
                "volume_bucket":
                    bucket_volume(
                        volume_ratio
                    ),
                "ema9":
                    ema9_value,
                "ema20":
                    ema20_value,
                "ema9_distance_pct":
                    ema9_distance_pct,
                "ema20_distance_pct":
                    ema20_distance_pct,
                "macd":
                    macd_value,
                "macd_signal":
                    macd_signal_value,
                "macd_histogram":
                    macd_hist_value,
                "signal_open":
                    signal_open,
                "signal_high":
                    signal_high,
                "signal_low":
                    signal_low,
                "signal_close":
                    signal_close,
                "signal_range":
                    signal_range,
                "signal_body_pct":
                    body_pct,
                "upper_wick_pct":
                    upper_wick_pct,
                "lower_wick_pct":
                    lower_wick_pct,
                "close_location":
                    close_location,
                "signal_candle_return_pct":
                    candle_return_pct,
                "previous_candle_return_pct":
                    previous_return_pct,
                "entry_gap_pct":
                    entry_gap_pct,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# MATCH STAGE 9 RESULTS
# ============================================================

def attach_stage9_results(diagnostics):
    """
    Match the actual Stage 9 accepted trades to the
    frozen OOS diagnostics.

    Uses normalized symbol + signal_timestamp keys.
    """

    trades = pd.read_csv(
        STAGE9_TRADES
    )

    if trades.empty:
        raise RuntimeError(
            "Stage 9 trades file is empty."
        )

    # This artifact contains accepted trades; `book` is its
    # artifact label (`stage9_candidate`), not a sub-book filter.
    expected_trade_count = 19
    expected_diagnostic_count = 37

    print(
        f"Stage 9 trades: {len(trades)}"
    )
    print(
        f"Diagnostic signals: {len(diagnostics)}"
    )

    if len(trades) != expected_trade_count:
        raise RuntimeError(
            f"Expected {expected_trade_count} Stage 9 accepted "
            f"trades, found {len(trades)}."
        )

    if len(diagnostics) != expected_diagnostic_count:
        raise RuntimeError(
            f"Expected {expected_diagnostic_count} diagnostic "
            f"signals, found {len(diagnostics)}."
        )

    # --------------------------------------------------------
    # Normalize timestamps
    # --------------------------------------------------------

    trades["signal_timestamp"] = (
        normalize_timestamp(
            trades["signal_timestamp"]
        )
    )

    diagnostics["signal_timestamp"] = (
        normalize_timestamp(
            diagnostics["signal_timestamp"]
        )
    )

    # --------------------------------------------------------
    # Normalize symbol text
    # --------------------------------------------------------

    trades["symbol"] = (
        trades["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    diagnostics["symbol"] = (
        diagnostics["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # --------------------------------------------------------
    # Diagnostic key
    # --------------------------------------------------------

    trades["_trade_key"] = (
        trades["symbol"]
        + "|"
        + trades[
            "signal_timestamp"
        ].astype(str)
    )

    diagnostics["_trade_key"] = (
        diagnostics["symbol"]
        + "|"
        + diagnostics[
            "signal_timestamp"
        ].astype(str)
    )

    duplicate_trade_keys = trades.loc[
        trades["_trade_key"].duplicated(keep=False),
        "_trade_key",
    ].unique()
    if len(duplicate_trade_keys):
        raise RuntimeError(
            "Duplicate Stage 9 trade keys: "
            f"{duplicate_trade_keys.tolist()}"
        )

    trade_keys = set(
        trades["_trade_key"]
    )

    accepted_diagnostics = diagnostics[
        diagnostics["_trade_key"].isin(trade_keys)
    ]
    duplicate_diagnostic_keys = accepted_diagnostics.loc[
        accepted_diagnostics["_trade_key"].duplicated(keep=False),
        "_trade_key",
    ].unique()
    if len(duplicate_diagnostic_keys):
        raise RuntimeError(
            "Duplicate diagnostic keys for accepted trades: "
            f"{duplicate_diagnostic_keys.tolist()}"
        )

    # --------------------------------------------------------
    # Check exact overlap before merge
    # --------------------------------------------------------

    diagnostic_keys = set(
        diagnostics["_trade_key"]
    )

    overlap = (
        diagnostic_keys
        & trade_keys
    )
    print(
        f"Matching accepted trades: {len(overlap)}"
    )

    if len(overlap) != len(trades):
        missing_trade_keys = sorted(
            trade_keys - diagnostic_keys
        )
        raise RuntimeError(
            "Not every Stage 9 accepted trade has a matching "
            f"diagnostic event. Missing keys: {missing_trade_keys}"
        )

    # --------------------------------------------------------
    # Keep only useful trade columns
    # --------------------------------------------------------

    trade_columns = [
        "symbol",
        "signal_timestamp",
        "entry_timestamp",
        "exit_timestamp",
        "net_pnl",
        "gross_pnl",
        "total_cost",
        "exit_reason",
        "mfe_pct",
        "mae_pct",
        "r_multiple",
        "quantity",
    ]

    available = [
        column
        for column in trade_columns
        if column in trades.columns
    ]

    trade_data = trades[
        available
        + ["_trade_key"]
    ].copy()

    # --------------------------------------------------------
    # Merge using the explicit normalized key
    # --------------------------------------------------------

    diagnostics = diagnostics.merge(
        trade_data,
        on="_trade_key",
        how="left",
        validate="many_to_one",
        suffixes=(
            "",
            "_trade",
        ),
    )

    # --------------------------------------------------------
    # Remove duplicate merge columns if created
    # --------------------------------------------------------

    for column in [
        "symbol_trade",
        "signal_timestamp_trade",
    ]:

        if column in diagnostics.columns:
            diagnostics.drop(
                columns=[column],
                inplace=True,
            )

    # --------------------------------------------------------
    # Identify actual trades
    # --------------------------------------------------------

    diagnostics[
        "accepted_trade"
    ] = diagnostics[
        "net_pnl"
    ].notna()

    diagnostics[
        "outcome"
    ] = diagnostics[
        "net_pnl"
    ].apply(
        lambda value:
            "WIN"
            if pd.notna(value)
            and float(value) > 0
            else (
                "LOSS"
                if pd.notna(value)
                else "NOT_TRADED"
            )
    )

    # --------------------------------------------------------
    # Final audit
    # --------------------------------------------------------

    matched_count = int(
        diagnostics[
            "accepted_trade"
        ].sum()
    )

    print(
        f"Stage 9 trades matched: "
        f"{matched_count}"
    )

    if matched_count != expected_trade_count:
        raise RuntimeError(
            f"Expected {expected_trade_count} matched accepted "
            f"trades after merge, found {matched_count}."
        )

    # Remove helper key from final diagnostic
    diagnostics.drop(
        columns=["_trade_key"],
        inplace=True,
        errors="ignore",
    )

    return diagnostics      


    """
    Match actual Stage 9 accepted trades.

    Signals vetoed by market context or rejected by risk remain
    in the full diagnostics dataset but have no trade outcome.
    """
    trades = pd.read_csv(
        STAGE9_TRADES
    )

    if trades.empty:
        raise RuntimeError(
            "Stage 9 trades file is empty."
        )

    # Stage 9 trade file should contain the portfolio book.
    if "book" in trades.columns:
        trades = trades[
            trades["book"] == "portfolio"
        ].copy()

    trades["signal_timestamp"] = (
        normalize_timestamp(
            trades["signal_timestamp"]
        )
    )

    diagnostics[
        "signal_timestamp"
    ] = normalize_timestamp(
        diagnostics["signal_timestamp"]
    )

    trade_columns = [
        "symbol",
        "signal_timestamp",
        "entry_timestamp",
        "exit_timestamp",
        "net_pnl",
        "gross_pnl",
        "total_cost",
        "exit_reason",
        "mfe_pct",
        "mae_pct",
        "r_multiple",
        "quantity",
    ]

    available = [
        column
        for column in trade_columns
        if column in trades.columns
    ]

    trade_data = trades[
        available
    ].copy()

    diagnostics = diagnostics.merge(
        trade_data,
        on=[
            "symbol",
            "signal_timestamp",
        ],
        how="left",
        suffixes=(
            "",
            "_trade",
        ),
    )

    diagnostics[
        "accepted_trade"
    ] = diagnostics[
        "net_pnl"
    ].notna()

    diagnostics[
        "outcome"
    ] = diagnostics[
        "net_pnl"
    ].apply(
        lambda value:
            "WIN"
            if pd.notna(value)
            and value > 0
            else (
                "LOSS"
                if pd.notna(value)
                else "NOT_TRADED"
            )
    )

    return diagnostics


# ============================================================
# SUMMARY FUNCTION
# ============================================================

def grouped_summary(
    df,
    group_column,
):
    """
    Produce descriptive statistics for a grouping.
    """
    if df.empty:
        return pd.DataFrame()

    rows = []

    for group_value, group in df.groupby(
        group_column,
        dropna=False,
    ):

        net_values = (
            pd.to_numeric(
                group["net_pnl"],
                errors="coerce",
            )
            .dropna()
        )

        wins = net_values[
            net_values > 0
        ]

        losses = net_values[
            net_values <= 0
        ]

        gross_wins = (
            wins.sum()
            if len(wins)
            else 0.0
        )

        gross_losses = abs(
            losses.sum()
        ) if len(losses) else 0.0

        profit_factor = (
            gross_wins / gross_losses
            if gross_losses > 0
            else None
        )

        rows.append(
            {
                group_column:
                    group_value,
                "signals":
                    len(group),
                "accepted_trades":
                    int(
                        group[
                            "accepted_trade"
                        ].sum()
                    ),
                "wins":
                    int(
                        (
                            group[
                                "outcome"
                            ]
                            == "WIN"
                        ).sum()
                    ),
                "losses":
                    int(
                        (
                            group[
                                "outcome"
                            ]
                            == "LOSS"
                        ).sum()
                    ),
                "win_rate_pct":
                    (
                        len(wins)
                        / len(net_values)
                        * 100.0
                    )
                    if len(net_values)
                    else None,
                "net_pnl":
                    net_values.sum()
                    if len(net_values)
                    else 0.0,
                "avg_net_pnl":
                    net_values.mean()
                    if len(net_values)
                    else None,
                "avg_mfe_pct":
                    pd.to_numeric(
                        group[
                            "mfe_pct"
                        ],
                        errors="coerce",
                    ).mean(),
                "avg_mae_pct":
                    pd.to_numeric(
                        group[
                            "mae_pct"
                        ],
                        errors="coerce",
                    ).mean(),
                "avg_decline_pct":
                    pd.to_numeric(
                        group[
                            "decline_pct"
                        ],
                        errors="coerce",
                    ).mean(),
                "avg_rsi":
                    pd.to_numeric(
                        group[
                            "rsi_14"
                        ],
                        errors="coerce",
                    ).mean(),
                "avg_volume_ratio":
                    pd.to_numeric(
                        group[
                            "volume_ratio"
                        ],
                        errors="coerce",
                    ).mean(),
                "profit_factor":
                    profit_factor,
            }
        )

    return pd.DataFrame(
        rows
    ).sort_values(
        "net_pnl",
        ascending=False,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)
    print(
        "STAGE 10A — ENTRY & REGIME DIAGNOSTIC"
    )
    print("=" * 72)

    print(
        "\nIMPORTANT:"
    )
    print(
        "This is descriptive research only."
    )
    print(
        "No strategy parameters are changed."
    )
    print(
        "No trades are simulated."
    )

    # --------------------------------------------------------
    # Load OOS candles
    # --------------------------------------------------------

    print(
        "\nLoading OOS warm-up candles..."
    )

    data, audit = load_historical_data(
        SOURCE
    )

    print(
        f"Loaded rows: {len(data):,}"
    )

    frames = prepare_symbol_frames(
        data
    )

    print(
        f"Loaded symbols: {len(frames)}"
    )

    # --------------------------------------------------------
    # Build frozen signal diagnostics
    # --------------------------------------------------------

    print(
        "\nBuilding frozen OOS signal diagnostics..."
    )

    diagnostics = (
        build_signal_diagnostics(
            frames
        )
    )

    if diagnostics.empty:
        raise RuntimeError(
            "No diagnostic signals were generated."
        )

    # --------------------------------------------------------
    # Attach market context
    # --------------------------------------------------------

    print(
        "\nAttaching Stage 9 market context..."
    )

    diagnostics = (
        attach_market_context(
            diagnostics.to_dict(
                orient="records"
            )
        )
    )

    # --------------------------------------------------------
    # Attach Stage 9 actual outcomes
    # --------------------------------------------------------

    print(
        "\nMatching Stage 9 accepted trades..."
    )

    diagnostics = (
        attach_stage9_results(
            diagnostics
        )
    )

    # --------------------------------------------------------
    # Output directory
    # --------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Save complete diagnostics
    # --------------------------------------------------------

    diagnostics.to_csv(
        OUTPUT_DIR
        / "signal_diagnostics.csv",
        index=False,
    )

    accepted = diagnostics[
        diagnostics[
            "accepted_trade"
        ]
    ].copy()

    accepted.to_csv(
        OUTPUT_DIR
        / "accepted_trade_diagnostics.csv",
        index=False,
    )

    # --------------------------------------------------------
    # Group summaries
    # --------------------------------------------------------

    summaries = {
        "by_symbol":
            grouped_summary(
                accepted,
                "symbol",
            ),

        "by_time_bucket":
            grouped_summary(
                accepted,
                "time_bucket",
            ),

        "by_decline_bucket":
            grouped_summary(
                accepted,
                "decline_bucket",
            ),

        "by_rsi_bucket":
            grouped_summary(
                accepted,
                "rsi_bucket",
            ),

        "by_volume_bucket":
            grouped_summary(
                accepted,
                "volume_bucket",
            ),

        "by_context":
            grouped_summary(
                accepted,
                "context_regime",
            ),
    }

    for name, summary in summaries.items():

        summary.to_csv(
            OUTPUT_DIR
            / f"{name}.csv",
            index=False,
        )

    # --------------------------------------------------------
    # Overall summary
    # --------------------------------------------------------

    trade_net = pd.to_numeric(
        accepted["net_pnl"],
        errors="coerce",
    ).dropna()

    winners = trade_net[
        trade_net > 0
    ]

    losers = trade_net[
        trade_net <= 0
    ]

    gross_wins = (
        winners.sum()
        if len(winners)
        else 0.0
    )

    gross_losses = abs(
        losers.sum()
    ) if len(losers) else 0.0

    profit_factor = (
        gross_wins / gross_losses
        if gross_losses > 0
        else None
    )

    summary = pd.DataFrame(
        [
            {
                "frozen_oos_signals":
                    len(diagnostics),

                "context_vetoed":
                    int(
                        diagnostics[
                            "context_veto"
                        ].sum()
                    ),

                "accepted_stage9_trades":
                    len(accepted),

                "wins":
                    len(winners),

                "losses":
                    len(losers),

                "win_rate_pct":
                    (
                        len(winners)
                        / len(trade_net)
                        * 100.0
                    )
                    if len(trade_net)
                    else None,

                "net_pnl":
                    trade_net.sum()
                    if len(trade_net)
                    else 0.0,

                "avg_net_pnl":
                    trade_net.mean()
                    if len(trade_net)
                    else None,

                "profit_factor":
                    profit_factor,

                "avg_mfe_pct":
                    pd.to_numeric(
                        accepted[
                            "mfe_pct"
                        ],
                        errors="coerce",
                    ).mean(),

                "avg_mae_pct":
                    pd.to_numeric(
                        accepted[
                            "mae_pct"
                        ],
                        errors="coerce",
                    ).mean(),

                "avg_decline_pct":
                    pd.to_numeric(
                        accepted[
                            "decline_pct"
                        ],
                        errors="coerce",
                    ).mean(),

                "avg_rsi":
                    pd.to_numeric(
                        accepted[
                            "rsi_14"
                        ],
                        errors="coerce",
                    ).mean(),

                "avg_volume_ratio":
                    pd.to_numeric(
                        accepted[
                            "volume_ratio"
                        ],
                        errors="coerce",
                    ).mean(),
            }
        ]
    )

    summary.to_csv(
        OUTPUT_DIR
        / "summary.csv",
        index=False,
    )

    # ========================================================
    # PRINT RESULTS
    # ========================================================

    print("\n")
    print("=" * 72)
    print(
        "STAGE 10A RESULTS"
    )
    print("=" * 72)

    print(
        f"\nFrozen OOS signals: "
        f"{len(diagnostics)}"
    )

    print(
        f"Context vetoed: "
        f"{int(diagnostics['context_veto'].sum())}"
    )

    print(
        f"Stage 9 accepted trades: "
        f"{len(accepted)}"
    )

    print(
        f"Winners: "
        f"{len(winners)}"
    )

    print(
        f"Losers: "
        f"{len(losers)}"
    )

    print(
        f"Win rate: "
        f"{(len(winners) / len(trade_net) * 100):.2f}%"
        if len(trade_net)
        else "Win rate: N/A"
    )

    print(
        f"Net P&L: "
        f"₹{trade_net.sum():.2f}"
    )

    print(
        f"Profit factor: "
        f"{profit_factor}"
    )

    # --------------------------------------------------------
    # Winner vs loser diagnostic
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "WINNER VS LOSER DIAGNOSTIC"
    )
    print("=" * 72)

    comparison = accepted.copy()

    comparison[
        "result_group"
    ] = comparison[
        "outcome"
    ].map(
        {
            "WIN": "WIN",
            "LOSS": "LOSS",
        }
    )

    comparison = comparison[
        comparison[
            "result_group"
        ].notna()
    ]

    comparison_summary = (
        comparison
        .groupby(
            "result_group"
        )
        .agg(
            trades=(
                "net_pnl",
                "size",
            ),
            avg_net_pnl=(
                "net_pnl",
                "mean",
            ),
            avg_decline_pct=(
                "decline_pct",
                "mean",
            ),
            avg_rsi=(
                "rsi_14",
                "mean",
            ),
            avg_volume_ratio=(
                "volume_ratio",
                "mean",
            ),
            avg_ema9_distance_pct=(
                "ema9_distance_pct",
                "mean",
            ),
            avg_ema20_distance_pct=(
                "ema20_distance_pct",
                "mean",
            ),
            avg_signal_body_pct=(
                "signal_body_pct",
                "mean",
            ),
            avg_lower_wick_pct=(
                "lower_wick_pct",
                "mean",
            ),
            avg_close_location=(
                "close_location",
                "mean",
            ),
            avg_mfe_pct=(
                "mfe_pct",
                "mean",
            ),
            avg_mae_pct=(
                "mae_pct",
                "mean",
            ),
        )
    )

    print(
        comparison_summary.round(
            4
        ).to_string()
    )

    # --------------------------------------------------------
    # Context results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "MARKET CONTEXT BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_context"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # Symbol results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "SYMBOL BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_symbol"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # Time results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "TIME-OF-DAY BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_time_bucket"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # Decline results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "DECLINE-STRENGTH BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_decline_bucket"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # RSI results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "RSI BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_rsi_bucket"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # Volume results
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "VOLUME BREAKDOWN"
    )
    print("=" * 72)

    print(
        summaries[
            "by_volume_bucket"
        ].round(
            4
        ).to_string(
            index=False
        )
    )

    # --------------------------------------------------------
    # MFE separation
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "MFE SEPARATION"
    )
    print("=" * 72)

    print(
        "Winner MFE >= 0.50%:",
        int(
            (
                (
                    accepted[
                        "outcome"
                    ]
                    == "WIN"
                )
                &
                (
                    pd.to_numeric(
                        accepted[
                            "mfe_pct"
                        ],
                        errors="coerce",
                    )
                    >= 0.50
                )
            ).sum()
        ),
    )

    print(
        "Loser MFE >= 0.50%:",
        int(
            (
                (
                    accepted[
                        "outcome"
                    ]
                    == "LOSS"
                )
                &
                (
                    pd.to_numeric(
                        accepted[
                            "mfe_pct"
                        ],
                        errors="coerce",
                    )
                    >= 0.50
                )
            ).sum()
        ),
    )

    # --------------------------------------------------------
    # Save winner/loser comparison
    # --------------------------------------------------------

    comparison_summary.reset_index().to_csv(
        OUTPUT_DIR
        / "winner_vs_loser.csv",
        index=False,
    )

    # --------------------------------------------------------
    # Final message
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print(
        "OUTPUT FILES"
    )
    print("=" * 72)

    print(
        f"Directory: {OUTPUT_DIR}"
    )

    print(
        "\nStage 10A diagnostic complete."
    )

    print(
        "No strategy parameters were changed."
    )

    print(
        "No trades were simulated."
    )

    print(
        "No AI replay was used."
    )


if __name__ == "__main__":
    main()