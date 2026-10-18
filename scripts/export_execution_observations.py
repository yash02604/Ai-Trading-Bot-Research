"""Read-only export and summary for persisted paper quote observations."""

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import statistics

from app.paper_store import EXECUTION_OBSERVATION_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "logs" / "paper_state.sqlite3"
DEFAULT_OUTPUT = PROJECT_ROOT / "backtests" / "execution_cost_review" / "observations"
REQUIRED_EXPORT_COLUMNS = frozenset({
    "observation_id", "signal_id", "observed_at", "provider_timestamp_kind",
    "source_timestamp_status", "symbol", "signal_timestamp", "spread_available",
    "spread_reason", "quote_valid", "quote_reason", "paper_entry_eligible",
    "eligibility_reason",
})


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.999999)))
    return ordered[index]


def export_and_summarize(db_path=DEFAULT_DB, output_dir=DEFAULT_OUTPUT):
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Observation database does not exist: {db_path}")
    uri = db_path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_observations'"
        ).fetchone()
        if exists:
            available = {row[1] for row in connection.execute(
                "PRAGMA table_info(execution_observations)"
            )}
            missing_required = sorted(REQUIRED_EXPORT_COLUMNS - available)
            if missing_required:
                raise ValueError(
                    "Incompatible execution_observations schema; missing required columns: "
                    f"{', '.join(missing_required)}. Export requires a compatible schema; "
                    "the exporter will not migrate or modify the database."
                )
            selected = ",".join(
                column if column in available else f"NULL AS {column}"
                for column in EXECUTION_OBSERVATION_COLUMNS
            )
            rows = [dict(row) for row in connection.execute(
                f"SELECT {selected} FROM execution_observations ORDER BY observed_at, observation_id"
            )]
        else:
            rows = []
    finally:
        connection.close()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "execution_observations.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXECUTION_OBSERVATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    quote_ages = [float(row["quote_age_ms"]) for row in rows if row["quote_age_ms"] is not None]
    spreads = [float(row["spread_bps"]) for row in rows if row["spread_available"] and row["spread_bps"] is not None]
    bid_depth = [int(row["bid_quantity"]) for row in rows if row["bid_quantity"] is not None]
    ask_depth = [int(row["ask_quantity"]) for row in rows if row["ask_quantity"] is not None]
    ask_reference_bps = [float(row["ask_vs_reference_bps"]) for row in rows
                         if row["ask_vs_reference_bps"] is not None]
    quantity_comparable = [row for row in rows
                           if row["required_quantity"] is not None and row["ask_quantity"] is not None]
    quote_valid = sum(bool(row["quote_valid"]) for row in rows)
    spread_count = sum(bool(row["spread_available"]) for row in rows)
    dates = [row["observed_at"] for row in rows]
    summary = {
        "observations": len(rows),
        "unique_signals": len({row["signal_id"] for row in rows}),
        "collection_start": min(dates) if dates else None,
        "collection_end": max(dates) if dates else None,
        "valid_quotes": quote_valid,
        "valid_quote_rate": quote_valid / len(rows) if rows else None,
        "missing_quotes": len(rows) - quote_valid,
        "missing_quote_rate": (len(rows) - quote_valid) / len(rows) if rows else None,
        "quotes_with_spread": spread_count,
        "spread_available_rate": spread_count / len(rows) if rows else None,
        "provider_response_age_ms": {
            "median": statistics.median(quote_ages) if quote_ages else None,
            "p95": _percentile(quote_ages, 0.95),
        },
        "spread_bps": {
            "median": statistics.median(spreads) if spreads else None,
            "p95": _percentile(spreads, 0.95),
        },
        "bid_depth_missing": sum(row["bid_quantity"] is None for row in rows),
        "ask_depth_missing": sum(row["ask_quantity"] is None for row in rows),
        "bid_quantity": {
            "median": statistics.median(bid_depth) if bid_depth else None,
            "p95": _percentile(bid_depth, 0.95),
        },
        "ask_quantity": {
            "median": statistics.median(ask_depth) if ask_depth else None,
            "p95": _percentile(ask_depth, 0.95),
        },
        "ask_vs_reference_bps": {
            "median": statistics.median(ask_reference_bps) if ask_reference_bps else None,
            "p95": _percentile(ask_reference_bps, 0.95),
        },
        "ask_quantity_sufficiency": {
            "comparable_observations": len(quantity_comparable),
            "sufficient": sum(row["ask_quantity"] >= row["required_quantity"]
                               for row in quantity_comparable),
            "insufficient": sum(row["ask_quantity"] < row["required_quantity"]
                                 for row in quantity_comparable),
        },
        "paper_entry_eligible": sum(bool(row["paper_entry_eligible"]) for row in rows),
        "paper_entry_eligibility_rate": (
            sum(bool(row["paper_entry_eligible"]) for row in rows) / len(rows) if rows else None
        ),
        "source_quote_timestamp_available": sum(row["source_quote_timestamp"] is not None for row in rows),
        "quote_reasons": {},
        "eligibility_reasons": {},
        "timestamp_note": "quote_age_ms uses Upstox's response-generated timestamp; source order-book age is not exposed by this endpoint.",
    }
    for column, target in (("quote_reason", "quote_reasons"), ("eligibility_reason", "eligibility_reasons")):
        counts = {}
        for row in rows:
            counts[row[column]] = counts.get(row[column], 0) + 1
        summary[target] = counts
    (output_dir / "observation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8",
    )
    _write_markdown(summary, output_dir / "observation_summary.md")
    return summary


def _write_markdown(summary, path):
    spread_rate = (f"{summary['spread_available_rate']:.2%}"
                   if summary["spread_available_rate"] is not None else "n/a")
    lines = [
        "# Paper Quote Observation Summary", "",
        f"Observations: {summary['observations']}; unique signals: {summary['unique_signals']}.",
        f"Collection period: {summary['collection_start'] or 'none'} to {summary['collection_end'] or 'none'}.",
        f"Valid quote rate: {summary['valid_quote_rate']:.2%}; missing quote rate: {summary['missing_quote_rate']:.2%}." if summary['observations'] else "Valid/missing quote rates: n/a (no observations).",
        f"Spread available: {summary['quotes_with_spread']} observations ({spread_rate}); median spread: {summary['spread_bps']['median'] if summary['spread_bps']['median'] is not None else 'n/a'} bps.",
        f"Median/p95 provider-response age: {summary['provider_response_age_ms']['median'] if summary['provider_response_age_ms']['median'] is not None else 'n/a'} / {summary['provider_response_age_ms']['p95'] if summary['provider_response_age_ms']['p95'] is not None else 'n/a'} ms.",
        f"Median bid/ask depth: {summary['bid_quantity']['median'] if summary['bid_quantity']['median'] is not None else 'n/a'} / {summary['ask_quantity']['median'] if summary['ask_quantity']['median'] is not None else 'n/a'} shares.",
        f"Median ask-minus-reference difference: {summary['ask_vs_reference_bps']['median'] if summary['ask_vs_reference_bps']['median'] is not None else 'n/a'} bps; quantity sufficient/insufficient: {summary['ask_quantity_sufficiency']['sufficient']} / {summary['ask_quantity_sufficiency']['insufficient']} of {summary['ask_quantity_sufficiency']['comparable_observations']} comparable observations.",
        f"Paper-entry eligible: {summary['paper_entry_eligible']}; independent source quote timestamps: {summary['source_quote_timestamp_available']}.",
        "", summary["timestamp_note"],
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = export_and_summarize(args.db, args.output)
    print(f"Exported {summary['observations']} observations to {args.output}")


if __name__ == "__main__":
    main()
