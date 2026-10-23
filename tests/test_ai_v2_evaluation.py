import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.v2.ai_led_research import (
    AIResearchDecisionService, AIResearchRequest, DECISION_CONTRACT_VERSION,
)
from app.v2.audit import V2AuditLedger
from app.v2.contracts import DecisionRequest
from app.v2.evaluation import EvaluationBlocked, _verified_session, evaluate_recorded_decisions
from app.research.execution_integrity import ExecutionIntegrityError
from app.v2.provider import ProviderResult
from app.v2.registry import StrategyCandidate, StrategyRegistry
from app.v2.safety import GateResult, RiskLimits, RiskState, SafetyGate
from app.research.sharp_reversal_experiment import load_session_dates

ROOT = Path(__file__).resolve().parents[1]
SESSION_MANIFEST = ROOT / "data/audits/nse_cm_session_manifest_v2.json"
STRATEGY = "research_only_fixture"
DAY = "2025-02-03"
IST = "+05:30"
SIGNAL = f"{DAY}T09:20:00{IST}"
RECORDED = f"{DAY}T09:25:00{IST}"


class Provider:
    provider_id = "offline-test-provider"
    model_id = "fixture-model-v1"

    def __init__(self, *, fail=False, stop_loss=995, malformed=False, returned_model=None,
                 entry_condition="NEXT_ELIGIBLE_BAR_OPEN"):
        self.fail = fail
        self.stop_loss = stop_loss
        self.malformed = malformed
        self.returned_model = returned_model or self.model_id
        self.entry_condition = entry_condition

    def complete(self, _payload):
        if self.fail:
            raise TimeoutError("fixture timeout")
        response = {
            "schema_version": DECISION_CONTRACT_VERSION, "action": "BUY",
            "market_regime": "SIDEWAYS",
            "ranked_symbols": [{"symbol": "ABC", "rank": 1, "evidence": ["fixture close=1000"]}],
            "symbol": "ABC", "strategy_id": STRATEGY,
            "entry_price": 1000, "stop_loss": self.stop_loss, "target_price": 1010,
            "holding_bars": 1, "entry_condition": self.entry_condition,
            "invalidation_conditions": ["close below stop"],
            "estimated_round_trip_cost_pct": 0.2,
            "rationale": "offline fixture only", "uncertainty": "synthetic data",
            "key_evidence": ["completed candle fixture"], "confidence": 0.5,
        }
        return ProviderResult("not-json" if self.malformed else json.dumps(response), self.provider_id, self.returned_model,
                              self.model_id, 1.0, 5, 10)


class Switch:
    def __init__(self, enabled=False):
        self.enabled = enabled


def _request(cutoff=SIGNAL, signal=SIGNAL, source_hash="0" * 64):
    market = DecisionRequest(
        request_id="fixture-request-" + cutoff[-14:-6].replace(":", ""),
        signal_timestamp=signal, data_cutoff=cutoff, eligible_symbols=("ABC",),
        strategy_ids=(STRATEGY,), features={"ABC": {"close": 1000.0}},
        feature_timestamps={"ABC": cutoff},
    )
    return AIResearchRequest(market, {"bars.csv": source_hash}, "VERIFIED", ())


def _bars(path, *, missing=(), zero_volume=False):
    rows = []
    for minute, opening, high, low, close in [
        (15, 999, 1000, 998, 999), (20, 1000, 1001, 999, 1000),
        (25, 1000, 1001, 999, 1000), (30, 1000, 1002, 999, 1001),
        (35, 1001, 1003, 1000, 1002), (40, 1002, 1003, 1001, 1002),
        (45, 1002, 1004, 1001, 1003), (50, 1003, 1004, 1002, 1003),
    ]:
        label = f"{DAY}T09:{minute:02d}:00{IST}"
        if label in missing:
            continue
        rows.append({"symbol": "ABC", "timestamp": label, "open": opening,
                     "high": high, "low": low, "close": close,
                     "volume": 0 if zero_volume and minute == 20 else 100})
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _write_evidence(path, source):
    import hashlib
    raw = Path(source).read_bytes()
    path.write_text(json.dumps({
        "schema_version": 1, "label_semantics": "START", "verification_status": "VERIFIED",
        "provider_documentation_url": "https://provider.invalid/docs/candles",
        "acquisition_manifest_sha256": "b" * 64, "acquisition_request_ids": ["fixture-request"],
        "dataset_sha256": {"bars.csv": hashlib.sha256(raw).hexdigest()},
    }), encoding="utf-8")
    return path


