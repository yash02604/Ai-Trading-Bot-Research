import copy
import json

import pandas as pd
import pytest

from test_ai_led_reviewed import setup, used, PRIORITY, NOW
from app.research import ai_signal_only as signal, ai_led_reviewed as reviewed, ai_led_guarded as guard, ai_led_offline as core
from app.research import signal_evaluation as evaluation
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH


def options(s, **changes):
    s.body[0]["schema_version"] = signal.SCHEMA
    result = dict(output_dir=s.output, instrument_priority=PRIORITY, capital=1000, provider_cap=1, market_data_cap=6,
                  include_quotes=True, enable_signal_only=True, allow_market_data_requests=True, allow_provider_requests=True)
    result.update(changes)
    return result


@pytest.mark.parametrize("quotes", [False, True])
def test_complete_batch_independent_prediction_never_touches_account(setup, monkeypatch, quotes):
    def denied(*a, **k):
        pytest.fail("signal collection called a paper-account/fill path")
    monkeypatch.setattr(guard, "_account", denied)
    monkeypatch.setattr(core, "run_offline_batch", denied)
    monkeypatch.setattr(core, "_advance", denied)
    monkeypatch.setattr(core, "_commit", denied)
    opts = options(setup, include_quotes=quotes)
    setup.behavior["after_sdk"] = lambda: setup.clock.__setitem__(0, NOW + pd.Timedelta(seconds=2))
    record = signal.run_signal_only(**opts)
    assert record["mode"] == "AI_SIGNAL_ONLY" and record["status"] == "VALID_PREDICTION"
    assert record["prediction"]["action"] == "BUY" and not any(record["baseline"].values())
    assert record["depth_source_freshness"] == "UNKNOWN" and record["taken"] is record["execution_enabled"] is False
    assert pd.Timestamp(record["decision_ready_at"]) >= pd.Timestamp(record["provider_response_received_at"]) >= NOW
    assert len(setup.sdk) == used(setup, "request_budget") == 1
    assert len(setup.http) == used(setup, "market_data_request_budget") == (4 if quotes else 2)
    assert not (setup.output / "paper_account").exists() and not (setup.output / guard.MANIFEST).exists()
    request = json.loads(setup.sdk[0]["messages"][1]["content"])
    assert set(request["market"]) == set(PRIORITY)
    assert all(pd.Timestamp(row["timestamp"]) + pd.Timedelta(minutes=5) <= pd.Timestamp(request["decision_time"])
               for body in request["market"].values() for row in body["candles"])
    before = {p: p.read_bytes() for p in setup.output.rglob("*") if p.is_file()}
    assert signal.run_signal_only(**opts) == record
    assert all(p.read_bytes() == data for p, data in before.items())
    assert len(setup.sdk) == 1
    assert bool(list((setup.output / "market_observations").glob("*.json"))) is quotes


@pytest.mark.parametrize("flag", ["enable_signal_only", "allow_market_data_requests", "allow_provider_requests"])
def test_explicit_opt_ins_required_before_initialization_or_requests(setup, flag):
    opts = options(setup); opts[flag] = False
    with pytest.raises(guard.GuardedBatchError):
        signal.run_signal_only(**opts)
    assert setup.http == setup.sdk == [] and not setup.output.exists()


@pytest.mark.parametrize("kind", ["no_trade", "score", "symbol", "rationale", "malformed", "provider_error", "wrong_model"])
def test_no_trade_invalid_responses_and_failures_are_distinct_safe_records(setup, kind):
    opts = options(setup)
    if kind == "no_trade": setup.body[0].update(action="NO_TRADE", proposals=[], rationale_codes=["no_edge"])
    elif kind == "score": setup.body[0]["proposals"][0]["score"] = 2
    elif kind == "symbol": setup.body[0]["proposals"][0]["symbol"] = "FAKE"
    elif kind == "rationale": setup.body[0]["rationale_codes"] = ["sk-secret-PRIVATE"]
    elif kind == "malformed": setup.body[0] = "private-body-not-json"
    elif kind == "provider_error": setup.behavior["sdk_error"] = RuntimeError("sk-secret-PRIVATE Bearer private-body")
    else: setup.behavior["returned_model"] = "wrong-model"
    record = signal.run_signal_only(**opts)
    if kind == "no_trade":
        assert record["status"] == "VALID_PREDICTION" and record["prediction"]["action"] == "NO_TRADE"
    else:
        assert record["status"] != "VALID_PREDICTION" and record["prediction"] is None
    assert used(setup, "request_budget") == len(setup.sdk) == 1
    for p in setup.output.rglob("*"):
        if p.is_file():
            assert all(secret not in p.read_bytes() for secret in [b"sk-secret-PRIVATE", b"private-body", b"FAKE-ACCESS-TOKEN-PRIVATE", b"sk-FAKE-KEY-PRIVATE"])
    assert signal.run_signal_only(**opts) == record and len(setup.sdk) == 1


