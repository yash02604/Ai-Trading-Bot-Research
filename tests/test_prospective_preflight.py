"""Real preflight/entry-point integration; all provider and socket access is mocked."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pandas as pd
import pytest

import app.config
import app.upstox_client
import app.research.deepseek_prospective as prospective
import app.research.prospective_preflight as gate
from app.client_attestation import ATTESTATION, ROOT, verify_client_attestation
from app.v2.provider import ProviderResult

NOW = pd.Timestamp("2026-10-28T12:30:00+05:30")


class MockProvider:
    calls = 0

    def complete(self, payload):
        self.calls += 1
        return ProviderResult('{"decision":"BUY","confidence":0.9,"reason":"mock"}',
                              "mock", "deepseek-flash", "deepseek-flash", 0)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("external access forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        paper_trading=True, upstox_access_token="mock-only"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = NOW.to_pydatetime()
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(prospective, "datetime", Clock)
    original_prepare = prospective._prepare_quant_candidates
    def latest(frame):
        prepared, _ = original_prepare(frame)
        return prepared, {len(prepared) - 1}
    monkeypatch.setattr(prospective, "_prepare_quant_candidates", latest)
    candles = [dict(timestamp=(NOW - pd.Timedelta(minutes=5 * (39-i))).isoformat(),
                    open=600, high=601, low=599, close=600, volume=10000)
               for i in range(39)]
    body = {"schema_version": prospective.PROSPECTIVE_SCHEMA,
            "source": "synthetic-mock-only", "symbol": "RELIANCE",
            "instrument_key": prospective.STOCKS["RELIANCE"],
            "observed_at": NOW.isoformat(), "candles": candles}
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(body))
    output = tmp_path / "new-experiment"
    initialize(output)
    calls = []
    def get(*args, **kwargs):
        calls.append(args)
        return {"data": {"candles": [[r[k] for k in (
            "timestamp", "open", "high", "low", "close", "volume")] + [0] for r in candles]}}
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", get)
    return SimpleNamespace(output=output, snapshot=snapshot, body=body, calls=calls,
                           provider=MockProvider())


def initialize(output, capital=1000, priority=None, market_cap=2, provider_cap=2, smoke_cap=1):
    return gate.initialize_request_policy(
        output, capital=capital, instrument_priority=priority or ["RELIANCE"],
        model="deepseek-flash", market_data_cap=market_cap,
        provider_cap=provider_cap, smoke_cap=smoke_cap, now=NOW)


def check(s, now=NOW, **kwargs):
    return gate.prospective_preflight(output_dir=s.output, symbol="RELIANCE", now=now,
                                     kind="market_data", maximum=2, **kwargs)


def record(s, **kwargs):
    return prospective.record_prospective_snapshot(
        s.snapshot, output_dir=s.output, provider=s.provider, model="deepseek-flash",
        max_total_requests=2, paper_capital=1000, now=NOW,
        allow_provider_requests=True, **kwargs)


def capture(s, unlocked=False):
    fn = (prospective._capture_upstox_snapshot_unlocked if unlocked
          else prospective.capture_upstox_snapshot)
    return fn("RELIANCE", output_dir=s.output, max_market_data_requests=2,
              allow_market_data_requests=True)


def tree_hash(output):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.rglob("*") if p.is_file() and p.name != ".prospective.lock"}


@pytest.mark.parametrize("capital", [1000, 2500, 10000, 100000])
def test_valid_evidence_policy_and_configurable_capital_pass_offline(setup, tmp_path, capital):
    output = tmp_path / str(capital)
    initialize(output, capital=capital)
    result = gate.prospective_preflight(output_dir=output, symbol="RELIANCE", now=NOW,
                                       kind="provider", maximum=2, capital=capital,
                                       model="deepseek-flash")
    assert result["client"]["current_source_verified"] is True
    assert result["policy"]["initial_capital"] == capital
    assert result["policy"]["trade_placement_enabled"] is False
    assert not list(output.glob("*budget*"))


@pytest.mark.parametrize("now", ["2027-01-01T12:30:00+05:30", "2026-11-10T12:30:00+05:30",
                                 "2026-11-07T12:30:00+05:30", "2026-11-08T14:30:00+05:30",
                                 "2026-10-28T08:55:00+05:30", "2026-10-28T15:15:00+05:30",
                                 "2026-10-28T12:30:00", None])
def test_unsupported_clock_calendar_and_special_sessions_block(setup, now):
    before = tree_hash(setup.output)
    with pytest.raises(gate.ProspectivePreflightError):
        check(setup, now=now)
    assert tree_hash(setup.output) == before


def test_dated_cas_cutoff_enforced(setup):
    with pytest.raises(gate.ProspectivePreflightError):
        check(setup, now="2026-12-04T15:20:00+05:30")
    assert check(setup, now="2026-12-07T15:20:00+05:30")


@pytest.mark.parametrize("defect", ["missing_policy", "malformed_policy", "capital", "priority",
                                    "instrument", "cap", "model", "calendar_pin", "attestation_pin",
                                    "experiment", "missing_spec", "missing_model",
                                    "budget_cap", "budget_orphan", "paper_disabled"])
def test_all_entry_paths_fail_before_requests_or_reservations(setup, monkeypatch, defect):
    path = setup.output / gate.POLICY_FILE
    policy = json.loads(path.read_bytes())
    if defect == "missing_policy":
        path.unlink()  # Only this test's new disposable directory.
    elif defect == "malformed_policy":
        path.write_text("[]")
    elif defect == "experiment":
        (setup.output / prospective.EXPERIMENT_CAPITAL_CONFIG).write_text("{}")
    elif defect in ("missing_spec", "missing_model"):
        config_path = setup.output / prospective.EXPERIMENT_CAPITAL_CONFIG
        config = json.loads(config_path.read_bytes())
        if defect == "missing_spec":
            config.pop("experiment_spec")
        else:
            config["experiment_spec"]["model_id"] = None
        config_path.write_text(json.dumps(config))
    elif defect == "budget_cap":
        (setup.output / "request_budget_config.json").write_text('{"max_total_requests":9}')
    elif defect == "budget_orphan":
        (setup.output / "request_budget.jsonl").write_text('{"request_id":"old"}\n')
    elif defect == "paper_disabled":
        monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(paper_trading=False))
    else:
        key, value = {"capital": ("initial_capital", -1), "priority": ("instrument_priority", ["BAD"]),
                      "instrument": ("instrument_keys", {"RELIANCE": "wrong"}),
                      "cap": ("request_caps", {"market_data": True, "provider": 2, "smoke": 1}),
                      "model": ("model_id", "other-model"),
                      "calendar_pin": ("calendar_sha256", "changed"),
                      "attestation_pin": ("client_attestation_sha256", "changed")}[defect]
        policy[key] = value
        path.write_text(json.dumps(policy))
    before = tree_hash(setup.output)
    for operation in (lambda: capture(setup), lambda: capture(setup, unlocked=True),
                      lambda: record(setup),
                      lambda: prospective._record_prospective_snapshot_unlocked(
                          setup.snapshot, output_dir=setup.output, provider=setup.provider,
                          model="deepseek-flash", max_total_requests=2, paper_capital=1000,
                          now=NOW, allow_provider_requests=True),
                      lambda: prospective.run_provider_smoke_test(
                          setup.snapshot, output_dir=setup.output, provider=setup.provider,
                          model="deepseek-flash", max_total_requests=1, now=NOW,
                          allow_provider_requests=True),
                      lambda: prospective._run_provider_smoke_test_unlocked(
                          setup.snapshot, output_dir=setup.output, provider=setup.provider,
                          model="deepseek-flash", max_total_requests=1, now=NOW,
                          allow_provider_requests=True)):
        with pytest.raises(gate.ProspectivePreflightError):
            operation()
    assert setup.calls == [] and setup.provider.calls == 0
    assert tree_hash(setup.output) == before


@pytest.mark.parametrize("defect", ["client", "missing_attestation", "corrupt_attestation"])
def test_real_attestation_validation_blocks_modified_copies(setup, tmp_path, monkeypatch, defect):
    root = tmp_path / "attestation-copy"
    shutil.copytree(ROOT / "data/attestations", root / "data/attestations")
    audit = "backtests/provenance_integrity_v1/input_hashes.json"
    (root / audit).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / audit, root / audit)
    (root / "app").mkdir()
    shutil.copyfile(ROOT / "app/upstox_client.py", root / "app/upstox_client.py")
    path = root / ("app/upstox_client.py" if defect == "client" else ATTESTATION)
    if defect == "missing_attestation":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n# modified")
    monkeypatch.setattr(gate, "verify_client_attestation", lambda: verify_client_attestation(root))
    with pytest.raises(gate.ProspectivePreflightError):
        capture(setup)
    with pytest.raises(gate.ProspectivePreflightError):
        record(setup)
    assert setup.calls == [] and setup.provider.calls == 0
    assert not list(setup.output.glob("*budget*"))


@pytest.mark.parametrize("defect", ["manifest", "source"])
def test_changed_calendar_evidence_blocks_requests(setup, tmp_path, monkeypatch, defect):
    if defect == "manifest":
        path = tmp_path / "bad-calendar.json"
        path.write_bytes(gate.CALENDAR_PATH.read_bytes() + b" ")
        monkeypatch.setattr(gate, "CALENDAR_PATH", path)
    else:
        import app.session_manifest_v3 as v3
        directory = tmp_path / "evidence"
        shutil.copytree(ROOT / v3.EVIDENCE, directory)
        (directory / "CMTR71775.pdf").write_bytes(b"changed evidence")
        monkeypatch.setattr(v3, "EVIDENCE", str(directory))
    with pytest.raises(gate.ProspectivePreflightError):
        capture(setup)
    with pytest.raises(gate.ProspectivePreflightError):
        record(setup)
    assert setup.calls == [] and setup.provider.calls == 0
    assert not list(setup.output.glob("*budget*"))


@pytest.mark.parametrize("defect", ["incomplete", "missing_bar", "overnight", "identity", "stale", "ohlcv"])
def test_post_capture_validation_blocks_deepseek(setup, defect):
    body = setup.body
    if defect == "incomplete":
        body["candles"][-1]["timestamp"] = (NOW - pd.Timedelta(minutes=1)).isoformat()
    elif defect == "missing_bar":
        body["candles"].pop(-10)
    elif defect == "overnight":
        body["candles"][10]["timestamp"] = "2026-10-27T12:30:00+05:30"
    elif defect == "identity":
        body["instrument_key"] = "wrong"
    elif defect == "stale":
        body["observed_at"] = (NOW - pd.Timedelta(minutes=16)).isoformat()
    else:
        body["candles"][-1]["high"] = -1
    setup.snapshot.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        record(setup)
    assert setup.provider.calls == 0
    assert not list(setup.output.glob("*budget*"))


def test_failed_upstox_post_validation_keeps_spent_reservation(setup, monkeypatch):
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *a, **k: {"data": {"candles": []}})
    with pytest.raises(RuntimeError, match="failure recorded"):
        capture(setup)
    budget = prospective.PersistentRequestBudget(setup.output / "market_data_request_budget.jsonl", 2)
    assert budget.used == 1
    setup.snapshot.write_text("{}")
    with pytest.raises(ValueError):
        record(setup)
    assert budget.used == 1 and setup.provider.calls == 0


def test_capture_calendar_validation_failure_preserves_reservation(setup, monkeypatch):
    body = setup.body
    # Still 39 aligned completed OHLCV rows; one is outside the verified session.
    rows = body["candles"]
    rows[0]["timestamp"] = (pd.Timestamp(rows[0]["timestamp"]) - pd.Timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *a, **k: {
        "data": {"candles": [[r[f] for f in ("timestamp", "open", "high", "low", "close", "volume")]
                             + [0] for r in rows]}})
    with pytest.raises(RuntimeError, match="failure recorded"):
        capture(setup)
    assert prospective.PersistentRequestBudget(
        setup.output / "market_data_request_budget.jsonl", 2).used == 1
    assert not list((setup.output / "snapshots").glob("*"))
    assert setup.provider.calls == 0


def test_budget_exhaustion_preserves_existing_failure_category(setup, tmp_path):
    setup.output = tmp_path / "zero-budget"
    initialize(setup.output, market_cap=0)
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=setup.output,
                                           max_market_data_requests=0, allow_market_data_requests=True)
    failure = json.loads((setup.output / "market_data_failures.jsonl").read_text())
    assert failure["category"] == "request_budget_exhausted"
    assert setup.calls == []
    assert prospective.PersistentRequestBudget(
        setup.output / "market_data_request_budget.jsonl", 0).used == 0


def test_valid_capture_provider_and_restart_revalidate(setup, monkeypatch):
    path = capture(setup)
    assert path.exists() and len(setup.calls) == 1
    result = record(setup)
    assert result["request_attempted"] is True and setup.provider.calls == 1
    assert result["trade_placement_enabled"] is False
    assert record(setup)["idempotent_replay"] is True
    assert setup.provider.calls == 1
    policy = setup.output / gate.POLICY_FILE
    policy.write_text("{}")
    with pytest.raises(gate.ProspectivePreflightError):
        record(setup)
    with pytest.raises(gate.ProspectivePreflightError):
        capture(setup)
    assert setup.provider.calls == 1 and len(setup.calls) == 1


def test_valid_smoke_remains_non_trading(setup, monkeypatch):
    provider = MockProvider()
    def complete(payload):
        provider.calls += 1
        return ProviderResult('{"status":"ok"}', "mock", "deepseek-flash", "deepseek-flash", 0)
    monkeypatch.setattr(provider, "complete", complete)
    report = prospective.run_provider_smoke_test(
        setup.snapshot, output_dir=setup.output, provider=provider, model="deepseek-flash",
        max_total_requests=1, now=NOW, allow_provider_requests=True)
    assert provider.calls == 1
    assert report["trading_decision_created"] is False
    assert report["trade_placement_enabled"] is False
    assert not (setup.output / "observations.jsonl").exists()


def test_existing_experiment_cannot_change_capital_caps_or_universe(setup):
    before = tree_hash(setup.output)
    for kwargs in ({"capital": 10000}, {"provider_cap": 3}, {"priority": ["RELIANCE", "TCS"]}):
        with pytest.raises(gate.ProspectivePreflightError):
            initialize(setup.output, **kwargs)
    assert tree_hash(setup.output) == before


def test_legacy_directory_is_not_retrofitted(setup, tmp_path):
    output = tmp_path / "old-output"
    output.mkdir()
    old = output / "observations.jsonl"
    old.write_text('{"historical":"evidence"}\n')
    with pytest.raises(gate.ProspectivePreflightError):
        initialize(output)
    assert old.read_text() == '{"historical":"evidence"}\n'
    assert not (output / gate.POLICY_FILE).exists()


def test_ai_account_gate_precedes_provider(setup, monkeypatch):
    original = prospective.check_trade_allowed
    calls = []
    def check_risk(config, state):
        calls.append(state)
        return original(config, state) if len(calls) == 1 else (False, "mock account blocked")
    monkeypatch.setattr(prospective, "check_trade_allowed", check_risk)
    result = record(setup)
    assert result["provider_skip_reason"] == "ai_account_risk_or_cost_ineligible"
    assert setup.provider.calls == 0
    assert prospective.PersistentRequestBudget(setup.output / "request_budget.jsonl", 2).used == 0


def test_offline_cli_checks_only_and_does_not_reserve(setup, monkeypatch, capsys):
    import sys
    from scripts.check_prospective_preflight import main
    before = tree_hash(setup.output)
    monkeypatch.setattr(sys, "argv", ["preflight", "--output", str(setup.output),
        "--instrument-priority", "RELIANCE", "--paper-capital", "1000",
        "--max-market-data-requests", "2", "--max-total-requests", "2",
        "--max-smoke-requests", "1", "--as-of", NOW.isoformat()])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["api_requests"] == 0
    assert tree_hash(setup.output) == before


@pytest.mark.parametrize("mode", ["fetch", "snapshot", "smoke"])
def test_original_cli_cannot_bypass_preflight(setup, monkeypatch, capsys, mode):
    import sys
    import app.v2.provider
    import scripts.run_deepseek_prospective_research as runner
    from scripts.run_deepseek_prospective_research import main
    monkeypatch.setattr(runner, "datetime", prospective.datetime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        paper_trading=True, upstox_access_token="mock", deepseek_api_key="mock",
        deepseek_model="deepseek-flash", request_timeout_seconds=1))
    monkeypatch.setattr(app.v2.provider, "DeepSeekProvider", lambda **kwargs: setup.provider)
    # All flags explicitly opt in; the immutable policy STILL takes precedence.
    # Wrong provider cap must block before Upstox for a combined capture command.
    flags = ["--mode", "prospective-paper", "--enable-prospective",
             "--allow-provider-requests", "--paper-capital", "1000", "--max-total-requests", "9"]
    if mode == "fetch":
        flags += ["--fetch-upstox", "--symbol", "RELIANCE", "--allow-market-data-requests",
                  "--max-market-data-requests", "2"]
    elif mode == "snapshot":
        flags += ["--snapshot", str(setup.snapshot)]
    else:
        flags = ["--mode", "provider-smoke-test", "--enable-provider-smoke-test",
                 "--allow-provider-requests", "--snapshot", str(setup.snapshot),
                 "--max-total-requests", "9"]
    monkeypatch.setattr(sys, "argv", ["runner", *flags, "--output", str(setup.output)])
    before = tree_hash(setup.output)
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    assert setup.provider.calls == 0 and setup.calls == []
    assert tree_hash(setup.output) == before
