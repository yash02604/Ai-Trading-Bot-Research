"""Versioned orchestration boundary: AI proposes; the caller's Python gate decides."""
from dataclasses import dataclass
from datetime import datetime
import json
import time
from typing import Protocol

from .contracts import (
    CONFIG_VERSION, CONTRACT_VERSION, PROMPT_VERSION, DecisionRequest,
    V2Recommendation, safe_hold,
)
from .registry import StrategyRegistry

SYSTEM_PROMPT = """You are the decision-support component of a paper-only trading research system.
Use only the supplied timestamp-valid features. Never infer missing prices, indicators,
future outcomes, fills, or unstated facts. Rank every eligible symbol exactly once.
Select only a supplied registered strategy ID. Return one JSON object matching the
contract. Recommend BUY, HOLD, or EXIT. HOLD has null symbol, strategy and order fields.
BUY requires entry, stop below entry, target above entry, and a bounded holding horizon.
EXIT is allowed only for a listed open symbol and has null stop/target/horizon. Evidence
must refer only to supplied feature names and values. Confidence is not a probability.
"""


@dataclass(frozen=True)
class DecisionEnvelope:
    contract_version: str
    prompt_version: str
    config_version: str
    request: DecisionRequest
    recommendation: V2Recommendation
    provider_id: str
    requested_model_id: str
    returned_model_id: str
    model_substituted: bool
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    failure_type: str | None = None


class Provider(Protocol):
    model_id: str
    def complete(self, request_payload: dict): ...


class DecisionService:
    def __init__(self, *, provider: Provider, registry: StrategyRegistry,
                 max_data_age_seconds: int = 300, allow_model_substitution: bool = False):
        self.provider, self.registry = provider, registry
        self.max_data_age_seconds = max_data_age_seconds
        self.allow_model_substitution = allow_model_substitution

    def decide(self, request: DecisionRequest, *, now: datetime) -> DecisionEnvelope:
        try:
            request.validate(now=now, max_data_age_seconds=self.max_data_age_seconds)
            known_ids = {candidate.strategy_id for candidate in self.registry.snapshot()}
            if set(request.strategy_ids) - known_ids:
                raise ValueError("request includes an unregistered strategy ID")
            payload = {
                "contract_version": CONTRACT_VERSION,
                "eligible_symbols": list(request.eligible_symbols),
                "registered_strategies": [
                    {"strategy_id": c.strategy_id, "version": c.version,
                     "description": c.description, "status": c.status}
                    for c in self.registry.snapshot() if c.strategy_id in request.strategy_ids
                ],
                "open_symbols": list(request.open_symbols),
                "signal_timestamp": request.signal_timestamp,
                "data_cutoff": request.data_cutoff,
                "feature_timestamps": dict(request.feature_timestamps),
                "features": request.features,
            }
            started = time.perf_counter()
            result = self.provider.complete({
                "system_prompt": SYSTEM_PROMPT,
                "user_payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            })
            latency = result.latency_ms if result.latency_ms is not None else (time.perf_counter() - started) * 1000
            substituted = result.model_id != result.requested_model_id
            if substituted and not self.allow_model_substitution:
                recommendation = safe_hold("provider model identifier changed")
                failure_type = "ModelSubstitution"
            else:
                try:
                    decoded = json.loads(result.content)
                    recommendation = V2Recommendation.from_mapping(decoded, request)
                    if recommendation.action.value == "EXIT" and recommendation.symbol not in request.open_symbols:
                        raise ValueError("EXIT symbol is not an open position")
                    failure_type = None
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    recommendation = safe_hold(f"invalid AI response: {type(error).__name__}")
                    failure_type = type(error).__name__
            return DecisionEnvelope(
                CONTRACT_VERSION, request.prompt_version, request.config_version,
                request, recommendation, result.provider_id, result.requested_model_id,
                result.model_id, substituted, latency, result.prompt_tokens,
                result.completion_tokens, failure_type,
            )
        except Exception as error:
            model = getattr(self.provider, "model_id", "unknown")
            return DecisionEnvelope(
                CONTRACT_VERSION, request.prompt_version, request.config_version,
                request, safe_hold(f"decision failed safely: {type(error).__name__}"),
                getattr(self.provider, "provider_id", "unknown"), model, "unavailable",
                False, None, None, None, type(error).__name__,
            )
