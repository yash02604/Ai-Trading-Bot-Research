"""Replay saved batches or create explicitly synthetic execution-validation data."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.ai_led_replay import create_synthetic_demo, replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path)
    mode.add_argument("--synthetic-demo", type=Path, help="New directory for artificial fixtures")
    parser.add_argument("--paper-capital", type=float, help="Required only for a synthetic fixture")
    parser.add_argument("--output", type=Path, required=True, help="Isolated replay account")
    args = parser.parse_args()
    if args.synthetic_demo is not None:
        if args.paper_capital is None or args.paper_capital <= 0:
            parser.error("synthetic demo requires positive --paper-capital")
        plan = create_synthetic_demo(args.synthetic_demo, args.paper_capital)
    else:
        if args.paper_capital is not None:
            parser.error("capital is frozen in a saved replay plan")
        plan = args.plan
    print(json.dumps(replay(plan, output_dir=args.output), sort_keys=True, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