def _ledger(tmp_path, *, count=1, provider=None, second_cutoff=None, second_recorded=None,
            missing=(), zero_volume=False, allow_model_substitution=False):
    source = _bars(tmp_path / "bars.csv", missing=missing, zero_volume=zero_volume)
    import hashlib
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    registry = StrategyRegistry([StrategyCandidate(STRATEGY, "candidate-v1", "test only")])
    service = AIResearchDecisionService(provider=provider or Provider(), registry=registry,
                                        allow_model_substitution=allow_model_substitution)
    ledger = V2AuditLedger(tmp_path / "decisions.sqlite3")
    for idx in range(count):
        cutoff = second_cutoff if idx == 1 else SIGNAL
        req = _request(cutoff=cutoff, signal=cutoff, source_hash=source_hash)
        recorded_text = RECORDED if idx == 0 else (second_recorded or f"{DAY}T09:30:00{IST}")
        now = datetime.fromisoformat(recorded_text)
        envelope = service.decide(req, now=now)
        # The historic controller correctly held research-only candidates from paper
        # execution. The offline evaluator re-applies the same risk checks without a permit.
        assert ledger.append_ai_research_decision(f"signal-{idx}", recorded_text, envelope,
                                                  GateResult(False, "research-only paper-disabled"), "HOLD")
    ledger.close()
    evidence = _write_evidence(tmp_path / "timestamp-evidence.json", source)
    return tmp_path / "decisions.sqlite3", source, evidence


def _evaluate(tmp_path, *, ledger=None, source=None, evidence=None, kill_switch=None, **kwargs):
    if ledger is None:
        ledger, source, evidence = _ledger(tmp_path)
    return evaluate_recorded_decisions(
        decision_ledger=ledger, source_files={"bars.csv": source},
        timestamp_evidence_path=evidence, session_manifest_path=SESSION_MANIFEST,
        output_dir=tmp_path / "evaluation", kill_switch=kill_switch or Switch(), **kwargs,
    )


def test_end_to_end_replay_is_causal_costed_and_repeatable(tmp_path):
    ledger, source, evidence = _ledger(tmp_path)
    before = ledger.read_bytes()
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    report = json.loads(Path(result.report_path).read_text())
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    fill = next(event for event in events if event["event_type"] == "SIMULATED_FILL")
    outcome = next(event for event in events if event["event_type"] == "TRADE_OUTCOME")
    assert fill["bar_timestamp"] == f"{DAY}T09:30:00+05:30"
    assert fill["bar_timestamp"] > RECORDED
    assert 0 < fill["quantity"] * fill["fill_price"] <= 25_000
    assert outcome["costs"]["total_cost"] > 0
    assert outcome["net_pnl"] < outcome["gross_pnl"]
    assert report["gross_to_net_deterioration"] == pytest.approx(outcome["costs"]["total_cost"])
    assert report["comparison_status"] == "BLOCKED"
    assert {"gross_pnl", "net_pnl", "costs_by_component", "max_drawdown", "trade_count",
            "win_rate", "profit_factor", "average_net_per_trade", "exposure_bar_minutes",
            "stock", "market_regime"} <= set(report)
    assert ledger.read_bytes() == before
    repeat = evaluate_recorded_decisions(decision_ledger=ledger, source_files={"bars.csv": source},
        timestamp_evidence_path=evidence, session_manifest_path=SESSION_MANIFEST,
        output_dir=tmp_path / "evaluation-repeat", kill_switch=Switch())
    repeat_report = json.loads(Path(repeat.report_path).read_text())
    assert repeat.run_id == result.run_id
    assert repeat_report["events_sha256"] == report["events_sha256"]
    with pytest.raises(EvaluationBlocked, match="already exists"):
        _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)


def test_missing_intervening_candle_rejects_without_synthesizing_a_fill(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, missing=(f"{DAY}T09:25:00{IST}",))
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert not any(event["event_type"] == "SIMULATED_FILL" for event in events)
    assert any(event["event_type"] == "TRADE_REJECTED" and "missing candle" in event["reason"]
               for event in events)


def test_zero_volume_equity_bar_is_not_silently_treated_as_valid(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, zero_volume=True)
    with pytest.raises(EvaluationBlocked, match="unresolved equity volume"):
        _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)


def test_stale_decision_data_fails_closed(tmp_path):
    ledger, source, evidence = _ledger(tmp_path)
    # Altering only the immutable DB is impossible through its public append API; use a
    # separately constructed stale recorded request to ensure strict freshness is enforced.
    registry = StrategyRegistry([StrategyCandidate(STRATEGY, "candidate-v1", "test only")])
    service = AIResearchDecisionService(provider=Provider(), registry=registry, max_data_age_seconds=3600)
    stale = _request(cutoff=f"{DAY}T09:15:00{IST}", source_hash=__import__("hashlib").sha256(source.read_bytes()).hexdigest())
    late = datetime.fromisoformat(RECORDED)
    envelope = service.decide(stale, now=late)
    stale_db = V2AuditLedger(tmp_path / "stale.sqlite3")
    stale_db.append_ai_research_decision("stale-signal", RECORDED, envelope,
                                         GateResult(False, "prior hold"), "HOLD")
    stale_db.close()
    # Bind unchanged source bytes; evaluation’s 300-second policy is stricter than replay source config.
    with pytest.raises(EvaluationBlocked, match="stale"):
        evaluate_recorded_decisions(decision_ledger=tmp_path / "stale.sqlite3",
            source_files={"bars.csv": source}, timestamp_evidence_path=evidence,
            session_manifest_path=SESSION_MANIFEST, output_dir=tmp_path / "stale-evaluation",
            kill_switch=Switch())


