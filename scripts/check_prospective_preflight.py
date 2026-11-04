"""Initialize a NEW request policy or check it offline. Never makes API requests."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.prospective_preflight import (
    ProspectivePreflightError, initialize_request_policy, prospective_preflight,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true", help="Explicitly freeze a fresh experiment policy")
    parser.add_argument("--instrument-priority", nargs="+", required=True,
                        help="Exactly one stock for the frozen baseline runner; AI-led live batches unsupported")
    parser.add_argument("--paper-capital", type=float, required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--max-market-data-requests", type=int, required=True)
    parser.add_argument("--max-total-requests", type=int, required=True)
    parser.add_argument("--max-smoke-requests", type=int, default=0)
    parser.add_argument("--as-of", help="Timezone-aware OFFLINE check clock only; grants no request permission")
    args = parser.parse_args()
    now = args.as_of or datetime.now(timezone.utc)
    try:
        if args.initialize:
            initialize_request_policy(
                args.output, capital=args.paper_capital, instrument_priority=args.instrument_priority,
                model=args.model, market_data_cap=args.max_market_data_requests,
                provider_cap=args.max_total_requests, smoke_cap=args.max_smoke_requests, now=now)
        for kind, cap in (("market_data", args.max_market_data_requests),
                          ("provider", args.max_total_requests), ("smoke", args.max_smoke_requests)):
            result = prospective_preflight(
                output_dir=args.output, symbol=args.instrument_priority[0], now=now,
                kind=kind, maximum=cap, model=args.model, capital=args.paper_capital)
            if args.instrument_priority != result["policy"]["instrument_priority"]:
                raise ProspectivePreflightError("Requested priority differs from frozen policy")
    except ProspectivePreflightError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "api_requests": 0,
                          "trade_placement_enabled": False}))
        return 2
    print(json.dumps({"status": "PREFLIGHT_PASSED", "api_requests": 0,
                      "trade_placement_enabled": False,
                      "classification": "OFFLINE_PREFLIGHT_ONLY",
                      "calendar_version": result["policy"]["calendar_version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
