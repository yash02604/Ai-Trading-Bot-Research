"""Synthetic unit cases for reconciliation; never acquire observations or AI decisions."""
import copy

import pandas as pd
import pytest

from app.research import signal_evaluation as evaluation
from app.research.ai_led_offline import _digest
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH
from strategy_backtest import STOCKS


@pytest.fixture
def sample():
    ready = pd.Timestamp("2026-10-28T12:10:01+05:30")
    start = ready.floor("5min") + pd.Timedelta(minutes=5)
    end = start + pd.Timedelta(minutes=30)
    decision = {"decision_id": "synthetic-only-1", "mode": "AI_SIGNAL_ONLY", "status": "VALID_PREDICTION",
                "evaluation_policy": evaluation.evaluation_policy(), "decision_ready_at": ready.isoformat(),
                "instrument_priority": ["RELIANCE"], "reference_prices": {"RELIANCE": 600},
                "baseline": {"RELIANCE": True}, "prediction": {"action": "BUY", "proposals": [
                    {"symbol": "RELIANCE", "score": .8, "stop_loss": 594, "target_price": 612}]}}
    bodies = {"RELIANCE": {"symbol": "RELIANCE", "instrument_key": STOCKS["RELIANCE"],
        "source": "SYNTHETIC_UNIT_TEST", "observed_at": end.isoformat(), "candles": [
            {"timestamp": stamp.isoformat(), "open": 600, "high": 602, "low": 599, "close": 601, "volume": 1000}
            for stamp in pd.date_range(start, periods=6, freq="5min")]}}
    return decision, bodies, end, OfflineSessionCalendar(CALENDAR_PATH)


def evaluate(sample, *, decision=None, bodies=None, as_of=None):
    d, b, end, calendar = sample
    return evaluation.evaluate_signal(decision or d, b if bodies is None else bodies,
                                      as_of=end if as_of is None else as_of, calendar=calendar)


def test_two_predictions_missing_negative_outcome_keeps_coverage_visible(sample):
    d, bodies, end, _ = sample
    second = copy.deepcopy(d)
    second["decision_id"] = "synthetic-only-2"
    negative = copy.deepcopy(bodies)
    for bar in negative["RELIANCE"]["candles"]:
        bar.update(low=598, close=599)
    win, loss = evaluate(sample), evaluate(sample, decision=second, bodies=negative)
    complete = evaluation.summarize_predictions([d, second], [win, loss])
    incomplete = evaluation.summarize_predictions([d, second], [win])
    assert complete["ai_up_frequency"] == .5 and complete["evaluation_coverage_pct"] == 100
    assert complete["rate_denominators"]["ai_up_frequency"] == 2
    assert incomplete["ai_up_frequency"] == 1 and incomplete["rate_denominators"]["ai_up_frequency"] == 1
    assert incomplete["total_recorded_decisions"] == incomplete["total_valid_buy_predictions"] == 2
    assert incomplete["total_expected_prediction_evaluations"] == 2
    assert incomplete["total_completed_evaluations"] == incomplete["total_missing_insufficient"] == 1
    assert incomplete["unevaluated_count"] == 1 and incomplete["evaluation_coverage_pct"] == 50
    assert incomplete["rate_denominators"] == {"no_trade_frequency": 2, "candidate_coverage": 2,
        "ai_up_frequency": 1, "baseline_up_frequency": 1, "evaluation_coverage_pct": 2}
    assert incomplete == evaluation.summarize_predictions([d, second], [win])


@pytest.mark.parametrize("supplied", [False, True])
def test_pending_horizon_is_explicit_even_without_an_evaluation(sample, supplied):
    d, _, end, _ = sample
    cutoff = end - pd.Timedelta(seconds=1)
    outcomes = [evaluate(sample, as_of=cutoff)] if supplied else []
    summary = evaluation.summarize_predictions([d], outcomes, as_of=cutoff)
    assert summary["total_pending"] == summary["unevaluated_count"] == 1
    assert summary["total_missing_insufficient"] == summary["total_completed_evaluations"] == 0
    assert summary["ai_up_frequency"] is None and summary["rate_denominators"]["ai_up_frequency"] == 0


@pytest.mark.parametrize("kind", ["missing", "empty", "short", "identity", "corrupt"])
def test_missing_and_invalid_observations_are_not_outcomes(sample, kind):
    d, bodies, _, _ = sample
    if kind == "missing": bodies.clear()
    elif kind == "empty": bodies["RELIANCE"]["candles"] = []
    elif kind == "short": bodies["RELIANCE"]["candles"].pop()
    elif kind == "identity": bodies["RELIANCE"]["instrument_key"] = STOCKS["HDFCBANK"]
    else: bodies["RELIANCE"]["candles"][0]["high"] = -1
    result = evaluate(sample)
    summary = evaluation.summarize_predictions([d], [result])
    field = "total_missing_insufficient" if kind in {"missing", "empty", "short"} else "total_invalid_conflicting"
    assert summary[field] == 1 and summary["total_completed_evaluations"] == 0
    assert summary["ai_up_frequency"] is None and summary["evaluation_coverage_pct"] == 0


