import json
from datetime import datetime
from pathlib import Path

import pytest

from app.v2.audit import V2AuditLedger
from app.v2.contracts import (
    Action, DecisionRequest, MarketRegime, V2Recommendation, safe_hold,
)
from app.v2.controller import DuplicateDecisionError, V2ResearchController
from app.v2.paper import PaperOnlyExecutor
from app.v2.registry import StrategyCandidate, StrategyRegistry
from app.v2.safety import RiskLimits, RiskState, SafetyGate, regular_weekday_session
from app.v2.service import DecisionService

DAY = "2026-10-05"
SIGNAL = f"{DAY}T10:00:00+05:30"
NOW = datetime.fromisoformat(f"{DAY}T10:01:00+05:30")
STRATEGY = "paper_candidate_test"


def request(**changes):
    data = dict(
        request_id="req-1", signal_timestamp=SIGNAL, data_cutoff=SIGNAL,
        eligible_symbols=("RELIANCE", "TCS"), strategy_ids=(STRATEGY,),
        features={"RELIANCE": {"rsi_14": 22.5}, "TCS": {"rsi_14": 27.0}},
        feature_timestamps={"RELIANCE": SIGNAL, "TCS": SIGNAL},
    )
    data.update(changes)
    return DecisionRequest(**data)


def raw(action="BUY", **changes):
    data = {
        "action": action, "market_regime": "SIDEWAYS",
        "ranked_symbols": [
            {"symbol": "RELIANCE", "rank": 1, "evidence": ["rsi_14=22.5"]},
            {"symbol": "TCS", "rank": 2, "evidence": ["rsi_14=27.0"]},
        ],
        "symbol": "RELIANCE" if action != "HOLD" else None,
        "strategy_id": STRATEGY if action != "HOLD" else None,
        "entry_price": 1000.0 if action == "BUY" else (995.0 if action == "EXIT" else None),
        "stop_loss": 995.0 if action == "BUY" else None,
        "target_price": 1010.0 if action == "BUY" else None,
        "holding_bars": 6 if action == "BUY" else None,
        "rationale": "Only supplied features were considered.",
        "key_evidence": ["rsi_14=22.5"], "confidence": 0.6,
    }
    data.update(changes)
    return data


class FakeProvider:
    provider_id = "test-provider"
    model_id = "model-v1"

    def __init__(self, content=None, returned_model="model-v1", error=None):
        self.content = json.dumps(content or raw())
        self.returned_model = returned_model
        self.error = error
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        if self.error:
            raise self.error
        from app.v2.provider import ProviderResult
        return ProviderResult(self.content, self.provider_id, self.returned_model,
                              self.model_id, 8.5, 20, 40)


def setup_gate(tmp_path, *, state=None, session=regular_weekday_session):
    registry = StrategyRegistry([StrategyCandidate(
        STRATEGY, "test-1", "Unit test candidate", "PAPER_APPROVED", True,
    )])
    switch_path = tmp_path / "KILL_SWITCH"
    gate = SafetyGate(
        registry=registry, limits=RiskLimits(), session_validator=session,
        kill_switch=type("Switch", (), {"enabled": property(lambda self: switch_path.exists())})(),
        state=state or RiskState(100_000, 100_000),
    )
    return gate, switch_path


def test_valid_response_has_versioned_recommendation():
    parsed = V2Recommendation.from_mapping(raw(), request())
    assert parsed.action is Action.BUY
    assert [item.symbol for item in parsed.ranked_symbols] == ["RELIANCE", "TCS"]
    assert parsed.confidence == 0.6  # Recorded only; never used by risk sizing.


@pytest.mark.parametrize("bad", [
    "not json", "[]", {"action": "BUY"},
    raw(ranked_symbols=[{"symbol": "UNKNOWN", "rank": 1, "evidence": ["x"]}]),
    raw(confidence=float("nan")), raw(entry_price=0), raw(stop_loss=1001),
    raw(target_price=1000), raw(holding_bars=99),
])
def test_malformed_responses_fail_to_hold(bad):
    provider = FakeProvider(content=bad if isinstance(bad, str) else json.dumps(bad))
    service = DecisionService(provider=provider, registry=setup_gate(Path("."))[0].registry)
    result = service.decide(request(), now=NOW)
    assert result.recommendation.action is Action.HOLD
    assert result.failure_type


def test_provider_failure_and_model_substitution_fail_closed():
    registry = setup_gate(Path("."))[0].registry
    failed = DecisionService(provider=FakeProvider(error=TimeoutError()), registry=registry).decide(request(), now=NOW)
    changed = DecisionService(provider=FakeProvider(returned_model="model-v2"), registry=registry).decide(request(), now=NOW)
    assert failed.recommendation.action is Action.HOLD
    assert failed.failure_type == "TimeoutError"
    assert changed.recommendation.action is Action.HOLD
    assert changed.model_substituted is True
    assert changed.returned_model_id == "model-v2"