@pytest.mark.parametrize("kind", ["missing", "future", "calendar", "attestation"])
def test_incomplete_future_or_unverified_batch_never_calls_provider(setup, monkeypatch, kind):
    opts = options(setup)
    if kind == "missing": setup.data["HDFCBANK"]["data"]["candles"] = []
    elif kind == "future": setup.data["HDFCBANK"]["data"]["candles"][-1][0] = (NOW + pd.Timedelta(minutes=5)).isoformat()
    elif kind == "calendar": setup.clock[0] = pd.Timestamp("2026-11-08T14:30:00+05:30")
    else:
        import app.research.prospective_preflight as preflight
        monkeypatch.setattr(preflight, "verify_client_attestation", lambda: (_ for _ in ()).throw(ValueError("private")))
    with pytest.raises((guard.GuardedBatchError, ValueError)):
        signal.run_signal_only(**opts)
    assert setup.sdk == [] and used(setup, "request_budget") == 0


@pytest.mark.parametrize("capital", [1000, 5000, 10000])
def test_capital_is_configurable_immutable_and_not_used_for_executable_sizing(setup, capital):
    opts = options(setup, capital=capital)
    # Prices exceed the smallest account, but predictions do not require shares.
    for response in setup.data.values():
        for row in response["data"]["candles"]: row[1:5] = [p * 3 for p in row[1:5]]
    for response in setup.quotes.values():
        depth = next(iter(response["data"].values()))["depth"]
        for side in ("buy", "sell"): depth[side][0]["price"] *= 3
    setup.body[0]["proposals"][0].update(entry_price=1800, stop_loss=1782, target_price=1836)
    record = signal.run_signal_only(**opts)
    assert record["status"] == "VALID_PREDICTION" and not (setup.output / "paper_account").exists()
    with pytest.raises(guard.GuardedBatchError):
        signal.run_signal_only(**{**opts, "capital": capital + 1})
    assert len(setup.sdk) == 1


def test_local_recovery_after_response_commit_failure_needs_no_keys_or_new_requests(setup, monkeypatch):
    opts = options(setup)
    write = guard._write
    def interrupted(path, record):
        if path.parent.name == "signals": raise OSError("test storage failure")
        return write(path, record)
    monkeypatch.setattr(guard, "_write", interrupted)
    with pytest.raises(OSError): signal.run_signal_only(**opts)
    monkeypatch.setattr(guard, "_write", write)
    setup.clock[0] += pd.Timedelta(hours=5)
    setup.settings.upstox_access_token = setup.settings.deepseek_api_key = None
    opts.update(recover_only=True, allow_market_data_requests=False, allow_provider_requests=False)
    first = signal.run_signal_only(**opts)
    assert signal.run_signal_only(**opts) == first
    assert pd.Timestamp(first["decision_ready_at"]) == NOW and len(setup.http) == 4 and len(setup.sdk) == 1


def test_spent_interrupted_request_is_not_repeated_or_reconstructed(setup):
    opts = options(setup)
    setup.behavior["sdk_error"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): signal.run_signal_only(**opts)
    with pytest.raises(guard.GuardedBatchError, match="no_saved_response"): signal.run_signal_only(**opts)
    assert len(setup.sdk) == used(setup, "request_budget") == 1


def test_provider_cap_zero_and_direct_entry_points_are_guarded(setup):
    opts = options(setup, provider_cap=0)
    record = signal.run_signal_only(**opts)
    assert record["status"] == "PROVIDER_BUDGET_EXHAUSTED" and setup.sdk == []
    binding = reviewed._ReviewedRun(**{k: v for k, v in opts.items() if k not in {"output_dir", "instrument_priority"}},
        output_dir=setup.output, instrument_priority=PRIORITY, purpose="signal")
    with pytest.raises(Exception): binding.complete({"system_prompt": "arbitrary", "user_payload": "{}"})
    assert setup.sdk == []


