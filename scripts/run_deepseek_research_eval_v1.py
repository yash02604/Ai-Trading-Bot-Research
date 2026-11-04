"""Run the DeepSeek historical research evaluation (v1), paper-only, opt-in live.

Historical-live mode is blocked unless the provenance-integrity audit reports
the dataset VERIFIED; offline-synthetic mode runs a deterministic comparison
with an injected mock provider and never touches the network.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.deepseek_eval_v1 import PROMPT_VERSION, run_evaluation
from app.v2.provider import ProviderResult

PROVENANCE_SUMMARY = ROOT / "backtests/provenance_integrity_v1/summary.json"
DEFAULT_OUTPUT = ROOT / "backtests/deepseek_research_eval_v1"


def _normalize_mode(value: str) -> str:
    """Accept CLI-friendly hyphens and evaluator enum-style underscores."""
    normalized = value.strip().replace("-", "_").upper()
    if normalized not in {"HISTORICAL_LIVE", "OFFLINE_SYNTHETIC"}:
        raise argparse.ArgumentTypeError("mode must be historical-live or offline-synthetic")
    return normalized


def _write_synthetic_candles(path: Path) -> Path:
    """Create a fixed, local-only fixture; this path never reads historical data."""
    import pandas as pd

    # Warm up all indicators, establish low RSI, then create one frozen 1–2%
    # three-bar decline followed by a turn-up and enough bars to simulate exit.
    closes = [100.0] * 24
    for _ in range(9):
        closes.append(closes[-1] - 0.6)
    for _ in range(2):
        closes.append(closes[-1] + 0.6)
    for _ in range(3):
        closes.append(closes[-1] - 0.6)
    closes.append(closes[-1] + 0.2)
    closes.extend(closes[-1] + 0.25 * index for index in range(1, 24))
    timestamp = datetime.fromisoformat("2025-02-03T09:15:00+05:30")
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        opening = previous if index else close
        rows.append({
            "stock": "SYNTH", "timestamp": (timestamp + timedelta(minutes=5 * index)).isoformat(),
            "open": opening, "high": max(opening, close) + 0.05,
            "low": min(opening, close) - 0.05, "close": close, "volume": 1000,
        })
        previous = close
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class _OfflineProvider:
    model_id = "deterministic-offline-fixture-v1"
    is_mock = True

    def complete(self, _payload):
        return ProviderResult(
            content=json.dumps({
                "decision": "BUY", "confidence": 1.0,
                "reason": "Deterministic offline fixture; not a historical model response.",
            }),
            provider_id="offline-fixture", model_id=self.model_id,
            requested_model_id=self.model_id, latency_ms=0.0,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", type=_normalize_mode,
                        default="HISTORICAL_LIVE")
    parser.add_argument("--candles", type=Path, default=None,
                        help="Historical candle CSV (required only in historical-live mode)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-live", action="store_true",
                        help="Explicitly enable live provider calls (historical-live mode only)")
    parser.add_argument("--max-requests", type=int, default=200,
                        help="Hard cap on live provider requests; exceeding fails the run closed")
    parser.add_argument("--holdout-start", type=str, default=None,
                        help="ISO timestamp starting the untouched holdout partition")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    args = parser.parse_args()

    settings = get_settings()
    mode = args.mode
    provider = None
    candles_path = args.candles
    if mode == "OFFLINE_SYNTHETIC":
        if args.allow_live:
            parser.error("--allow-live cannot be used with offline-synthetic mode")
        if candles_path is not None:
            parser.error("offline-synthetic creates its own fixture; do not pass --candles")
        args.output.mkdir(parents=True, exist_ok=True)
        candles_path = _write_synthetic_candles(args.output / "synthetic_candles.csv")
        provider = _OfflineProvider()
    elif candles_path is None:
        parser.error("--candles is required in historical-live mode")
    elif args.allow_live:
        if not settings.deepseek_api_key:
            print(json.dumps({"status": "BLOCKED",
                              "blockers": ["DEEPSEEK_API_KEY is not configured"]}))
            return 2
        from app.v2.provider import DeepSeekProvider
        provider = DeepSeekProvider(api_key=settings.deepseek_api_key,
                                    model_id=settings.deepseek_model,
                                    timeout_seconds=settings.request_timeout_seconds)

    result = run_evaluation(
        provider, mode=mode, candles_path=candles_path,
        provenance_summary=PROVENANCE_SUMMARY, output_dir=args.output,
        model=settings.deepseek_model if mode == "HISTORICAL_LIVE" else provider.model_id,
        initial_capital=args.initial_capital, max_requests=args.max_requests,
        allow_live=args.allow_live, holdout_start=args.holdout_start,
    )
    report = result.to_report()
    report["prompt_version"] = PROMPT_VERSION
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "evaluation_report.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"],
                      "results_classification": report["results_classification"],
                      "report": str(report_path),
                      "requests_made": report["request_count"]}, sort_keys=True))
    return 0 if report["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
