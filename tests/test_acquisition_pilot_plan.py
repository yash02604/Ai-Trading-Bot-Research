import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data/acquisition_pilots/pilot_v1.json"


def test_pilot_plan_is_single_allowlisted_bounded_request_and_has_new_output_paths():
    plan = json.loads(PILOT.read_text(encoding="utf-8-sig"))
    request = plan["request"]
    assert plan["status"] == "BLOCKED_PENDING_SOURCE_AUTHORIZATION_AND_RETENTION_REVIEW"
    assert request["instrument_allowlist"] == [{
        "symbol": "RELIANCE",
        "instrument_key": "NSE_EQ|INE002A01018",
        "key_evidence": "Existing local strategy_backtest.py mapping; re-confirm against a dated official instrument-master snapshot before request.",
        "exchange": "NSE",
        "segment": "CM",
    }]
    assert request["from_date"] == request["to_date"] == "2025-02-03"
    assert request["interval"] == {"value": 5, "unit": "minutes"}
    assert request["max_http_requests"] == 1
    assert request["max_retries"] == 0
    assert request["max_candle_rows"] == 75
    assert request["max_response_bytes"] == 2 * 1024 * 1024
    assert plan["retention"]["retention_permission_confirmed"] is False
    assert "otherwise STOP" in plan["retention"]["raw_response_policy"]
    assert all(path.startswith(plan["outputs"]["root"]) for key, path in plan["outputs"].items() if key != "root")
    assert "not_authorized_for" in plan


def test_pilot_session_is_verified_regular_day_in_pinned_manifest():
    plan = json.loads(PILOT.read_text(encoding="utf-8-sig"))
    manifest = json.loads((ROOT / plan["session_validation"]["manifest_path"]).read_text(encoding="utf-8"))
    entry = next(row for row in manifest["sessions"] if row["date"] == plan["request"]["from_date"])
    assert manifest["manifest_version"] == plan["session_validation"]["manifest_version"]
    assert entry["verification_status"] == "VERIFIED"
    assert entry["session_type"] == "REGULAR"
    assert entry["normal_market_intervals"] == [{"start": "09:15", "end": "15:30"}]


def test_pilot_plan_contains_no_secret_material():
    raw = PILOT.read_text(encoding="utf-8-sig")
    plan = json.loads(raw)
    forbidden = ("UPSTOX_ACCESS_TOKEN", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
                 "Authorization:", "Bearer ")
    assert all(value not in raw for value in forbidden)
    assert "provider_terms_review_required" in plan["retention"]
    runbook = (ROOT / "docs/ACQUISITION_PILOT_READINESS_V1.md").read_text(encoding="utf-8").lower()
    assert "provider's applicable terms" in runbook
    assert "retention duration" in runbook
