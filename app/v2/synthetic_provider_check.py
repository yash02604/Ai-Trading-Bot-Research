"""Four-scenario, paper-only DeepSeek decision-path check on synthetic inputs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping

from app.research.sharp_reversal_experiment import load_session_dates
from .ai_led_research import AIResearchDecisionService, AIResearchRequest, ResearchAction
from .contracts import Action, DecisionRequest, RankedSymbol, V2Recommendation
from .evaluation import _verified_session
from .provider import (DEFAULT_MAX_COMPLETION_TOKENS, DEFAULT_MAX_RESPONSE_BYTES,
                       MAX_PROVIDER_TIMEOUT_SECONDS)
from .registry import StrategyCandidate, StrategyRegistry
from .safety import RiskLimits, RiskState, SafetyGate

SYNTHETIC_TEST_TIME = "2025-02-03T10:01:00+05:30"
SYNTHETIC_CUTOFF = "2025-02-03T10:00:00+05:30"
SYNTHETIC_SYMBOL = "SYNTH"
SYNTHETIC_STRATEGY_ID = "synthetic_provider_check_candidate"
MAX_MODEL_REQUESTS = 4


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    features: Mapping[str, float]


SCENARIOS = (
    Scenario("bullish_reversal", "Synthetic possible bullish reversal", {
        "return_5bar_pct": -4.0, "return_1bar_pct": 1.8, "volume_ratio": 2.2,
        "close_location": 0.82, "signal_conflict": 0.0,
    }),
    Scenario("continuing_downtrend", "Synthetic continuing downtrend", {
        "return_5bar_pct": -5.2, "return_1bar_pct": -1.4, "volume_ratio": 1.3,
        "close_location": 0.18, "signal_conflict": 0.0,
    }),
    Scenario("sideways_low_opportunity", "Synthetic sideways low-opportunity market", {
        "return_5bar_pct": 0.15, "return_1bar_pct": -0.03, "volume_ratio": 0.72,
        "close_location": 0.51, "signal_conflict": 0.0,
    }),
    Scenario("ambiguous_insufficient", "Synthetic ambiguous/insufficient setup", {
        "return_5bar_pct": -1.1, "return_1bar_pct": 1.0, "volume_ratio": 0.91,
        "close_location": 0.52, "signal_conflict": 1.0,
    }),
)


class DisabledSwitch:
    """Explicit per-run test switch; there is no executor or order interface here."""
    enabled = False


class _CountingProvider:
    def __init__(self, delegate):
        self.delegate = delegate
        self.provider_id = getattr(delegate, "provider_id", "unknown")
        self.model_id = getattr(delegate, "model_id", "unknown")
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return self.delegate.complete(payload)


def _synthetic_request(scenario: Scenario) -> AIResearchRequest:
    feature_record = {
        SYNTHETIC_SYMBOL: dict(scenario.features),
        "MARKET": {"benchmark_return_5bar_pct": -0.2, "volatility_pct": 1.1},
    }
    encoded = json.dumps({"scenario_id": scenario.scenario_id, "features": feature_record},
                         sort_keys=True, separators=(",", ":")).encode()
    market = DecisionRequest(
        request_id=f"synthetic-{scenario.scenario_id}",
        signal_timestamp=SYNTHETIC_CUTOFF, data_cutoff=SYNTHETIC_CUTOFF,
        eligible_symbols=(SYNTHETIC_SYMBOL,), strategy_ids=(SYNTHETIC_STRATEGY_ID,),
        features=feature_record,
        feature_timestamps={SYNTHETIC_SYMBOL: SYNTHETIC_CUTOFF, "MARKET": SYNTHETIC_CUTOFF},
    )
    return AIResearchRequest(
        market=market,
        input_data_sha256={f"synthetic://{scenario.scenario_id}": hashlib.sha256(encoded).hexdigest()},
        source_provenance_status="VERIFIED",
        data_quality_issues=(),
    )


def _evidence_references_inputs(decision, request: AIResearchRequest) -> bool:
    """Require each evidence string to name a supplied feature and repeat its value."""
    pairs = [(str(name).casefold(), float(value))
             for values in request.market.features.values() for name, value in values.items()]
    evidence = list(decision.key_evidence)
    evidence.extend(item for row in decision.ranked_symbols for item in row.evidence)
    evidence.append(decision.rationale)
    def anchored(item):
        numeric_values = [float(value) for value in
                          re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", item)]
        lowered = item.casefold()
        return any(name in lowered and any(math.isclose(value, feature_value, rel_tol=0, abs_tol=1e-9)
                                           for value in numeric_values)
                   for name, feature_value in pairs)
    return bool(evidence) and all(anchored(item) for item in evidence)


def _risk_check(envelope, request, *, registry, limits, session_dates, now, signal_id):
    decision = envelope.recommendation
    if envelope.failure_type:
        return {"status": "NOT_RUN", "approved": False,
                "reason": f"decision validation failed: {envelope.failure_type}"}
    if decision.action is ResearchAction.NO_TRADE:
        return {"status": "NOT_APPLICABLE", "approved": False, "reason": "NO_TRADE has no proposal to approve"}
    action = Action(decision.action.value)
    recommendation = V2Recommendation(
        action, decision.market_regime,
        tuple(RankedSymbol(row.symbol, row.rank, row.evidence) for row in decision.ranked_symbols),
        decision.symbol, decision.strategy_id, decision.entry_price, decision.stop_loss,
        decision.target_price, decision.holding_bars, decision.rationale,
        decision.key_evidence, decision.confidence,
    )
    validator = lambda stamp: _verified_session(stamp, session_dates)
    validator.authoritative = True
    gate = SafetyGate(registry=registry, limits=limits, session_validator=validator,
                      kill_switch=DisabledSwitch(), state=RiskState(100_000.0, 100_000.0))
    result = gate.approve_research(request.market, recommendation, now=now, signal_id=signal_id)
    return {"status": "APPROVED" if result.approved else "REJECTED",
            "approved": result.approved, "reason": result.reason,
            "quantity": result.quantity, "allocation": result.allocation,
            "planned_risk": result.planned_risk,
            "paper_permit_issued": result._permit is not None}


def run_synthetic_decisions(provider, *, now: datetime, session_manifest_path: Path,
                            requested_model_id: str | None = None) -> dict:
    """Run exactly four sequential provider completions with no retry or executor path."""
    if len(SCENARIOS) != MAX_MODEL_REQUESTS:
        raise RuntimeError("scenario set changed from the reviewed four-call bound")
    limits = RiskLimits()
    registry = StrategyRegistry((StrategyCandidate(
        SYNTHETIC_STRATEGY_ID, "synthetic-v1",
        "Synthetic provider integration test candidate only; not a validated or enabled strategy.",
        status="RESEARCH_ONLY", enabled_for_paper=False),))
    counted = _CountingProvider(provider)
    service = AIResearchDecisionService(provider=counted, registry=registry,
                                        max_data_age_seconds=300, allow_model_substitution=False)
    session_dates = load_session_dates(session_manifest_path)
    results = []
    requests_made = 0
    for scenario in SCENARIOS:
        request = _synthetic_request(scenario)
        envelope = service.decide(request, now=now)
        # Count actual .complete() attempts, including calls that raise or time out.
        requests_made = counted.calls
        recommendation = envelope.recommendation
        evidence_valid = (envelope.failure_type is not None
                          or _evidence_references_inputs(recommendation, request))
        validation_failure = envelope.failure_type
        if validation_failure is None and not evidence_valid:
            validation_failure = "UnsupportedEvidence"
        if envelope.failure_type:
            risk = {"status": "NOT_RUN", "approved": False,
                    "reason": f"decision validation failed: {envelope.failure_type}"}
        elif not evidence_valid:
            risk = {"status": "NOT_RUN", "approved": False,
                    "reason": "decision evidence does not match supplied synthetic feature names and values"}
        else:
            risk = _risk_check(envelope, request, registry=registry, limits=limits,
                               session_dates=session_dates, now=now,
                               signal_id=f"provider-check-{scenario.scenario_id}")
        results.append({
            "scenario_id": scenario.scenario_id,
            "input_origin": "DETERMINISTIC_SYNTHETIC",
            "requested_model_id": envelope.requested_model_id,
            "returned_model_id": envelope.returned_model_id,
            "provider_id": envelope.provider_id,
            "latency_ms": envelope.latency_ms,
            "prompt_tokens": envelope.prompt_tokens,
            "completion_tokens": envelope.completion_tokens,
            "response_valid": envelope.failure_type is None and evidence_valid,
            "decision_action": recommendation.action.value,
            "validation_failure": validation_failure,
            "failure_stage": envelope.failure_stage,
            "failure_code": envelope.failure_code,
            "response_finish_reason": envelope.response_finish_reason,
            "response_bytes": envelope.response_bytes,
            "provider_http_status": envelope.provider_http_status,
            "provider_error_code": envelope.provider_error_code,
            "provider_error_type": envelope.provider_error_type,
            "risk_outcome": risk,
        })
    all_scenarios_passed = all(row["response_valid"] for row in results)
    return {
        "schema_version": "deepseek-synthetic-decision-check-v1",
        "status": "COMPLETED" if all_scenarios_passed else "COMPLETED_WITH_FAILURES",
        "workflow_completed": True,
        "provider_test_passed": all_scenarios_passed,
        "input_origin": "DETERMINISTIC_SYNTHETIC",
        "synthetic_test_time": now.isoformat(),
        "max_model_requests": MAX_MODEL_REQUESTS,
        "requests_made": requests_made,
        "automatic_retries": 0,
        "requested_model_id": requested_model_id or getattr(provider, "model_id", "unknown"),
        "model_substitution_allowed": False,
        "max_completion_tokens": getattr(provider, "max_completion_tokens", DEFAULT_MAX_COMPLETION_TOKENS),
        "max_response_bytes": getattr(provider, "max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES),
        "timeout_seconds": getattr(provider, "timeout_seconds", None),
        "performance_evidence": False,
        "request_metadata": {
            "endpoint": "https://api.deepseek.com/chat/completions",
            "http_method": "POST",
            "response_format": "json_object",
            "messages_included": True,
            "authorization_included": False,
        },
        "scenarios": results,
    }


def build_preflight_report(*, settings, checked_at: datetime) -> dict:
    model = getattr(settings, "deepseek_model", "")
    key = getattr(settings, "deepseek_api_key", None)
    key_present = isinstance(key, str) and bool(key.strip())
    timeout = getattr(settings, "request_timeout_seconds", None)
    blockers = []
    if not isinstance(model, str) or not model.strip():
        blockers.append("configured model ID is missing")
    if not key_present:
        blockers.append("DEEPSEEK_API_KEY is unavailable through the existing settings loader")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        blockers.append("configured timeout is invalid")
    return {
        "schema_version": "deepseek-synthetic-decision-check-v1",
        "status": "BLOCKED" if blockers else "READY",
        "checked_at_utc": checked_at.astimezone(timezone.utc).isoformat(),
        "configured_model_id": model if isinstance(model, str) else "",
        "api_key_configured": key_present,
        "configured_timeout_seconds": timeout,
        "effective_timeout_seconds": min(float(timeout), MAX_PROVIDER_TIMEOUT_SECONDS)
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0 else None,
        "max_completion_tokens": DEFAULT_MAX_COMPLETION_TOKENS,
        "max_response_bytes": DEFAULT_MAX_RESPONSE_BYTES,
        "automatic_retries": 0,
        "model_substitution_allowed": False,
        "max_model_requests": MAX_MODEL_REQUESTS,
        "requests_made": 0,
        "blockers": blockers,
        "credentials_logged": False,
        "synthetic_inputs_only": True,
        "performance_evidence": False,
    }


def write_report_exclusive(report: dict, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return output_path
