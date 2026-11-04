"""Reviewed read-only market/provider collection. Paper-only; network disabled by default."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.ai_led_reviewed import (
    _ReviewedRun, run_reviewed_paper, recover_reviewed_paper, run_reviewed_settlement,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Fresh isolated paper experiment")
    parser.add_argument("--instrument-priority", nargs="+", required=True)
    parser.add_argument("--paper-capital", type=float, required=True)
    parser.add_argument("--max-provider-requests", type=int, required=True)
    parser.add_argument("--max-market-data-requests", type=int, required=True)
    parser.add_argument("--enable-prospective-paper", action="store_true")
    parser.add_argument("--allow-market-data-requests", action="store_true")
    parser.add_argument("--allow-provider-requests", action="store_true")
    parser.add_argument("--include-quotes", action="store_true", help="Require/budget a fresh depth quote per stock")
    parser.add_argument("--preflight-only", action="store_true", help="Freeze/check policy without any requests")
    parser.add_argument("--signal-only", action="store_true", help="AI_SIGNAL_ONLY predictions; no account or fills")
    parser.add_argument("--enable-signal-only", action="store_true")
    purpose = parser.add_mutually_exclusive_group()
    purpose.add_argument("--recover-only", action="store_true", help="Verify/commit saved evidence; no network")
    purpose.add_argument("--settlement-only", action="store_true", help="Candles for an existing accepted position; no AI/new trade")
    parser.add_argument("--allow-settlement-market-data-requests", action="store_true")
    args = parser.parse_args()
    if args.enable_signal_only and not args.signal_only:
        parser.error("signal authorization requires --signal-only")
    if args.signal_only and (args.enable_prospective_paper or args.settlement_only or args.allow_settlement_market_data_requests):
        parser.error("signal-only cannot authorize paper entries or settlement")
    if args.settlement_only and (args.enable_prospective_paper or args.allow_market_data_requests or args.allow_provider_requests):
        parser.error("settlement-only cannot authorize new entries or provider requests")
    if args.recover_only and (args.allow_market_data_requests or args.allow_provider_requests):
        parser.error("recover-only cannot authorize network requests")
    if args.allow_settlement_market_data_requests and not args.settlement_only:
        parser.error("settlement request opt-in requires --settlement-only")
    if args.preflight_only and (args.recover_only or args.settlement_only):
        parser.error("preflight-only is separate from recovery/settlement execution")
    options = dict(output_dir=args.output, instrument_priority=args.instrument_priority, capital=args.paper_capital,
                   provider_cap=args.max_provider_requests, market_data_cap=args.max_market_data_requests,
                   enable_prospective_paper=args.enable_prospective_paper,
                   allow_market_data_requests=args.allow_market_data_requests,
                   allow_provider_requests=args.allow_provider_requests, include_quotes=args.include_quotes)
    try:
        if args.signal_only:
            from app.research.ai_signal_only import run_signal_only
            options.pop("enable_prospective_paper")
            result = run_signal_only(**options, enable_signal_only=args.enable_signal_only,
                recover_only=args.recover_only, preflight_only=args.preflight_only)
        elif args.settlement_only:
            for name in ("enable_prospective_paper", "allow_market_data_requests", "allow_provider_requests"):
                options.pop(name)
            result = run_reviewed_settlement(**options, enable_settlement_only=True,
                allow_settlement_market_data_requests=args.allow_settlement_market_data_requests)
        elif args.recover_only:
            result = recover_reviewed_paper(**options)
        elif args.preflight_only:
            from app.research.deepseek_prospective import _experiment_lock
            binding = _ReviewedRun(**options)
            with _experiment_lock(args.output):
                binding._preflight()
            result = {"status": "PREFLIGHT_ONLY", "external_api_requests": 0, "trade_placement_enabled": False}
        else:
            result = run_reviewed_paper(**options)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "Reviewed paper request blocked; details redacted",
                          "trade_placement_enabled": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
