import csv
import json
import os
from pathlib import Path


LOG_DIR = str(Path(__file__).resolve().parents[1] / "logs")
LOG_FILE = os.path.join(LOG_DIR, "paper_trades.csv")
DECISION_LOG_FILE = os.path.join(LOG_DIR, "decisions.jsonl")


FIELDNAMES = [
    "symbol",
    "quantity",
    "entry_price",
    "exit_price",
    "stop_loss",
    "pnl",
    "reason",
    "entry_time",
    "exit_time",
    "ai_decision",
    "ai_confidence",
    "ai_reason",
    "ema_9",
    "ema_20",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "volume",
    "volume_ma_20",
    "strategy_result",
    "strategy_reason",
    "risk_result",
]


def log_trade(trade):
    os.makedirs(LOG_DIR, exist_ok=True)

    file_exists = os.path.exists(LOG_FILE)

    with open(LOG_FILE, "a", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=FIELDNAMES,
            extrasaction="ignore",
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            field: trade.get(field)
            for field in FIELDNAMES
        })


def log_decision(market_data, result):
    """Append an auditable decision record without including credentials."""
    os.makedirs(LOG_DIR, exist_ok=True)
    record = {
        "market_data": {
            key: market_data.get(key)
            for key in (
                "symbol", "timestamp", "open", "high", "low", "close", "volume",
                "ema_9", "ema_20", "rsi_14", "macd", "macd_signal",
                "macd_histogram", "volume_ma_20", "stop_loss",
            )
        },
        "strategy_result": result.get("strategy"),
        "strategy_reason": result.get("reason"),
        "risk_result": result.get("risk"),
        "ai_result": result.get("ai"),
        "final_decision": result.get("decision"),
    }
    with open(DECISION_LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, default=str) + "\n")
