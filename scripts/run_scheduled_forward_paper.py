"""Scheduled V2.2 preparation entry; real transports remain unreviewed/blocked."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research import scheduled_forward_paper as runner
from app.research.paper_observer import unique_fields


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["FORWARD_PAPER"], default="FORWARD_PAPER")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--offline-preflight", action="store_true")
    parser.add_argument("--prepare-launch", action="store_true", help="Validate/create owned control metadata only; no engine or transport")
    parser.add_argument("--as-of", help="Explicit clock only for read-only --offline-preflight")
    args = parser.parse_args(argv)
    if args.as_of and not args.offline_preflight:
        parser.error("--as-of requires --offline-preflight")
    if args.prepare_launch and args.offline_preflight:
        parser.error("--prepare-launch and --offline-preflight are exclusive")
    if not args.config.is_absolute() or not args.control_root.is_absolute():
        parser.error("absolute config/control paths are required")
    try:
        config = json.loads(args.config.read_bytes(), object_pairs_hook=unique_fields)
        result = (runner.prepare_launch(config, args.control_root) if args.prepare_launch else
                  runner.preflight(config, args.as_of) if args.offline_preflight else runner.run_scheduled(config, args.control_root))
    except KeyboardInterrupt:
        result = {"status": "INTERRUPTED", "mode": "FORWARD_PAPER", "reason_codes": ["INTERRUPTED"],
                  "external_requests": 0, "trade_placement_enabled": False}
    except Exception:
        result = {"status": "NO_GO", "mode": "FORWARD_PAPER", "reason_codes": ["CONFIG_INVALID"],
                  "external_requests": 0, "trade_placement_enabled": False}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 130 if result["status"] == "INTERRUPTED" else 0 if result["status"] in {"NO_SESSION", "CONTROL_PREPARED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
