import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.v2.ai_led_research import DECISION_CONTRACT_VERSION
from app.v2.provider import OpenRouterProvider
from app.v2.synthetic_provider_check import (
    SCENARIOS, SYNTHETIC_TEST_TIME, _synthetic_request, build_preflight_report,
    run_synthetic_decisions,
)

NOW = datetime.fromisoformat(SYNTHETIC_TEST_TIME)
MANIFEST = __import__("pathlib").Path(__file__).resolve().parents[1] / "data/audits/nse_cm_session_manifest_v2.json"


def decision(action, *, entry_price=None, stop_loss=None, target_price=None,
             holding_bars=None, symbol=None, strategy_id=None, evidence_name="return_5bar_pct",
             evidence_value=-4.0):
    buy = action == "BUY"
    exit_action = action == "EXIT"
    trading = buy or exit_action
    return {
        "schema_version": DECISION_CONTRACT_VERSION,
        "action": action,
        "market_regime": "SIDEWAYS",
        "ranked_symbols": [{"symbol": "SYNTH", "rank": 1,
                             "evidence": [f"{evidence_name}={evidence_value} from synthetic input"]}],
        "symbol": "SYNTH" if trading else None,
        "strategy_id": "synthetic_provider_check_candidate" if trading else None,
        "entry_price": (100.0 if buy else 100.5 if exit_action else None) if entry_price is None else entry_price,
        "stop_loss": (99.2 if buy else None) if stop_loss is None else stop_loss,
        "target_price": (101.0 if buy else None) if target_price is None else target_price,
        "holding_bars": (3 if buy else None) if holding_bars is None else holding_bars,
        "entry_condition": "Synthetic next eligible candle confirmation" if buy else None,
        "invalidation_conditions": ["Synthetic close below stated stop"] if buy else [],
        "estimated_round_trip_cost_pct": 0.2 if buy else None,
        "rationale": f"The supplied {evidence_name} value is {evidence_value}; no other fact is asserted.",
        "uncertainty": "Synthetic inputs cannot establish trading performance.",
        "key_evidence": [f"{evidence_name}={evidence_value} supplied synthetic feature"],
        "confidence": 0.5,
    }


class MockProvider:
    provider_id = "openrouter-mock"
    model_id = "fixture-model"
    timeout_seconds = 1.0
    max_completion_tokens = 64
    max_response_bytes = 4096

    def __init__(self, responses, *, returned_model=None, error=None):
        self.responses = iter(responses)
        self.returned_model = returned_model or self.model_id
        self.error = error
        self.calls = 0

    def complete(self, _payload):
        self.calls += 1
        if self.error:
            raise self.error
        content = json.dumps(next(self.responses))
        from app.v2.provider import ProviderResult
        return ProviderResult(content, self.provider_id, self.returned_model,
                              self.model_id, 1.25, 25, 40)


def run(provider):
    return run_synthetic_decisions(provider, now=NOW, session_manifest_path=MANIFEST,
                                   requested_model_id=provider.model_id)


def no_trade_responses():
    return [decision("NO_TRADE", evidence_value=-5.2),
            decision("NO_TRADE", evidence_value=0.15),
            decision("NO_TRADE", evidence_name="signal_conflict", evidence_value=1.0)]


def test_four_deterministic_synthetic_scenarios_parse_and_run_python_risk():
    provider = MockProvider([
        decision("BUY"), decision("NO_TRADE", evidence_value=-5.2),
        decision("HOLD", evidence_value=0.15),
        decision("NO_TRADE", evidence_name="signal_conflict", evidence_value=1.0)])
    report = run(provider)
    assert [item.scenario_id for item in SCENARIOS] == [
        "bullish_reversal", "continuing_downtrend", "sideways_low_opportunity", "ambiguous_insufficient"]
    assert provider.calls == report["requests_made"] == report["max_model_requests"] == 4
    assert [row["decision_action"] for row in report["scenarios"]] == [
        "BUY", "NO_TRADE", "HOLD", "NO_TRADE"]
    assert report["scenarios"][0]["risk_outcome"]["status"] == "APPROVED"
    assert report["scenarios"][0]["risk_outcome"]["paper_permit_issued"] is False
    assert report["scenarios"][1]["risk_outcome"]["status"] == "NOT_APPLICABLE"
    assert report["scenarios"][2]["risk_outcome"]["status"] == "APPROVED"
    assert all(row["input_origin"] == "DETERMINISTIC_SYNTHETIC" for row in report["scenarios"])
    assert all(row["requested_model_id"] == row["returned_model_id"] == "fixture-model"
               for row in report["scenarios"])
    assert all(row["response_valid"] for row in report["scenarios"])
    assert report["performance_evidence"] is False


def test_invalid_buy_is_rejected_by_python_risk_gate():
    provider = MockProvider([decision("BUY", stop_loss=98.0)] + no_trade_responses())
    report = run(provider)
    first = report["scenarios"][0]
    assert first["response_valid"] is True
    assert first["risk_outcome"]["status"] == "REJECTED"
    assert "stop distance" in first["risk_outcome"]["reason"]
    assert first["risk_outcome"]["paper_permit_issued"] is False


