import hashlib
import json
from pathlib import Path

import pandas as pd

from app.research.deepseek_eval_v1 import (
    _parse_decision,
    build_decision_request,
    evaluate_provenance_gate,
    load_candles,
    run_evaluation,
)
from app.v2.provider import ProviderResult
from app.risk_engine import RiskConfig, RiskState, check_trade_allowed
from scripts.run_deepseek_research_eval_v1 import _normalize_mode, _write_synthetic_candles


class MockProvider:
    model_id = "fixture-model-v1"
    is_mock = True

    def __init__(self, decision="BUY", error=None):
        self.decision = decision
        self.error = error
        self.calls = 0
        self.payloads = []

    def complete(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        if self.error:
            raise self.error
        content = json.dumps({
            "decision": self.decision,
            "confidence": 0.75,
            "reason": "deterministic mock response",
        })
        return ProviderResult(content, "mock", self.model_id, self.model_id, 0.0)


def _offline_run(provider, candles, output, *, cache, ledger, **kwargs):
    return run_evaluation(
        provider, mode="OFFLINE_SYNTHETIC", candles_path=candles,
        provenance_summary=output / "not-used.json", output_dir=output,
        model=provider.model_id, initial_capital=100_000, cache_path=cache,
        ledger_path=ledger, max_requests=10, **kwargs,
    )


def test_cli_mode_normalization_accepts_both_spellings():
    assert _normalize_mode("offline-synthetic") == "OFFLINE_SYNTHETIC"
    assert _normalize_mode("OFFLINE_SYNTHETIC") == "OFFLINE_SYNTHETIC"
    assert _normalize_mode("historical-live") == "HISTORICAL_LIVE"


def test_risk_state_uses_supported_fields_and_daily_limits():
    allowed, _ = check_trade_allowed(RiskConfig(capital=100_000), RiskState(
        daily_loss=0.0, open_positions=0, trades_today=0))
    denied, reason = check_trade_allowed(RiskConfig(capital=100_000), RiskState(
        daily_loss=2_000.0, open_positions=0, trades_today=0))
    assert allowed
    assert not denied
    assert reason == "Daily loss limit reached"


def test_offline_replay_is_causal_costed_and_cache_idempotent(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "fixture.csv")
    provider = MockProvider()
    shared = tmp_path / "shared"
    first = _offline_run(provider, candles, tmp_path / "run-1",
                         cache=shared / "cache.json", ledger=shared / "ledger.jsonl")
    report = first.to_report()
    ledger_path = shared / "ledger.jsonl"
    recorded = [json.loads(line) for line in ledger_path.read_text().splitlines()]

    assert first.classification == "SYNTHETIC"
    assert first.status == "COMPLETED"
    assert report["performance_evidence"] is False
    assert report["synthetic_inputs_only"] is True
    assert "not historical trading performance" in report["results_note"]
    assert provider.calls == first.request_count == 1
    assert len(recorded) == 1
    request = json.loads(provider.payloads[0]["user_payload"])
    assert all(bar["timestamp"] <= request["signal_timestamp"]
               for bar in request["recent_bars"])
    assert request["recent_bars"][-1]["timestamp"] == request["signal_timestamp"]
    assert first.partitions["train"]["quantitative_only"]["trade_count"] == 1
    assert first.partitions["train"]["ai_assisted"]["trade_count"] == 1
    assert first.partitions["train"]["ai_assisted"]["estimated_costs"] > 0
    assert "SYNTH" in first.partitions["train"]["ai_assisted"]["per_symbol"]

    repeated_provider = MockProvider()
    second = _offline_run(repeated_provider, candles, tmp_path / "run-2",
                          cache=shared / "cache.json", ledger=ledger_path)
    assert repeated_provider.calls == second.request_count == 0
    assert second.cache_hits == 1
    assert len(ledger_path.read_text().splitlines()) == 1


def test_point_in_time_request_does_not_change_when_future_rows_are_appended(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "fixture.csv")
    frame = load_candles(candles)["SYNTH"]
    signal_index = 38
    prefix = frame.iloc[:signal_index + 1]
    full_request = build_decision_request("SYNTH", frame, signal_index)
    prefix_request = build_decision_request("SYNTH", prefix, signal_index)
    assert full_request == prefix_request


def test_sell_is_recorded_as_a_veto_and_never_creates_a_short_trade(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "fixture.csv")
    provider = MockProvider(decision="SELL")
    result = _offline_run(provider, candles, tmp_path / "sell",
                          cache=tmp_path / "sell-cache.json",
                          ledger=tmp_path / "sell-ledger.jsonl")
    recorded = json.loads((tmp_path / "sell-ledger.jsonl").read_text().splitlines()[0])
    assert recorded["decision"] == "SELL"
    assert result.partitions["train"]["ai_assisted"]["trade_count"] == 0
    assert result.partitions["train"]["quantitative_only"]["trade_count"] == 1


def test_invalid_decision_fails_closed_but_known_actions_are_preserved():
    for action in ("BUY", "SELL", "HOLD"):
        response = _parse_decision(json.dumps({
            "decision": action, "confidence": 0.5, "reason": "fixture",
        }))
        assert response["decision"] == action
    assert _parse_decision('{"decision":"SHORT","confidence":1,"reason":"bad"}')["decision"] == "HOLD"


def test_provenance_gate_requires_exact_candle_hash_binding(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "candles.csv")
    digest = hashlib.sha256(candles.read_bytes()).hexdigest()
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "overall_dataset_status": "VERIFIED",
        "dataset_binding_status": "VERIFIED",
        "findings": [],
        "dataset_sha256": {"candles.csv": digest},
    }))
    assert evaluate_provenance_gate(summary, candles) == (True, [])

    summary.write_text(json.dumps({
        "overall_dataset_status": "VERIFIED",
        "dataset_binding_status": "VERIFIED",
        "findings": [],
        "dataset_sha256": {"candles.csv": "0" * 64},
    }))
    passed, blockers = evaluate_provenance_gate(summary, candles)
    assert not passed
    assert any("does not match" in blocker for blocker in blockers)

    summary.write_text(json.dumps({
        "overall_dataset_status": "VERIFIED",
        "dataset_binding_status": "INVALIDATED",
        "findings": [],
        "dataset_sha256": {"candles.csv": digest},
    }))
    passed, blockers = evaluate_provenance_gate(summary, candles)
    assert not passed
    assert any("byte binding is 'INVALIDATED'" in blocker for blocker in blockers)


