"""Complete-seal trust boundaries; synthetic fixtures and mocked transports only."""
import hashlib
import json

import pandas as pd
import pytest

from test_ai_led_reviewed import setup, used
from test_signal_observation_chain import collected, capture, replay
from test_signal_torn_seal_recovery import seal_bytes
from app.research import signal_observations as future, ai_led_guarded as guard, ai_led_offline as core
from app.research import ai_led_reviewed as reviewed, signal_request_budget as slots
from app.research.deepseek_prospective import AppendOnlyJsonl
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH


FIELDS = ("request_id", "experiment_sha256", "observation_sha256", "saved_artifact_sha256")


def files(output):
    return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def assert_blocked_unchanged(collected):
    s, _, _, _ = collected
    before = files(s.output)
    calls = len(s.http), len(s.sdk)
    budgets = tuple(used(s, name) for name in ("market_data_request_budget", "physical_request_attempts", "request_budget"))
    errors = []
    for action in (lambda: replay(collected), lambda: replay(collected),
                   lambda: capture(collected), lambda: capture(collected)):
        with pytest.raises(guard.GuardedBatchError) as error:
            action()
        errors.append(str(error.value))
    assert len(set(errors)) == 1
    assert (len(s.http), len(s.sdk)) == calls
    assert tuple(used(s, name) for name in ("market_data_request_budget", "physical_request_attempts", "request_budget")) == budgets
    assert files(s.output) == before and not (s.output / "paper_account").exists()


def duplicate_fields(record, kind):
    key = "request_id" if kind == "request" else "foo" if kind == "unrelated" else "experiment_sha256"
    value = record.get(key, 1)
    second = "e" * 64 if kind == "conflicting" else value
    pairs = [(key, value), (key, second)] + [(k, v) for k, v in record.items() if k != key]
    return ("{" + ",".join(json.dumps(k) + ":" + json.dumps(v) for k, v in pairs) + "}").encode()


@pytest.mark.parametrize("kind", ["conflicting", "identical", "request", "unrelated"])
@pytest.mark.parametrize("ending", [b"", b"\n"])
def test_duplicate_json_keys_are_integrity_errors_not_torn_tails(tmp_path, kind, ending):
    path = tmp_path / "future_observation_seals.jsonl"
    data = duplicate_fields(json.loads(seal_bytes()), kind) + ending
    path.write_bytes(data)
    with pytest.raises(guard.GuardedBatchError, match="future_seal_ledger_corrupt"):
        future._read_future_seals(tmp_path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("field", FIELDS)
def test_each_required_seal_field_is_mandatory(tmp_path, field):
    record = json.loads(seal_bytes())
    del record[field]
    (tmp_path / "future_observation_seals.jsonl").write_text(json.dumps(record))
    with pytest.raises(guard.GuardedBatchError, match="future_seal_ledger_corrupt"):
        future._read_future_seals(tmp_path)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [None, "short", "G" * 64])
def test_seal_hashes_require_existing_lowercase_sha256_shape(tmp_path, field, value):
    record = json.loads(seal_bytes())
    record[field] = value
    (tmp_path / "future_observation_seals.jsonl").write_text(json.dumps(record))
    with pytest.raises(guard.GuardedBatchError, match="future_seal_ledger_corrupt"):
        future._read_future_seals(tmp_path)


def test_complete_seal_with_extra_fields_is_rejected(tmp_path):
    record = json.loads(seal_bytes())
    record["extra"] = "not part of the existing seal contract"
    (tmp_path / "future_observation_seals.jsonl").write_text(json.dumps(record))
    with pytest.raises(guard.GuardedBatchError):
        future._read_future_seals(tmp_path)


def test_append_idempotence_does_not_permit_duplicate_physical_rows(tmp_path):
    path = tmp_path / "future_observation_seals.jsonl"
    record = json.loads(seal_bytes())
    ledger = AppendOnlyJsonl(path, "request_id")
    assert ledger.append(record)
    before = path.read_bytes()
    assert ledger.append(record) is False and path.read_bytes() == before
    assert future._read_future_seals(tmp_path) == ([record], None)
    with path.open("ab") as stream:
        stream.write(before)
    with pytest.raises(guard.GuardedBatchError):
        future._read_future_seals(tmp_path)


@pytest.mark.parametrize("kind", ["incomplete_schema", "unknown_request", "wrong_experiment",
                                  "missing_observation", "unknown_before_torn", "duplicate_json"])
def test_invalid_complete_ledger_blocks_unspent_future_acquisition(collected, kind):
    s, _, prediction, _ = collected
    config = guard._read(s.output / "signal_experiment.json")
    job = future.jobs(config, [prediction], OfflineSessionCalendar(CALENDAR_PATH))[0]
    record = json.loads(seal_bytes(job["request_id"]))
    record["experiment_sha256"] = job["experiment_sha256"]
    if kind == "incomplete_schema":
        data = b'{"request_id":"bogus"}\n'
    elif kind == "duplicate_json":
        data = duplicate_fields(record, "identical") + b"\n"
    else:
        if kind in {"unknown_request", "unknown_before_torn"}:
            record["request_id"] = "e" * 64
        elif kind == "wrong_experiment":
            record["experiment_sha256"] = "e" * 64
        data = core._encoded(record) + b"\n"
        if kind == "unknown_before_torn":
            data += b'{"request_id":"unfinished'
    (s.output / "future_observation_seals.jsonl").write_bytes(data)
    assert len(s.http) == len(s.sdk) == used(s, "market_data_request_budget") == 1
    assert_blocked_unchanged(collected)


