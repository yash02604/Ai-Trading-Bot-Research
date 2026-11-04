"""Run offline safety checks or explicitly opt-in prospective paper research.

This runner accepts timestamped snapshot JSON files and has no broker order path.
It never invokes the historical-live evaluator or changes its provenance gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.deepseek_prospective import (
    capture_upstox_snapshot, ensure_prospective_experiment_capital,
    offline_synthetic_check, record_prospective_snapshot, run_provider_smoke_test,
)
from app.research.prospective_preflight import ProspectivePreflightError, prospective_preflight

DEFAULT_OUTPUT = ROOT / "backtests" / "deepseek_prospective_research_v1"


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline-test", "prospective-paper",
                                            "provider-smoke-test"), required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--snapshot", type=Path,
                              help="Previously captured timestamped JSON snapshot")
    source_group.add_argument("--fetch-upstox", action="store_true",
                              help="Fetch one current snapshot through the read-only Upstox V3 client")
    parser.add_argument("--symbol", help="Configured stock symbol used with --fetch-upstox")
    parser.add_argument("--allow-market-data-requests", action="store_true",
                        help="Explicitly allow one Upstox read-only snapshot request")
    parser.add_argument("--max-market-data-requests", type=int, default=20,
                        help="Persistent lifetime Upstox request cap for this output directory")
    parser.add_argument("--enable-prospective", action="store_true",
                        help="Explicitly opt in to recording a prospective observation")
    parser.add_argument("--enable-provider-smoke-test", action="store_true",
                        help="Explicitly opt in to a non-trading provider connectivity/JSON test")
    parser.add_argument("--allow-provider-requests", action="store_true",
                        help="Explicitly allow DeepSeek requests for eligible prospective candidates")
    parser.add_argument("--max-total-requests", type=int, default=20,
                        help="Persistent lifetime request cap for this output directory")
    parser.add_argument("--paper-capital", type=float, default=None,
                        help="Initial virtual capital; must match the output directory's experiment")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    report_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.mode == "offline-test":
        if (args.snapshot or args.fetch_upstox or args.symbol or args.enable_prospective
                or args.enable_provider_smoke_test or args.allow_provider_requests
                or args.allow_market_data_requests):
            parser.error("offline-test does not accept snapshots or provider opt-in flags")
        report = offline_synthetic_check()
        report_path = args.output / f"offline_synthetic_check_{report_stamp}.json"
    elif args.mode == "provider-smoke-test":
        if not args.enable_provider_smoke_test:
            parser.error("provider-smoke-test requires --enable-provider-smoke-test")
        if not args.allow_provider_requests:
            parser.error("provider-smoke-test requires --allow-provider-requests")
        if args.snapshot is None:
            parser.error("provider-smoke-test requires a user-supplied --snapshot")
        if (args.fetch_upstox or args.allow_market_data_requests or args.symbol
                or args.enable_prospective):
            parser.error("provider-smoke-test accepts a snapshot only; it cannot capture market data or run strategy evaluation")
        from app.config import get_settings
        settings = get_settings()
        if not settings.paper_trading:
            print(json.dumps({"status": "BLOCKED",
                              "reason": "PAPER_TRADING must be enabled for provider smoke testing",
                              "trade_placement_enabled": False}, sort_keys=True))
            return 2
        if not settings.deepseek_api_key:
            print(json.dumps({"status": "BLOCKED", "reason": "DEEPSEEK_API_KEY is not configured",
                              "trade_placement_enabled": False}, sort_keys=True))
            return 2
        from app.v2.provider import DeepSeekProvider
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key,
                                    model_id=settings.deepseek_model,
                                    timeout_seconds=settings.request_timeout_seconds)
        report = run_provider_smoke_test(
            args.snapshot, output_dir=args.output, provider=provider,
            model=settings.deepseek_model, api_key=settings.deepseek_api_key,
            max_total_requests=args.max_total_requests,
            now=datetime.now(timezone.utc), allow_provider_requests=True,
        )
        report["mode"] = "PROVIDER_SMOKE_TEST"
        report_path = args.output / f"provider_smoke_run_{report_stamp}.json"
    else:
        if args.enable_provider_smoke_test:
            parser.error("--enable-provider-smoke-test is only valid with --mode provider-smoke-test")
        if not args.enable_prospective:
            parser.error("prospective-paper requires --enable-prospective")
        if not args.allow_provider_requests:
            parser.error("prospective-paper requires --allow-provider-requests")
        if args.snapshot is None and not args.fetch_upstox:
            parser.error("prospective-paper requires --snapshot or --fetch-upstox")
        if args.fetch_upstox and (not args.allow_market_data_requests or not args.symbol):
            parser.error("--fetch-upstox requires --symbol and --allow-market-data-requests")
        if args.allow_market_data_requests and not args.fetch_upstox:
            parser.error("--allow-market-data-requests requires --fetch-upstox")
        if args.paper_capital is None:
            parser.error("prospective-paper requires an explicit --paper-capital value")
        if not math.isfinite(args.paper_capital) or args.paper_capital <= 0:
            parser.error("prospective-paper capital must be finite and positive")
        from app.config import get_settings
        settings = get_settings()
        if not settings.paper_trading:
            print(json.dumps({"status": "BLOCKED",
                              "reason": "PAPER_TRADING must be enabled for prospective paper research",
                              "trade_placement_enabled": False}, sort_keys=True))
            return 2
        if not settings.deepseek_api_key:
            print(json.dumps({"status": "BLOCKED", "reason": "DEEPSEEK_API_KEY is not configured"}))
            return 2
        try:
            ensure_prospective_experiment_capital(
                args.output, args.paper_capital, model=settings.deepseek_model)
        except ValueError as exc:
            print(json.dumps({"status": "BLOCKED", "reason": str(exc),
                              "trade_placement_enabled": False}, sort_keys=True))
            return 2
        snapshot_path = args.snapshot
        if args.fetch_upstox:
            try:
                # Validate the downstream provider configuration BEFORE spending
                # a market-data reservation for this combined CLI operation.
                prospective_preflight(
                    output_dir=args.output, symbol=args.symbol.upper(),
                    now=datetime.now(timezone.utc), kind="provider",
                    maximum=args.max_total_requests, model=settings.deepseek_model,
                    capital=args.paper_capital)
                snapshot_path = capture_upstox_snapshot(
                    args.symbol, output_dir=args.output,
                    max_market_data_requests=args.max_market_data_requests,
                    allow_market_data_requests=args.allow_market_data_requests,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                print(json.dumps({"status": "BLOCKED", "reason": str(exc),
                                  "trade_placement_enabled": False}, sort_keys=True))
                return 2
        from app.v2.provider import DeepSeekProvider
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key,
                                    model_id=settings.deepseek_model,
                                    timeout_seconds=settings.request_timeout_seconds)
        report = record_prospective_snapshot(
            snapshot_path, output_dir=args.output, provider=provider,
            model=settings.deepseek_model, api_key=settings.deepseek_api_key,
            max_total_requests=args.max_total_requests, paper_capital=args.paper_capital,
            now=datetime.now(timezone.utc), allow_provider_requests=True,
        )
        report["mode"] = "PROSPECTIVE_PAPER"
        report["model_id"] = settings.deepseek_model
        report["prospective_observation"] = True
        report["historical_backtest"] = False
        report["snapshot_path"] = str(snapshot_path)
        report_path = args.output / f"prospective_run_{report_stamp}.json"

    with report_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "classification": report["classification"],
                      "report": str(report_path), "api_requests": report.get("api_requests",
                      int(report.get("provider_request_attempted",
                                     report.get("request_attempted", False)))),
                      "provider_request_attempted": report.get("provider_request_attempted",
                                                               report.get("request_attempted", False)),
                      "provider_skip_reason": report.get("provider_skip_reason"),
                      "trade_placement_enabled": report["trade_placement_enabled"]}, sort_keys=True))
    return 0 if report["status"] == "COMPLETED" else 2


def main() -> int:
    try:
        return _main()
    except ProspectivePreflightError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc),
                          "trade_placement_enabled": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
