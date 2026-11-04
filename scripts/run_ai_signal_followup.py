"""Offline signal outcome replay; explicitly authorized candle capture is optional."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.signal_observations import run_followup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instrument-priority", nargs="+", required=True)
    parser.add_argument("--paper-capital", type=float, required=True)
    parser.add_argument("--max-provider-requests", type=int, required=True)
    parser.add_argument("--max-market-data-requests", type=int, required=True)
    parser.add_argument("--enable-signal-only", action="store_true")
    parser.add_argument("--capture-future", action="store_true")
    parser.add_argument("--enable-signal-future", action="store_true")
    parser.add_argument("--allow-market-data-requests", action="store_true")
    parser.add_argument("--as-of", help="Explicit aware offline evaluation cutoff")
    args = parser.parse_args()
    if not args.capture_future and args.as_of is None:
        parser.error("offline replay requires an explicit --as-of")
    try:
        result = run_followup(output_dir=args.output, instrument_priority=args.instrument_priority,
            capital=args.paper_capital, provider_cap=args.max_provider_requests,
            market_data_cap=args.max_market_data_requests, enable_signal_only=args.enable_signal_only,
            capture_future=args.capture_future, enable_signal_future=args.enable_signal_future,
            allow_market_data_requests=args.allow_market_data_requests, as_of=args.as_of)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "Signal follow-up evidence or authorization invalid; details redacted",
                          "trade_placement_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
