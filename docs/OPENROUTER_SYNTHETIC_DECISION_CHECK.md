> **Superseded (2026-10-25):** the AI provider migrated from OpenRouter to the official DeepSeek API (`https://api.deepseek.com`, `DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL`). This document and the `backtests/openrouter_synthetic_decision_check/` artifacts are retained as a historical record of the pre-migration state; new runs write to `backtests/deepseek_synthetic_decision_check/` via `scripts/run_deepseek_synthetic_decision_test.py`.

# OpenRouter synthetic decision integration check

Date: 2026-10-23

## Result

**Live test blocked before any external request.** The existing application settings loader resolved the configured model as `openrouter/free`, a 10-second configured timeout, and no configured OpenRouter API key. The safe preflight report is [`run_20261023T083013697321Z.json`](../backtests/openrouter_synthetic_decision_check/run_20261023T083013697321Z.json). It records `requests_made: 0`; no response, action, latency, or token usage is available from OpenRouter.

No credential value was printed or stored. The runner uses the existing `get_settings()` mechanism, checks only whether a key is present, and stops before constructing the provider if it is absent. It makes at most four sequential completions, has no automatic retries, keeps the configured model unchanged, rejects model-ID substitution, and writes reports exclusively to new files.

## Offline implementation and verification

The test path uses the existing `AIResearchDecisionService` and strict AI-led decision contract, then independently checks evidence grounding and calls `SafetyGate.approve_research` for valid BUY/HOLD/EXIT recommendations. It issues no paper permit and has no executor. The four inputs are deterministic synthetic bullish-reversal, continuing-downtrend, sideways, and ambiguous/insufficient scenarios with timestamped features and reproducible hashes.

The provider adapter now sends a maximum of 512 completion tokens, rejects response text larger than 16 KiB, bounds timeout to 20 seconds, and configures the OpenAI client with `max_retries=0`. The configured timeout is clamped to that maximum. Model substitution remains disallowed. Evidence and rationale references must name a supplied synthetic feature and repeat one of its supplied numeric values before the risk gate runs. A valid EXIT cannot be fabricated because the scenarios have no open positions; an EXIT response therefore fails the contract and becomes a validation failure.

Mocked focused tests: **71 passed** across `tests/test_synthetic_provider_check.py`, `tests/test_ai_led_research.py`, and `tests/test_ai_v2.py`. Full offline suite: **309 passed**. These are not OpenRouter responses and are not trading-performance evidence.

## Limitations and next action

No OpenRouter model output has been observed, so actual requested/returned model IDs, returned actions, latency, token use, response validity, and risk outcomes are unknown. The configured alias `openrouter/free` may resolve to a different model; the integration check will fail closed if the returned model ID differs. The response byte cap is checked immediately after the SDK returns content; the completion-token ceiling is also sent to the provider. Free-text evidence grounding is a bounded syntactic check, not a proof that all natural-language reasoning is factually sound.

The next action is to make an OpenRouter API key available through the existing settings mechanism, then explicitly rerun `scripts/run_openrouter_synthetic_decision_test.py`. The script will save a new report and never overwrite this blocked preflight record. No Upstox, market-data, historical-performance, or broker-order path is involved.