def test_signal_cli_requires_mode_specific_authorization_and_cannot_mix_execution(setup, monkeypatch, capsys):
    import sys
    from scripts.run_ai_led_prospective_paper import main
    base = ["runner", "--output", str(setup.output), "--instrument-priority", *PRIORITY, "--paper-capital", "1000",
            "--max-provider-requests", "1", "--max-market-data-requests", "4", "--signal-only"]
    monkeypatch.setattr(sys, "argv", base)
    assert main() == 2 and not setup.http and not setup.sdk
    monkeypatch.setattr(sys, "argv", base + ["--enable-signal-only", "--enable-prospective-paper"])
    with pytest.raises(SystemExit): main()
    options(setup)
    monkeypatch.setattr(sys, "argv", base + ["--enable-signal-only", "--allow-market-data-requests", "--allow-provider-requests"])
    assert main() == 0
    assert not (setup.output / "paper_account").exists()


def future(record, *, both=False, missing=False):
    start = pd.Timestamp(record["decision_ready_at"]).floor("5min") + pd.Timedelta(minutes=5)
    stamps = pd.date_range(start, periods=6, freq="5min")
    end = stamps[-1] + pd.Timedelta(minutes=5)
    bodies = {s: {"symbol": s, "instrument_key": core.STOCKS[s], "observed_at": end.isoformat(),
        "candles": [{"timestamp": t.isoformat(), "open": 600, "high": 602, "low": 599, "close": 601, "volume": 1000}
                    for t in stamps]} for s in PRIORITY}
    if both: bodies["RELIANCE"]["candles"][0].update(high=613, low=593)
    if missing: bodies["RELIANCE"]["candles"].pop()
    return bodies, end


@pytest.mark.parametrize("kind", ["valid", "missing", "ambiguous", "not_yet_observed"])
def test_offline_prediction_evaluation_has_no_execution_profitability(setup, kind):
    record = signal.run_signal_only(**options(setup))
    bodies, end = future(record, both=kind == "ambiguous", missing=kind == "missing")
    cutoff = end - pd.Timedelta(minutes=1) if kind == "not_yet_observed" else end
    before = copy.deepcopy(record)
    result = evaluation.evaluate_signal(record, bodies, as_of=cutoff, calendar=OfflineSessionCalendar(CALENDAR_PATH))
    assert result["execution_pnl"] is None and result["trade_placement_enabled"] is False and record == before
    assert result["status"] == ("PENDING_FUTURE_DATA" if kind == "not_yet_observed" else
                                "INCOMPLETE_FUTURE_DATA" if kind == "missing" else "EVALUATED")
    if kind == "ambiguous": assert result["rows"][0]["target_stop_order"] == "AMBIGUOUS"
    summary = evaluation.summarize_predictions([record], [result])
    assert summary["calibration_status"] == "INSUFFICIENT_SAMPLE" and summary["execution_pnl"] is None
    with pytest.raises(ValueError): evaluation.summarize_predictions([record, record], [result])


def test_tampered_response_or_raw_capture_cannot_be_recovered_as_a_prediction(setup):
    opts = options(setup)
    record = signal.run_signal_only(**opts)
    original = next((setup.output / "received").glob("*.json"))
    original.write_text("{}")
    opts.update(recover_only=True, allow_market_data_requests=False, allow_provider_requests=False)
    with pytest.raises(guard.GuardedBatchError): signal.run_signal_only(**opts)
    assert len(setup.sdk) == 1 and record["taken"] is False


def test_published_price_bands_reject_impossible_signal_prices_when_available(setup):
    opts = options(setup)
    next(iter(setup.quotes["RELIANCE"]["data"].values())).update(lower_circuit_limit=595, upper_circuit_limit=610)
    record = signal.run_signal_only(**opts)
    assert record["status"] == "INVALID_PROPOSAL" and record["prediction"] is None
    assert used(setup, "request_budget") == 1


def test_signal_manifest_cannot_adopt_an_existing_experiment_directory(setup):
    opts = options(setup)
    setup.output.mkdir()
    artifact = setup.output / "existing_budget.json"
    artifact.write_text('{"used":2}')
    with pytest.raises(guard.GuardedBatchError, match="fresh_directory"):
        signal.run_signal_only(**opts)
    assert artifact.read_text() == '{"used":2}' and not setup.http and not setup.sdk


