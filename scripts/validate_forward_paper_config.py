"""Dedicated offline validation, never a scheduled/live session entry point."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.research.exact_config_validation import run_validation, SCENARIOS, TAG

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new absolute validation-only directory')
    parser.add_argument('--scenario', choices=SCENARIOS, action='append')
    args = parser.parse_args(argv)
    try:
        report = run_validation(args.output, scenarios=tuple(args.scenario) if args.scenario else SCENARIOS)
    except (ValueError, OSError):
        print(json.dumps(dict(validation_kind=TAG, status='VALIDATION_FAILED', external_requests=0)))
        return 2
    print(json.dumps(dict(validation_kind=TAG, status='OFFLINE_VALIDATION_COMPLETED',
                         config_identity=report['canonical_config_identity'], report=str(args.output / 'validation-report.json'),
                         production_preflight=report['production_preflight_after']['status'], external_requests=0)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
