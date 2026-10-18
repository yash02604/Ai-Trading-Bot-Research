import pandas as pd

from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates, is_frozen_reversal
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from app.candle_integrity import require_valid_candles


class Backtester:
    def __init__(self, candles):
        if not candles:
            raise ValueError("No candles supplied")

        self.df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
            ],
        )

        self.df, _ = require_valid_candles(self.df, key_columns=[])
        self.df = self.df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    def add_indicators(self):
        self.df = calculate_indicators(self.df)
        return self.df

    def validate(self):
        df = self.df

        invalid_ohlc = (
            (df["high"] < df["low"])
            | (df["high"] < df["open"])
            | (df["high"] < df["close"])
            | (df["low"] > df["open"])
            | (df["low"] > df["close"])
            | (df["open"] <= 0)
            | (df["high"] <= 0)
            | (df["low"] <= 0)
            | (df["close"] <= 0)
        )

        return {
            "candles": len(df),
            "start": df["timestamp"].iloc[0],
            "end": df["timestamp"].iloc[-1],
            "trading_days": df["timestamp"].dt.date.nunique(),
            "duplicate_timestamps": int(
                df["timestamp"].duplicated().sum()
            ),
            "invalid_ohlc_rows": int(
                invalid_ohlc.sum()
            ),
            "zero_volume_candles": int(
                (df["volume"] == 0).sum()
            ),
        }

    def analyze_forward_movement(
        self,
        candidates,
        horizon=6,
    ):
        """
        Measure price movement after each candidate.

        horizon:
            Number of candles after the candidate to inspect.

        Returns one row per candidate with:
        - entry price
        - price after the horizon
        - maximum favorable move
        - maximum adverse move
        """

        if candidates.empty:
            return pd.DataFrame()

        results = []

        for index in candidates.index:
            entry_price = self.df.loc[index, "close"]

            future = self.df.loc[
                index + 1:index + horizon
            ]

            if future.empty:
                continue

            future_close = future["close"].iloc[-1]

            future_high = future["high"].max()
            future_low = future["low"].min()

            return_pct = (
                (future_close - entry_price)
                / entry_price
            ) * 100

            max_favorable_pct = (
                (future_high - entry_price)
                / entry_price
            ) * 100

            max_adverse_pct = (
                (future_low - entry_price)
                / entry_price
            ) * 100

            results.append(
                {
                    "timestamp": self.df.loc[
                        index, "timestamp"
                    ],
                    "entry_price": entry_price,
                    "future_close": future_close,
                    "return_pct": return_pct,
                    "max_favorable_pct": max_favorable_pct,
                    "max_adverse_pct": max_adverse_pct,
                }
            )

        return pd.DataFrame(results)

    def summary(self):
        return {
            "candles": len(self.df),
            "start": self.df["timestamp"].iloc[0],
            "end": self.df["timestamp"].iloc[-1],
            "first_close": self.df["close"].iloc[0],
            "last_close": self.df["close"].iloc[-1],
        }

    def run_reversal_backtest(self, config=None, stop_pct=1.0, horizon=6):
        """Run the frozen long reversal setup with next-bar-open fills.

        Signals use only completed candle data. Stop exits use subsequent lows,
        with gap-through stops filled at the candle open. Positions are closed
        within the signal's trading session; no overnight exposure is modeled.
        """
        if not 0 < stop_pct < 100 or horizon < 1:
            raise ValueError("stop_pct must be in (0, 100) and horizon positive")
        integrity = self.validate()
        if integrity["invalid_ohlc_rows"]:
            raise ValueError(f"Historical data contains {integrity['invalid_ohlc_rows']} invalid OHLC rows")
        if integrity["duplicate_timestamps"]:
            raise ValueError("Historical data contains duplicate timestamps")
        config = config or RiskConfig()
        df = calculate_indicators(self.df.copy())
        df = detect_reversal_candidates(df, decline_candles=3, min_decline_pct=1.0, rsi_threshold=25.0)
        capital = config.capital
        trades = []
        daily_trade_counts = {}
        daily_losses = {}
        equity_curve = [capital]
        idx = 0
        while idx + 1 < len(df):
            row = df.iloc[idx]
            if not is_frozen_reversal(row):
                idx += 1
                continue
            signal_day = row["timestamp"].date()
            entry_idx = idx + 1
            signal_window = df.loc[max(0, idx - 3):idx, "timestamp"]
            contiguous_signal = (
                len(signal_window) == 4
                and all(
                    right.date() != left.date() or right - left == pd.Timedelta(minutes=5)
                    for left, right in zip(signal_window.tolist(), signal_window.tolist()[1:])
                )
            )
            if (
                not contiguous_signal
                or df.iloc[entry_idx]["timestamp"].date() != signal_day
                or df.iloc[entry_idx]["timestamp"] - row["timestamp"] != pd.Timedelta(minutes=5)
            ):
                idx += 1
                continue
            state = RiskState(
                trades_today=daily_trade_counts.get(signal_day, 0),
                daily_loss=daily_losses.get(signal_day, 0.0),
            )
            allowed, _ = check_trade_allowed(config, state)
            if not allowed:
                idx += 1
                continue
            entry = float(df.iloc[entry_idx]["open"])
            stop = entry * (1 - stop_pct / 100)
            sizing_config = RiskConfig(
                capital=max(0.0 + capital, 0.01),
                max_risk_per_trade=config.max_risk_per_trade,
                max_daily_loss=config.max_daily_loss,
                max_positions=config.max_positions,
                max_trades_per_day=config.max_trades_per_day,
            )
            size = calculate_position_size(sizing_config, entry, stop)
            if size.quantity <= 0:
                idx += 1
                continue
            last_idx = min(entry_idx + horizon - 1, len(df) - 1)
            exit_idx = entry_idx
            exit_price = None
            reason = "horizon_exit"
            session_ended = False
            for future_idx in range(entry_idx, last_idx + 1):
                candle = df.iloc[future_idx]
                if candle["timestamp"].date() != signal_day:
                    session_ended = True
                    break
                if float(candle["low"]) <= stop:
                    exit_idx = future_idx
                    exit_price = min(float(candle["open"]), stop)
                    reason = "gap_exit" if float(candle["open"]) < stop else "stop_exit"
                    break
                exit_idx = future_idx
            if exit_price is None:
                exit_price = float(df.iloc[exit_idx]["close"])
                if session_ended:
                    reason = "session_exit"
                elif entry_idx + horizon - 1 >= len(df):
                    reason = "data_end_exit"
                else:
                    reason = "horizon_exit"
            pnl = (exit_price - entry) * size.quantity
            trades.append({
                "signal_timestamp": row["timestamp"],
                "entry_timestamp": df.iloc[entry_idx]["timestamp"],
                "exit_timestamp": df.iloc[exit_idx]["timestamp"],
                "entry_price": entry,
                "exit_price": exit_price,
                "stop_loss": stop,
                "quantity": size.quantity,
                "planned_risk": size.planned_risk,
                "pnl": pnl,
                "r_multiple": pnl / size.planned_risk if size.planned_risk else 0.0,
                "exit_reason": reason,
            })
            capital += pnl
            equity_curve.append(capital)
            daily_trade_counts[signal_day] = daily_trade_counts.get(signal_day, 0) + 1
            if pnl < 0:
                daily_losses[signal_day] = daily_losses.get(signal_day, 0.0) + abs(pnl)
            idx = exit_idx + 1
        trade_df = pd.DataFrame(trades)
        if trade_df.empty:
            gross_profit = gross_loss = net_pnl = win_rate = avg_trade = max_drawdown = 0.0
            profit_factor = None
        else:
            gross_profit = float(trade_df.loc[trade_df.pnl > 0, "pnl"].sum())
            gross_loss = float(-trade_df.loc[trade_df.pnl < 0, "pnl"].sum())
            net_pnl = float(trade_df.pnl.sum())
            win_rate = float((trade_df.pnl > 0).mean())
            avg_trade = float(trade_df.pnl.mean())
            curve = pd.Series(equity_curve, dtype=float)
            max_drawdown = float((curve / curve.cummax() - 1).min())
            profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else None)
        return {
            "trades": trade_df,
            "total_trades": len(trade_df),
            "winning_trades": int((trade_df.pnl > 0).sum()) if not trade_df.empty else 0,
            "losing_trades": int((trade_df.pnl < 0).sum()) if not trade_df.empty else 0,
            "win_rate": win_rate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_pnl": net_pnl,
            "ending_capital": capital,
            "max_drawdown": max_drawdown,
            "average_trade": avg_trade,
            "average_r_multiple": float(trade_df.r_multiple.mean()) if not trade_df.empty else 0.0,
            "profit_factor": profit_factor,
        }


if __name__ == "__main__":
    print("Backtester module loaded successfully.")