def test_unverified_timestamp_evidence_blocks_before_any_fill(tmp_path):
    ledger, source, evidence = _ledger(tmp_path)
    raw = json.loads(evidence.read_text())
    raw["verification_status"] = "UNRESOLVED"
    evidence.write_text(json.dumps(raw))
    with pytest.raises(ExecutionIntegrityError, match="not verified"):
        _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)


def test_kill_switch_and_risk_rejection_never_create_simulated_fills(tmp_path):
    ledger, source, evidence = _ledger(tmp_path)
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence,
                       kill_switch=Switch(enabled=True))
    report = json.loads(Path(result.report_path).read_text())
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert report["trade_count"] == 0
    assert any(event["event_type"] == "TRADE_REJECTED" and "kill switch" in event["reason"]
               for event in events)
    assert not any(event["event_type"] == "SIMULATED_FILL" for event in events)


def test_python_risk_rejects_invalid_stop_and_limits_daily_trades(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, provider=Provider(stop_loss=900))
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 0
    assert any(event["event_type"] == "TRADE_REJECTED" and "stop distance" in event["reason"]
               for event in events)

    other = tmp_path / "daily-limit"
    other.mkdir()
    ledger, source, evidence = _ledger(other, count=2, second_cutoff=f"{DAY}T09:35:00{IST}",
                                       second_recorded=f"{DAY}T09:40:00{IST}")
    result = _evaluate(other, ledger=ledger, source=source, evidence=evidence,
                       limits=RiskLimits(max_trades_per_day=1))
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 1
    assert any(event["event_type"] == "TRADE_REJECTED" and "maximum trades per day" in event["reason"]
               for event in events)


def test_kill_switch_is_rechecked_at_simulated_fill_entry(tmp_path):
    class FlipSwitch:
        calls = 0
        @property
        def enabled(self):
            self.calls += 1
            return self.calls >= 2

    ledger, source, evidence = _ledger(tmp_path)
    switch = FlipSwitch()
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence, kill_switch=switch)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert switch.calls >= 2
    assert not any(event["event_type"] == "SIMULATED_FILL" for event in events)
    assert any(event["event_type"] == "TRADE_REJECTED" and "kill switch" in event["reason"]
               for event in events)


def test_future_feature_timestamp_fails_closed(tmp_path):
    import hashlib
    source = _bars(tmp_path / "future-bars.csv")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = _request(source_hash=digest)
    future_market = replace(request.market, feature_timestamps={"ABC": f"{DAY}T09:25:00{IST}"})
    request = replace(request, market=future_market)
    service = AIResearchDecisionService(provider=Provider(), registry=StrategyRegistry([
        StrategyCandidate(STRATEGY, "candidate-v1", "test only")]))
    envelope = service.decide(request, now=datetime.fromisoformat(RECORDED))
    ledger = V2AuditLedger(tmp_path / "future.sqlite3")
    ledger.append_ai_research_decision("future-signal", RECORDED, envelope,
                                       GateResult(False, "prior hold"), "NO_TRADE")
    ledger.close()
    evidence = _write_evidence(tmp_path / "future-evidence.json", source)
    with pytest.raises(EvaluationBlocked, match="feature timestamp is after the data cutoff"):
        evaluate_recorded_decisions(decision_ledger=tmp_path / "future.sqlite3",
            source_files={"bars.csv": source}, timestamp_evidence_path=evidence,
            session_manifest_path=SESSION_MANIFEST, output_dir=tmp_path / "future-eval", kill_switch=Switch())


def test_concurrent_duplicate_decision_is_rejected_after_first_trade(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, count=2, second_cutoff=f"{DAY}T09:25:00{IST}")
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    rejected = [event for event in events if event["event_type"] == "TRADE_REJECTED"]
    assert result.fill_count == 1
    assert any("overlaps" in event["reason"] for event in rejected)


def test_duplicate_decision_key_is_not_filled_twice(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, count=2, second_cutoff=SIGNAL,
                                       second_recorded=RECORDED)
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 1
    assert any(event["event_type"] == "TRADE_REJECTED" and "duplicate decision" in event["reason"]
               for event in events)


