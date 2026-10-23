import json
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from app.v2.ai_led_research import (
    AIHypothesisDiscoveryService,
    AIResearchController,
    AIResearchDecisionService,
    AIResearchRequest,
    DECISION_CONTRACT_VERSION,
    DISCOVERY_CONTRACT_VERSION,
    ResearchAction,
    StrategyHypothesis,
)
from app.v2.audit import V2AuditLedger
from app.v2.contracts import DecisionRequest
from app.v2.provider import ProviderResult
from app.v2.registry import StrategyCandidate, StrategyRegistry
from app.v2.safety import RiskLimits, RiskState, SafetyGate

NOW = datetime.fromisoformat("2026-10-05T10:01:00+05:30")
SIGNAL = "2026-10-05T10:00:00+05:30"
STRATEGY = "research_candidate_v1"


class FakeProvider:
    provider_id = "fake-local"
    model_id = "fake-model-v1"

    def __init__(self, content, *, error=None, returned_model=None):
        self.content = content if isinstance(content, str) else json.dumps(content)
        self.error = error
        self.returned_model = returned_model or self.model_id
        self.calls = 0
        self.payloads = []

    def complete(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return ProviderResult(self.content, self.provider_id, self.returned_model,
                              self.model_id, 7.5, 12, 20)


def market_request(**changes):
    data = {
        "request_id": "request-led-1", "signal_timestamp": SIGNAL, "data_cutoff": SIGNAL,
        "eligible_symbols": ("RELIANCE", "TCS"), "strategy_ids": (STRATEGY,),
        "features": {"RELIANCE": {"rsi_14": 24.0, "volume_ratio": 1.4},
                     "TCS": {"rsi_14": 55.0, "volume_ratio": 0.8},
                     "MARKET": {"trend_score": -0.2, "volatility_pct": 1.1}},
        "feature_timestamps": {"RELIANCE": SIGNAL, "TCS": SIGNAL, "MARKET": SIGNAL},
    }
    data.update(changes)
    return DecisionRequest(**data)


def research_request(**changes):
    values = {"market": market_request(), "input_data_sha256": {"fixture.csv": "a" * 64},
              "source_provenance_status": "UNRESOLVED", "data_quality_issues": ()}
    if "request_id" in changes:
        values["market"] = market_request(request_id=changes.pop("request_id"))
    values.update(changes)
    return AIResearchRequest(**values)


def response(action="BUY", **changes):
    trading = action in {"BUY", "EXIT"}
    data = {
        "schema_version": DECISION_CONTRACT_VERSION,
        "action": action, "market_regime": "SIDEWAYS",
        "ranked_symbols": [
            {"symbol": "RELIANCE", "rank": 1, "evidence": ["rsi_14=24.0"]},
            {"symbol": "TCS", "rank": 2, "evidence": ["rsi_14=55.0"]},
        ],
        "symbol": "RELIANCE" if trading else None,
        "strategy_id": STRATEGY if trading else None,
        "entry_price": 1000.0 if action == "BUY" else (1005.0 if action == "EXIT" else None),
        "stop_loss": 995.0 if action == "BUY" else None,
        "target_price": 1010.0 if action == "BUY" else None,
        "holding_bars": 6 if action == "BUY" else None,
        "entry_condition": "Completed candle closes above the stated reversal level" if action == "BUY" else None,
        "invalidation_conditions": ["Price closes below the rejection low"] if action == "BUY" else [],
        "estimated_round_trip_cost_pct": 0.2 if action == "BUY" else None,
        "rationale": "The supplied features support a research candidate review.",
        "uncertainty": "Historical source and timestamp semantics remain unresolved.",
        "key_evidence": ["RELIANCE rsi_14=24.0", "MARKET trend_score=-0.2"],
        "confidence": 0.6,
    }
    data.update(changes)
    return data


def proposal(**changes):
    data = {
        "name": "Volatility compression release",
        "market_mechanism": "Compression may precede a directional expansion.",
        "hypothesis": "A breakout after low realized range may persist briefly.",
        "entry_conditions": "Enter after a completed close above the prior range.",
        "exit_conditions": "Exit at stop, target, or the registered horizon.",
        "invalidation_conditions": "Invalidate on a close back inside the range.",
        "required_features": ["atr_pct", "range_high", "range_low", "volume_ratio"],
        "max_holding_bars": 6,
        "falsification_criteria": "No positive net expectancy across a preregistered holdout.",
        "evidence": ["Use only the supplied realized-range and volume features."],
        "uncertainty": "The mechanism and source data have not been validated.",
    }
    data.update(changes)
    return data


def session_validator(_timestamp):
    return True


session_validator.authoritative = True


class Switch:
    enabled = False


def harness(tmp_path, provider, *, discovery_provider=None, candidate_status="RESEARCH_ONLY"):
    candidate = StrategyCandidate(STRATEGY, "candidate-1", "Registered research candidate",
                                  status=candidate_status,
                                  enabled_for_paper=(candidate_status == "PAPER_APPROVED"))
    registry = StrategyRegistry([candidate])
    gate = SafetyGate(registry=registry, limits=RiskLimits(), session_validator=session_validator,
                      kill_switch=Switch(), state=RiskState(100_000, 100_000))
    ledger = V2AuditLedger(tmp_path / "research.sqlite3")
    discovery_provider = discovery_provider or provider
    controller = AIResearchController(
        decision_service=AIResearchDecisionService(provider=provider, registry=registry),
        discovery_service=AIHypothesisDiscoveryService(provider=discovery_provider),
        safety_gate=gate, audit_ledger=ledger,
    )
    return controller, gate, ledger, registry


def test_ai_can_propose_and_immutably_register_new_research_hypothesis(tmp_path):
    provider = FakeProvider({"schema_version": DISCOVERY_CONTRACT_VERSION,
                             "hypotheses": [proposal()]})
    controller, _gate, ledger, registry = harness(tmp_path, FakeProvider(response()), discovery_provider=provider)
    answer = controller.discover(research_request(), now=NOW)
    assert answer.failure_type is None
    assert len(answer.proposals) == 1
    proposed = answer.proposals[0]
    candidate = controller.register_hypothesis(proposed, recorded_at=NOW)
    assert candidate.strategy_id == proposed.proposal_id
    assert candidate.status == "RESEARCH_ONLY" and candidate.enabled_for_paper is False
    assert registry.require(candidate.strategy_id) == candidate
    assert controller.load_registered_hypotheses() == (candidate,)
    with pytest.raises(FrozenInstanceError):
        proposed.hypothesis = "changed"
    changed = StrategyHypothesis.from_mapping(proposal(hypothesis="changed"))
    assert changed.proposal_id != proposed.proposal_id
    with pytest.raises(ValueError, match="exact proposal"):
        controller.register_hypothesis(changed, recorded_at=NOW)

    provider = controller.decisions.provider
    provider.content = json.dumps(response(strategy_id=candidate.strategy_id))
    expanded_market = market_request(strategy_ids=(STRATEGY, candidate.strategy_id))
    chosen = controller.evaluate(research_request(market=expanded_market),
                                 signal_id="selected-ai-hypothesis", now=NOW)
    assert chosen["envelope"].recommendation.strategy_id == candidate.strategy_id
    assert not chosen["risk_result"].approved
    assert chosen["final_decision"] == "HOLD"
    assert ledger.events_by_type("AI_HYPOTHESIS_DISCOVERY")
    ledger.close()


@pytest.mark.parametrize("action", ["BUY", "HOLD", "EXIT", "NO_TRADE"])
def test_versioned_decision_contract_parses_each_supported_action(tmp_path, action):
    market = market_request(open_symbols=("RELIANCE",) if action == "EXIT" else ())
    provider = FakeProvider(response(action))
    controller, _gate, ledger, _registry = harness(tmp_path, provider)
    result = controller.decisions.decide(research_request(market=market), now=NOW)
    assert result.failure_type is None
    assert result.recommendation.action is ResearchAction(action)
    assert [item.rank for item in result.recommendation.ranked_symbols] == [1, 2]
    assert result.request.input_data_sha256 == {"fixture.csv": "a" * 64}
    ledger.close()


def test_ai_selects_registered_candidate_then_python_gate_rejects_research_only(tmp_path):
    provider = FakeProvider(response())
    controller, _gate, ledger, _registry = harness(tmp_path, provider)
    result = controller.evaluate(research_request(source_provenance_status="VERIFIED"),
                                 signal_id="decision-1", now=NOW)
    assert result["envelope"].recommendation.strategy_id == STRATEGY
    assert result["risk_result"].approved is False
    assert "not approved" in result["risk_result"].reason
    assert result["final_decision"] == "HOLD"
    assert result["execution_enabled"] is False
    payload = json.loads(ledger.db.execute("SELECT payload FROM decisions WHERE signal_id='decision-1'").fetchone()[0])
    assert payload["envelope"]["recommendation"]["action"] == "BUY"
    assert payload["final_decision"] == "HOLD"
    assert payload["execution_enabled"] is False
    ledger.close()


def test_hypothesis_registration_is_frozen_once_decision_records_exist(tmp_path):
    provider = FakeProvider(response())
    discovery_provider = FakeProvider({"schema_version": DISCOVERY_CONTRACT_VERSION,
                                       "hypotheses": [proposal()]})
    controller, _gate, ledger, _registry = harness(tmp_path, provider,
                                                  discovery_provider=discovery_provider)
    controller.evaluate(research_request(), signal_id="already-evaluated", now=NOW)
    discovered = controller.discover(research_request(request_id="new-discovery"), now=NOW)
    with pytest.raises(ValueError, match="frozen before"):
        controller.register_hypothesis(discovered.proposals[0], recorded_at=NOW)
    ledger.close()


def test_registry_mutation_after_first_evaluation_is_detected(tmp_path):
    controller, _gate, ledger, registry = harness(tmp_path, FakeProvider(response()))
    controller.evaluate(research_request(), signal_id="first-evaluation", now=NOW)
    registry.register(StrategyCandidate("late-candidate", "v1", "Late mutation"))
    with pytest.raises(ValueError, match="catalog changed"):
        controller.evaluate(research_request(request_id="next-decision"),
                            signal_id="second-evaluation", now=NOW)
    ledger.close()


def test_python_risk_rejection_and_paper_approved_path_still_has_no_executor(tmp_path):
    too_wide = FakeProvider(response(stop_loss=900.0))
    controller, _gate, ledger, _registry = harness(tmp_path, too_wide, candidate_status="PAPER_APPROVED")
    unresolved = controller.evaluate(research_request(), signal_id="unresolved-source", now=NOW)
    assert not unresolved["risk_result"].approved
    assert "provenance" in unresolved["risk_result"].reason
    assert unresolved["final_decision"] == "HOLD"
    rejected = controller.evaluate(research_request(source_provenance_status="VERIFIED"),
                                   signal_id="wide-stop", now=NOW)
    assert not rejected["risk_result"].approved
    assert "stop distance" in rejected["risk_result"].reason
    assert rejected["final_decision"] == "HOLD"

    approved_provider = FakeProvider(response())
    controller, _gate, ledger2, _registry = harness(tmp_path / "approved", approved_provider,
                                                   candidate_status="PAPER_APPROVED")
    approved = controller.evaluate(research_request(source_provenance_status="VERIFIED"),
                                   signal_id="risk-approved", now=NOW)
    assert approved["risk_result"].approved
    assert approved["final_decision"] == "BUY"
    assert approved["execution_enabled"] is False
    assert ledger2.db.execute("SELECT event_type FROM events").fetchall() == []
    approved_payload = json.loads(ledger2.db.execute(
        "SELECT payload FROM decisions WHERE signal_id='risk-approved'").fetchone()[0])
    assert "_permit" not in approved_payload["risk_result"]
    assert approved_payload["execution_enabled"] is False
    ledger.close()
    ledger2.close()


@pytest.mark.parametrize("bad", [
    "not-json", {"schema_version": DECISION_CONTRACT_VERSION},
    response(action="BUY", stop_loss=1001.0), response(action="BUY", target_price=999.0),
    response(action="BUY", holding_bars=13), response(action="BUY", strategy_id="unregistered"),
    response(action="BUY", ranked_symbols=[{"symbol": "RELIANCE", "rank": 1, "evidence": ["x"]}]),
    response(action="BUY", estimated_round_trip_cost_pct=-0.1),
])
def test_malformed_or_unsafe_ai_decisions_fail_to_no_trade(tmp_path, bad):
    provider = FakeProvider(bad if isinstance(bad, str) else bad)
    controller, _gate, ledger, _registry = harness(tmp_path, provider)
    result = controller.decisions.decide(research_request(), now=NOW)
    assert result.recommendation.action is ResearchAction.NO_TRADE
    assert result.failure_type
    ledger.close()


def test_provider_failure_substitution_and_invalid_data_fail_closed(tmp_path):
    failed = FakeProvider(response(), error=TimeoutError())
    controller, _gate, ledger, _registry = harness(tmp_path, failed)
    assert controller.decisions.decide(research_request(), now=NOW).recommendation.action is ResearchAction.NO_TRADE
    assert failed.calls == 1
    ledger.close()

    failed_controller, _gate, failed_ledger, _registry = harness(tmp_path / "logged", failed)
    recorded = failed_controller.evaluate(research_request(request_id="provider-timeout"),
                                          signal_id="provider-timeout", now=NOW)
    assert recorded["final_decision"] == "NO_TRADE"
    assert failed_ledger.seen("provider-timeout")
    failure_payload = json.loads(failed_ledger.db.execute(
        "SELECT payload FROM decisions WHERE signal_id='provider-timeout'").fetchone()[0])
    assert failure_payload["envelope"]["failure_type"] == "TimeoutError"
    failed_ledger.close()

    substituted = FakeProvider(response(), returned_model="different-model")
    controller, _gate, ledger, _registry = harness(tmp_path, substituted)
    answer = controller.decisions.decide(research_request(), now=NOW)
    assert answer.recommendation.action is ResearchAction.NO_TRADE
    assert answer.model_substituted and answer.returned_model_id == "different-model"
    controller.decisions.allow_model_substitution = True
    allowed_substitution = controller.decisions.decide(research_request(), now=NOW)
    assert allowed_substitution.recommendation.action is ResearchAction.BUY
    assert allowed_substitution.model_substituted
    ledger.close()

    stale_market = market_request(data_cutoff="2026-10-05T09:00:00+05:30",
                                  feature_timestamps={"RELIANCE": "2026-10-05T09:00:00+05:30",
                                                      "TCS": "2026-10-05T09:00:00+05:30",
                                                      "MARKET": "2026-10-05T09:00:00+05:30"})
    invalid_provider = FakeProvider(response())
    controller, _gate, ledger, _registry = harness(tmp_path, invalid_provider)
    invalid = controller.decisions.decide(research_request(market=stale_market), now=NOW)
    assert invalid.recommendation.action is ResearchAction.NO_TRADE
    assert invalid_provider.calls == 0
    invalid_quality = controller.decisions.decide(
        research_request(data_quality_issues=("timestamp semantics unresolved",)), now=NOW)
    assert invalid_quality.recommendation.action is ResearchAction.NO_TRADE
    assert invalid_provider.calls == 0
    lookahead_market = market_request(
        features={"RELIANCE": {"rsi_14": 24.0, "future_return": 0.05},
                  "TCS": {"rsi_14": 55.0, "volume_ratio": 0.8},
                  "MARKET": {"trend_score": -0.2, "volatility_pct": 1.1}})
    lookahead = controller.decisions.decide(research_request(market=lookahead_market), now=NOW)
    assert lookahead.recommendation.action is ResearchAction.NO_TRADE
    assert invalid_provider.calls == 0
    ledger.close()


def test_hypothesis_proposals_are_bounded_falsifiable_and_content_addressed(tmp_path):
    malformed = FakeProvider({"schema_version": DISCOVERY_CONTRACT_VERSION,
                              "hypotheses": [proposal(max_holding_bars=13)]})
    controller, _gate, ledger, _registry = harness(tmp_path, FakeProvider(response()), discovery_provider=malformed)
    result = controller.discovery.discover(research_request(), now=NOW)
    assert result.failure_type
    assert result.proposals == ()
    valid = StrategyHypothesis.from_mapping(proposal())
    same = StrategyHypothesis.from_mapping(proposal())
    changed = StrategyHypothesis.from_mapping(proposal(hypothesis="different hypothesis"))
    assert valid.proposal_id == same.proposal_id
    assert valid.proposal_id != changed.proposal_id
    ledger.close()


def test_discovery_and_decision_prompts_are_reproducible_and_audited(tmp_path):
    discovery_provider = FakeProvider({"schema_version": DISCOVERY_CONTRACT_VERSION,
                                       "hypotheses": [proposal()]})
    decision_provider = FakeProvider(response())
    controller, _gate, ledger, _registry = harness(tmp_path, decision_provider,
                                                  discovery_provider=discovery_provider)
    first = controller.discover(research_request(), now=NOW)
    second = controller.discover(research_request(), now=NOW)
    assert first == second
    decision_one = controller.decisions.decide(research_request(), now=NOW)
    decision_two = controller.decisions.decide(research_request(), now=NOW)
    assert decision_one.recommendation == decision_two.recommendation
    assert decision_provider.payloads[0] == decision_provider.payloads[1]
    assert decision_one.provider_input_sha256 == decision_two.provider_input_sha256
    assert len(decision_one.prompt_sha256) == 64
    assert decision_one.registered_strategy_catalog[0].version == "candidate-1"
    assert len(ledger.events_by_type("AI_HYPOTHESIS_DISCOVERY")) == 1
    ledger.close()
