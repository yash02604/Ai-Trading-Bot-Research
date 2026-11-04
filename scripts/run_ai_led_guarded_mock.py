"""Offline-only guarded multi-stock paper adapter; no live provider or broker mode."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.ai_led_guarded import (
    MockDecisionProvider, MockMarketDataClient, capture_mock_batch, run_mock_batch,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-statuses", type=Path, required=True,
                        help="JSON mapping each configured stock to an explicit READY/snapshot or failed status")
    parser.add_argument("--mock-response", type=Path, required=True)
    parser.add_argument("--instrument-priority", nargs="+", required=True)
    parser.add_argument("--paper-capital", type=float, required=True)
    parser.add_argument("--as-of", required=True, help="Explicit timezone-aware offline clock")
    parser.add_argument("--output", type=Path, required=True, help="New isolated experiment, never Session 02-05")
    parser.add_argument("--max-provider-requests", type=int, required=True, help="Simulated request cap")
    parser.add_argument("--max-market-data-requests", type=int, default=0, help="Simulated capture cap")
    parser.add_argument("--mock-capture", action="store_true", help="Simulate per-stock capture from local files")
    parser.add_argument("--candidate-timestamp", help="Required with --mock-capture")
    args = parser.parse_args()
    if args.mock_capture and not args.candidate_timestamp:
        parser.error("--mock-capture requires --candidate-timestamp")
    try:
        statuses = json.loads(args.batch_statuses.read_bytes())
        for item in statuses.values():
            if "snapshot" in item:
                item["snapshot"] = args.batch_statuses.parent / item["snapshot"]
        provider = MockDecisionProvider(args.mock_response.read_text(encoding="utf-8"))
        options = dict(output_dir=args.output, instrument_priority=args.instrument_priority,
                       capital=args.paper_capital, now=args.as_of, provider=provider,
                       provider_cap=args.max_provider_requests, market_data_cap=args.max_market_data_requests)
        if args.mock_capture:
            if set(statuses) != set(args.instrument_priority):
                raise ValueError()
            client = MockMarketDataClient({s: v["snapshot"] for s, v in statuses.items() if v["status"] == "READY"},
                                          failures=[s for s, v in statuses.items() if v["status"] != "READY"])
            result = capture_mock_batch(client, candidate_timestamp=args.candidate_timestamp, **options)
        else:
            result = run_mock_batch(statuses, **options)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "Guarded mock adapter failed; details redacted",
                          "external_api_requests": 0, "trade_placement_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