def test_stale_and_future_feature_timestamps_fail_to_hold():
    registry = setup_gate(Path("."))[0].registry
    stale = request(data_cutoff="2026-10-05T09:00:00+05:30",
                    feature_timestamps={"RELIANCE": "2026-10-05T09:00:00+05:30", "TCS": "2026-10-05T09:00:00+05:30"})
    future = request(feature_timestamps={"RELIANCE": "2026-10-05T10:02:00+05:30", "TCS": SIGNAL})
    for req in (stale, future):
        answer = DecisionService(provider=FakeProvider(), registry=registry).decide(req, now=NOW)
        assert answer.recommendation.action is Action.HOLD


def test_unregistered_strategy_is_rejected_by_service():
    registry = setup_gate(Path("."))[0].registry
    answer = DecisionService(provider=FakeProvider(), registry=registry).decide(
        request(strategy_ids=("invented",)), now=NOW)
    assert answer.recommendation.action is Action.HOLD
    assert answer.failure_type == "ValueError"
    assert answer.recommendation.action is Action.HOLD


def test_independent_gate_checks_stops_position_size_and_daily_limits(tmp_path):
    gate, _ = setup_gate(tmp_path)
    decision = V2Recommendation.from_mapping(raw(), request())
    accepted = gate.approve(request(), decision, now=NOW, signal_id="s1")
    assert accepted.approved and accepted.quantity == 25
    assert accepted.allocation == 25_000
    assert accepted.planned_risk == 125
    state = RiskState(100_000, 100_000, daily_loss=2_000)
    gate, _ = setup_gate(tmp_path, state=state)
    assert not gate.approve(request(), decision, now=NOW, signal_id="s2").approved
    state = RiskState(100_000, 100_000, trades_today=3)
    gate, _ = setup_gate(tmp_path, state=state)
    assert "trades per day" in gate.approve(request(), decision, now=NOW, signal_id="s3").reason


def test_duplicate_invalid_session_and_kill_switch_are_rejected(tmp_path):
    gate, switch = setup_gate(tmp_path)
    decision = V2Recommendation.from_mapping(raw(), request())
    assert gate.approve(request(), decision, now=NOW, signal_id="same").approved
    assert "duplicate" in gate.approve(request(), decision, now=NOW, signal_id="same").reason
    gate, _ = setup_gate(tmp_path, session=lambda _ts: False)
    assert "session" in gate.approve(request(), decision, now=NOW, signal_id="bad-session").reason
    gate, switch = setup_gate(tmp_path)
    switch.touch()
    assert "kill switch" in gate.approve(request(), decision, now=NOW, signal_id="killed").reason


def test_session_rule_rejects_weekends_and_after_hours():
    assert not regular_weekday_session(datetime.fromisoformat("2026-10-04T10:00:00+05:30"))
    assert not regular_weekday_session(datetime.fromisoformat("2026-10-05T15:35:00+05:30"))
    assert regular_weekday_session(NOW)


def test_controller_logs_paper_only_fill_and_rejects_duplicate(tmp_path):
    registry = setup_gate(tmp_path)[0].registry
    state = RiskState(100_000, 100_000)
    gate, _ = setup_gate(tmp_path, state=state)
    ledger = V2AuditLedger(tmp_path / "audit.sqlite3")
    executor = PaperOnlyExecutor(audit_ledger=ledger, risk_state=state)
    controller = V2ResearchController(
        service=DecisionService(provider=FakeProvider(), registry=registry),
        safety_gate=gate, audit_ledger=ledger, paper_executor=executor,
    )
    result = controller.evaluate(request(), signal_id="fill-1", now=NOW)
    assert result.risk_result.approved
    assert result.paper_result["paper_only"] is True
    assert result.paper_result["quantity"] == 24  # Slippage-adjusted allocation cap
    row = ledger.db.execute("SELECT payload FROM decisions WHERE signal_id='fill-1'").fetchone()
    assert json.loads(row[0])["paper_only"] is True
    assert ledger.db.execute("SELECT event_type FROM events").fetchone()[0] == "PAPER_FILL"
    with pytest.raises(DuplicateDecisionError):
        controller.evaluate(request(), signal_id="fill-1", now=NOW)
    assert "place_order" not in dir(executor)
    ledger.close()


