import json
import pytest

from scripts.summarize_deepseek_prospective import SCENARIOS, summarize


def _line(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_summary_keeps_directional_accuracy_separate_from_paired_trade_metrics(tmp_path):
    decision = {
        "decision_id": "decision-1", "classification": "PROSPECTIVE_OBSERVATION",
        "capital": 100_000, "quantitative_baseline": {"eligible": True},
        "decision": {"decision": "HOLD"},
    }
    outcomes = {}
    for scenario in SCENARIOS:
        outcomes[scenario] = {
            "cost_breakdown": {"total_cost": 4.0},
            "estimated_costs": 4.0, "net_pnl": -14.0,
            "assumptions": {"slippage_bps_per_side": 5.0,
                            "gst_on_sebi": scenario.startswith("gst_incl")},
        }
    outcome = {
        "decision_id": "decision-1", "classification": "PROSPECTIVE_PAPER_OBSERVATION",
        "entry_timestamp": "2026-10-28T10:00:00+05:30",
        "future_direction_label": "SELL", "baseline_prediction": "BUY",
        "deepseek_prediction": "HOLD",
        "quantitative_baseline_trade": {
            "taken": True, "gross_pnl": -10.0, "cost_scenarios": outcomes,
        },
        "deepseek_assisted_trade": {
            "taken": False, "gross_pnl": 0.0,
            "cost_scenarios": {key: {**value, "estimated_costs": 0.0, "net_pnl": 0.0}
                                for key, value in outcomes.items()},
        },
    }
    _line(tmp_path / "observations.jsonl", decision)
    _line(tmp_path / "outcomes.jsonl", outcome)

    report = summarize(tmp_path)
    assert report["classification"] == "PROSPECTIVE_OBSERVATION"
    assert report["historical_backtest"] is False
    assert report["broker_orders_enabled"] is False
    assert report["directional_accuracy"]["quantitative_baseline"]["accuracy_pct"] == 0.0
    assert report["directional_accuracy"]["deepseek"]["directional_predictions"] == 0
    assert report["directional_accuracy"]["deepseek"]["accuracy_pct_among_directional_predictions"] is None
    baseline = report["trading_performance_by_cost_scenario"][SCENARIOS[0]]["quantitative_baseline"]
    assisted = report["trading_performance_by_cost_scenario"][SCENARIOS[0]]["deepseek_assisted"]
    assert baseline["trade_count"] == 1
    assert baseline["estimated_costs"] == 4.0
    assert baseline["net_pnl"] == -14.0
    assert baseline["max_closed_trade_drawdown_pct"] > 0
    assert baseline["sample_assessment"] == "DESCRIPTIVE_ONLY"
    assert assisted["trade_count"] == 0
    assert report["cost_scenarios_are_assumptions"] is True


def test_summary_handles_missing_or_immature_outcomes(tmp_path):
    _line(tmp_path / "observations.jsonl", {
        "decision_id": "awaiting-1", "classification": "PROSPECTIVE_OBSERVATION",
        "capital": 100_000, "quantitative_baseline": {"eligible": True},
    })

    report = summarize(tmp_path)
    assert report["quantitative_eligible_decision_count"] == 1
    assert report["paired_completed_outcome_count"] == 0
    assert report["awaiting_outcome_count"] == 1
    assert report["directional_accuracy"]["quantitative_baseline"]["accuracy_pct"] is None
    assert report["directional_accuracy"]["deepseek"]["accuracy_pct_among_directional_predictions"] is None
    for scenario in SCENARIOS:
        for strategy in ("quantitative_baseline", "deepseek_assisted"):
            metrics = report["trading_performance_by_cost_scenario"][scenario][strategy]
            assert metrics["trade_count"] == 0
            assert metrics["net_pnl"] == 0.0
            assert metrics["win_rate_pct"] is None
            assert metrics["sample_assessment"] == "DESCRIPTIVE_ONLY"


def test_summary_does_not_count_rejected_actual_entry_as_pending_or_trade(tmp_path):
    _line(tmp_path / "observations.jsonl", {
        "decision_id": "rejected-1", "classification": "PROSPECTIVE_OBSERVATION",
        "capital": 1000, "quantitative_baseline": {"eligible": True},
    })
    _line(tmp_path / "outcomes.jsonl", {
        "decision_id": "rejected-1", "classification": "PROSPECTIVE_ENTRY_REJECTED",
        "entry_timestamp": "2026-10-28T10:00:00+05:30", "entry_price": 1100.0,
        "rejection_reason": "entry cash requirement exceeds configured capital",
        "quantitative_baseline_trade": {"taken": False, "quantity": 0},
        "deepseek_assisted_trade": {"taken": False, "quantity": 0},
    })

    report = summarize(tmp_path)
    assert report["quantitative_eligible_decision_count"] == 1
    assert report["paired_completed_outcome_count"] == 0
    assert report["awaiting_outcome_count"] == 0
    assert report["rejected_entry_count"] == 1
    for scenario in SCENARIOS:
        for strategy in ("quantitative_baseline", "deepseek_assisted"):
            assert report["trading_performance_by_cost_scenario"][scenario][strategy]["trade_count"] == 0


def test_summary_exposes_each_arm_entry_state_without_inventing_an_outcome(tmp_path):
    for decision_id in ("baseline-open", "awaiting-entry"):
        with (tmp_path / "observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "decision_id": decision_id, "classification": "PROSPECTIVE_OBSERVATION",
                "capital": 1000, "quantitative_baseline": {"eligible": True},
                "ai_assisted_eligible": True,
            }) + "\n")
    _line(tmp_path / "entry_events.jsonl", {
        "entry_event_id": "baseline-open", "decision_id": "baseline-open",
        "quantitative_baseline_trade": {"accepted": True},
        "deepseek_assisted_trade": {"accepted": False},
    })
    first = summarize(tmp_path)
    second = summarize(tmp_path)
    assert first == second
    assert first["awaiting_outcome_count"] == 2
    assert first["awaiting_entry_candle_count"] == 1
    assert first["baseline_accepted_awaiting_settlement_count"] == 1
    assert first["ai_accepted_awaiting_settlement_count"] == 0
    assert first["ai_actual_entry_rejected_count"] == 1
    assert first["paired_completed_outcome_count"] == 0


