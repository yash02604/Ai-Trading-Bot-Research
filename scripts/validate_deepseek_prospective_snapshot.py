"""Read-only snapshot preflight; performs no market-data or provider requests."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.deepseek_prospective import _load_snapshot, _quant_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--paper-capital", type=float, default=1_000.0)
    args = parser.parse_args()
    try:
        snapshot, raw, frame, observed_at, decision_time = _load_snapshot(
            args.snapshot, now=datetime.now(timezone.utc), max_age_seconds=900)
        baseline = _quant_baseline(frame, capital=args.paper_capital,
                                   has_open_paper_trade=False)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "reason": str(exc)}, sort_keys=True))
        return 2

    age_seconds = (decision_time - observed_at).total_seconds()
    candle_age_seconds = (observed_at - frame["timestamp"].iloc[-1]).total_seconds()
    report = {
        "status": "READY" if baseline["eligible"] else "BLOCKED",
        "provider_requests": 0,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "source_label": snapshot["source"],
        "symbol": snapshot["symbol"].upper(),
        "instrument_key": snapshot["instrument_key"],
        "observed_at": observed_at.isoformat(),
        "decision_time": decision_time.isoformat(),
        "snapshot_age_seconds": age_seconds,
        "latest_candle_timestamp": frame["timestamp"].iloc[-1].isoformat(),
        "latest_candle_age_seconds": candle_age_seconds,
        "candle_count": len(frame),
        "baseline": baseline,
        "classification": "PROSPECTIVE_PREFLIGHT_ONLY",
        "evidence_note": "Snapshot source label is operator-supplied and is not authenticated by this local validator.",
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if baseline["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
