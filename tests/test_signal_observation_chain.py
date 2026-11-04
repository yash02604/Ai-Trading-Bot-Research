"""Reviewed-path tests use mocked HTTP/SDK, never genuine market evidence."""
import json

import pandas as pd
import pytest

from test_ai_led_reviewed import setup, used, NOW
from app.research import ai_signal_only as signal, ai_led_reviewed as reviewed, ai_led_guarded as guard
from app.research import ai_led_offline as core, signal_observations as future, signal_evaluation as evaluation


@pytest.fixture
def collected(setup, monkeypatch):
    def denied(*a, **k):
        pytest.fail("signal outcomes invoked a paper-account lifecycle")
    for module, names in ((guard, ["_account"]), (core, ["_advance", "_commit", "run_offline_batch"])):
        for name in names: monkeypatch.setattr(module, name, denied)
    setup.body[0]["schema_version"] = signal.SCHEMA
    options = dict(output_dir=setup.output, instrument_priority=["RELIANCE"], capital=1000,
                   provider_cap=1, market_data_cap=2, enable_signal_only=True)
    prediction = signal.run_signal_only(**options, allow_market_data_requests=True, allow_provider_requests=True)
    end = pd.Timestamp(prediction["decision_ready_at"]).floor("5min") + pd.Timedelta(minutes=35)
    stamps = pd.date_range(NOW.normalize() + pd.Timedelta(hours=9, minutes=15), end - pd.Timedelta(minutes=5), freq="5min")
    setup.data["RELIANCE"]["data"]["candles"] = [[t.isoformat(),600,601,599,600,10000,0] for t in stamps]
    setup.data["RELIANCE"]["timestamp"] = (end + pd.Timedelta(seconds=9)).isoformat()
    setup.clock[0] = end + pd.Timedelta(seconds=10)
    setup.settings.deepseek_api_key = None
    return setup, options, prediction, end


def capture(collected, **changes):
    s, options, _, _ = collected
    return future.run_followup(**{**options, "capture_future": True, "enable_signal_future": True,
                                  "allow_market_data_requests": True, **changes})


def replay(collected, **changes):
    s, options, _, _ = collected
    return future.run_followup(**options, as_of=changes.pop("as_of", s.clock[0]), **changes)


def test_reviewed_path_retains_binding_and_original_provider_fields(collected):
    s, options, prediction, end = collected
    result = capture(collected)
    assert result["total_completed_evaluations"] == result["total_expected_prediction_evaluations"] == 1
    assert result["future_observation_provenance"] == "OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE"
    assert used(s, "request_budget") == len(s.sdk) == 1
    assert used(s, "market_data_request_budget") == len(s.http) == 2
    assert all(kind == "candles" for kind, _ in s.http)
    record = guard._read(next((s.output / "future_observations").glob("*.json")))
    manifest = guard._read(s.output / signal.MANIFEST)
    assert record["identity"]["experiment_sha256"] == core._digest(manifest)
    assert record["identity"]["prediction_sha256"] == core._digest(prediction)
    assert record["provenance"] == "REVIEWED_UPSTOX_CAPTURE"
    assert record["client_attestation_version"] == 3
    assert record["provider_timestamp_fields"] == [{"field": "response.timestamp",
        "value": s.data["RELIANCE"]["timestamp"], "semantics": "PROVIDER_FIELD_UNAUTHENTICATED"}]
    assert record["provider_candle_start_labels"] == [r[0] for r in s.data["RELIANCE"]["data"]["candles"]]
    assert record["execution_enabled"] is record["trade_placement_enabled"] is False
    assert result["execution_pnl"] is None and not (s.output / "paper_account").exists()
    assert replay(collected) == result


@pytest.mark.parametrize("kind", ["observation", "prediction", "instrument", "experiment", "window", "raw", "seal", "receipt", "snapshot"])
def test_tampered_evidence_or_binding_is_rejected(collected, kind):
    s, _, _, _ = collected
    capture(collected)
    path = next((s.output / "future_observations").glob("*.json"))
    record = guard._read(path)
    if kind in {"prediction", "instrument", "experiment", "window"}:
        key = {"prediction": "prediction_id", "instrument": "instrument_key", "experiment": "experiment_sha256", "window": "window_end"}[kind]
        record["identity"][key] = "changed"
    elif kind == "observation": record["body"]["candles"][-1]["close"] += .1
    elif kind == "raw":
        path = next((s.output / "future_received").glob("*.json"));record = guard._read(path)
        record["data"]["candles"][-1][4] += .1
    elif kind == "seal":
        (s.output / "future_observation_seals.jsonl").write_text("")
    elif kind == "receipt":
        receipt = s.output / "transport_receipts.jsonl"
        rows = [json.loads(line) for line in receipt.read_text().splitlines()]
        rows[-1]["received_at_utc"] = NOW.isoformat()
        receipt.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    else:
        (s.output / "received" / (record["snapshot_sha256"] + ".json")).write_text("{}")
    if kind not in {"seal", "receipt", "snapshot"}:
        path.write_bytes(core._encoded({"sha256": core._digest(record), "record": record}))
    if kind == "seal":
        result = replay(collected)
        assert result["total_completed_evaluations"] == 0
        assert result["observation_bindings"][0]["validation_status"] == "UNRECOVERABLE_PARTIAL_PUBLICATION"
    else:
        with pytest.raises((guard.GuardedBatchError, ValueError, KeyError)):
            replay(collected)
    assert len(s.http) == 2 and len(s.sdk) == 1