def test_evaluation_policy_is_frozen_before_first_provider_call(setup, monkeypatch):
    opts = options(setup)
    assert signal.run_signal_only(**{**opts, "preflight_only": True})["status"] == "PREFLIGHT_ONLY"
    assert not setup.http and not setup.sdk
    monkeypatch.setitem(evaluation.POLICY, "horizon_minutes", 60)
    with pytest.raises(guard.GuardedBatchError, match="configuration_changed"):
        signal.run_signal_only(**opts)
    assert not setup.http and not setup.sdk


def test_summary_reports_no_trade_coverage_and_descriptive_confidence_only(setup):
    record = signal.run_signal_only(**options(setup))
    bodies, end = future(record)
    result = evaluation.evaluate_signal(record, bodies, as_of=end, calendar=OfflineSessionCalendar(CALENDAR_PATH))
    # Synthetic copies exercise aggregation only; these are not new AI decisions.
    decisions, outcomes = [], []
    for n in range(30):
        d, e = copy.deepcopy(record), copy.deepcopy(result)
        d["decision_id"] = e["decision_id"] = str(n)
        e["decision_sha256"] = core._digest(d)
        decisions.append(d); outcomes.append(e)
    summary = evaluation.summarize_predictions(decisions, outcomes)
    assert summary["candidate_coverage"] == .5 and summary["no_trade_frequency"] == 0
    assert summary["calibration_status"] == "DESCRIPTIVE_SCORES_NOT_ESTABLISHED_PROBABILITIES"
    assert sum(b["count"] for b in summary["confidence_bins"]) == 30 and summary["execution_pnl"] is None
    outcomes[0]["policy_sha256"] = "tampered"
    with pytest.raises(ValueError, match="identity_mismatch"):
        evaluation.summarize_predictions(decisions, outcomes)


def test_signal_prompt_identity_matches_manifest_and_saved_request(setup):
    import hashlib
    record = signal.run_signal_only(**options(setup))
    manifest = guard._read(setup.output / signal.MANIFEST)
    request = guard._read(setup.output / "requests" / (record["provider_request_id"] + ".json"))
    actual = hashlib.sha256(request["payload"]["system_prompt"].encode("utf-8")).hexdigest()
    assert actual == manifest["prompt_sha256"] == manifest["account"]["prompt_sha256"]
    assert request["payload"]["system_prompt"] == signal.PROMPT
    assert actual != hashlib.sha256(guard.PROMPT.replace(guard.SCHEMA, guard.REVIEWED_SCHEMA).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("kind", ["paper_hash", "saved_prompt", "changed_prompt"])
def test_signal_prompt_conflicts_cannot_resume_or_rewrite_evidence(setup, monkeypatch, kind):
    import hashlib
    opts = options(setup)
    signal.run_signal_only(**opts)
    if kind == "changed_prompt":
        monkeypatch.setattr(signal, "PROMPT", signal.PROMPT + " changed")
    else:
        path = (setup.output / signal.MANIFEST if kind == "paper_hash" else
                next((setup.output / "requests").glob("*.json")))
        raw = guard._read(path)
        if kind == "paper_hash":
            raw["prompt_sha256"] = hashlib.sha256(guard.PROMPT.replace(guard.SCHEMA, guard.REVIEWED_SCHEMA).encode("utf-8")).hexdigest()
        else:
            raw["payload"]["system_prompt"] = guard.PROMPT
        path.write_bytes(core._encoded({"sha256": core._digest(raw), "record": raw}))
    before = {p: p.read_bytes() for p in setup.output.rglob("*") if p.is_file()}
    opts.update(recover_only=True, allow_market_data_requests=False, allow_provider_requests=False)
    with pytest.raises(guard.GuardedBatchError):
        signal.run_signal_only(**opts)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert len(setup.sdk) == 1 and not (setup.output / "paper_account").exists()


def test_saved_inputs_reject_conflicting_manifest_prompt_hash(setup):
    opts = options(setup)
    signal.run_signal_only(**opts)
    manifest = guard._read(setup.output / signal.MANIFEST)
    intent = guard._read(next((setup.output / "intents").glob("*.json")))
    manifest["prompt_sha256"] = "0" * 64
    with pytest.raises(guard.GuardedBatchError, match="saved_request_invalid"):
        signal._saved_inputs(setup.output, intent, manifest, OfflineSessionCalendar(CALENDAR_PATH))
