"""Strict, versioned, provider-neutral V2 decision contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Any, Mapping

CONTRACT_VERSION = "ai-decision-v2.1"
PROMPT_VERSION = "ai-led-v2.1"
CONFIG_VERSION = "ai-v2-risk-2026-10-18.1"


class Action(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    EXIT = "EXIT"


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


def parse_timestamp(value: str | datetime, field_name: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    signal_timestamp: str
    data_cutoff: str
    eligible_symbols: tuple[str, ...]
    strategy_ids: tuple[str, ...]
    features: Mapping[str, Mapping[str, float]]
    feature_timestamps: Mapping[str, str]
    open_symbols: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION
    config_version: str = CONFIG_VERSION

    def validate(self, *, now: datetime, max_data_age_seconds: int) -> None:
        if not self.request_id.strip() or self.prompt_version != PROMPT_VERSION:
            raise ValueError("request_id and supported prompt_version are required")
        signal = parse_timestamp(self.signal_timestamp, "signal_timestamp")
        cutoff = parse_timestamp(self.data_cutoff, "data_cutoff")
        current = parse_timestamp(now, "now")
        if cutoff > signal or cutoff > current or signal > current:
            raise ValueError("request contains a future or contradictory timestamp")
        if not self.eligible_symbols or not self.strategy_ids:
            raise ValueError("eligible_symbols and strategy_ids cannot be empty")
        if len(set(self.eligible_symbols)) != len(self.eligible_symbols):
            raise ValueError("eligible_symbols contains duplicates")
        if len(set(self.strategy_ids)) != len(self.strategy_ids):
            raise ValueError("strategy_ids contains duplicates")
        if max_data_age_seconds < 0 or (current - cutoff).total_seconds() > max_data_age_seconds:
            raise ValueError("market data is stale")
        if set(self.features) - set(self.eligible_symbols) - {"MARKET"}:
            raise ValueError("features contain an ineligible symbol")
        if set(self.feature_timestamps) != set(self.features):
            raise ValueError("each feature record must have its own timestamp")
        for key, timestamp in self.feature_timestamps.items():
            ts = parse_timestamp(timestamp, f"feature_timestamps[{key}]")
            if ts > cutoff or ts > current:
                raise ValueError("feature timestamp is after the data cutoff")
            if (current - ts).total_seconds() > max_data_age_seconds:
                raise ValueError(f"feature data is stale: {key}")
        if set(self.feature_timestamps) - (set(self.features) | {"MARKET"}):
            raise ValueError("feature timestamp has no matching feature record")
        for symbol, values in self.features.items():
            if not isinstance(values, Mapping) or not values:
                raise ValueError(f"features for {symbol} must be a non-empty mapping")
            for name, value in values.items():
                if re.search(r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization|cookie)", str(name)):
                    raise ValueError("credential-like feature names are prohibited")
                _finite_number(value, f"features.{symbol}.{name}")


@dataclass(frozen=True)
class RankedSymbol:
    symbol: str
    rank: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class V2Recommendation:
    action: Action
    market_regime: MarketRegime
    ranked_symbols: tuple[RankedSymbol, ...]
    symbol: str | None
    strategy_id: str | None
    entry_price: float | None
    stop_loss: float | None
    target_price: float | None
    holding_bars: int | None
    rationale: str
    key_evidence: tuple[str, ...]
    confidence: float | None = None
    failure_reason: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, request: DecisionRequest) -> "V2Recommendation":
        if not isinstance(raw, dict):
            raise ValueError("response must be a JSON object")
        required = {
            "action", "market_regime", "ranked_symbols", "symbol", "strategy_id",
            "entry_price", "stop_loss", "target_price", "holding_bars", "rationale", "key_evidence",
        }
        if set(raw) != required | ({"confidence"} if "confidence" in raw else set()):
            raise ValueError("response fields do not match the V2 contract")
        try:
            action = Action(raw["action"])
            regime = MarketRegime(raw["market_regime"])
        except (ValueError, TypeError) as error:
            raise ValueError("action or market_regime is invalid") from error
        ranking_raw = raw["ranked_symbols"]
        if not isinstance(ranking_raw, list):
            raise ValueError("ranked_symbols must be a list")
        ranking: list[RankedSymbol] = []
        seen: set[str] = set()
        for expected_rank, item in enumerate(ranking_raw, start=1):
            if not isinstance(item, dict) or set(item) != {"symbol", "rank", "evidence"}:
                raise ValueError("ranked_symbols contains an invalid item")
            symbol, rank, evidence = item["symbol"], item["rank"], item["evidence"]
            if symbol not in request.eligible_symbols or symbol in seen or rank != expected_rank:
                raise ValueError("ranking contains an ineligible, duplicate, or misordered symbol")
            if not isinstance(evidence, list) or any(not isinstance(x, str) or not x.strip() for x in evidence):
                raise ValueError("ranking evidence must contain non-empty strings")
            seen.add(symbol)
            ranking.append(RankedSymbol(symbol, rank, tuple(x.strip() for x in evidence)))
        if set(seen) != set(request.eligible_symbols):
            raise ValueError("ranking must include every eligible symbol exactly once")
        symbol = raw["symbol"]
        strategy_id = raw["strategy_id"]
        if action is Action.HOLD:
            if any(raw[name] is not None for name in ("entry_price", "stop_loss", "target_price", "holding_bars")):
                raise ValueError("HOLD cannot include order parameters")
            if symbol is not None or strategy_id is not None:
                raise ValueError("HOLD cannot select a symbol or strategy")
        else:
            if symbol not in request.eligible_symbols or strategy_id not in request.strategy_ids:
                raise ValueError("action references an ineligible symbol or unregistered strategy")
            if action is Action.BUY:
                for name in ("entry_price", "stop_loss", "target_price"):
                    _finite_number(raw[name], name)
                if isinstance(raw["holding_bars"], bool) or not isinstance(raw["holding_bars"], int):
                    raise ValueError("holding_bars must be an integer")
                if raw["entry_price"] <= 0:
                    raise ValueError("entry_price must be positive")
            else:
                if raw["entry_price"] is not None:
                    _finite_number(raw["entry_price"], "entry_price")
                    if raw["entry_price"] <= 0:
                        raise ValueError("EXIT reference price must be positive")
                if any(raw[name] is not None for name in ("stop_loss", "target_price", "holding_bars")):
                    raise ValueError("EXIT cannot propose a new stop, target, or horizon")
        rationale, evidence = raw["rationale"], raw["key_evidence"]
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2000:
            raise ValueError("rationale must be a non-empty concise string")
        if not isinstance(evidence, list) or any(not isinstance(x, str) or not x.strip() for x in evidence):
            raise ValueError("key_evidence must contain non-empty strings")
        confidence = raw.get("confidence")
        if confidence is not None:
            confidence = _finite_number(confidence, "confidence")
            if not 0 <= confidence <= 1:
                raise ValueError("confidence must be between 0 and 1")
        return cls(action, regime, tuple(ranking), symbol, strategy_id,
                   raw["entry_price"], raw["stop_loss"], raw["target_price"],
                   raw["holding_bars"], rationale.strip(), tuple(x.strip() for x in evidence), confidence)


def safe_hold(reason: str) -> V2Recommendation:
    """Fail-closed decision used for provider, schema, or policy errors."""
    return V2Recommendation(
        Action.HOLD, MarketRegime.UNKNOWN, (), None, None, None, None, None, None,
        "No trade: V2 decision failed closed.", (), None, reason[:240],
    )


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value