@pytest.mark.parametrize("kind", ["http", "missing_bar", "wrong_instrument", "corrupt"])
def test_capture_failures_keep_denominators_and_spent_reservations(collected, kind):
    s, _, _, _ = collected
    if kind == "http": s.data["RELIANCE"] = 401
    elif kind == "missing_bar": s.data["RELIANCE"]["data"]["candles"].pop(-2)
    elif kind == "wrong_instrument": s.data["RELIANCE"]["data"]["instrument_key"] = core.STOCKS["HDFCBANK"]
    else: s.data["RELIANCE"]["data"]["candles"][-1][2] = -1
    result = capture(collected)
    assert result["total_expected_prediction_evaluations"] == result["total_missing_insufficient"] == 1
    assert result["total_completed_evaluations"] == 0 and result["ai_up_frequency"] is None
    assert result["observation_bindings"][0]["validation_status"] == "CAPTURE_FAILED"
    assert used(s, "market_data_request_budget") == 2
    assert capture(collected) == result and len(s.http) == 2 and len(s.sdk) == 1


def test_missing_and_pending_observations_remain_in_offline_denominator(collected):
    s, _, prediction, end = collected
    missing = replay(collected)
    assert missing["total_missing_insufficient"] == missing["unevaluated_count"] == 1
    pending = replay(collected, as_of=end - pd.Timedelta(seconds=1))
    assert pending["total_pending"] == 1 and pending["evaluation_coverage_pct"] == 0
    assert len(s.http) == len(s.sdk) == 1


@pytest.mark.parametrize("flag", ["enable_signal_only", "enable_signal_future", "allow_market_data_requests"])
def test_followup_network_opt_ins_are_mandatory(collected, flag):
    s, _, _, _ = collected
    with pytest.raises(guard.GuardedBatchError): capture(collected, **{flag: False})
    assert len(s.http) == len(s.sdk) == 1


@pytest.mark.parametrize("when", ["early", "late", "holiday"])
def test_schedule_and_calendar_cannot_be_bypassed(collected, when):
    s, _, _, end = collected
    s.clock[0] = (end if when == "early" else end + pd.Timedelta(minutes=5) if when == "late" else
                  pd.Timestamp("2026-11-08T14:30:00+05:30"))
    with pytest.raises((guard.GuardedBatchError, ValueError)): capture(collected)
    assert used(s, "market_data_request_budget") == 1 and len(s.http) == 1


def test_restart_never_spends_another_request_and_local_replay_needs_no_keys(collected):
    s, _, _, end = collected
    result = capture(collected)
    before = {p: p.read_bytes() for p in s.output.rglob('*') if p.is_file()}
    s.clock[0] += pd.Timedelta(hours=5)
    s.settings.upstox_access_token = None
    assert replay(collected, as_of=end + pd.Timedelta(seconds=10)) == result
    assert all(p.read_bytes() == b for p, b in before.items())
    assert len(s.http) == 2 and len(s.sdk) == 1


def test_unknown_or_duplicate_artifacts_and_removed_prediction_fail(collected):
    s, _, _, _ = collected
    capture(collected)
    path = next((s.output / "future_observations").glob("*.json"))
    other = path.with_name('duplicate.json');other.write_bytes(path.read_bytes())
    with pytest.raises(guard.GuardedBatchError): replay(collected)
    other.unlink()
    next((s.output / "intents").glob("*.json")).unlink()
    next((s.output / "signals").glob("*.json")).unlink()
    with pytest.raises(guard.GuardedBatchError): replay(collected)


