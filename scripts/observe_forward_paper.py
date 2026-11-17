"""Read-only observer; no engine, recovery, network or trading command surface."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.paper_observer import read_snapshot, read_events, human_summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--format", choices=["json", "human"], default="json")
    parser.add_argument("--max-age-seconds", type=float, default=120)
    parser.add_argument("--as-of", help="Read-only observer freshness-check clock")
    args = parser.parse_args(argv)
    result = read_snapshot(args.snapshot, as_of=args.as_of, max_age_seconds=args.max_age_seconds)
    if args.events is not None:
        result["event_stream"] = read_events(args.events)
    print(human_summary(result) if args.format == "human" else json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["observer_status"] == "AVAILABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
