"""Run four synthetic DeepSeek decision checks after safe fail-closed preflight."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.sharp_reversal_experiment import load_session_dates
from app.v2.evaluation import _verified_session
from app.v2.provider import (DEFAULT_MAX_COMPLETION_TOKENS, DEFAULT_MAX_RESPONSE_BYTES,
                             MAX_PROVIDER_TIMEOUT_SECONDS, DeepSeekProvider)
from app.v2.synthetic_provider_check import (SYNTHETIC_TEST_TIME, build_preflight_report,
                                             run_synthetic_decisions, write_report_exclusive)

SESSION_MANIFEST = ROOT / "data/audits/nse_cm_session_manifest_v2.json"
OUTPUT_DIR = ROOT / "backtests/deepseek_synthetic_decision_check"


def _run_id(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%S%fZ")


def main() -> int:
    checked_at = datetime.now(timezone.utc)
    try:
        # This is the existing application credential-loading path. Only the
        # presence boolean and non-secret settings are written to the report.
        settings = get_settings()
        report = build_preflight_report(settings=settings, checked_at=checked_at)
    except Exception as exc:
        report = {
            "schema_version": "deepseek-synthetic-decision-check-v1",
            "status": "BLOCKED", "checked_at_utc": checked_at.isoformat(),
            "requests_made": 0, "max_model_requests": 4,
            "credentials_logged": False, "synthetic_inputs_only": True,
            "performance_evidence": False,
            "blockers": [f"settings preflight failed: {type(exc).__name__}"],
        }
    if report.get("status") == "READY":
        try:
            test_time = datetime.fromisoformat(SYNTHETIC_TEST_TIME)
            sessions = load_session_dates(SESSION_MANIFEST)
            if not _verified_session(test_time, sessions):
                report["status"] = "BLOCKED"
                report["blockers"] = ["synthetic test timestamp is not in a verified regular session"]
            else:
                timeout = min(float(report["configured_timeout_seconds"]), MAX_PROVIDER_TIMEOUT_SECONDS)
                provider = DeepSeekProvider(
                    api_key=settings.deepseek_api_key,
                    model_id=settings.deepseek_model,
                    timeout_seconds=timeout,
                    max_completion_tokens=DEFAULT_MAX_COMPLETION_TOKENS,
                    max_response_bytes=DEFAULT_MAX_RESPONSE_BYTES,
                )
                report = run_synthetic_decisions(provider, now=test_time,
                    session_manifest_path=SESSION_MANIFEST,
                    requested_model_id=settings.deepseek_model)
                report["executed_at_utc"] = checked_at.isoformat()
                report["configured_model_id"] = settings.deepseek_model
        except Exception as exc:
            # Never serialize provider exception text or response bodies.
            report = {
                "schema_version": "deepseek-synthetic-decision-check-v1",
                "status": "BLOCKED", "checked_at_utc": checked_at.isoformat(),
                "requests_made": 0, "max_model_requests": 4,
                "credentials_logged": False, "synthetic_inputs_only": True,
                "performance_evidence": False,
                "blockers": [f"safe preflight failed: {type(exc).__name__}"],
            }
    report_path = write_report_exclusive(report, OUTPUT_DIR / f"run_{_run_id(checked_at)}.json")
    summary = {"status": report["status"], "report": str(report_path),
               "requests_made": report.get("requests_made", 0),
               "max_model_requests": report.get("max_model_requests", 4),
               "configured_model_id": report.get("configured_model_id")}
    print(json.dumps(summary, sort_keys=True))
    return 0 if report["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