@pytest.fixture
def sealed(collected):
    capture(collected)
    s, _, _, _ = collected
    path = s.output / "future_observation_seals.jsonl"
    return collected, path, json.loads(path.read_bytes())


@pytest.mark.parametrize("kind", ["wrong_experiment", "wrong_request", "extra_unknown_seal",
    "exact_duplicate", "conflicting_duplicate", "observation_hash", "artifact_hash",
    "prediction_id", "prediction_sha256", "instrument_key", "symbol", "purpose", "window_end",
    "job_request_id", "raw_response"])
def test_complete_seals_bind_all_scheduled_identity_and_saved_evidence(sealed, kind):
    collected, seal_path, seal = sealed
    s, _, _, _ = collected
    original = seal_path.read_bytes()
    if kind == "wrong_experiment":
        seal["experiment_sha256"] = "e" * 64
    elif kind == "wrong_request":
        seal["request_id"] = "e" * 64
    elif kind == "extra_unknown_seal":
        seal["request_id"] = "e" * 64
        seal_path.write_bytes(original + core._encoded(seal) + b"\n")
    elif kind in {"exact_duplicate", "conflicting_duplicate"}:
        if kind == "conflicting_duplicate":
            seal["observation_sha256"] = "e" * 64
        seal_path.write_bytes(original + core._encoded(seal) + b"\n")
    elif kind in {"observation_hash", "artifact_hash"}:
        seal["observation_sha256" if kind == "observation_hash" else "saved_artifact_sha256"] = "e" * 64
    elif kind == "raw_response":
        raw_path = s.output / "future_received" / (seal["request_id"] + ".json")
        raw = guard._read(raw_path)
        raw["data"]["candles"][-1][4] += .1
        raw_path.write_bytes(core._encoded({"sha256": core._digest(raw), "record": raw}))
    else:
        path = s.output / "future_observations" / (seal["request_id"] + ".json")
        record = guard._read(path)
        field = "request_id" if kind == "job_request_id" else kind
        value = ("HDFCBANK" if field == "symbol" else core.STOCKS["HDFCBANK"] if field == "instrument_key"
                 else "OTHER_PURPOSE" if field == "purpose" else
                 (pd.Timestamp(record["identity"][field]) + pd.Timedelta(minutes=5)).isoformat()
                 if field == "window_end" else "e" * 64)
        record["identity"][field] = value
        path.write_bytes(core._encoded({"sha256": core._digest(record), "record": record}))
        # Recompute local hashes to prove rejection comes from the frozen job binding.
        seal["observation_sha256"] = core._digest(record)
        seal["saved_artifact_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if kind not in {"extra_unknown_seal", "exact_duplicate", "conflicting_duplicate"}:
        seal_path.write_bytes(core._encoded(seal) + b"\n")
    assert_blocked_unchanged(collected)


@pytest.mark.parametrize("kind", ["conflicting", "identical", "request", "unrelated"])
def test_duplicate_json_fields_cannot_hide_behind_an_existing_valid_seal(sealed, kind):
    collected, path, seal = sealed
    path.write_bytes(duplicate_fields(seal, kind) + b"\n")
    assert_blocked_unchanged(collected)


def test_valid_complete_seal_still_evaluates_and_reuses_evidence(sealed):
    collected, path, seal = sealed
    s, _, _, _ = collected
    before = files(s.output)
    result = replay(collected)
    assert result["total_completed_evaluations"] == result["total_expected_prediction_evaluations"] == 1
    assert future._read_future_seals(s.output) == ([seal], None)
    assert capture(collected) == replay(collected) == result
    assert len(s.http) == 2 and len(s.sdk) == 1 and files(s.output) == before


@pytest.mark.parametrize("kind", ["incomplete_schema", "unknown_request", "torn"])
def test_direct_future_transport_cannot_bypass_seal_validation(collected, kind):
    s, options, prediction, _ = collected
    binding = reviewed._ReviewedRun(**options, purpose="signal_future", enable_signal_future=True,
                                    allow_market_data_requests=True)
    config = guard._read(s.output / "signal_experiment.json")
    binding.future_job = future.jobs(config, [prediction], OfflineSessionCalendar(CALENDAR_PATH))[0]
    with core._experiment_lock(s.output):
        assert slots.reserve(binding, binding.future_job["request_id"], "RELIANCE", "FUTURE_OBSERVATION")
    if kind == "incomplete_schema":
        data = b'{"request_id":"bogus"}\n'
    elif kind == "torn":
        data = b'{"request_id":"unfinished'
    else:
        data = seal_bytes("e" * 64) + b"\n"
    (s.output / "future_observation_seals.jsonl").write_bytes(data)
    before = files(s.output)
    for action in (lambda: binding.fetch("RELIANCE"), lambda: binding._send_market("RELIANCE")):
        with pytest.raises(guard.GuardedBatchError):
            action()
    assert files(s.output) == before and len(s.http) == len(s.sdk) == 1
    assert used(s, "market_data_request_budget") == 2 and used(s, "physical_request_attempts") == 1
