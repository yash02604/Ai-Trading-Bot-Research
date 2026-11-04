import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import math
from pathlib import Path

import pandas as pd
import pytest

import app.research.deepseek_prospective as prospective
from app.research.deepseek_prospective import (
    PersistentRequestBudget,
    ProspectiveCache,
    _cost_aware_position_check,
    _quant_baseline,
    offline_synthetic_check,
    record_prospective_snapshot,
    run_provider_smoke_test,
)
from app.risk_engine import RiskConfig, calculate_daily_loss_limit, calculate_max_trade_risk
from app.v2.provider import ProviderResult
from scripts.run_deepseek_research_eval_v1 import _write_synthetic_candles


@pytest.fixture(autouse=True)
def isolated_request_gate_unit_boundary(monkeypatch):
    """Legacy lifecycle/diagnostic unit tests; real gates tested separately.

    These fixtures intentionally include incomplete synthetic candles and legacy
    directories. No production bypass exists: integration tests exercise the
    unpatched entry points with real evidence and immutable policies.
    """
    monkeypatch.setattr(prospective, "prospective_preflight", lambda **kwargs: {})
    monkeypatch.setattr(prospective, "validate_request_snapshot", lambda *args: None)


class MockProvider:
    model_id = "deepseek-flash"
    provider_id = "mock-deepseek"

    def __init__(self, response=None, error=None):
        self.response = response or '{"decision":"BUY","confidence":0.8,"reason":"fixture"}'
        self.error = error
        self.calls = 0
        self.payloads = []

    def complete(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return ProviderResult(self.response, self.provider_id, self.model_id,
                              self.model_id, 1.5, prompt_tokens=20, completion_tokens=5)


def snapshot_fixture(tmp_path, *, count=39, source="synthetic-fixture", mutation=None):
    candles_path = _write_synthetic_candles(tmp_path / "source.csv")
    frame = pd.read_csv(candles_path).iloc[:count].copy()
    if mutation:
        mutation(frame)
    # Exercise the fixed ₹1,000 prospective sizing path with one-share prices.
    frame[["open", "high", "low", "close"]] *= 6
    last = pd.Timestamp(frame.iloc[-1].timestamp)
    observed_at = (last + pd.Timedelta(minutes=1)).isoformat()
    body = {"schema_version": "deepseek-prospective-observation-v1",
            "source": source, "symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018",
            "observed_at": observed_at,
            "candles": frame.to_dict(orient="records")}
    path = tmp_path / f"snapshot-{source}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path, pd.Timestamp(observed_at).to_pydatetime() + timedelta(seconds=1), body


def test_cost_aware_gate_accepts_affordable_one_share_and_documents_gap_risk():
    result = _cost_aware_position_check(
        capital=1000, entry_price=600, stop_price=594, quantity=1,
        trade_timestamp=pd.Timestamp("2025-02-03T10:00:00+05:30"),
    )
    assert result["eligible"] is True
    assert result["entry_notional"] == pytest.approx(600)
    assert result["estimated_buy_side_costs"] == pytest.approx(1.0483356)
    assert result["entry_cash_required"] == pytest.approx(601.0483356)
    assert result["estimated_stop_loss"] == pytest.approx(6)
    assert result["estimated_round_trip_costs"] == pytest.approx(2.216867844)
    assert result["cost_inclusive_stop_risk"] == pytest.approx(8.216867844)
    assert result["max_trade_risk"] == pytest.approx(10)
    assert result["stop_gap_can_exceed_estimate"] is True


@pytest.mark.parametrize("capital,entry_price", [
    (1000, 600), (5000, 3000), (10000, 6000),
])
def test_cost_aware_gate_scales_with_configured_capital(capital, entry_price, monkeypatch):
    stop_price = entry_price * 0.99
    result = _cost_aware_position_check(
        capital=capital, entry_price=entry_price, stop_price=stop_price,
        quantity=1, trade_timestamp=pd.Timestamp("2025-02-03T10:00:00+05:30"),
    )
    assert result["eligible"] is True
    assert result["entry_cash_required"] < capital
    assert result["max_trade_risk"] == pytest.approx(capital * 0.01)
    assert result["cost_inclusive_stop_risk"] <= capital * 0.01
    risk = RiskConfig(capital=capital)
    assert calculate_max_trade_risk(risk) == pytest.approx(capital * 0.01)
    assert calculate_daily_loss_limit(risk) == pytest.approx(capital * 0.02)

    frame = pd.DataFrame([{
        "timestamp": pd.Timestamp("2025-02-03T10:00:00+05:30"),
        "open": entry_price, "close": entry_price,
    }])
    monkeypatch.setattr(prospective, "_prepare_quant_candidates",
                        lambda value: (value, {0}))
    baseline = _quant_baseline(frame, capital=capital, has_open_paper_trade=False)
    assert baseline["eligible"] is True
    assert baseline["quantity"] == 1
    assert baseline["cost_eligibility"]["eligible"] is True


def test_cost_aware_gate_rejects_entry_cash_over_capital():
    result = _cost_aware_position_check(
        capital=1000, entry_price=999, stop_price=989.01, quantity=1,
        trade_timestamp=pd.Timestamp("2025-02-03T10:00:00+05:30"),
    )
    assert result["eligible"] is False
    assert result["reason"] == "entry notional plus buy-side costs exceeds capital"
    assert result["entry_cash_required"] == pytest.approx(1000.745478774)


def test_realized_loss_reduces_affordability_without_resizing_or_raising_risk():
    loss = {"classification": "PROSPECTIVE_PAPER_OBSERVATION",
            "exit_timestamp": "2025-01-31T10:00:00+05:30",
            "quantitative_baseline_trade": {"taken": True, "default_net_pnl": -300.0},
            "deepseek_assisted_trade": {"taken": False, "default_net_pnl": 0.0}}
    as_of = pd.Timestamp("2025-02-03T10:00:00+05:30")
    baseline_cash = prospective._arm_available_cash(
        1000, [loss], "quantitative_baseline_trade", as_of)
    ai_cash = prospective._arm_available_cash(
        1000, [loss], "deepseek_assisted_trade", as_of)
    assert (baseline_cash, ai_cash) == (700, 1000)
    baseline = _cost_aware_position_check(
        capital=1000, available_cash=baseline_cash, entry_price=699,
        stop_price=692.01, quantity=1, trade_timestamp=as_of)
    ai = _cost_aware_position_check(
        capital=1000, available_cash=ai_cash, entry_price=699,
        stop_price=692.01, quantity=1, trade_timestamp=as_of)
    assert baseline["eligible"] is False
    assert "available account cash" in baseline["reason"]
    assert ai["eligible"] is True
    assert baseline["quantity"] == ai["quantity"] == 1
    assert baseline["max_trade_risk"] == ai["max_trade_risk"] == 10


def test_cost_aware_gate_rejects_cost_inclusive_stop_risk_over_limit():
    result = _cost_aware_position_check(
        capital=1000, entry_price=400, stop_price=396, quantity=2,
        trade_timestamp=pd.Timestamp("2025-02-03T10:00:00+05:30"),
    )
    assert result["eligible"] is False
    assert result["reason"] == "cost-inclusive stop risk exceeds the trade-risk limit"
    assert result["estimated_stop_loss"] == pytest.approx(8)
    assert result["estimated_round_trip_costs"] == pytest.approx(2.955823792)
    assert result["cost_inclusive_stop_risk"] == pytest.approx(10.955823792)


def test_cost_aware_gate_rejects_zero_share_and_invalid_inputs(monkeypatch):
    timestamp = pd.Timestamp("2025-02-03T10:00:00+05:30")
    zero = _cost_aware_position_check(
        capital=1000, entry_price=600, stop_price=594, quantity=0,
        trade_timestamp=timestamp,
    )
    assert zero["eligible"] is False

    frame = pd.DataFrame([{
        "timestamp": pd.Timestamp("2025-02-03T10:00:00+05:30"),
        "open": 1200.0, "close": 1200.0,
    }])
    monkeypatch.setattr(prospective, "_prepare_quant_candidates",
                        lambda value: (value, {0}))
    no_shares = _quant_baseline(frame, capital=1000, has_open_paper_trade=False)
    assert no_shares["eligible"] is False
    assert no_shares["reason"] == "risk sizing produced zero shares"

    invalid = _cost_aware_position_check(
        capital=1000, entry_price=math.nan, stop_price=594, quantity=1,
        trade_timestamp=timestamp,
    )
    assert invalid["eligible"] is False
    assert _cost_aware_position_check(
        capital=1000, entry_price=600, stop_price=594, quantity=1,
        trade_timestamp=None,
    )["eligible"] is False

    monkeypatch.setattr(prospective, "estimate_buy_side_costs", lambda *a, **k: {"total_cost": 0})
    missing_fees = _cost_aware_position_check(
        capital=1000, entry_price=600, stop_price=594, quantity=1,
        trade_timestamp=timestamp,
    )
    assert missing_fees["eligible"] is False
    assert missing_fees["reason"] == "invalid or unavailable cost inputs"


def test_cost_aware_gate_rejects_nonfinite_cost_estimates(monkeypatch):
    timestamp = pd.Timestamp("2025-02-03T10:00:00+05:30")
    monkeypatch.setattr(prospective, "estimate_round_trip_costs",
                        lambda *a, **k: {"brokerage": 0, "stt": 0, "exchange": 0,
                                         "ipft": 0, "sebi": 0, "stamp_duty": 0,
                                         "gst": 0, "slippage": 0, "total_cost": math.nan})
    result = _cost_aware_position_check(
        capital=1000, entry_price=600, stop_price=594, quantity=1,
        trade_timestamp=timestamp,
    )
    assert result["eligible"] is False
    assert result["reason"] == "invalid or unavailable cost inputs"


def test_cost_gate_is_shared_by_baseline_and_ai_arms(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="shared-cost-gate")
    provider = MockProvider(response='{"decision":"BUY","confidence":0.8,"reason":"fixture"}')
    output = tmp_path / "shared-cost-gate-output"
    result = record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=now, allow_provider_requests=True,
        paper_capital=1000,
    )
    event = json.loads((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    baseline = event["quantitative_baseline"]
    assert baseline["eligible"] is True
    assert baseline["cost_eligibility"]["eligible"] is True
    assert event["position_cost_eligibility"] == baseline["cost_eligibility"]
    assert event["ai_assisted_eligible"] is True
    assert event["quantitative_baseline"]["quantity"] == 1
    assert event["capital"] == event["configuration"]["paper_capital"] == 1000
    assert event["trade_placement_enabled"] is False
    assert result["provider_request_attempted"] is True


def test_deepseek_cannot_bypass_failed_cost_gate(tmp_path):
    path, now, body = snapshot_fixture(tmp_path, source="cost-gate-veto")
    factor = 999.0 / body["candles"][-1]["close"]
    for candle in body["candles"]:
        for key in ("open", "high", "low", "close"):
            candle[key] *= factor
    path.write_text(json.dumps(body), encoding="utf-8")
    provider = MockProvider(response='{"decision":"BUY","confidence":1.0,"reason":"fixture"}')
    output = tmp_path / "cost-gate-veto-output"
    result = record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=now, allow_provider_requests=True,
        paper_capital=1000,
    )
    event = json.loads((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert event["quantitative_baseline"]["eligible"] is False
    assert event["quantitative_baseline"]["cost_eligibility"]["eligible"] is False
    assert event["ai_assisted_eligible"] is False
    assert event["paper_action"] == "NO_TRADE"
    assert event["provider_request_attempted"] is False
    assert result["provider_request_attempted"] is False
    assert provider.calls == 0


def test_experiment_initial_capital_is_immutable(tmp_path, monkeypatch):
    output = tmp_path / "fixed-experiment"
    assert prospective.ensure_prospective_experiment_capital(
        output, 1000, model="deepseek-flash") == 1000
    config_path = output / prospective.EXPERIMENT_CAPITAL_CONFIG
    original = config_path.read_text(encoding="utf-8")
    config = json.loads(original)
    assert config["experiment_spec"]["model_id"] == "deepseek-flash"
    assert config["experiment_spec"]["stop_pct"] == 1.0
    assert config["experiment_spec"]["source_sha256"]
    with pytest.raises(ValueError, match="differs from this experiment"):
        prospective.ensure_prospective_experiment_capital(output, 5000)
    with pytest.raises(ValueError, match="specification differs"):
        prospective.ensure_prospective_experiment_capital(
            output, 1000, model="different-model")
    original_hashes = prospective._experiment_source_hashes
    monkeypatch.setattr(prospective, "_experiment_source_hashes",
                        lambda: {**original_hashes(), "app/cost_model.py": "changed"})
    with pytest.raises(ValueError, match="specification differs"):
        prospective.ensure_prospective_experiment_capital(
            output, 1000, model="deepseek-flash")
    assert config_path.read_text(encoding="utf-8") == original

    monkeypatch.setattr(prospective, "_experiment_source_hashes", original_hashes)
    unspecified = tmp_path / "unspecified-model"
    prospective.ensure_prospective_experiment_capital(unspecified, 1000)
    assert prospective.ensure_prospective_experiment_capital(
        unspecified, 1000, model="deepseek-flash") == 1000


def test_legacy_observation_capital_is_verified_without_rewriting(tmp_path):
    output = tmp_path / "legacy-experiment"
    output.mkdir()
    observations = output / "observations.jsonl"
    observations.write_text(json.dumps({
        "classification": "PROSPECTIVE_OBSERVATION", "capital": 10000.0,
        "configuration": {"paper_capital": 10000.0},
    }) + "\n", encoding="utf-8")
    before = observations.read_text(encoding="utf-8")
    assert prospective.ensure_prospective_experiment_capital(output, 10000) == 10000
    assert not (output / prospective.EXPERIMENT_CAPITAL_CONFIG).exists()
    with pytest.raises(ValueError, match="differs from this experiment"):
        prospective.ensure_prospective_experiment_capital(output, 5000)
    assert observations.read_text(encoding="utf-8") == before


def test_cli_accepts_configured_capital_above_1000_before_capture(tmp_path, monkeypatch):
    import app.config
    import sys
    from scripts.run_deepseek_prospective_research import main

    class Settings:
        paper_trading = True
        upstox_access_token = "test-only"
        deepseek_api_key = "test-only"
        deepseek_model = "deepseek-flash"
        request_timeout_seconds = 1

    output = tmp_path / "five-thousand-experiment"
    def stop_before_network(*args, **kwargs):
        raise RuntimeError("offline test stop")

    monkeypatch.setattr(app.config, "get_settings", lambda: Settings())
    monkeypatch.setattr(prospective, "capture_upstox_snapshot", stop_before_network)
    monkeypatch.setattr("scripts.run_deepseek_prospective_research.capture_upstox_snapshot",
                        stop_before_network)
    monkeypatch.setattr(sys, "argv", [
        "runner", "--mode", "prospective-paper", "--enable-prospective",
        "--allow-provider-requests", "--fetch-upstox", "--symbol", "RELIANCE",
        "--allow-market-data-requests", "--paper-capital", "5000",
        "--output", str(output),
    ])
    assert main() == 2
    config = json.loads((output / prospective.EXPERIMENT_CAPITAL_CONFIG).read_text(encoding="utf-8"))
    assert config["initial_capital"] == 5000


def test_prospective_cli_requires_explicit_experiment_capital(tmp_path, monkeypatch):
    import sys
    from scripts.run_deepseek_prospective_research import main

    monkeypatch.setattr(sys, "argv", [
        "runner", "--mode", "prospective-paper", "--enable-prospective",
        "--allow-provider-requests", "--fetch-upstox", "--symbol", "RELIANCE",
        "--allow-market-data-requests", "--output", str(tmp_path / "missing-capital"),
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_actual_entry_rechecks_cash_cap_and_never_keeps_oversized_candidate(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="actual-entry-cost-recheck")
    provider = MockProvider(response='{"decision":"BUY","confidence":0.8,"reason":"fixture"}')
    output = tmp_path / "actual-entry-cost-recheck-output"
    record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=now, allow_provider_requests=True,
        paper_capital=1000,
    )

    later_path, later_now, later_body = snapshot_fixture(
        tmp_path, count=46, source="actual-entry-too-expensive",
    )
    for key in ("open", "high", "low", "close"):
        later_body["candles"][39][key] = 1100.0
    later_path.write_text(json.dumps(later_body), encoding="utf-8")
    result = record_prospective_snapshot(
        later_path, output_dir=output, model=provider.model_id,
        max_total_requests=1, now=later_now, paper_capital=1000,
    )
    assert result["settled_outcomes"] == 1
    outcome = json.loads((output / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert outcome["entry_cost_eligibility"]["eligible"] is False
    assert outcome["classification"] == "PROSPECTIVE_ENTRY_REJECTED"
    assert outcome["entry_timestamp"] is not None
    assert outcome["entry_price"] == 1100.0
    assert outcome["rejection_reason"]
    assert outcome["quantity"] == 0
    assert outcome["quantitative_baseline_trade"]["taken"] is False
    assert outcome["deepseek_assisted_trade"]["taken"] is False


def test_rejected_actual_entry_clears_both_arms_and_does_not_consume_quota_or_block_next_candidate(
        tmp_path, monkeypatch):
    original_prepare = prospective._prepare_quant_candidates
    force_candidate = {"enabled": True}

    def prepare_latest_candidate(frame):
        prepared, candidates = original_prepare(frame)
        if force_candidate["enabled"]:
            candidates = {len(prepared) - 1}
        return prepared, candidates

    monkeypatch.setattr(prospective, "_prepare_quant_candidates", prepare_latest_candidate)
    path, now, _ = snapshot_fixture(tmp_path, count=39, source="reject-then-allow")
    output = tmp_path / "reject-then-allow-output"
    provider = MockProvider(response='{"decision":"BUY","confidence":0.8,"reason":"fixture"}')
    first = record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=2, now=now, allow_provider_requests=True, paper_capital=1000,
    )
    assert first["request_attempted"] is True
    first_observation = json.loads((output / "observations.jsonl").read_text().splitlines()[0])
    assert first_observation["quantitative_baseline"]["eligible"] is True
    assert first_observation["ai_assisted_eligible"] is True

    rejected_path, rejected_now, rejected_body = snapshot_fixture(
        tmp_path, count=40, source="one-bar-actual-entry-rejected",
    )
    for key in ("open", "high", "low", "close"):
        rejected_body["candles"][39][key] = 1100.0
    rejected_path.write_text(json.dumps(rejected_body), encoding="utf-8")
    force_candidate["enabled"] = False
    second = record_prospective_snapshot(
        rejected_path, output_dir=output, model=provider.model_id,
        max_total_requests=2, now=rejected_now, paper_capital=1000,
    )
    assert second["settled_outcomes"] == 1
    rejection = json.loads((output / "outcomes.jsonl").read_text().splitlines()[0])
    assert rejection["classification"] == "PROSPECTIVE_ENTRY_REJECTED"
    assert rejection["decision_id"] == first["decision_id"]
    assert rejection["entry_price"] == 1100.0
    assert rejection["entry_cost_eligibility"]["eligible"] is False
    assert rejection["rejection_reason"]
    for arm in ("quantitative_baseline_trade", "deepseek_assisted_trade"):
        assert rejection[arm]["taken"] is False
        assert rejection[arm]["quantity"] == 0

    risk_states = []
    original_check = prospective.check_trade_allowed

    def track_risk_state(config, state):
        risk_states.append(state)
        return original_check(config, state)

    monkeypatch.setattr(prospective, "check_trade_allowed", track_risk_state)
    force_candidate["enabled"] = True
    next_path, next_now, _ = snapshot_fixture(tmp_path, count=41, source="candidate-after-rejection")
    third = record_prospective_snapshot(
        next_path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=2, now=next_now, allow_provider_requests=True, paper_capital=1000,
    )
    next_observation = json.loads((output / "observations.jsonl").read_text().splitlines()[-1])
    assert third["decision_id"] != first["decision_id"]
    assert next_observation["quantitative_baseline"]["eligible"] is True
    assert next_observation["ai_assisted_eligible"] is True
    assert len(risk_states) >= 2
    assert all(state.open_positions == 0 and state.trades_today == 0 for state in risk_states)


def test_accepted_actual_entry_remains_open_until_normal_settlement(tmp_path, monkeypatch):
    original_prepare = prospective._prepare_quant_candidates

    def prepare_latest_candidate(frame):
        prepared, _ = original_prepare(frame)
        return prepared, {len(prepared) - 1}

    monkeypatch.setattr(prospective, "_prepare_quant_candidates", prepare_latest_candidate)
    path, now, _ = snapshot_fixture(tmp_path, count=39, source="accepted-remains-open")
    output = tmp_path / "accepted-remains-open-output"
    provider = MockProvider(response='{"decision":"BUY","confidence":0.8,"reason":"fixture"}')
    record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=2, now=now, allow_provider_requests=True, paper_capital=1000,
    )
    later, later_now, _ = snapshot_fixture(tmp_path, count=40, source="accepted-one-bar")
    risk_states = []
    original_check = prospective.check_trade_allowed

    def track_risk_state(config, state):
        risk_states.append(state)
        return original_check(config, state)

    monkeypatch.setattr(prospective, "check_trade_allowed", track_risk_state)
    result = record_prospective_snapshot(
        later, output_dir=output, model=provider.model_id,
        max_total_requests=2, now=later_now, paper_capital=1000,
    )
    assert result["settled_outcomes"] == 0
    assert not (output / "outcomes.jsonl").exists()
    assert len(risk_states) >= 2
    assert all(state.open_positions == 1 and state.trades_today == 1 for state in risk_states)


def test_realized_cash_is_separate_and_ai_actual_rejection_clears_only_ai_arm(
        tmp_path, monkeypatch):
    output = tmp_path / "shared-cash"
    prospective.ensure_prospective_experiment_capital(
        output, 1000, model="deepseek-flash")
    prior_decisions = []
    prior_outcomes = []
    for number, (baseline_pnl, ai_taken, ai_pnl) in enumerate(
            ((-300.0, True, -300.0), (300.0, False, 0.0)), 1):
        decision_id = f"earlier-{number}"
        stamp = f"2025-01-31T{9 + number:02d}:00:00+05:30"
        prior_decisions.append({
            "decision_id": decision_id, "classification": "PROSPECTIVE_OBSERVATION",
            "capital": 1000, "decision_time": stamp, "symbol": "RELIANCE",
            "quantitative_baseline": {"eligible": True},
            "paper_action": "PENDING_BUY" if ai_taken else "NO_TRADE",
        })
        prior_outcomes.append({
            "outcome_id": decision_id, "decision_id": decision_id,
            "classification": "PROSPECTIVE_PAPER_OBSERVATION",
            "entry_timestamp": stamp, "exit_timestamp": stamp,
            "quantitative_baseline_trade": {
                "taken": True, "default_net_pnl": baseline_pnl},
            "deepseek_assisted_trade": {
                "taken": ai_taken, "default_net_pnl": ai_pnl},
        })
    (output / "observations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in prior_decisions), encoding="utf-8")
    (output / "outcomes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in prior_outcomes), encoding="utf-8")

    path, now, _ = snapshot_fixture(tmp_path, count=39, source="cash-divergence")
    provider = MockProvider()
    first = record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=now, allow_provider_requests=True, paper_capital=1000,
    )
    observation = json.loads((output / "observations.jsonl").read_text().splitlines()[-1])
    assert observation["baseline_available_cash"] == 1000
    assert observation["ai_available_cash"] == 700
    assert observation["ai_assisted_eligible"] is True

    later, later_now, body = snapshot_fixture(tmp_path, count=40, source="cash-entry")
    for field in ("open", "high", "low", "close"):
        body["candles"][39][field] = 699.0
    later.write_text(json.dumps(body), encoding="utf-8")
    second = record_prospective_snapshot(
        later, output_dir=output, model=provider.model_id,
        max_total_requests=1, now=later_now, paper_capital=1000,
    )
    assert second["settled_outcomes"] == 0
    entry = next(row for row in map(json.loads, (output / "entry_events.jsonl").read_text().splitlines())
                 if row["decision_id"] == first["decision_id"])
    assert entry["entry_price"] == 699
    assert entry["quantitative_baseline_trade"]["accepted"] is True
    assert entry["deepseek_assisted_trade"]["accepted"] is False
    assert "available account cash" in entry["deepseek_assisted_trade"]["cost_eligibility"]["reason"]

    states = []
    original_check = prospective.check_trade_allowed

    def track_state(config, state):
        states.append(state)
        return original_check(config, state)

    monkeypatch.setattr(prospective, "check_trade_allowed", track_state)
    original_prepare = prospective._prepare_quant_candidates

    def force_candidate(frame):
        prepared, _ = original_prepare(frame)
        return prepared, {len(prepared) - 1}

    monkeypatch.setattr(prospective, "_prepare_quant_candidates", force_candidate)
    next_path, next_now, next_body = snapshot_fixture(tmp_path, count=41, source="cash-resume")
    for index in (39, 40):
        for field in ("open", "high", "low", "close"):
            next_body["candles"][index][field] = 699.0
    next_path.write_text(json.dumps(next_body), encoding="utf-8")
    record_prospective_snapshot(
        next_path, output_dir=output, model=provider.model_id,
        max_total_requests=1, now=next_now, paper_capital=1000,
    )
    assert len(states) >= 2
    assert states[0].open_positions == 1
    assert states[-1].open_positions == 0
    assert states[-1].trades_today == 0
    first_entry_rows = [row for row in map(json.loads, (output / "entry_events.jsonl").read_text().splitlines())
                        if row["decision_id"] == first["decision_id"]]
    assert len(first_entry_rows) == 1


def test_missing_next_entry_candle_is_rejected_without_a_trade(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, count=39, source="missing-entry-start")
    output = tmp_path / "missing-entry"
    first = record_prospective_snapshot(
        path, output_dir=output, model="deepseek-flash", now=now, paper_capital=1000,
    )
    later, later_now, body = snapshot_fixture(tmp_path, count=41, source="missing-entry-later")
    body["candles"].pop(39)
    later.write_text(json.dumps(body), encoding="utf-8")
    second = record_prospective_snapshot(
        later, output_dir=output, model="deepseek-flash", now=later_now,
        paper_capital=1000,
    )
    assert second["settled_outcomes"] == 1
    outcome = json.loads((output / "outcomes.jsonl").read_text().splitlines()[0])
    assert outcome["decision_id"] == first["decision_id"]
    assert outcome["classification"] == "PROSPECTIVE_ENTRY_REJECTED"
    assert outcome["entry_price"] is None
    assert outcome["quantity"] == 0
    assert outcome["quantitative_baseline_trade"]["taken"] is False
    assert outcome["deepseek_assisted_trade"]["taken"] is False
    assert "entry candle is missing" in outcome["rejection_reason"]


def test_missing_continuation_keeps_accepted_entry_unsettled_and_visible(tmp_path):
    from scripts.summarize_deepseek_prospective import summarize

    path, now, _ = snapshot_fixture(tmp_path, count=39, source="continuation-start")
    output = tmp_path / "continuation-gap"
    first = record_prospective_snapshot(
        path, output_dir=output, model="deepseek-flash", now=now, paper_capital=1000,
    )
    later, later_now, body = snapshot_fixture(tmp_path, count=41, source="continuation-later")
    body["candles"].pop(40)
    later.write_text(json.dumps(body), encoding="utf-8")
    second = record_prospective_snapshot(
        later, output_dir=output, model="deepseek-flash", now=later_now,
        paper_capital=1000,
    )
    assert second["settled_outcomes"] == 0
    assert not (output / "outcomes.jsonl").exists()
    entry = json.loads((output / "entry_events.jsonl").read_text().splitlines()[0])
    assert entry["decision_id"] == first["decision_id"]
    assert entry["quantitative_baseline_trade"]["accepted"] is True
    summary = summarize(output)
    assert summary["awaiting_outcome_count"] == 1
    assert summary["baseline_accepted_awaiting_settlement_count"] == 1
    replay = record_prospective_snapshot(
        later, output_dir=output, model="deepseek-flash", now=later_now,
        paper_capital=1000,
    )
    assert replay["idempotent_replay"] is True
    assert len((output / "entry_events.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("second_count", [39, 40])
def test_one_output_directory_shares_open_position_across_symbols(
        tmp_path, monkeypatch, second_count):
    original_prepare = prospective._prepare_quant_candidates

    def force_candidate(frame):
        prepared, _ = original_prepare(frame)
        return prepared, {len(prepared) - 1}

    monkeypatch.setattr(prospective, "_prepare_quant_candidates", force_candidate)
    first_path, first_now, _ = snapshot_fixture(tmp_path, count=39, source="first-stock")
    output = tmp_path / "two-stocks-one-account"
    provider = MockProvider()
    first = record_prospective_snapshot(
        first_path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=first_now, allow_provider_requests=True,
        paper_capital=1000,
    )
    assert first["request_attempted"] is True
    second_path, second_now, second_body = snapshot_fixture(
        tmp_path, count=second_count, source="second-stock")
    second_body["symbol"] = "HDFCBANK"
    second_body["instrument_key"] = "NSE_EQ|INE040A01034"
    second_path.write_text(json.dumps(second_body), encoding="utf-8")
    second = record_prospective_snapshot(
        second_path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=second_now, allow_provider_requests=True,
        paper_capital=1000,
    )
    observation = json.loads((output / "observations.jsonl").read_text().splitlines()[-1])
    assert observation["symbol"] == "HDFCBANK"
    assert observation["quantitative_baseline"]["eligible"] is False
    assert "open positions" in observation["quantitative_baseline"]["reason"].lower()
    assert observation["ai_assisted_eligible"] is False
    assert second["request_attempted"] is False
    assert provider.calls == 1


def test_offline_synthetic_matrix_is_zero_request_and_fails_closed():
    report = offline_synthetic_check()
    outcomes = {row["case"]: row["decision"] for row in report["cases"]}
    assert report["status"] == "COMPLETED"
    assert report["classification"] == "SYNTHETIC"
    assert report["api_requests"] == 0
    assert outcomes == {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD",
                        "malformed": "HOLD", "timeout": "HOLD"}
    assert all(row["classification"] == "SYNTHETIC"
               and row["evidence_type"] == "SYNTHETIC_FIXTURE" for row in report["cases"])
    assert {row["case"] for row in report["cases"] if row["safe_fallback"]} == {
        "malformed", "timeout"}
    assert report["trade_placement_enabled"] is False


def test_snapshot_order_and_observation_cutoff_are_enforced(tmp_path):
    path, now, body = snapshot_fixture(tmp_path)
    body["candles"][0], body["candles"][1] = body["candles"][1], body["candles"][0]
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="strictly chronological"):
        record_prospective_snapshot(path, output_dir=tmp_path / "order", model="deepseek-flash", now=now)

    path, now, body = snapshot_fixture(tmp_path, source="future")
    body["candles"].append({**body["candles"][-1], "timestamp": (pd.Timestamp(body["observed_at"])
                                  + pd.Timedelta(minutes=5)).isoformat()})
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="later than its observed_at"):
        record_prospective_snapshot(path, output_dir=tmp_path / "future", model="deepseek-flash", now=now)

    path, now, body = snapshot_fixture(tmp_path, source="stale")
    stale_now = pd.Timestamp(body["observed_at"]).to_pydatetime() + timedelta(days=1)
    with pytest.raises(ValueError, match="observed_at is stale"):
        record_prospective_snapshot(path, output_dir=tmp_path / "stale", model="deepseek-flash",
                                   now=stale_now)

    path, now, body = snapshot_fixture(tmp_path, source="old-candles")
    body["observed_at"] = (pd.Timestamp(body["candles"][-1]["timestamp"])
                            + pd.Timedelta(minutes=30)).isoformat()
    path.write_text(json.dumps(body), encoding="utf-8")
    now = pd.Timestamp(body["observed_at"]).to_pydatetime() + timedelta(seconds=1)
    with pytest.raises(ValueError, match="latest candle is stale"):
        record_prospective_snapshot(path, output_dir=tmp_path / "old-candles",
                                   model="deepseek-flash", now=now)

    path, now, body = snapshot_fixture(tmp_path, source="wrong-map")
    body["instrument_key"] = "NSE_EQ|WRONG"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match the configured symbol mapping"):
        record_prospective_snapshot(path, output_dir=tmp_path / "wrong-map",
                                   model="deepseek-flash", now=now)


def test_exact_model_input_has_no_future_bars_and_outcome_is_appended_later(tmp_path):
    path, decision_time, body = snapshot_fixture(tmp_path, count=39)
    body["private_notes"] = "must not be sent to provider"
    path.write_text(json.dumps(body), encoding="utf-8")
    provider = MockProvider(response='{"decision":"BUY","confidence":0.8,"reason":"authorization: Bearer test-secret-key"}')
    out = tmp_path / "prospective"
    first = record_prospective_snapshot(
        path, output_dir=out, provider=provider, model=provider.model_id,
        api_key="test-secret-key", max_total_requests=2, now=decision_time,
        allow_provider_requests=True,
    )
    assert first["request_attempted"] is True
    event = json.loads((out / "observations.jsonl").read_text().splitlines()[0])
    request = json.loads(event["provider_request"]["user_payload"])
    assert event["classification"] == "PROSPECTIVE_OBSERVATION"
    assert event["quantitative_baseline"]["decision"] == "BUY_CANDIDATE"
    assert event["paper_action"] == "PENDING_BUY"
    assert all(pd.Timestamp(bar["timestamp"]) <= pd.Timestamp(event["decision_time"])
               for bar in request["recent_bars"])
    assert request["recent_bars"][-1]["timestamp"] == request["signal_timestamp"]
    assert "instrument_key" not in request
    assert "source" not in request
    assert "private_notes" not in event["provider_request"]["user_payload"]
    assert "test-secret-key" not in event["provider_request"]["user_payload"]
    assert "schema_version" not in request
    assert event["raw_decision_response_redacted"] is not None
    assert "test-secret-key" not in json.dumps(event)
    assert "[REDACTED]" in event["raw_decision_response_redacted"]
    assert event["trade_placement_enabled"] is False
    assert not (out / "outcomes.jsonl").exists()

    other_symbol, other_now, other_body = snapshot_fixture(tmp_path, count=46,
                                                           source="other-instrument")
    other_body["symbol"] = "HDFCBANK"
    other_body["instrument_key"] = "NSE_EQ|INE040A01034"
    other_symbol.write_text(json.dumps(other_body), encoding="utf-8")
    wrong_instrument = record_prospective_snapshot(
        other_symbol, output_dir=out, model=provider.model_id, now=other_now,
        max_total_requests=2,
    )
    assert wrong_instrument["settled_outcomes"] == 0
    assert not (out / "outcomes.jsonl").exists()

    later, later_now, _ = snapshot_fixture(tmp_path, count=46, source="later-observation")
    settled = record_prospective_snapshot(
        later, output_dir=out, model=provider.model_id, now=later_now,
        max_total_requests=2,
    )
    assert settled["settled_outcomes"] == 1
    outcome = json.loads((out / "outcomes.jsonl").read_text().splitlines()[0])
    assert outcome["classification"] == "PROSPECTIVE_PAPER_OBSERVATION"
    assert outcome["estimated_costs"] > 0
    assert outcome["net_pnl"] == pytest.approx(outcome["gross_pnl"] - outcome["estimated_costs"])
    assert outcome["quantitative_baseline_trade"]["taken"] is True
    assert outcome["deepseek_assisted_trade"]["taken"] is True
    assert len(outcome["cost_scenario_ids"]) == 6
    assert outcome["entry_cost_eligibility"]["eligible"] is True
    assert "gaps and execution variance can exceed it" in outcome["evidence_note"]
    # Decision-time record remains unchanged; later outcome is separate.
    event_after = json.loads((out / "observations.jsonl").read_text().splitlines()[0])
    assert "outcome" not in event_after


def test_timeout_and_invalid_response_are_safe_hold(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path)
    out = tmp_path / "timeout"
    provider = MockProvider(error=TimeoutError("private timeout detail"))
    result = record_prospective_snapshot(path, output_dir=out, provider=provider,
                                         model=provider.model_id, max_total_requests=1,
                                         now=now, allow_provider_requests=True)
    event = json.loads((out / "observations.jsonl").read_text().splitlines()[0])
    assert result["request_attempted"] is True
    assert event["decision"]["decision"] == "HOLD"
    assert event["paper_action"] == "NO_TRADE"
    assert "private timeout detail" not in json.dumps(event)

    path, now, _ = snapshot_fixture(tmp_path, source="malformed")
    provider = MockProvider(response='{"decision":"BUY","confidence":"high","reason":"bad"}')
    result = record_prospective_snapshot(path, output_dir=tmp_path / "malformed",
                                         provider=provider, model=provider.model_id,
                                         max_total_requests=1, now=now,
                                         allow_provider_requests=True)
    event = json.loads((tmp_path / "malformed" / "observations.jsonl").read_text().splitlines()[0])
    assert result["request_attempted"] is True
    assert event["decision"]["decision"] == "HOLD"
    assert event["paper_action"] == "NO_TRADE"


def test_response_cache_reuses_exact_request_after_interrupted_ledger_write(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="cache-one")
    provider = MockProvider(response='{"decision":"SELL","confidence":0.8,"reason":"veto"}')
    out = tmp_path / "cache-run"
    first = record_prospective_snapshot(path, output_dir=out, provider=provider,
                                        model=provider.model_id, max_total_requests=1,
                                        now=now, allow_provider_requests=True)
    # Simulate a process interruption after response-cache persistence but
    # before the append-only observation row was written.
    (out / "observations.jsonl").unlink()
    second = record_prospective_snapshot(path, output_dir=out, provider=provider,
                                         model=provider.model_id, max_total_requests=1,
                                         now=now, allow_provider_requests=True)
    assert first["request_attempted"] is True
    assert second["request_attempted"] is False
    assert second["cache_hits"] == 1
    assert provider.calls == 1
    assert second["request_budget_used"] == 1

    replay = record_prospective_snapshot(path, output_dir=out, provider=provider,
                                         model=provider.model_id, max_total_requests=1,
                                         now=now, allow_provider_requests=True)
    assert replay["idempotent_replay"] is True
    assert provider.calls == 1

    budget = PersistentRequestBudget(tmp_path / "budget.jsonl", 1)
    assert budget.reserve("request-1")
    assert not budget.reserve("request-1")
    with pytest.raises(RuntimeError, match="budget exhausted"):
        budget.reserve("request-2")
    reopened = PersistentRequestBudget(tmp_path / "budget.jsonl", 1)
    assert reopened.used == 1
    with pytest.raises(ValueError, match="must match the persistent request budget"):
        PersistentRequestBudget(tmp_path / "budget.jsonl", 5)
    config = json.loads((tmp_path / "budget_config.json").read_text())
    assert config["max_total_requests"] == 1


def test_simultaneous_replay_uses_one_decision_and_one_provider_reservation(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="parallel-replay")
    output = tmp_path / "parallel-output"
    provider = MockProvider()

    def capture():
        return record_prospective_snapshot(
            path, output_dir=output, provider=provider, model=provider.model_id,
            max_total_requests=1, now=now, allow_provider_requests=True,
            paper_capital=1000,
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        reports = list(workers.map(lambda _: capture(), range(2)))
    assert len({report["decision_id"] for report in reports}) == 1
    assert sum(report["request_attempted"] for report in reports) == 1
    assert provider.calls == 1
    assert len((output / "observations.jsonl").read_text().splitlines()) == 1
    assert len((output / "request_budget.jsonl").read_text().splitlines()) == 1


def test_cache_refuses_conflicting_replacement(tmp_path):
    cache = ProspectiveCache(tmp_path / "response_cache.json")
    cache.put("request-key", {"decision": {"decision": "BUY"}})
    with pytest.raises(ValueError, match="refusing to replace"):
        cache.put("request-key", {"decision": {"decision": "SELL"}})
    persisted = json.loads((tmp_path / "response_cache.json").read_text(encoding="utf-8"))
    assert persisted["request-key"]["decision"]["decision"] == "BUY"


def test_cache_write_failure_preserves_prior_response(tmp_path, monkeypatch):
    cache = ProspectiveCache(tmp_path / "response_cache.json")
    cache.put("first", {"decision": {"decision": "BUY"}})
    original = cache.path.read_bytes()

    def fail_replace(*args):
        raise OSError("mock replacement failure")

    monkeypatch.setattr(prospective.os, "replace", fail_replace)
    with pytest.raises(OSError, match="mock replacement failure"):
        cache.put("second", {"decision": {"decision": "SELL"}})
    assert cache.path.read_bytes() == original
    assert cache.get("second") is None


def test_baseline_outcome_is_recorded_when_deepseek_vetoes(tmp_path):
    path, now, body = snapshot_fixture(tmp_path, source="paired-veto")
    provider = MockProvider(response='{"decision":"HOLD","confidence":0.9,"reason":"veto"}')
    out = tmp_path / "paired-veto"
    first = record_prospective_snapshot(path, output_dir=out, provider=provider,
                                        model=provider.model_id, max_total_requests=1,
                                        now=now, allow_provider_requests=True)
    observation = json.loads((out / "observations.jsonl").read_text().splitlines()[0])
    assert observation["quantitative_baseline"]["eligible"] is True
    assert observation["ai_assisted_eligible"] is False

    later_path, later_now, later_body = snapshot_fixture(tmp_path, count=46,
                                                         source="paired-veto-later")
    later_body["candles"][:len(body["candles"])] = body["candles"]
    later_path.write_text(json.dumps(later_body), encoding="utf-8")
    result = record_prospective_snapshot(later_path, output_dir=out, model=provider.model_id,
                                         now=later_now, max_total_requests=1)
    assert result["settled_outcomes"] == 1
    outcome = json.loads((out / "outcomes.jsonl").read_text().splitlines()[0])
    assert outcome["quantitative_baseline_trade"]["taken"] is True
    assert outcome["deepseek_assisted_trade"]["taken"] is False
    assert outcome["deepseek_assisted_trade"]["default_net_pnl"] == 0


def test_provider_call_requires_explicit_opt_in(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path)
    provider = MockProvider()
    with pytest.raises(ValueError, match="explicit allow_provider_requests opt-in"):
        record_prospective_snapshot(path, output_dir=tmp_path / "no-opt-in",
                                    provider=provider, model=provider.model_id, now=now)
    assert provider.calls == 0


def test_ineligible_quantitative_baseline_skips_provider_with_specific_reason(tmp_path):
    path, now, body = snapshot_fixture(tmp_path, source="ineligible-no-signal")
    for bar in body["candles"]:
        bar.update({"open": 100.0, "high": 101.0, "low": 99.0,
                    "close": 100.0, "volume": 1000})
    path.write_text(json.dumps(body), encoding="utf-8")
    provider = MockProvider()
    output = tmp_path / "ineligible-output"

    result = record_prospective_snapshot(
        path, output_dir=output, provider=provider, model=provider.model_id,
        max_total_requests=1, now=now, allow_provider_requests=True)
    event = json.loads((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert event["quantitative_baseline"] == {
        "decision": "HOLD", "eligible": False,
        "reason": "no frozen quantitative candidate",
    }
    assert event["provider_configuration"]["configured"] is True
    assert event["provider_request_attempted"] is False
    assert event["provider_skip_reason"] == "quantitative_baseline_ineligible"
    assert event["provider_response_redacted"] is None
    assert result["provider_request_attempted"] is False
    assert result["provider_skip_reason"] == "quantitative_baseline_ineligible"
    assert provider.calls == 0
    assert not (output / "request_budget.jsonl").exists()


def test_provider_smoke_test_is_non_trading_and_parses_only_acknowledgement(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="provider-smoke-current-snapshot")
    provider = MockProvider(response='{"status":"ok"}')
    output = tmp_path / "provider-smoke"

    report = run_provider_smoke_test(
        path, output_dir=output, provider=provider, model=provider.model_id,
        api_key="test-only-key", max_total_requests=1, now=now,
        allow_provider_requests=True)

    assert report["classification"] == "PROVIDER_CONNECTIVITY_TEST"
    assert report["status"] == "COMPLETED"
    assert report["response_parse_status"] == "VALID_ACK"
    assert report["provider_request_attempted"] is True
    assert report["prospective_observation"] is False
    assert report["strategy_performance_evidence"] is False
    assert report["trading_decision_created"] is False
    assert report["paper_trade_simulated"] is False
    assert report["trade_placement_enabled"] is False
    assert provider.calls == 1
    sent = provider.payloads[0]
    payload = json.loads(sent["user_payload"])
    assert payload["test_type"] == "NON_TRADING_PROVIDER_CONNECTIVITY_TEST"
    assert len(payload["recent_completed_candles"]) == 3
    assert all("decision" not in bar and "outcome" not in bar
               for bar in payload["recent_completed_candles"])
    assert "test-only-key" not in json.dumps(sent)
    assert not (output / "observations.jsonl").exists()
    assert not (output / "outcomes.jsonl").exists()
    assert (output / "provider_smoke_request_budget.jsonl").exists()
    replay = run_provider_smoke_test(
        path, output_dir=output, provider=provider, model=provider.model_id,
        api_key="test-only-key", max_total_requests=1, now=now,
        allow_provider_requests=True)
    assert replay["provider_request_attempted"] is False
    assert replay["provider_skip_reason"] == "request_already_reserved"
    assert provider.calls == 1


def test_provider_smoke_requires_explicit_request_opt_in(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="provider-smoke-opt-in")
    provider = MockProvider(response='{"status":"ok"}')
    with pytest.raises(ValueError, match="explicit opt-in"):
        run_provider_smoke_test(path, output_dir=tmp_path / "smoke-no-opt-in",
                                provider=provider, model=provider.model_id, now=now)
    assert provider.calls == 0


def test_provider_smoke_rejects_trading_response_without_creating_signal(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="provider-smoke-invalid-response")
    provider = MockProvider(response='{"decision":"BUY","confidence":0.99}')
    report = run_provider_smoke_test(
        path, output_dir=tmp_path / "provider-smoke-invalid", provider=provider,
        model=provider.model_id, max_total_requests=1, now=now,
        allow_provider_requests=True)

    assert report["status"] == "INVALID_RESPONSE"
    assert report["response_parse_status"] == "INVALID_RESPONSE"
    assert report["trading_decision_created"] is False
    assert report["paper_trade_simulated"] is False
    assert "decision" not in report


def test_provider_smoke_cli_requires_both_explicit_opt_ins(tmp_path, monkeypatch):
    from scripts.run_deepseek_prospective_research import main
    import sys

    monkeypatch.setattr(sys, "argv", ["runner", "--mode", "provider-smoke-test",
        "--snapshot", "not-read.json", "--output", str(tmp_path / "smoke-cli")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_provider_smoke_cli_requires_provider_request_opt_in(tmp_path, monkeypatch, capsys):
    from scripts.run_deepseek_prospective_research import main
    import sys

    monkeypatch.setattr(sys, "argv", ["runner", "--mode", "provider-smoke-test",
        "--enable-provider-smoke-test", "--snapshot", "not-read.json",
        "--output", str(tmp_path / "smoke-cli-no-request-opt-in")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "requires --allow-provider-requests" in capsys.readouterr().err


def test_provider_smoke_provider_failure_is_logged_without_action_or_secrets(tmp_path):
    path, now, _ = snapshot_fixture(tmp_path, source="smoke-timeout")
    provider = MockProvider(error=TimeoutError("secret timeout detail"))
    report = run_provider_smoke_test(
        path, output_dir=tmp_path / "smoke-timeout-output", provider=provider,
        model=provider.model_id, api_key="test-only-key", max_total_requests=1,
        now=now, allow_provider_requests=True)

    assert report["status"] == "PROVIDER_ERROR"
    assert report["provider_error_type"] == "TimeoutError"
    assert report["response_parse_status"] == "NOT_EVALUATED"
    assert report["trading_decision_created"] is False
    assert "secret timeout detail" not in json.dumps(report)
    assert "test-only-key" not in json.dumps(report)


def test_authorized_provider_smoke_cli_uses_mock_and_writes_separate_report(
        tmp_path, monkeypatch, capsys):
    from datetime import datetime
    from types import SimpleNamespace
    import app.config
    import app.v2.provider
    import scripts.run_deepseek_prospective_research as runner
    import sys

    snapshot, fixed_now, _ = snapshot_fixture(tmp_path, source="smoke-cli-snapshot")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    provider = MockProvider(response='{"status":"ok"}')
    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        paper_trading=True, deepseek_api_key="test-only-key", deepseek_model=provider.model_id,
        request_timeout_seconds=10.0))
    monkeypatch.setattr(app.v2.provider, "DeepSeekProvider", lambda **kwargs: provider)
    output = tmp_path / "smoke-cli-output"
    monkeypatch.setattr(sys, "argv", ["runner", "--mode", "provider-smoke-test",
        "--enable-provider-smoke-test", "--allow-provider-requests", "--snapshot",
        str(snapshot), "--output", str(output), "--max-total-requests", "1"])

    assert runner.main() == 0
    cli_report = json.loads(capsys.readouterr().out)
    persisted = json.loads(Path(cli_report["report"]).read_text(encoding="utf-8"))
    assert cli_report["api_requests"] == 1
    assert persisted["classification"] == "PROVIDER_CONNECTIVITY_TEST"
    assert persisted["strategy_performance_evidence"] is False
    assert provider.calls == 1


def test_upstox_snapshot_capture_requires_opt_in_and_uses_read_only_client(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    with pytest.raises(ValueError, match="explicit market-data opt-in"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=tmp_path / "no-opt-in",
                                            max_market_data_requests=1)

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="test-token-not-printed"))
    calls = []
    candles = []
    start = pd.Timestamp("2026-10-28T09:15:00+05:30")
    for index in range(39):
        stamp = start + pd.Timedelta(minutes=5 * index)
        candles.append([stamp.isoformat(), 100, 101, 99, 100.5, 1000, 0])

    def fake_get(instrument_key, interval):
        calls.append((instrument_key, interval))
        return {"data": {"candles": candles}}

    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", fake_get)
    captured = prospective.capture_upstox_snapshot(
        "RELIANCE", output_dir=tmp_path / "capture", max_market_data_requests=1,
        allow_market_data_requests=True,
    )
    body = json.loads(captured.read_text(encoding="utf-8"))
    assert calls == [("NSE_EQ|INE002A01018", 5)]
    assert body["source"] == "upstox-v3-intraday-readonly"
    assert body["instrument_key"] == "NSE_EQ|INE002A01018"
    assert len(body["candles"]) == 39
    assert json.loads((tmp_path / "capture" / "market_data_events.jsonl").read_text().splitlines()[0])[
        "classification"] == "PROSPECTIVE_MARKET_DATA_CAPTURE"
    budget = PersistentRequestBudget(tmp_path / "capture" / "market_data_request_budget.jsonl", 1)
    assert budget.used == 1


def test_upstox_capture_records_sanitized_read_failure(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="test-token-not-printed"))

    def failed_get(*args, **kwargs):
        raise TimeoutError("Bearer secret-token-detail")

    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", failed_get)
    output = tmp_path / "capture-failed"
    with pytest.raises(RuntimeError, match=r"TimeoutError\); failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    failure = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    assert failure["error_type"] == "TimeoutError"
    assert "secret-token-detail" not in json.dumps(failure)


def _capture_with_mocked_candles(tmp_path, monkeypatch, candles):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    called = []
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles",
                        lambda *args, **kwargs: called.append(True) or
                        {"data": {"candles": candles}})
    output = tmp_path / "capture"
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    return row, called, output


@pytest.mark.parametrize("status, category", [
    (401, "authentication"), (403, "authentication"),
    (429, "rate_limit"), (503, "http_error"),
])
def test_upstox_client_classifies_mocked_http_responses(monkeypatch, status, category):
    from types import SimpleNamespace
    import app.config
    import app.upstox_client as client

    class FakeResponse:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                error = client.requests.HTTPError("safe test HTTP error")
                error.response = self
                raise error

    class FakeSession:
        def mount(self, *args):
            pass

        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(client.requests, "Session", FakeSession)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token", request_timeout_seconds=1))
    monkeypatch.setattr(client, "get_settings", app.config.get_settings)
    with pytest.raises(client.UpstoxRequestError) as caught:
        client._get("https://example.invalid/test?token=never-log-this")
    assert caught.value.category == category
    assert caught.value.status_code == status


def test_upstox_client_classifies_mocked_json_decode_failure(monkeypatch):
    from types import SimpleNamespace
    import app.config
    import app.upstox_client as client

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("malformed JSON")

    class FakeSession:
        def mount(self, *args):
            pass

        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(client.requests, "Session", FakeSession)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token", request_timeout_seconds=1))
    monkeypatch.setattr(client, "get_settings", app.config.get_settings)
    with pytest.raises(client.UpstoxRequestError) as caught:
        client._get("https://example.invalid/test")
    assert (caught.value.stage, caught.value.category) == ("response_json", "json_decode")


def test_upstox_client_classifies_mocked_transport_failure(monkeypatch):
    from types import SimpleNamespace
    import app.config
    import app.upstox_client as client

    class FakeSession:
        def mount(self, *args):
            pass

        def get(self, *args, **kwargs):
            raise client.requests.Timeout("https://host.invalid/?access_token=secret")

    monkeypatch.setattr(client.requests, "Session", FakeSession)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token", request_timeout_seconds=1))
    monkeypatch.setattr(client, "get_settings", app.config.get_settings)
    with pytest.raises(client.UpstoxRequestError) as caught:
        client._get("https://example.invalid/test")
    assert (caught.value.stage, caught.value.category) == ("upstox_http", "network_error")
    assert "secret" not in str(caught.value)


def test_upstox_capture_diagnostics_for_candle_categories(tmp_path, monkeypatch):
    start = pd.Timestamp("2026-10-28T09:15:00+05:30")
    valid = [[(start + pd.Timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100.5, 1000, 0]
             for i in range(39)]
    schema, _, _ = _capture_with_mocked_candles(tmp_path / "schema", monkeypatch, {"bad": True})
    assert (schema["stage"], schema["category"]) == ("response_schema", "candle_schema")

    bad_timestamp = [row.copy() for row in valid]
    bad_timestamp[3][0] = "not-a-timestamp"
    timestamp, _, _ = _capture_with_mocked_candles(tmp_path / "timestamp", monkeypatch, bad_timestamp)
    assert (timestamp["stage"], timestamp["category"]) == ("candle_validation", "timestamp_validation")

    bad_ohlcv = [row.copy() for row in valid]
    bad_ohlcv[3][1] = "not-a-price"
    ohlcv, _, _ = _capture_with_mocked_candles(tmp_path / "ohlcv", monkeypatch, bad_ohlcv)
    assert (ohlcv["stage"], ohlcv["category"]) == ("candle_validation", "invalid_ohlcv")

    insufficient, _, _ = _capture_with_mocked_candles(tmp_path / "few", monkeypatch, valid[:34])
    assert (insufficient["stage"], insufficient["category"]) == (
        "completed_candle_count", "insufficient_completed_candles")


def test_upstox_capture_budget_exhaustion_is_structured_and_does_not_call_client(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    output = tmp_path / "budget-exhausted"
    budget_path = output / "market_data_request_budget.jsonl"
    budget = PersistentRequestBudget(budget_path, 1)
    budget.reserve("already-used")
    calls = []
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles",
                        lambda *args, **kwargs: calls.append(True))
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    assert (row["stage"], row["category"]) == ("request_budget", "request_budget_exhausted")
    assert calls == []


def test_upstox_diagnostic_redacts_query_and_authorization_text(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="private-access-token"))
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        (_ for _ in ()).throw(RuntimeError(
                            "GET https://host.invalid/path?access_token=query-secret Authorization: Bearer header-secret")))
    output = tmp_path / "redaction"
    with pytest.raises(RuntimeError):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    encoded = json.dumps(row)
    for secret in ("private-access-token", "query-secret", "header-secret"):
        assert secret not in encoded
    assert row["message"] == "Unexpected error; details redacted"


def test_failure_ledger_redacts_common_api_key_formats(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    sk_key = "sk-live_1234567890abcdef"
    labeled_key = "AK_test_secret_987654321"
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        (_ for _ in ()).throw(RuntimeError(
                            f"provider rejected key {sk_key}; api_key={labeled_key}")))
    output = tmp_path / "api-key-redaction"
    with pytest.raises(RuntimeError):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    for secret in (sk_key, labeled_key):
        assert secret not in row["message"]
        assert secret not in json.dumps(row)
    assert row["message"] == "Unexpected error; details redacted"


def test_unexpected_capture_exception_has_safe_stage_and_category(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        (_ for _ in ()).throw(RuntimeError("unexpected local capture fault")))
    output = tmp_path / "unexpected-failure"
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    assert row["stage"] == "upstox_http"
    assert row["category"] == "unexpected_failure"
    assert row["message"] == "Unexpected error; details redacted"


def _record_mock_capture_failure(tmp_path, monkeypatch, exception, folder):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", fail)
    output = tmp_path / folder
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    return json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])


def test_valid_candle_data_error_report_keeps_recognized_diagnostic(tmp_path, monkeypatch):
    from app.candle_integrity import CandleDataError, validate_candles

    frame = pd.DataFrame([{"timestamp": "2026-10-28T09:15:00+05:30", "open": 100,
                           "high": 99, "low": 98, "close": 99, "volume": 100}])
    report = validate_candles(frame)
    assert report.errors[0]["reason"] == "invalid_ohlc_relationship"
    row = _record_mock_capture_failure(
        tmp_path, monkeypatch, CandleDataError("arbitrary text not recorded", report), "valid-report")
    assert row["stage"] == "candle_validation"
    assert row["category"] == "invalid_ohlcv"
    assert row["message"] == "Candle OHLCV validation failed"
    assert row["validation_errors"] == [
        {"reason": "invalid_ohlc_relationship", "fields": None}]


def test_plain_value_error_with_forged_report_is_unexpected(tmp_path, monkeypatch):
    from app.candle_integrity import validate_candles

    report = validate_candles(pd.DataFrame([{"timestamp": "2026-10-28T09:15:00+05:30",
                                             "open": 100, "high": 99, "low": 98,
                                             "close": 99, "volume": 100}]))
    error = ValueError("forged metadata contains secret-value")
    error.report = report
    row = _record_mock_capture_failure(tmp_path, monkeypatch, error, "forged-report")
    assert row["stage"] == "upstox_http"
    assert row["category"] == "unexpected_failure"
    assert row["message"] == "Unexpected error; details redacted"
    assert "secret-value" not in json.dumps(row)


def test_candle_data_error_with_malformed_report_is_unexpected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.candle_integrity import CandleDataError

    malformed_report = SimpleNamespace(frame=pd.DataFrame(), errors="not-a-list", warnings=[])
    row = _record_mock_capture_failure(
        tmp_path, monkeypatch, CandleDataError("untrusted diagnostic text", malformed_report),
        "malformed-report")
    assert row["category"] == "unexpected_failure"
    assert row["message"] == "Unexpected error; details redacted"
    assert "validation_errors" not in row


@pytest.mark.parametrize("reason, fields", [
    ("secret_reason_code", None),
    ("invalid_ohlc_relationship", ["raw-secret-field"]),
])
def test_candle_data_error_with_unallowlisted_report_values_is_unexpected(
        tmp_path, monkeypatch, reason, fields):
    from app.candle_integrity import CandleDataError, CandleValidation

    report = CandleValidation(frame=pd.DataFrame(), errors=[
        {"row": 0, "reason": reason, "fields": fields}], warnings=[])
    row = _record_mock_capture_failure(
        tmp_path, monkeypatch, CandleDataError("untrusted diagnostic text", report),
        "invalid-report-values")
    assert row["category"] == "unexpected_failure"
    assert row["message"] == "Unexpected error; details redacted"
    assert "validation_errors" not in row
    assert reason not in json.dumps(row)
    assert "raw-secret-field" not in json.dumps(row)


def test_mocked_http_response_body_never_enters_failure_ledger(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client as client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    body_secrets = (
        "sk-fakebody_1234567890abcdef",
        "Bearer fake-body-token-987",
        "SENSITIVE_PROVIDER_RESPONSE_TEXT",
    )
    response_body = " ".join(body_secrets)

    class FakeResponse:
        status_code = 502
        text = response_body

        def raise_for_status(self):
            error = client.requests.HTTPError(f"502 response body: {self.text}")
            error.response = self
            raise error

    class FakeSession:
        def mount(self, *args):
            pass

        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token", request_timeout_seconds=1))
    monkeypatch.setattr(client, "get_settings", app.config.get_settings)
    monkeypatch.setattr(client.requests, "Session", FakeSession)
    output = tmp_path / "response-body-redaction"
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    record = (output / "market_data_failures.jsonl").read_text()
    row = json.loads(record.splitlines()[0])
    for secret in body_secrets:
        assert secret not in record
        assert secret not in row["message"]
    assert row["message"] == "Upstox returned HTTP status 502"


@pytest.mark.parametrize("secret_text, secret", [
    ("unexpected payload r4nd0mSecret_8xQ9vL2p", "r4nd0mSecret_8xQ9vL2p"),
    ("unexpected payload sk-test_1234567890abcdef", "sk-test_1234567890abcdef"),
    ("unexpected payload Bearer token_secret_value_987", "token_secret_value_987"),
    ("unexpected candle timestamp=09:35 close=1234.50 volume=876543",
     "close=1234.50"),
])
def test_unexpected_exception_details_are_always_redacted(tmp_path, monkeypatch,
                                                           secret_text, secret):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        (_ for _ in ()).throw(RuntimeError(secret_text)))
    output = tmp_path / "unexpected-secret"
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    record = (output / "market_data_failures.jsonl").read_text()
    row = json.loads(record.splitlines()[0])
    assert row["stage"] == "upstox_http"
    assert row["category"] == "unexpected_failure"
    assert row["message"] == "Unexpected error; details redacted"
    assert secret not in record
    assert secret_text not in record


@pytest.mark.parametrize("invalid_row, reason", [
    (lambda row: row.__setitem__(1, 0), "nonpositive_price"),
    (lambda row: row.__setitem__(5, -1), "negative_volume"),
    (lambda row: row.__setitem__(2, 99), "invalid_ohlc_relationship"),
])
def test_filtered_ohlcv_rows_are_counted_in_success_event(tmp_path, monkeypatch, invalid_row, reason):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    start = pd.Timestamp("2026-10-28T09:15:00+05:30")
    candles = [[(start + pd.Timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100.5, 1000, 0]
               for i in range(36)]
    invalid_row(candles[0])
    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        {"data": {"candles": candles}})
    output = tmp_path / reason
    captured = prospective.capture_upstox_snapshot(
        "RELIANCE", output_dir=output, max_market_data_requests=1,
        allow_market_data_requests=True)
    event = json.loads((output / "market_data_events.jsonl").read_text().splitlines()[0])
    diagnostics = event["ohlcv_filter_diagnostics"]
    assert diagnostics["filtered_row_count"] == 1
    assert diagnostics["reason_counts"][reason] == 1
    assert len(json.loads(captured.read_text())["candles"]) == 35


def test_filtered_ohlcv_counts_are_attached_to_insufficient_candle_failure(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import app.config
    import app.upstox_client
    import app.research.deepseek_prospective as prospective

    fixed_now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    start = pd.Timestamp("2026-10-28T09:15:00+05:30")
    candles = [[(start + pd.Timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100.5, 1000, 0]
               for i in range(35)]
    candles[0][2] = 99
    monkeypatch.setattr(prospective, "datetime", FrozenDateTime)
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        upstox_access_token="offline-test-token"))
    monkeypatch.setattr(app.upstox_client, "get_intraday_candles", lambda *args, **kwargs:
                        {"data": {"candles": candles}})
    output = tmp_path / "filtered-to-insufficient"
    with pytest.raises(RuntimeError, match="failure recorded"):
        prospective.capture_upstox_snapshot("RELIANCE", output_dir=output,
                                            max_market_data_requests=1,
                                            allow_market_data_requests=True)
    row = json.loads((output / "market_data_failures.jsonl").read_text().splitlines()[0])
    assert row["category"] == "insufficient_completed_candles"
    assert row["ohlcv_filter_diagnostics"] == {
        "filtered_row_count": 1, "reason_counts": {"invalid_ohlc_relationship": 1}}


def test_old_market_data_failure_ledger_records_remain_readable(tmp_path):
    from app.research.deepseek_prospective import AppendOnlyJsonl

    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"failure_id": "legacy", "error_type": "ValueError",
                               "classification": "PROSPECTIVE_MARKET_DATA_FAILURE"}) + "\n")
    ledger = AppendOnlyJsonl(path, "failure_id")
    assert ledger.records()[0]["error_type"] == "ValueError"
    assert "stage" not in ledger.records()[0]


@pytest.mark.parametrize("flags, message", [
    (["--mode", "prospective-paper", "--allow-provider-requests", "--snapshot", "x.json"],
     "requires --enable-prospective"),
    (["--mode", "prospective-paper", "--enable-prospective", "--snapshot", "x.json"],
     "requires --allow-provider-requests"),
    (["--mode", "prospective-paper", "--enable-prospective", "--allow-provider-requests",
      "--fetch-upstox", "--symbol", "RELIANCE", "--snapshot", "x.json"],
     "not allowed with argument"),
])
def test_cli_requires_both_prospective_opt_ins(tmp_path, monkeypatch, flags, message, capsys):
    from scripts.run_deepseek_prospective_research import main
    import sys

    monkeypatch.setattr(sys, "argv", ["run_deepseek_prospective_research.py", *flags,
                                      "--output", str(tmp_path / "cli-output")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_refuses_prospective_mode_when_paper_trading_is_disabled(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import app.config
    import scripts.run_deepseek_prospective_research as runner
    import sys

    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(
        paper_trading=False, deepseek_api_key="test-only", deepseek_model="deepseek-flash",
        request_timeout_seconds=10.0))
    monkeypatch.setattr(runner, "capture_upstox_snapshot",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")))
    output = tmp_path / "paper-disabled"
    monkeypatch.setattr(sys, "argv", ["runner", "--mode", "prospective-paper",
        "--enable-prospective", "--allow-provider-requests", "--fetch-upstox",
        "--symbol", "RELIANCE", "--allow-market-data-requests",
        "--paper-capital", "1000", "--output", str(output)])

    assert runner.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert "PAPER_TRADING" in result["reason"]
    assert not list(output.glob("prospective_run_*.json"))


def test_offline_cli_preserves_each_report_when_output_directory_is_reused(tmp_path, monkeypatch, capsys):
    from scripts.run_deepseek_prospective_research import main
    import sys

    output = tmp_path / "offline-runs"
    monkeypatch.setattr(sys, "argv", ["runner", "--mode", "offline-test", "--output", str(output)])
    assert main() == 0
    first = json.loads(capsys.readouterr().out)["report"]
    assert main() == 0
    second = json.loads(capsys.readouterr().out)["report"]
    assert first != second
    reports = list(output.glob("offline_synthetic_check_*.json"))
    assert len(reports) == 2
    assert all(json.loads(path.read_text(encoding="utf-8"))["classification"] == "SYNTHETIC"
               for path in reports)
