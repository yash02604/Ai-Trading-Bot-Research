import json
import hashlib
from pathlib import Path
import shutil

import pytest

from app.client_attestation import (
    ATTESTATION, ROOT, verify_client_attestation, verify_historical_audit_inputs,
)
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.session_manifest_v3 import (
    EVIDENCE, BASE_SHA256, build_session_manifest_v3,
    load_session_manifest_v3, verify_calendar_evidence,
)

V3 = ROOT / "data/audits/nse_cm_session_manifest_v3.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("readiness validation is offline")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.mark.parametrize("day", ["2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"])
def test_verified_v3_holidays_are_closed(day):
    cal = OfflineSessionCalendar(V3)
    assert cal.sessions[day]["session_type"] == "HOLIDAY"
    assert cal.intervals(day + "T10:00:00+05:30") == []
    assert cal.entry_allowed(day + "T10:00:00+05:30") is False


def test_regular_weekend_and_unsupported_dates():
    cal = OfflineSessionCalendar(V3)
    assert cal.entry_allowed("2026-10-30T12:00:00+05:30")
    assert cal.entry_allowed("2026-12-31T12:00:00+05:30")
    assert cal.candle_end("2026-10-30T09:15:00+05:30").hour == 15
    assert cal.candle_end("2026-10-30T09:15:00+05:30").minute == 15
    assert cal.candle_end("2026-12-31T09:15:00+05:30").isoformat() == "2026-12-31T15:30:00+05:30"
    assert not cal.entry_allowed("2026-11-07T12:00:00+05:30")
    assert not cal.entry_allowed("2026-10-30T08:55:00+05:30")
    assert not cal.entry_allowed("2026-10-30T15:25:00+05:30")
    assert not cal.entry_allowed("2026-12-04T15:10:00+05:30")
    assert cal.entry_allowed("2026-12-07T15:20:00+05:30")
    with pytest.raises(ValueError, match="outside"):
        cal.candle_end("2026-10-30T15:15:00+05:30")
    assert cal.candle_end("2026-12-07T15:25:00+05:30").minute == 30
    for stamp in ("2027-01-01T12:00:00+05:30", "2023-11-30T12:00:00+05:30"):
        with pytest.raises(ValueError, match="absent"):
            cal.entry_allowed(stamp)
    with pytest.raises(ValueError, match="timezone"):
        cal.entry_allowed("2026-10-30T12:00:00")


def test_muhurat_phases_retained_without_generic_execution_permission():
    cal = OfflineSessionCalendar(V3)
    session = cal.sessions["2026-11-08"]
    assert session["session_type"] == "SPECIAL"
    assert session["normal_market_intervals"] == [{"start": "14:20", "end": "15:15"}]
    assert session["security_specific_continuous_intervals"]["CAS"]["end"] == "15:15"
    assert session["security_specific_continuous_intervals"]["NON_CAS"]["end"] == "15:30"
    assert session["ucc_compliance_cutoff"] == "2026-11-07T22:00:00+05:30"
    assert session["execution_supported"] is False
    for hour in ("13:55", "14:10", "14:30", "15:15", "15:20", "15:30", "15:55"):
        assert not cal.entry_allowed("2026-11-08T" + hour + ":00+05:30")
    with pytest.raises(ValueError, match="outside"):
        cal.candle_end("2026-11-08T14:20:00+05:30")


