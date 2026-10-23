import hashlib
import json

import pandas as pd
import pytest

from app.research.execution_integrity import (
    ExecutionIntegrityError,
    TimestampSemanticsEvidence,
    validate_simulation_event,
)
from app.research.sharp_reversal_experiment import simulate
from app.research.sharp_reversal_experiment import write_experiment
import app.research.sharp_reversal_experiment as experiment
from test_sharp_reversal_experiment import candidate, fixture_evidence, fixture_sessions, frame, make_event


def test_evidence_requires_exact_schema_lineage_and_hash_binding():
    base = {
        "schema_version": 1, "label_semantics": "START", "verification_status": "VERIFIED",
        "provider_documentation_url": "https://provider.example/candles",
        "acquisition_manifest_sha256": "a" * 64,
        "acquisition_request_ids": ["request-1"], "dataset_sha256": {"data.csv": "b" * 64},
    }
    evidence = TimestampSemanticsEvidence.from_mapping(base)
    evidence.require_verified_start_labels({"data.csv": "b" * 64})
    with pytest.raises(ExecutionIntegrityError, match="does not bind"):
        evidence.require_verified_start_labels({"data.csv": "c" * 64})
    for changes in ({"verification_status": "UNRESOLVED"}, {"label_semantics": "END"},
                    {"acquisition_request_ids": []}, {"provider_documentation_url": "http://x"}):
        with pytest.raises(ExecutionIntegrityError):
            TimestampSemanticsEvidence.from_mapping({**base, **changes}).require_verified_start_labels()
    with pytest.raises(ExecutionIntegrityError):
        TimestampSemanticsEvidence.from_mapping({**base, "extra": "field"})


def test_evidence_file_hash_is_recorded_exactly(tmp_path):
    payload = {
        "schema_version": 1, "label_semantics": "START", "verification_status": "VERIFIED",
        "provider_documentation_url": "https://provider.example/candles",
        "acquisition_manifest_sha256": "a" * 64, "acquisition_request_ids": ["request-1"],
        "dataset_sha256": {"data.csv": "b" * 64},
    }
    path = tmp_path / "evidence.json"
    content = json.dumps(payload).encode()
    path.write_bytes(content)
    evidence = TimestampSemanticsEvidence.from_file(path)
    assert evidence.evidence_file_sha256 == hashlib.sha256(content).hexdigest()


def test_execution_requires_timezone_normalized_next_start_label_and_contiguous_session():
    data = frame(count=32)
    event = make_event(data, 10, last_index=12)
    validate_simulation_event(event, fixture_evidence())

    for mutate in (
        lambda item: item.update(entry_index=9),
        lambda item: item.update(entry_timestamp=data.timestamp.iloc[11]),
        lambda item: item.update(last_index=40),
    ):
        broken = dict(event)
        mutate(broken)
        with pytest.raises(ExecutionIntegrityError):
            validate_simulation_event(broken, fixture_evidence())


def test_execution_rejects_gap_session_crossing_and_non_ist_labels():
    data = frame(count=32)
    event = make_event(data, 10, last_index=12)
    gap = data.copy()
    gap.loc[11:, "timestamp"] += pd.Timedelta(minutes=5)
    bad_gap = {**event, "frame": gap, "signal_timestamp": gap.timestamp.iloc[9],
               "entry_timestamp": gap.timestamp.iloc[10]}
    with pytest.raises(ExecutionIntegrityError):
        validate_simulation_event(bad_gap, fixture_evidence())

    boundary = data.iloc[:3].copy()
    boundary["timestamp"] = pd.to_datetime([
        "2025-02-03T15:20:00+05:30", "2025-02-03T15:25:00+05:30",
        "2025-02-04T09:15:00+05:30",
    ])
    bad_session = {"frame": boundary, "signal_index": 0, "entry_index": 1, "last_index": 2,
                   "signal_timestamp": boundary.timestamp.iloc[0], "entry_timestamp": boundary.timestamp.iloc[1]}
    with pytest.raises(ExecutionIntegrityError):
        validate_simulation_event(bad_session, fixture_evidence())

    utc = data.copy()
    utc["timestamp"] = utc.timestamp.dt.tz_convert("UTC")
    bad_zone = {**event, "frame": utc, "signal_timestamp": utc.timestamp.iloc[9],
                "entry_timestamp": utc.timestamp.iloc[10]}
    with pytest.raises(ExecutionIntegrityError, match="Asia/Kolkata"):
        validate_simulation_event(bad_zone, fixture_evidence())

    bad_ohlc = data.copy()
    bad_ohlc.loc[10, "low"] = bad_ohlc.loc[10, "open"] + 1
    with pytest.raises(ExecutionIntegrityError, match="OHLC"):
        validate_simulation_event({**event, "frame": bad_ohlc}, fixture_evidence())


def test_simulator_fails_closed_without_evidence_and_logs_malformed_events_as_rejects():
    data = frame(count=24)
    event = make_event(data, 10)
    with pytest.raises(ExecutionIntegrityError, match="evidence"):
        simulate([event], candidate(horizon=1), 100_000)
    with pytest.raises(ExecutionIntegrityError, match="regular-session dates"):
        simulate([event], candidate(horizon=1), 100_000, timestamp_evidence=fixture_evidence())
    malformed = {"symbol": "BROKEN"}
    trades, summary = simulate([malformed], candidate(horizon=1), 100_000,
                               timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert trades == []
    assert summary["risk_rejections"] == {"execution_integrity_rejected": 1}


def test_experiment_writer_stops_before_data_processing_without_evidence(tmp_path):
    import json

    (tmp_path / "data").mkdir()
    data_path = tmp_path / "data" / "fixture.csv"
    data_path.write_text("fixture", encoding="utf-8")
    session_path = tmp_path / "sessions.json"
    session_path.write_text("{}", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"data_files": ["data/fixture.csv"],
                                     "session_manifest": "sessions.json"}), encoding="utf-8")
    output = tmp_path / "output"
    with pytest.raises(ExecutionIntegrityError, match="no verified timestamp"):
        write_experiment(tmp_path, spec_path, output)
    assert not output.exists()


def test_shared_risk_engine_receives_actual_open_position_state(monkeypatch):
    data = frame(count=30)
    events = [make_event(data, 10), make_event(data, 10), make_event(data, 11)]
    seen = []
    original = experiment.check_trade_allowed

    def recording_check(config, state):
        seen.append(state.open_positions)
        return original(config, state)

    monkeypatch.setattr(experiment, "check_trade_allowed", recording_check)
    trades, summary = simulate(events, candidate(horizon=1), 100_000,
                               timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert seen == [0, 1, 0]
    assert len(trades) == 2
    assert summary["risk_rejections"].get("Maximum open positions reached") == 1


def test_simulator_rejects_out_of_order_events_without_opening_them():
    data = frame(count=30)
    events = [make_event(data, 12), make_event(data, 10)]
    trades, summary = simulate(events, candidate(horizon=1), 100_000,
                               timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert len(trades) == 1
    assert summary["risk_rejections"].get("execution_integrity_rejected") == 1