def test_provider_failure_is_recorded_as_no_trade(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, provider=Provider(fail=True))
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 0
    assert any(event["event_type"] == "NO_TRADE" for event in events)
    assert not any(event["event_type"] == "SIMULATED_FILL" for event in events)


@pytest.mark.parametrize("provider", [Provider(malformed=True), Provider(returned_model="fixture-model-v2")])
def test_malformed_and_substituted_model_responses_fail_to_no_trade(tmp_path, provider):
    ledger, source, evidence = _ledger(tmp_path, provider=provider)
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 0
    assert any(event["event_type"] == "NO_TRADE" for event in events)


def test_unimplemented_free_form_entry_condition_is_rejected(tmp_path):
    ledger, source, evidence = _ledger(tmp_path, provider=Provider(entry_condition="buy after bullish reversal"))
    result = _evaluate(tmp_path, ledger=ledger, source=source, evidence=evidence)
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert result.fill_count == 0
    assert any(event["event_type"] == "TRADE_REJECTED"
               and "outside the implemented execution contract" in event["reason"] for event in events)


def test_session_manifest_holiday_and_clock_boundaries_fail_closed():
    manifest = load_session_dates(SESSION_MANIFEST)
    assert not _verified_session(datetime.fromisoformat("2024-11-20T10:00:00+05:30"), manifest)
    assert _verified_session(datetime.fromisoformat("2025-02-03T09:15:00+05:30"), manifest)
    assert not _verified_session(datetime.fromisoformat("2025-02-03T15:30:00+05:30"), manifest)


def test_next_session_candle_cannot_be_used_for_entry(tmp_path):
    import hashlib
    cutoff = f"{DAY}T15:20:00{IST}"
    recorded = f"{DAY}T15:25:00{IST}"
    next_day = "2025-02-04"
    source = tmp_path / "late-bars.csv"
    rows = []
    for stamp in (f"{DAY}T15:15:00{IST}", cutoff, f"{DAY}T15:25:00{IST}",
                  f"{next_day}T09:15:00{IST}"):
        rows.append({"symbol": "ABC", "timestamp": stamp, "open": 1000, "high": 1001,
                     "low": 999, "close": 1000, "volume": 100})
    pd.DataFrame(rows).to_csv(source, index=False)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    request = _request(cutoff=cutoff, signal=cutoff, source_hash=digest)
    service = AIResearchDecisionService(provider=Provider(), registry=StrategyRegistry([
        StrategyCandidate(STRATEGY, "candidate-v1", "test only")]))
    envelope = service.decide(request, now=datetime.fromisoformat(recorded))
    ledger = V2AuditLedger(tmp_path / "late.sqlite3")
    ledger.append_ai_research_decision("late-signal", recorded, envelope,
                                       GateResult(False, "prior paper hold"), "HOLD")
    ledger.close()
    evidence = _write_evidence(tmp_path / "late-evidence.json", source)
    result = evaluate_recorded_decisions(decision_ledger=tmp_path / "late.sqlite3",
        source_files={"bars.csv": source}, timestamp_evidence_path=evidence,
        session_manifest_path=SESSION_MANIFEST, output_dir=tmp_path / "late-eval", kill_switch=Switch())
    events = [json.loads(line) for line in Path(result.ledger_path).read_text().splitlines()]
    assert not any(event["event_type"] == "SIMULATED_FILL" for event in events)
    assert any(event["event_type"] == "TRADE_REJECTED" and "future same-session" in event["reason"]
               for event in events)


def test_research_safety_approval_uses_same_limits_without_executor_permit():
    candidate = StrategyCandidate(STRATEGY, "candidate-v1", "research only")
    registry = StrategyRegistry([candidate])
    def validator(_stamp):
        return True
    validator.authoritative = True
    gate = SafetyGate(registry=registry, limits=RiskLimits(), session_validator=validator,
                      kill_switch=Switch(), state=RiskState(100_000, 100_000))
    request = _request().market
    from app.v2.contracts import Action, MarketRegime, V2Recommendation
    recommendation = V2Recommendation(Action.BUY, MarketRegime.SIDEWAYS, (), "ABC", STRATEGY,
        1000, 995, 1010, 1, "reason", ("evidence",))
    approved = gate.approve_research(request, recommendation,
        now=datetime.fromisoformat(RECORDED), signal_id="research-gate")
    assert approved.approved and approved.quantity > 0 and approved._permit is None
    assert not gate.consume_paper_permit(approved._permit, request, recommendation,
        "research-gate", approved.quantity, datetime.fromisoformat(RECORDED))
    rejected = gate.approve_research(request, recommendation,
        now=datetime.fromisoformat(RECORDED), signal_id="duplicate-research-gate")
    assert not rejected.approved
