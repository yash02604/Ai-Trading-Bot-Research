import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

from scripts.run_acquisition_pilot_v1 import (
    AUTHORIZATION_RELATIVE,
    MAX_RESPONSE_BYTES,
    PILOT_ID,
    PILOT_RELATIVE,
    PilotBlocked,
    PilotExecutionError,
    preflight,
    run_pilot,
)

ROOT = Path(__file__).resolve().parents[1]
IST = ZoneInfo("Asia/Kolkata")
TOKEN = "test-only-access-token-do-not-log"


class FakeRaw:
    def __init__(self, body):
        self.body = body
        self.read_bytes = 0

    def stream(self, chunk_size, decode_content=False):
        for start in range(0, len(self.body), chunk_size):
            chunk = self.body[start:start + chunk_size]
            self.read_bytes += len(chunk)
            yield chunk


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = FakeRaw(body)


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.adapters = {}
        self.closed = False

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _evidence(verified=True):
    common = {"verified": verified, "evidence_reference": "owner-reviewed-document-ref", "reviewed_at_utc": "2026-10-22T10:00:00Z"}
    master = {
        **common,
        "snapshot_path": "data/acquisition_pilots/instrument_master_snapshot.json",
        "snapshot_sha256": "",
        "snapshot_date": "2026-10-20",
        "official_source_url": "https://assets.upstox.com/instrument-master/complete.json.gz",
        "symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE", "segment": "NSE_EQ",
    }
    return {
        "schema_version": 1,
        "pilot_id": PILOT_ID,
        "approval_status": "APPROVED",
        "provider_authorization": {**common, "endpoint": "https://api.upstox.com/v3/historical-candle/{instrument_key}/minutes/{interval}/{to_date}/{from_date}", "read_only": True},
        "research_use": {**common, "research_only_permitted": True},
        "raw_retention": {**common, "raw_bytes_permitted": True, "retention_duration": "30 days"},
        "request_limits": {**common, "requests_per_minute": 10, "max_concurrent_requests": 1, "zero_retries_permitted": True},
        "instrument_master": master,
        "execution_authorization": {"approved": True, "evidence_reference": "single-request-approval-ref", "pilot_id": PILOT_ID,
                                    "instrument_key": "NSE_EQ|INE002A01018", "date": "2025-02-03", "interval_minutes": 5, "max_http_requests": 1},
    }


