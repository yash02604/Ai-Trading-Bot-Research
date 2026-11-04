"""Summarize paired, completed DeepSeek prospective shadow outcomes."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from datetime import datetime, timezone


SCENARIOS = (
    "gst_ex_sebi_slippage_0bps", "gst_ex_sebi_slippage_5bps",
    "gst_ex_sebi_slippage_10bps", "gst_incl_sebi_slippage_0bps",
    "gst_incl_sebi_slippage_5bps", "gst_incl_sebi_slippage_10bps",
)
MIN_DESCRIPTIVE_TRADES = 30


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _by_decision_id(rows: list[dict], label: str) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("decision_id"), str):
            raise ValueError(f"{label} contains a malformed decision ID")
        decision_id = row["decision_id"]
        if not decision_id or decision_id in indexed:
            raise ValueError(f"{label} contains a duplicate or empty decision ID")
        indexed[decision_id] = row
    return indexed


def _performance(outcomes: list[dict], strategy: str, scenario: str,
                 initial_capital: float) -> dict:
    trades = []
    for outcome in outcomes:
        trade = outcome[strategy]
        if trade.get("taken"):
            priced = trade["cost_scenarios"][scenario]
            trades.append({"timestamp": outcome["entry_timestamp"],
                           "gross": float(trade["gross_pnl"]),
                           "costs": float(priced["estimated_costs"]),
                           "net": float(priced["net_pnl"])})
    trades.sort(key=lambda item: item["timestamp"])
    positives = sum(row["net"] for row in trades if row["net"] > 0)
    negatives = -sum(row["net"] for row in trades if row["net"] < 0)
    equity = peak = float(initial_capital)
    drawdown = 0.0
    for row in trades:
        equity += row["net"]
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak * 100)
    count = len(trades)
    return {
        "trade_count": count,
        "gross_pnl": round(sum(row["gross"] for row in trades), 2),
        "estimated_costs": round(sum(row["costs"] for row in trades), 2),
        "net_pnl": round(sum(row["net"] for row in trades), 2),
        "max_closed_trade_drawdown_pct": round(drawdown, 4),
        "win_rate_pct": round(sum(row["net"] > 0 for row in trades) / count * 100, 2) if count else None,
        "profit_factor": round(positives / negatives, 6) if negatives else (None if not positives else "infinite"),
        "sample_assessment": "DESCRIPTIVE_ONLY" if count < MIN_DESCRIPTIVE_TRADES else "REVIEW_REQUIRED",
    }


def summarize(output_dir: Path) -> dict:
    observations = _read_jsonl(output_dir / "observations.jsonl")
    outcomes = _read_jsonl(output_dir / "outcomes.jsonl")
    entries = _read_jsonl(output_dir / "entry_events.jsonl")
    decision_by_id = _by_decision_id(observations, "observations")
    outcome_by_id = _by_decision_id(outcomes, "outcomes")
    entry_by_id = _by_decision_id(entries, "entry events")
    if any(row.get("outcome_id", decision_id) != decision_id
           for decision_id, row in outcome_by_id.items()):
        raise ValueError("outcome ID does not match its decision ID")
    if any(row.get("entry_event_id", decision_id) != decision_id
           for decision_id, row in entry_by_id.items()):
        raise ValueError("entry event ID does not match its decision ID")
    if set(outcome_by_id) - set(decision_by_id) or set(entry_by_id) - set(decision_by_id):
        raise ValueError("entry or outcome record has no matching observation")
    prospective_decisions = [row for row in observations
                             if row.get("classification") == "PROSPECTIVE_OBSERVATION"]
    if not prospective_decisions:
        raise ValueError("prospective observations do not establish experiment capital")
    capitals = []
    for row in prospective_decisions:
        try:
            capital = float(row["capital"])
            nested = row.get("configuration", {}).get("paper_capital")
            if nested is not None and float(nested) != capital:
                raise ValueError("observation capital fields disagree")
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("prospective observation has invalid capital") from exc
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("prospective observation has invalid capital")
        capitals.append(capital)
    if any(value != capitals[0] for value in capitals[1:]):
        raise ValueError("prospective observations disagree on initial capital")
    capital = capitals[0]
    eligible = [row for row in prospective_decisions
                if row.get("quantitative_baseline", {}).get("eligible")]
    matched_outcomes = [row for row in outcomes
                        if row.get("classification") == "PROSPECTIVE_PAPER_OBSERVATION"
                        and row.get("decision_id") in decision_by_id
                        and row.get("quantitative_baseline_trade", {}).get("taken") is True
                        and decision_by_id[row["decision_id"]].get("quantitative_baseline", {}).get("eligible")]
    completed_ids = {row["decision_id"] for row in matched_outcomes}
    rejected_ids = {row["decision_id"] for row in outcomes
                    if row.get("classification") == "PROSPECTIVE_ENTRY_REJECTED" or
                    (row.get("classification") == "PROSPECTIVE_PAPER_OBSERVATION" and
                     row.get("quantitative_baseline_trade", {}).get("taken") is False)}
    pending_ids = {row["decision_id"] for row in eligible} - completed_ids - rejected_ids
    pending = len(pending_ids)

    baseline_correct = sum(row["baseline_prediction"] == row["future_direction_label"]
                           for row in matched_outcomes)
    directional_ai = [row for row in matched_outcomes
                      if row.get("deepseek_prediction") in {"BUY", "SELL"}]
    ai_correct = sum(row["deepseek_prediction"] == row["future_direction_label"]
                     for row in directional_ai)
    directions = len(matched_outcomes)
    directional = {
        "paired_completed_decisions": directions,
        "future_direction_labels": {label: sum(row["future_direction_label"] == label
                                                 for row in matched_outcomes)
                                     for label in ("BUY", "SELL", "HOLD")},
        "quantitative_baseline": {
            "directional_predictions": directions,
            "correct": baseline_correct,
            "accuracy_pct": round(baseline_correct / directions * 100, 2) if directions else None,
        },
        "deepseek": {
            "directional_predictions": len(directional_ai),
            "abstentions_or_non_directional": directions - len(directional_ai),
            "directional_coverage_pct": round(len(directional_ai) / directions * 100, 2) if directions else None,
            "correct": ai_correct,
            "accuracy_pct_among_directional_predictions": round(ai_correct / len(directional_ai) * 100, 2) if directional_ai else None,
        },
    }
    performance = {
        scenario: {
            "quantitative_baseline": _performance(matched_outcomes, "quantitative_baseline_trade", scenario, capital),
            "deepseek_assisted": _performance(matched_outcomes, "deepseek_assisted_trade", scenario, capital),
        }
        for scenario in SCENARIOS
    }
    return {
        "schema_version": "deepseek-prospective-summary-v1",
        "classification": "PROSPECTIVE_OBSERVATION",
        "evidence_note": "Prospective paired shadow observations only; not synthetic performance, historical backtest results, observed fills, or a profitability claim.",
        "historical_backtest": False,
        "broker_orders_enabled": False,
        "decision_count": len(prospective_decisions),
        "quantitative_eligible_decision_count": len(eligible),
        "paired_completed_outcome_count": directions,
        "awaiting_outcome_count": pending,
        "rejected_entry_count": len(rejected_ids & {row["decision_id"] for row in eligible}),
        "awaiting_entry_candle_count": len(pending_ids - entry_by_id.keys()),
        "baseline_accepted_awaiting_settlement_count": sum(
            entry_by_id[decision_id].get("quantitative_baseline_trade", {}).get("accepted") is True
            for decision_id in pending_ids & entry_by_id.keys()),
        "ai_accepted_awaiting_settlement_count": sum(
            entry_by_id[decision_id].get("deepseek_assisted_trade", {}).get("accepted") is True
            for decision_id in pending_ids & entry_by_id.keys()),
        "ai_actual_entry_rejected_count": sum(
            row.get("deepseek_assisted_trade", {}).get("accepted") is False
            and decision_by_id.get(decision_id, {}).get("ai_assisted_eligible") is True
            for decision_id, row in entry_by_id.items()),
        "directional_accuracy": directional,
        "trading_performance_by_cost_scenario": performance,
        "cost_scenarios_are_assumptions": True,
        "small_sample_warning": "Metrics are descriptive. Fewer than 30 completed trades is labeled DESCRIPTIVE_ONLY; even larger samples require uncertainty and execution review.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Prospective research output directory containing observations.jsonl/outcomes.jsonl")
    args = parser.parse_args()
    summary = summarize(args.output)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = args.output / f"prospective_summary_{stamp}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "COMPLETED", "classification": summary["classification"],
                      "summary": str(path), "paired_completed_outcomes": summary["paired_completed_outcome_count"],
                      "broker_orders_enabled": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
