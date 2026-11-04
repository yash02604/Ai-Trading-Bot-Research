"""Explicitly opt-in, single-use DeepSeek provider diagnostic; no trading path."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.v2.provider_smoke import DISABLED, run_provider_smoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--enable-provider-smoke", action="store_true")
    args = parser.parse_args()
    try:
        result = run_provider_smoke(run_id=args.run_id, enabled=args.enable_provider_smoke)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "OK" else 2
    except Exception:
        print(json.dumps({"status": "BLOCKED", "message": "Provider diagnostic blocked; details redacted", **DISABLED}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