def test_paper_outcome_records_costs_slippage_and_updates_risk(tmp_path):
    ledger = V2AuditLedger(tmp_path / "audit.sqlite3")
    state = RiskState(100_000, 100_000)
    executor = PaperOnlyExecutor(audit_ledger=ledger, risk_state=state)
    gate_result = type("Approved", (), {"approved": True, "quantity": 25})()
    decision = V2Recommendation.from_mapping(raw(), request())
    fill = executor.open_position(signal_id="trade-1", decision=decision,
                                  gate_result=gate_result, recorded_at=NOW)
    outcome = executor.close_position(position_signal_id="trade-1", exit_signal_id="exit-1",
                                      reference_exit=990, recorded_at=NOW, exit_reason="test")
    assert fill["entry_slippage"] > 0
    assert outcome["cost_breakdown"]["slippage"] > 0
    assert outcome["net_pnl"] < outcome["gross_pnl"]
    assert state.daily_loss > 0 and "RELIANCE" not in state.open_positions
    assert ledger.db.execute("SELECT event_type FROM events ORDER BY event_id").fetchall() == [("PAPER_FILL",), ("PAPER_OUTCOME",)]
    with pytest.raises(Exception, match="append-only"):
        ledger.db.execute("UPDATE events SET event_type='changed'")
    ledger.close()


def test_audit_rejects_credential_fields(tmp_path):
    ledger = V2AuditLedger(tmp_path / "audit.sqlite3")
    with pytest.raises(ValueError, match="credential-like"):
        ledger.append_event("s", "TEST", NOW.isoformat(), {"openrouter_api_key": "no"})
    ledger.close()



def test_provider_failure_is_logged_as_hold(tmp_path):
    registry = setup_gate(tmp_path)[0].registry
    gate, _ = setup_gate(tmp_path)
    ledger = V2AuditLedger(tmp_path / "failed.sqlite3")
    controller = V2ResearchController(
        service=DecisionService(provider=FakeProvider(error=TimeoutError()), registry=registry),
        safety_gate=gate, audit_ledger=ledger,
    )
    result = controller.evaluate(request(), signal_id="failed-1", now=NOW)
    assert result.envelope.recommendation.action is Action.HOLD
    assert ledger.seen("failed-1")
    payload = json.loads(ledger.db.execute("SELECT payload FROM decisions").fetchone()[0])
    assert payload["envelope"]["recommendation"]["action"] == "HOLD"
    assert payload["envelope"]["failure_type"] == "TimeoutError"
    ledger.close()


def test_exit_recommendation_closes_matching_paper_position(tmp_path):
    registry = setup_gate(tmp_path)[0].registry
    state = RiskState(100_000, 100_000)
    gate, _ = setup_gate(tmp_path, state=state)
    ledger = V2AuditLedger(tmp_path / "exit.sqlite3")
    executor = PaperOnlyExecutor(audit_ledger=ledger, risk_state=state)
    entry_controller = V2ResearchController(
        service=DecisionService(provider=FakeProvider(), registry=registry),
        safety_gate=gate, audit_ledger=ledger, paper_executor=executor,
    )
    entry_controller.evaluate(request(), signal_id="entry-1", now=NOW)
    exit_time = datetime.fromisoformat(f"{DAY}T10:06:00+05:30")
    exit_request = request(
        request_id="req-exit", signal_timestamp=exit_time.isoformat(),
        data_cutoff=exit_time.isoformat(), open_symbols=("RELIANCE",),
        features={"RELIANCE": {"rsi_14": 23.0}, "TCS": {"rsi_14": 27.0}},
        feature_timestamps={"RELIANCE": exit_time.isoformat(), "TCS": exit_time.isoformat()},
    )
    exit_answer = raw("EXIT")
    controller = V2ResearchController(
        service=DecisionService(provider=FakeProvider(content=exit_answer), registry=registry),
        safety_gate=gate, audit_ledger=ledger, paper_executor=executor,
    )
    closed = controller.evaluate(exit_request, signal_id="exit-1", now=exit_time)
    assert closed.risk_result.approved
    assert closed.paper_result["exit_signal_id"] == "exit-1"
    assert not state.open_positions
    assert ledger.db.execute("SELECT event_type FROM events ORDER BY event_id").fetchall()[-1] == ("PAPER_OUTCOME",)
    ledger.close()


def test_credential_named_features_are_rejected_before_provider_call(tmp_path):
    registry = setup_gate(tmp_path)[0].registry
    provider = FakeProvider()
    bad_request = request(features={"RELIANCE": {"api_key": 1}, "TCS": {"rsi_14": 27.0}},
                          feature_timestamps={"RELIANCE": SIGNAL, "TCS": SIGNAL})
    answer = DecisionService(provider=provider, registry=registry).decide(bad_request, now=NOW)
    assert answer.recommendation.action is Action.HOLD
    assert provider.calls == 0