def test_historical_mode_fails_before_provider_call_when_provenance_is_unresolved(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "candles.csv")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "overall_dataset_status": "UNRESOLVED",
        "dataset_binding_status": "VERIFIED",
        "status_counts": {"UNRESOLVED": 10},
        "findings": [{"finding_id": "source", "status": "UNRESOLVED"}],
    }))
    provider = MockProvider()
    result = run_evaluation(
        provider, mode="HISTORICAL_LIVE", candles_path=candles,
        provenance_summary=summary, output_dir=tmp_path / "blocked",
        model=provider.model_id, max_requests=1, allow_live=True,
    )
    assert result.status == "BLOCKED_MISSING_PREREQUISITES"
    assert result.request_count == provider.calls == 0
    assert any("UNRESOLVED" in blocker for blocker in result.blockers)
    assert any("10 unresolved finding(s)" in blocker for blocker in result.blockers)
    assert any("SHA-256" in blocker for blocker in result.blockers)


def test_symbols_are_evaluated_in_global_chronological_order(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "one.csv")
    source = pd.read_csv(candles)
    later = source.copy()
    later["stock"] = "AAA"
    later["timestamp"] = (pd.to_datetime(later["timestamp"], utc=True)
                          + pd.Timedelta(minutes=40)).dt.tz_convert("Asia/Kolkata").dt.strftime(
                              "%Y-%m-%dT%H:%M:%S+05:30")
    earlier = source.copy()
    earlier["stock"] = "ZZZ"
    both = pd.concat([later, earlier], ignore_index=True)
    path = tmp_path / "two-symbols.csv"
    both.to_csv(path, index=False)

    provider = MockProvider()
    result = _offline_run(provider, path, tmp_path / "ordered",
                          cache=tmp_path / "ordered-cache.json",
                          ledger=tmp_path / "ordered-ledger.jsonl")
    decisions = [json.loads(line) for line in
                 (tmp_path / "ordered-ledger.jsonl").read_text().splitlines()]
    assert [item["symbol"] for item in decisions] == ["ZZZ", "AAA"]
    assert result.partitions["train"]["ai_assisted"]["trade_count"] == 2


def test_provider_failure_is_visible_and_does_not_leak_exception_text(tmp_path):
    candles = _write_synthetic_candles(tmp_path / "fixture.csv")
    provider = MockProvider(error=RuntimeError("fixture-secret-must-not-appear"))
    result = _offline_run(provider, candles, tmp_path / "failure",
                          cache=tmp_path / "failure-cache.json",
                          ledger=tmp_path / "failure-ledger.jsonl")
    serialized = json.dumps(result.to_report())
    assert result.status == "COMPLETED_WITH_FAILURES"
    assert result.request_count == 1
    assert "fixture-secret-must-not-appear" not in serialized
    assert result.partitions["train"]["ai_assisted"]["trade_count"] == 0
    assert "provider request(s) failed" in result.blockers[0]