@pytest.fixture
def approved_root(tmp_path):
    plan = json.loads((ROOT / PILOT_RELATIVE).read_text(encoding="utf-8"))
    plan["status"] = "APPROVED_FOR_SINGLE_REQUEST"
    plan["retention"]["retention_permission_confirmed"] = True
    _write_json(tmp_path / PILOT_RELATIVE, plan)
    manifest_path = tmp_path / "data/audits/nse_cm_session_manifest_v2.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes((ROOT / "data/audits/nse_cm_session_manifest_v2.json").read_bytes())
    snapshot = tmp_path / "data/acquisition_pilots/instrument_master_snapshot.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps([{"instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE",
                                     "exchange": "NSE", "segment": "NSE_EQ"}]), encoding="utf-8")
    auth = _evidence()
    auth["instrument_master"]["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    _write_json(tmp_path / AUTHORIZATION_RELATIVE, auth)
    return tmp_path


def _candles(count=75):
    start = datetime(2025, 2, 3, 9, 15, tzinfo=IST)
    return [[(start + timedelta(minutes=5 * i)).isoformat(), 100.0, 101.0, 99.0, 100.5, 10, 0] for i in range(count)]


def _payload(candles=None):
    return json.dumps({"status": "success", "data": {"candles": _candles() if candles is None else candles}}).encode("utf-8")


def test_actual_blocked_pilot_refuses_before_transport_or_output_creation():
    output = ROOT / "data/acquisition_pilots/runs" / PILOT_ID
    assert not output.exists()
    with pytest.raises(PilotBlocked) as error:
        preflight(ROOT)
    assert "not explicitly approved" in str(error.value)
    assert "raw-response retention permission is not confirmed" in str(error.value)
    assert "authorization attestation is missing" in str(error.value)
    assert not output.exists()


@pytest.mark.parametrize("section", ["provider_authorization", "research_use", "raw_retention", "request_limits", "instrument_master"])
def test_each_unverified_prerequisite_blocks_before_http(approved_root, section):
    path = approved_root / AUTHORIZATION_RELATIVE
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence[section]["verified"] = False
    _write_json(path, evidence)
    session = FakeSession(FakeResponse(_payload()))
    with pytest.raises(PilotBlocked):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert session.calls == []
    assert not (approved_root / "data/acquisition_pilots/runs" / PILOT_ID).exists()


def test_explicit_execution_approval_is_required(approved_root):
    path = approved_root / AUTHORIZATION_RELATIVE
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["execution_authorization"]["approved"] = False
    _write_json(path, evidence)
    session = FakeSession(FakeResponse(_payload()))
    with pytest.raises(PilotBlocked, match="execution authorization"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert session.calls == []


@pytest.mark.parametrize("mutation", ["instrument", "date", "interval", "request_limit", "retry_limit", "response_bytes", "row_limit", "output_path"])
def test_configuration_cannot_expand_single_pilot_scope(approved_root, mutation):
    path = approved_root / PILOT_RELATIVE
    plan = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "instrument":
        plan["request"]["instrument_allowlist"][0]["instrument_key"] = "NSE_EQ|OTHER"
    elif mutation == "date":
        plan["request"]["from_date"] = "2025-02-04"
    elif mutation == "interval":
        plan["request"]["interval"]["value"] = 15
    elif mutation == "request_limit":
        plan["request"]["max_http_requests"] = 2
    elif mutation == "retry_limit":
        plan["request"]["max_retries"] = 1
    elif mutation == "response_bytes":
        plan["request"]["max_response_bytes"] = MAX_RESPONSE_BYTES + 1
    elif mutation == "row_limit":
        plan["request"]["max_candle_rows"] = 76
    else:
        plan["outputs"]["root"] = "data/research/"
    _write_json(path, plan)
    with pytest.raises(PilotBlocked):
        preflight(approved_root)


def test_snapshot_hash_and_mapping_must_match(approved_root):
    path = approved_root / AUTHORIZATION_RELATIVE
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["instrument_master"]["snapshot_sha256"] = "0" * 64
    _write_json(path, evidence)
    with pytest.raises(PilotBlocked, match="snapshot SHA-256"):
        preflight(approved_root)


def test_success_is_one_get_zero_retries_exact_raw_bytes_and_redacted_artifacts(approved_root, caplog):
    body = _payload()
    session = FakeSession(FakeResponse(body, headers={"Content-Length": str(len(body)), "X-Request-Id": "provider-123"}))
    def clock_after_response():
        assert len(session.calls) == 1
        return datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    result = run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session, now=clock_after_response)
    assert result["status"] == "PASS"
    assert result["counts"]["VERIFIED_VALID"] == 75
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert "NSE_EQ%7CINE002A01018" in url
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert session.adapters["https://"].max_retries.total == 0
    assert session.closed
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    raw_path = output / "raw" / f"response-{hashlib.sha256(body).hexdigest()}.json"
    assert raw_path.read_bytes() == body
    artifacts = b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
    assert TOKEN.encode() not in artifacts
    assert TOKEN not in caplog.text
    request_manifest = json.loads(next((output / "requests").glob("*.json")).read_text(encoding="utf-8"))
    assert request_manifest["retry_metadata"] == {"attempts": 1, "retries": 0, "retry_delays_seconds": []}
    assert "authorization" not in json.dumps(request_manifest).lower()
    normalized_bytes = (output / "normalized/candles.jsonl").read_bytes()
    assert request_manifest["normalized_output_sha256"] == hashlib.sha256(normalized_bytes).hexdigest()
    assert request_manifest["retrieved_at_utc"] == "2026-10-22T12:00:00Z"


def test_timeout_is_not_retried_and_secret_is_not_in_error_or_logs(approved_root, caplog):
    session = FakeSession(error=requests.Timeout(TOKEN))
    with pytest.raises(PilotExecutionError, match="validation failed") as error:
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert len(session.calls) == 1
    assert session.adapters["https://"].max_retries.total == 0
    assert TOKEN not in str(error.value) + caplog.text
    report = json.loads((approved_root / "data/acquisition_pilots/runs" / PILOT_ID / "validation.json").read_text(encoding="utf-8"))
    assert report["validation_errors"] == ["request failed (Timeout)"]


def test_response_byte_limit_fails_closed_without_partial_raw_file(approved_root):
    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    session = FakeSession(FakeResponse(body))
    with pytest.raises(PilotExecutionError, match="validation failed"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert len(session.calls) == 1
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    assert list((output / "raw").iterdir()) == []
    assert session.response.raw.read_bytes <= MAX_RESPONSE_BYTES + 64 * 1024
    report = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert "response exceeds the configured byte limit" in report["validation_errors"]


def test_more_than_75_returned_rows_fails_closed(approved_root):
    body = _payload(_candles(76))
    session = FakeSession(FakeResponse(body, headers={"Content-Length": str(len(body))}))
    with pytest.raises(PilotExecutionError, match="validation failed"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert len(session.calls) == 1
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    report = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    assert report["validation_errors"] == ["response_row_limit_exceeded"]
    assert list((output / "raw").glob("response-*.json"))


def test_unexpected_response_schema_is_quarantined_without_echoing_body(approved_root):
    body = json.dumps({"unexpected": {"token": "never-write-this"}}).encode("utf-8")
    session = FakeSession(FakeResponse(body))
    with pytest.raises(PilotExecutionError, match="validation failed"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    unresolved = (output / "quarantine/unresolved.jsonl").read_text(encoding="utf-8")
    assert "unexpected_response_schema" in unresolved
    assert "never-write-this" not in unresolved


def test_unexpected_status_and_duplicate_or_bad_rows_are_quarantined(approved_root):
    duplicate_candles = _candles()
    duplicate_candles[-1] = duplicate_candles[-2]
    session = FakeSession(FakeResponse(_payload(duplicate_candles)))
    with pytest.raises(PilotExecutionError, match="validation failed"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert len(session.calls) == 1
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    invalid = (output / "quarantine/verified_invalid.jsonl").read_text(encoding="utf-8")
    assert "duplicate_instrument_timestamp" in invalid
    report = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "FAIL"
    assert "expected_session_coverage_incomplete" in report["validation_errors"]


def test_existing_output_path_is_never_overwritten(approved_root):
    output = approved_root / "data/acquisition_pilots/runs" / PILOT_ID
    output.mkdir(parents=True)
    marker = output / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    session = FakeSession(FakeResponse(_payload()))
    with pytest.raises(PilotBlocked, match="output directory already exists"):
        run_pilot(root=approved_root, token=TOKEN, session_factory=lambda: session)
    assert marker.read_text(encoding="utf-8") == "existing"
    assert session.calls == []