def test_model_substitution_fails_closed_for_all_scenarios():
    provider = MockProvider([decision("BUY")] * 4, returned_model="different-model")
    report = run(provider)
    assert provider.calls == 4
    assert all(row["decision_action"] == "NO_TRADE" and not row["response_valid"]
               for row in report["scenarios"])
    assert all(row["validation_failure"] == "ModelSubstitutionError" for row in report["scenarios"])
    assert all(row["returned_model_id"] == "different-model" for row in report["scenarios"])


def test_exit_without_a_recorded_open_position_is_not_fabricated_or_accepted():
    provider = MockProvider([decision("EXIT")] + no_trade_responses())
    report = run(provider)
    row = report["scenarios"][0]
    assert row["decision_action"] == "NO_TRADE"
    assert row["response_valid"] is False
    assert row["validation_failure"] == "ValueError"
    assert row["risk_outcome"]["status"] == "NOT_RUN"


def test_unanchored_decision_evidence_is_rejected_before_risk_check():
    provider = MockProvider([decision("BUY", evidence_name="unsupported_claim")] + no_trade_responses())
    row = run(provider)["scenarios"][0]
    assert row["response_valid"] is False
    assert row["validation_failure"] == "UnsupportedEvidence"
    assert row["risk_outcome"]["status"] == "NOT_RUN"


def test_evidence_referencing_a_feature_with_an_invented_value_is_rejected():
    provider = MockProvider([decision("BUY", evidence_value=999.0)] + no_trade_responses())
    row = run(provider)["scenarios"][0]
    assert row["response_valid"] is False
    assert row["validation_failure"] == "UnsupportedEvidence"
    assert row["risk_outcome"]["status"] == "NOT_RUN"


def test_provider_errors_do_not_leak_error_text_and_are_not_retried():
    provider = MockProvider([], error=RuntimeError("secret-bearing fake error"))
    report = run(provider)
    serialized = json.dumps(report)
    assert provider.calls == report["requests_made"] == 4
    assert all(row["decision_action"] == "NO_TRADE" for row in report["scenarios"])
    assert "secret-bearing fake error" not in serialized


def test_openrouter_provider_sends_token_cap_and_rejects_oversized_content():
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2), model="fixture-model")

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    provider = OpenRouterProvider(api_key="unit-test-only", model_id="fixture-model",
        timeout_seconds=2, client=client, max_completion_tokens=12, max_response_bytes=100)
    provider.complete({"system_prompt": "synthetic", "user_payload": "synthetic"})
    assert captured["model"] == "fixture-model"
    assert captured["max_tokens"] == 12

    large_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_:
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="x" * 101))],
                        usage=None, model="fixture-model"))))
    bounded = OpenRouterProvider(api_key="unit-test-only", model_id="fixture-model",
        timeout_seconds=2, client=large_client, max_response_bytes=100)
    with pytest.raises(ValueError, match="byte limit"):
        bounded.complete({"system_prompt": "synthetic", "user_payload": "synthetic"})


def test_provider_timeout_is_bounded_and_retry_is_disabled(monkeypatch):
    captured = {}
    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    provider = OpenRouterProvider(api_key="unit-test-only", model_id="fixture-model", timeout_seconds=5)
    provider._get_client()
    assert captured["timeout"] == 5
    assert captured["max_retries"] == 0
    with pytest.raises(ValueError, match="timeout"):
        OpenRouterProvider(api_key="unit-test-only", model_id="fixture-model", timeout_seconds=21)
    with pytest.raises(ValueError, match="timeout"):
        OpenRouterProvider(api_key="unit-test-only", model_id="fixture-model", timeout_seconds=float("nan"))


def test_stale_synthetic_request_fails_before_provider_call():
    from app.v2.ai_led_research import AIResearchDecisionService
    from app.v2.registry import StrategyCandidate, StrategyRegistry
    from dataclasses import replace
    provider = MockProvider([decision("NO_TRADE")])
    request = _synthetic_request(SCENARIOS[0])
    old_cutoff = "2025-02-03T09:00:00+05:30"
    market = replace(request.market, signal_timestamp=old_cutoff, data_cutoff=old_cutoff,
                     feature_timestamps={key: old_cutoff for key in request.market.feature_timestamps})
    stale_request = replace(request, market=market)
    registry = StrategyRegistry([StrategyCandidate("synthetic_provider_check_candidate", "synthetic-v1",
        "Synthetic only")])
    envelope = AIResearchDecisionService(provider=provider, registry=registry).decide(
        stale_request, now=NOW)
    assert provider.calls == 0
    assert envelope.failure_type == "ValueError"
    assert envelope.recommendation.action.value == "NO_TRADE"


def test_missing_or_blank_credential_preflight_is_blocked_without_provider_requests():
    for key in (None, "   "):
        settings = SimpleNamespace(openrouter_model="openrouter/free",
                                   openrouter_api_key=key, request_timeout_seconds=10.0)
        report = build_preflight_report(settings=settings, checked_at=NOW)
        assert report["status"] == "BLOCKED"
        assert report["api_key_configured"] is False
        assert report["requests_made"] == 0
        assert report["credentials_logged"] is False
