import csv
import json
import os
import unicodedata
from io import StringIO
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


def sanitize_csv_cell_for_spreadsheet(value):
    """Escape formula-like text only in the export; preserve numeric values."""
    if isinstance(value, str):
        for char in value:
            # Viewers can ignore leading whitespace, controls and format marks.
            if char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}:
                continue
            if char in "=+-@":
                return "'" + value
            break
    return value


def log_trade(trade):
    # Serialize strictly before opening the destination: encoding failure must
    # not leave even a header or partial row behind.
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    header = output.getvalue().encode("utf-8")
    output.seek(0)
    output.truncate(0)
    writer.writerow({field: sanitize_csv_cell_for_spreadsheet(trade.get(field))
                     for field in FIELDNAMES})
    row = output.getvalue().encode("utf-8")

    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_FILE, "a+b") as file:
        file.seek(0)
        existing = file.read()
        if existing:
            try:
                text = existing.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError("CSV append refused: existing file is not UTF-8 compatible; "
                                 "explicit encoding review is required") from error
            if next(csv.reader(StringIO(text, newline="")), None) != FIELDNAMES:
                raise ValueError("CSV append refused: unrecognized legacy header")
            if not existing.endswith((b"\r", b"\n")):
                raise ValueError("CSV append refused: incomplete final record boundary")
        file.write(row if existing else header + row)


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