def test_policy_is_frozen_before_prediction_and_cannot_be_changed(collected, monkeypatch):
    s, _, _, _ = collected
    manifest = guard._read(s.output / signal.MANIFEST)
    assert manifest["future_observation_policy"]["bar_count"] == 6
    monkeypatch.setitem(future.POLICY, "bar_count", 7)
    with pytest.raises(guard.GuardedBatchError): capture(collected)
    assert len(s.http) == 1


def test_caller_and_synthetic_numeric_bodies_cannot_claim_reviewed_path(collected):
    _, _, prediction, end = collected
    from app.research.offline_session_calendar import OfflineSessionCalendar
    from app.research.prospective_preflight import CALENDAR_PATH
    from test_ai_signal_only import future as synthetic_bodies
    bodies, cutoff = synthetic_bodies(prediction)
    bodies['RELIANCE']['source'] = 'REVIEWED_UPSTOX_CAPTURE'
    result = evaluation.evaluate_signal(prediction, bodies, as_of=cutoff, calendar=OfflineSessionCalendar(CALENDAR_PATH))
    assert result['rows'][0]['observation_evidence']['provenance'] == 'UNVERIFIED_CALLER_SUPPLIED'
    assert replay(collected)['total_completed_evaluations'] == 0


def test_late_prediction_is_blocked_before_requests_to_leave_followup_window(setup):
    setup.body[0]['schema_version'] = signal.SCHEMA
    setup.clock[0] = pd.Timestamp('2026-10-28T14:40:01+05:30')
    with pytest.raises(guard.GuardedBatchError, match='future_horizon'):
        signal.run_signal_only(output_dir=setup.output, instrument_priority=['RELIANCE'], capital=1000,
            provider_cap=1, market_data_cap=2, enable_signal_only=True,
            allow_market_data_requests=True, allow_provider_requests=True)
    assert setup.http == setup.sdk == []


def test_direct_followup_methods_cannot_send_provider_quotes_or_duplicate_candles(collected):
    s, options, _, _ = collected
    capture(collected)
    binding = reviewed._ReviewedRun(**options, purpose='signal_future', enable_signal_future=True,
                                    allow_market_data_requests=True)
    config, calendar = signal.preflight(binding)
    binding.future_job = future.jobs(config, future.predictions(binding, config, calendar), calendar)[0]
    with pytest.raises(guard.GuardedBatchError): binding.complete({'system_prompt': 'arbitrary', 'user_payload': '{}'})
    with pytest.raises(guard.GuardedBatchError): binding._send_market('RELIANCE', quote=True)
    with pytest.raises(guard.GuardedBatchError): binding.fetch('RELIANCE')
    assert len(s.http) == 2 and len(s.sdk) == 1


@pytest.mark.parametrize('boundary', ['response', 'publication', 'seal'])
def test_interrupted_followup_fails_closed_without_a_second_request(collected, monkeypatch, boundary):
    s, _, _, _ = collected
    if boundary == 'response':
        s.behavior['http_error'] = KeyboardInterrupt()
    else:
        original = guard._write
        def interrupted(path, record):
            if path.parent.name == 'future_observations':
                if boundary == 'seal': original(path, record)
                raise KeyboardInterrupt()
            return original(path, record)
        monkeypatch.setattr(guard, '_write', interrupted)
    with pytest.raises(KeyboardInterrupt): capture(collected)
    result = capture(collected)
    assert result == replay(collected)
    assert result['total_missing_insufficient'] == 1 and result['total_completed_evaluations'] == 0
    expected = 'UNRECOVERABLE_PARTIAL_PUBLICATION' if boundary == 'seal' else 'UNRECOVERABLE_PARTIAL_CAPTURE'
    assert result['observation_bindings'][0]['validation_status'] == expected
    assert used(s, 'market_data_request_budget') == 2
    assert len(s.http) == 2 and len(s.sdk) == 1


def test_followup_cli_defaults_to_local_and_does_not_offer_provider_authorization(collected, monkeypatch, capsys):
    import sys
    from scripts.run_ai_signal_followup import main
    s, _, _, end = collected
    base = ['followup', '--output', str(s.output), '--instrument-priority', 'RELIANCE', '--paper-capital', '1000',
            '--max-provider-requests', '1', '--max-market-data-requests', '2', '--enable-signal-only',
            '--as-of', s.clock[0].isoformat()]
    before = {p: p.read_bytes() for p in s.output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sys, 'argv', base)
    assert main() == 0 and json.loads(capsys.readouterr().out)['total_missing_insufficient'] == 1
    assert all(p.read_bytes() == b for p, b in before.items())
    monkeypatch.setattr(sys, 'argv', base + ['--allow-provider-requests'])
    with pytest.raises(SystemExit): main()
    assert len(s.http) == len(s.sdk) == 1
