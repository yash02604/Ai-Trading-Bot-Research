"""Run one complete saved-data batch through the isolated AI-led paper account."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.ai_led_offline import SavedResponseProvider, run_offline_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, action="append", required=True)
    parser.add_argument("--instrument-priority", nargs="+", required=True)
    parser.add_argument("--paper-capital", type=float, required=True)
    parser.add_argument("--response", type=Path, required=True, help="Saved AI JSON; no provider calls")
    parser.add_argument("--as-of", required=True, help="Timezone-aware offline simulation clock")
    parser.add_argument("--output", type=Path, required=True, help="New isolated AI-led directory")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--min-recent-turnover", type=float, default=100000.0)
    args = parser.parse_args()
    provider = SavedResponseProvider(args.response.read_text(encoding="utf-8"), args.model)
    result = run_offline_batch(args.snapshot, output_dir=args.output,
                               instrument_priority=args.instrument_priority,
                               capital=args.paper_capital, now=args.as_of, provider=provider,
                               model=args.model, min_recent_turnover=args.min_recent_turnover)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
