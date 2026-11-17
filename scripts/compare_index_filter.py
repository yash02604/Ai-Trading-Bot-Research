"""Compare a zero-down-index filter on closed trades at their recorded sizes and costs."""
import argparse
import csv
import json
import math
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
INDICES = {"NIFTY50", "NIFTY_BANK", "NIFTY_IT", "SENSEX"}


def read_stage9(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            yield (row["symbol"], row["signal_timestamp"], int(row["index_down_count"]),
                   float(row["gross_pnl"]), float(row["total_cost"]), float(row["net_pnl"]))


def read_paper(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        for (payload,) in db.execute("SELECT payload FROM trades"):
            row = json.loads(payload)
            context = row.get("context")
            if (not isinstance(context, dict) or set(context) != INDICES
                    or any(value not in ("up", "down", "flat") for value in context.values())):
                raise ValueError("Closed paper trade lacks four valid signal-time index directions")
            yield (row["symbol"], row["signal_time"], list(context.values()).count("down"),
                   row["gross_pnl"], row["estimated_costs"], row["net_pnl"])
    finally:
        db.close()


def compare(trades):
    baseline, candidate = [], []
    seen = set()
    for symbol, stamp, down, gross, costs, net in trades:
        if (not symbol or not stamp or (symbol, stamp) in seen or not 0 <= down <= 2
                or not all(math.isfinite(x) for x in (gross, costs, net))
                or costs < 0 or abs(gross - costs - net) > 0.011):
            raise ValueError("Invalid or duplicate baseline trade")
        seen.add((symbol, stamp))
        baseline.append((gross, costs, net))
        if down == 0:
            candidate.append((gross, costs, net))

    def totals(rows):
        net = math.fsum(row[2] for row in rows)
        return {"trades": len(rows), "wins_after_costs": sum(row[2] > 0 for row in rows),
                "gross_inr": round(math.fsum(row[0] for row in rows), 2),
                "costs_inr": round(math.fsum(row[1] for row in rows), 2),
                "net_inr": round(net, 2), "net_per_trade_inr": round(net / len(rows), 2) if rows else None}

    return {"method": "Subset of baseline closed trades at recorded sizes; excludes replacement entries.",
            "baseline": totals(baseline), "zero_down_indices": totals(candidate)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ledger", type=Path, default=ROOT / "backtests/stage9_candidate/trades.csv")
    source.add_argument("--paper-db", type=Path, help="Read closed V1 paper trades from SQLite")
    args = parser.parse_args()
    rows = read_paper(args.paper_db) if args.paper_db else read_stage9(args.ledger)
    print(json.dumps(compare(rows), indent=2, allow_nan=False))
