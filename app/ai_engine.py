"""OpenRouter market-context analysis. Failures always produce a safe HOLD."""

import json
import logging

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


def _safe_hold(reason):
    return {"decision": "HOLD", "confidence": 0.0, "reason": reason}


def parse_ai_response(content):
    if not isinstance(content, str) or not content.strip():
        return _safe_hold("AI returned an empty response")
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return _safe_hold("AI returned invalid JSON")
    if not isinstance(result, dict):
        return _safe_hold("AI response was not a JSON object")
    decision = result.get("decision")
    confidence = result.get("confidence")
    reason = result.get("reason")
    if decision not in {"BUY", "SELL", "HOLD"}:
        return _safe_hold("AI returned an invalid decision")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _safe_hold("AI returned an invalid confidence value")
    if not 0 <= confidence <= 1:
        return _safe_hold("AI confidence was outside the allowed range")
    if not isinstance(reason, str) or not reason.strip():
        return _safe_hold("AI returned no valid reason")
    return {"decision": decision, "confidence": float(confidence), "reason": reason.strip()}


def analyze_market(market_data, client=None):
    settings = get_settings()
    if client is None:
        if not settings.openrouter_api_key:
            return _safe_hold("OPENROUTER_API_KEY is not configured")
        try:
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                timeout=settings.request_timeout_seconds,
                max_retries=1,
            )
        except Exception:
            logger.exception("Could not initialize OpenRouter client")
            return _safe_hold("AI client initialization failed")
    prompt = (
        "Analyze this Indian-stock intraday market context for decision support. "
        "Do not initiate or authorize orders. The quantitative strategy and risk "
        "engine control eligibility. Return only JSON with decision (BUY, SELL, "
        "or HOLD), confidence (0..1), and reason. When uncertain, HOLD.\n"
        + json.dumps(market_data, default=str)
    )
    try:
        response = client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return parse_ai_response(content)
    except Exception as error:
        # Error text may include request details; never log headers or credentials.
        logger.warning("OpenRouter analysis failed (%s)", type(error).__name__)
        return _safe_hold(f"AI analysis unavailable ({type(error).__name__})")