def test_summary_rejects_duplicate_outcomes_and_unknown_capital(tmp_path):
    _line(tmp_path / "observations.jsonl", {
        "decision_id": "one", "classification": "PROSPECTIVE_OBSERVATION",
        "capital": 1000, "quantitative_baseline": {"eligible": True},
    })
    rejection = {"outcome_id": "one", "decision_id": "one",
                 "classification": "PROSPECTIVE_ENTRY_REJECTED"}
    with (tmp_path / "outcomes.jsonl").open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(rejection) + "\n" + json.dumps(rejection) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        summarize(tmp_path)
    (tmp_path / "outcomes.jsonl").unlink()
    with (tmp_path / "observations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "decision_id": "two", "classification": "PROSPECTIVE_OBSERVATION",
            "capital": 5000, "quantitative_baseline": {"eligible": False},
        }) + "\n")
    with pytest.raises(ValueError, match="disagree"):
        summarize(tmp_path)


def test_summary_treats_legacy_not_taken_outcome_as_rejected(tmp_path):
    _line(tmp_path / "observations.jsonl", {
        "decision_id": "legacy-reject", "classification": "PROSPECTIVE_OBSERVATION",
        "capital": 1000, "quantitative_baseline": {"eligible": True},
    })
    _line(tmp_path / "outcomes.jsonl", {
        "outcome_id": "legacy-reject", "decision_id": "legacy-reject",
        "classification": "PROSPECTIVE_PAPER_OBSERVATION",
        "quantitative_baseline_trade": {"taken": False},
        "deepseek_assisted_trade": {"taken": False},
    })
    report = summarize(tmp_path)
    assert report["rejected_entry_count"] == 1
    assert report["awaiting_outcome_count"] == 0
    assert report["paired_completed_outcome_count"] == 0