def test_ambiguity_is_completed_movement_not_an_execution_win(sample):
    d, bodies, _, _ = sample
    bodies["RELIANCE"]["candles"][0].update(high=613, low=593)
    result = evaluate(sample)
    summary = evaluation.summarize_predictions([d], [result])
    assert summary["total_ambiguous"] == summary["total_completed_evaluations"] == 1
    assert summary["evaluation_accounting"][0]["target_stop_order"] == "AMBIGUOUS"
    assert summary["execution_pnl"] is None


@pytest.mark.parametrize("kind", ["duplicate", "unknown", "hash", "duplicate_row", "unknown_row", "direction", "selection", "status", "horizon", "observation_hash", "observation_time"])
def test_conflicting_evaluation_evidence_fails_closed(sample, kind):
    d, _, _, _ = sample
    result = evaluate(sample)
    outcomes = [result]
    if kind == "duplicate": outcomes.append(copy.deepcopy(result))
    elif kind == "unknown": result["decision_id"] = "unknown"
    elif kind == "hash": result["decision_sha256"] = "0" * 64
    elif kind == "duplicate_row": result["rows"].append(copy.deepcopy(result["rows"][0]))
    elif kind == "unknown_row": result["rows"][0]["symbol"] = "HDFCBANK"
    elif kind == "direction": result["rows"][0]["direction"] = "DOWN"
    elif kind == "selection": result["rows"][0]["ai_selected"] = False
    elif kind == "status": result["status"] = "INCOMPLETE_FUTURE_DATA"
    elif kind == "observation_hash": result["rows"][0]["observation_evidence"]["input_sha256"] = "0" * 64
    elif kind == "observation_time": result["rows"][0]["observation_evidence"]["observed_at"] = d["decision_ready_at"]
    else: result["rows"][0].update(status="PENDING_FUTURE_DATA", return_pct=None, direction=None, target_stop_order=None)
    with pytest.raises(ValueError): evaluation.summarize_predictions([d], outcomes)


def test_genuine_no_trade_still_has_expected_universe_evaluations(sample):
    d, _, _, _ = sample
    d["prediction"] = {"action": "NO_TRADE", "proposals": []}
    summary = evaluation.summarize_predictions([d], [evaluate(sample)])
    assert summary["total_no_trade_decisions"] == summary["total_expected_prediction_evaluations"] == 1
    assert summary["total_valid_buy_predictions"] == summary["total_expected_buy_evaluations"] == 0
    assert summary["no_trade_frequency"] == 1 and summary["rate_denominators"]["no_trade_frequency"] == 1
    assert summary["ai_up_frequency"] is None


def test_omitted_instrument_row_is_reconciled_from_prediction(sample):
    d, _, _, _ = sample
    result = evaluate(sample)
    result["rows"] = []
    summary = evaluation.summarize_predictions([d], [result])
    assert summary["total_missing_insufficient"] == 1 and summary["evaluation_coverage_pct"] == 0


def test_observed_cutoff_and_unknown_identity_cannot_be_silently_changed(sample):
    d, _, end, _ = sample
    result = evaluate(sample)
    with pytest.raises(ValueError):
        evaluation.summarize_predictions([d], [result], as_of=end + pd.Timedelta(minutes=5))
    result["decision_id"] = "unknown"
    result.pop("decision_sha256")
    with pytest.raises(ValueError, match="identity_mismatch"):
        evaluation.summarize_predictions([d], [result])


def test_future_evidence_identity_is_retained_without_claiming_authenticity(sample):
    d, bodies, _, _ = sample
    before = copy.deepcopy(sample[:2])
    result = evaluate(sample)
    proof = result["rows"][0]["observation_evidence"]
    assert proof["input_sha256"] == _digest(bodies["RELIANCE"])
    assert proof["source"] == "SYNTHETIC_UNIT_TEST" and proof["observed_at"] == bodies["RELIANCE"]["observed_at"]
    assert proof["provenance"] == "UNVERIFIED_CALLER_SUPPLIED"
    assert result == evaluate(sample) and (d, bodies) == before


def test_failures_empty_summary_and_predecision_cutoff(sample):
    d, _, end, _ = sample
    with pytest.raises(ValueError): evaluate(sample, as_of=pd.Timestamp(d["decision_ready_at"]) - pd.Timedelta(seconds=1))
    d["status"] = "PROVIDER_FAILURE"
    summary = evaluation.summarize_predictions([d], [evaluate(sample)])
    assert summary["failures"] == 1 and summary["total_no_trade_decisions"] == 0
    assert summary["total_expected_prediction_evaluations"] == 0
    assert evaluation.summarize_predictions([], [])["evaluation_coverage_pct"] is None