def test_v3_preserves_all_legacy_entries_and_binds_retained_sources(tmp_path):
    import hashlib
    old_path = ROOT / "data/audits/nse_cm_session_manifest_v2.json"
    old = json.loads(old_path.read_text())
    manifest = load_session_manifest_v3(V3)
    assert hashlib.sha256(old_path.read_bytes()).hexdigest() == BASE_SHA256
    for previous, current in zip(old["sessions"], manifest["sessions"]):
        if previous["session_type"] == "REGULAR" and previous["date"] >= "2026-08-03":
            assert current["prior_v2_normal_market_intervals"] == previous["normal_market_intervals"]
            assert current["normal_market_intervals"] == [{"start": "09:15", "end": "15:15"}]
            assert current["session_type"] == previous["session_type"]
        else:
            assert current == previous
    assert manifest["coverage"]["end"] == "2026-12-31"
    assert len(manifest["sessions"]) == 1127
    generated = tmp_path / "calendar.json"
    build_session_manifest_v3(generated)
    assert generated.read_bytes() == V3.read_bytes()
    with pytest.raises(FileExistsError):
        build_session_manifest_v3(generated)


@pytest.mark.parametrize("kind", ["pdf", "registry", "retrieval_record"])
def test_calendar_evidence_tampering_is_rejected(tmp_path, kind):
    shutil.copytree(ROOT / EVIDENCE, tmp_path / EVIDENCE)
    name = {"pdf": "CMTR71775.pdf", "registry": "evidence_registry.json",
            "retrieval_record": "CMTR71775.pdf.retrieval.json"}[kind]
    path = tmp_path / EVIDENCE / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_calendar_evidence(tmp_path)


def test_calendar_bytes_cannot_silently_enable_extra_phase(tmp_path):
    changed = json.loads(V3.read_text())
    next(s for s in changed["sessions"] if s["date"] == "2026-11-08")["execution_supported"] = True
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_session_manifest_v3(path)


def test_historical_client_and_current_attestation_are_distinct():
    result = verify_client_attestation()
    assert result["historical_sha256"] == "59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3"
    assert result["reviewed_parent_sha256"] == "27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb"
    assert result["reviewed_v2_sha256"] == "20bf9738577c5e4bf19b04587a609c352514f944bceb0cb0d6df3c5a1ea73580"
    assert result["current_sha256"] == hashlib.sha256((ROOT / "app/upstox_client.py").read_bytes()).hexdigest()
    assert result["current_sha256"] != result["historical_sha256"]
    assert result["operational_trading_approval"] is False
    assert result["source_authentication"] is False
    assert result["independent_review_approval"] is False
    bindings = verify_historical_audit_inputs()
    assert len(bindings) == 38
    assert bindings["app/upstox_client.py"]["historical_observed_sha256"] == result["historical_sha256"]


@pytest.mark.parametrize("kind", ["working_client", "historical_client", "current_snapshot", "diff", "attestation", "audit"])
def test_modified_current_or_historical_client_evidence_is_detected(tmp_path, kind):
    shutil.copytree(ROOT / "data/attestations", tmp_path / "data/attestations")
    for name in ("app/upstox_client.py", "backtests/provenance_integrity_v1/input_hashes.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    evidence = tmp_path / Path(ATTESTATION).parent
    target = {"working_client": tmp_path / "app/upstox_client.py",
              "historical_client": tmp_path / "data/attestations/upstox_client_v1/historical_client.crlf.py.snapshot",
              "current_snapshot": evidence / "current_client.crlf.py.snapshot",
              "diff": evidence / "reviewed_diff.patch", "attestation": evidence / "attestation.json",
              "audit": tmp_path / "backtests/provenance_integrity_v1/input_hashes.json"}[kind]
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError):
        verify_client_attestation(tmp_path)


def test_v3_calendar_integrates_without_risk_or_account_change(tmp_path):
    from app.research.ai_led_replay import create_synthetic_demo, replay
    plan = create_synthetic_demo(tmp_path / "fixtures", 2500)
    raw = json.loads(plan.read_text()); raw["calendar"] = str(V3)
    plan.write_text(json.dumps(raw))
    report = replay(plan, output_dir=tmp_path / "account")
    assert report["initial_capital"] == 2500
    assert report["total_accepted_trades"] == report["completed_trades"] == 3
    assert report["provider_requests"] == 0
    assert report["trade_placement_enabled"] is False
    assert report["calendar"]["manifest_version"].endswith("v3-asof-20261030")
