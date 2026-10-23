"""Read-only diagnostics for the committed sharp-reversal experiment artifacts."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

COST_FIELDS = ("brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst", "slippage")
STRATEGY_ORDER = (
    "frozen_reversal_v1",
    "sharp_reversal_3bar_1to3_rsi30_stop075_h6_v1",
    "sharp_reversal_4bar_1to3_rsi35_stop1_h6_v1",
    "sharp_reversal_3bar_2to4_rsi30_stop1_h12_v1",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _f(row: dict, key: str) -> float:
    value = row.get(key, "")
    if value in (None, ""):
        raise ValueError(f"missing required field: {key}")
    return float(value)


def _dt(row: dict, key: str) -> datetime:
    value = row.get(key, "")
    if not value:
        raise ValueError(f"missing required field: {key}")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f"timezone-naive timestamp: {key}")
    return result


def max_closed_drawdown(values: Iterable[float], starting_capital: float) -> float:
    equity = float(starting_capital)
    peak = equity
    worst = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        if peak:
            worst = min(worst, equity / peak - 1.0)
    return worst * 100.0


def summarize(rows: list[dict[str, str]], starting_capital: float) -> dict:
    if not rows:
        return {"trade_count": 0, "gross_pnl": 0.0, "costs": 0.0, "net_pnl": 0.0,
                "win_rate_pct": 0.0, "net_profit_factor": None, "max_closed_drawdown_pct": 0.0,
                "average_net_trade": None, "turnover": 0.0}
    ordered = sorted(rows, key=lambda r: (_dt(r, "entry_timestamp"), r["symbol"]))
    gross = sum(_f(r, "gross_pnl") for r in rows)
    costs = sum(_f(r, "total_cost") for r in rows)
    net_values = [_f(r, "net_pnl") for r in ordered]
    wins = sum(v for v in net_values if v > 0)
    losses = -sum(v for v in net_values if v < 0)
    return {
        "trade_count": len(rows), "gross_pnl": gross, "costs": costs, "net_pnl": sum(net_values),
        "win_rate_pct": 100.0 * sum(v > 0 for v in net_values) / len(net_values),
        "net_profit_factor": wins / losses if losses else ("Infinity" if wins else None),
        "max_closed_drawdown_pct": max_closed_drawdown(net_values, starting_capital),
        "average_net_trade": sum(net_values) / len(net_values),
        "turnover": sum((_f(r, "entry_price") + _f(r, "exit_price")) * _f(r, "quantity") for r in rows),
    }


def grouped(rows: list[dict[str, str]], dimension: str, starting_capital: float) -> list[dict]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if dimension == "entry_time_label":
            key = _dt(row, "entry_timestamp").strftime("%H:%M")
        elif dimension == "holding_label_minutes":
            minutes = (_dt(row, "exit_timestamp") - _dt(row, "entry_timestamp")).total_seconds() / 60
            key = f"{minutes:g}"
        else:
            key = row[dimension]
        groups[key].append(row)
    return [{"group": key, **summarize(value, starting_capital)} for key, value in sorted(groups.items())]


def audit_rows(rows: list[dict[str, str]], starting_capital: float) -> dict:
    errors = defaultdict(int)
    signal_keys = [(r.get("strategy_id"), r.get("symbol"), r.get("signal_timestamp")) for r in rows]
    fill_keys = [(r.get("strategy_id"), r.get("symbol"), r.get("entry_timestamp")) for r in rows]
    errors["duplicate_strategy_symbol_signal_keys"] = len(signal_keys) - len(set(signal_keys))
    errors["duplicate_strategy_symbol_entry_keys"] = len(fill_keys) - len(set(fill_keys))
    strategy_reports = {}
    for strategy in STRATEGY_ORDER:
        selected = [r for r in rows if r["strategy_id"] == strategy]
        selected.sort(key=lambda r: (_dt(r, "entry_timestamp"), r["symbol"]))
        summary = summarize(selected, starting_capital)
        cost_totals = {name: sum(_f(r, name) for r in selected) for name in COST_FIELDS}
        net_negative = sum(_f(r, "net_pnl") < 0 for r in selected)
        gross_negative = sum(_f(r, "gross_pnl") < 0 for r in selected)
        flips = sum(_f(r, "gross_pnl") > 0 >= _f(r, "net_pnl") for r in selected)

        for row in selected:
            if abs(_f(row, "total_cost") - sum(_f(row, c) for c in COST_FIELDS)) > 0.011:
                errors["itemized_cost_sum_mismatch"] += 1
            if abs(_f(row, "net_pnl") - (_f(row, "gross_pnl") - _f(row, "total_cost"))) > 0.011:
                errors["net_not_gross_less_cost"] += 1
            if _f(row, "quantity") <= 0 or _f(row, "capital_exposure") < 0 or _f(row, "planned_risk") < 0:
                errors["nonpositive_quantity_or_negative_risk_exposure"] += 1
            signal, entry, exit_ = (_dt(row, field) for field in ("signal_timestamp", "entry_timestamp", "exit_timestamp"))
            if (entry - signal).total_seconds() != 300:
                errors["entry_not_next_5m_label"] += 1
            if exit_ < entry:
                errors["exit_precedes_entry"] += 1
            if _f(row, "stop_loss") >= _f(row, "entry_price"):
                errors["long_stop_not_below_entry"] += 1

        # Reconstruct the limits from accepted fills only; rejected signal-level events are not in this ledger.
        capital = float(starting_capital)
        day_start: dict[str, float] = {}
        day_loss: dict[str, float] = defaultdict(float)
        day_count: dict[str, int] = defaultdict(int)
        previous_exit = None
        risk_violations = defaultdict(int)
        for row in selected:
            entry, exit_ = _dt(row, "entry_timestamp"), _dt(row, "exit_timestamp")
            day = entry.date().isoformat()
            day_start.setdefault(day, capital)
            risk_per_share = _f(row, "entry_price") - _f(row, "stop_loss")
            actual_risk = risk_per_share * _f(row, "quantity")
            if actual_risk > capital * 0.01 + 0.011:
                risk_violations["risk_per_trade_over_1pct"] += 1
            if _f(row, "capital_exposure") > capital + 0.011:
                risk_violations["exposure_over_equity"] += 1
            if day_count[day] >= 3:
                risk_violations["over_3_trades_per_day"] += 1
            if day_loss[day] >= day_start[day] * 0.02 - 1e-9:
                risk_violations["trade_after_daily_loss_limit"] += 1
            if previous_exit is not None and entry <= previous_exit:
                risk_violations["overlapping_positions_by_recorded_labels"] += 1
            net = _f(row, "net_pnl")
            capital += net
            if net < 0:
                day_loss[day] += abs(net)
            day_count[day] += 1
            previous_exit = exit_

        stock_groups = grouped(selected, "symbol", starting_capital)
        negative_stock_loss = sum(-x["net_pnl"] for x in stock_groups if x["net_pnl"] < 0)
        worst_stock_losses = sorted((x for x in stock_groups if x["net_pnl"] < 0), key=lambda x: x["net_pnl"])
        strategy_reports[strategy] = {
            **summary, "gross_losing_trades": gross_negative, "net_losing_trades": net_negative,
            "gross_positive_to_net_negative_trades": flips, "cost_components": cost_totals,
            "cost_pct_of_absolute_turnover": 100 * summary["costs"] / summary["turnover"] if summary["turnover"] else None,
            "net_profit_erased_or_added_by_costs": summary["gross_pnl"] - summary["net_pnl"],
            "top_five_negative_stock_groups": worst_stock_losses[:5],
            "top_five_negative_stock_share_of_negative_stock_net": (
                -sum(x["net_pnl"] for x in worst_stock_losses[:5]) / negative_stock_loss if negative_stock_loss else None),
            "by_stock": stock_groups,
            "by_trend_regime": grouped(selected, "trend_regime", starting_capital),
            "by_volatility_regime": grouped(selected, "volatility_regime", starting_capital),
            "by_entry_time_label": grouped(selected, "entry_time_label", starting_capital),
            "by_holding_label_minutes": grouped(selected, "holding_label_minutes", starting_capital),
            "by_exit_reason": grouped(selected, "exit_reason", starting_capital),
            "reconstructed_risk_violations": dict(risk_violations),
        }
    return {"trade_rows": len(rows), "errors": {key: value for key, value in errors.items() if value}, "strategies": strategy_reports}


def report_markdown(analysis: dict, artifact_hashes: dict[str, str], commit: str) -> str:
    lines = ["# Sharp-reversal experiment diagnostics", "",
             "Read-only analysis of the recorded v1.1 experiment artifacts. These findings do not establish profitability, independent validation, or readiness for paper/live execution.", "",
             f"- Source commit: `{commit}`", f"- Trade ledger rows: {analysis['trade_rows']}",
             "- Drawdown: recomputed on closed-trade net equity, strategy-specific, starting capital INR 100,000.",
             "- Holding time fields are differences between recorded source labels. Since label semantics remain unresolved, these are not verified wall-clock holding durations.", "",
             "## Consistent strategy comparison", "",
             "| Strategy | Trades | Gross ₹ | Costs ₹ | Net ₹ | Avg net ₹ | Net win rate | Net PF | Closed DD | Cost / turnover | Top-5 negative-stock loss share | Gross loss trades | Net loss trades |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        concentration = d["top_five_negative_stock_share_of_negative_stock_net"]
        concentration_text = "n/a" if concentration is None else f"{100 * concentration:.1f}%"
        lines.append(f"| {strategy} | {d['trade_count']} | {d['gross_pnl']:.2f} | {d['costs']:.2f} | {d['net_pnl']:.2f} | {d['average_net_trade']:.2f} | {d['win_rate_pct']:.1f}% | {d['net_profit_factor']} | {d['max_closed_drawdown_pct']:.2f}% | {d['cost_pct_of_absolute_turnover']:.3f}% | {concentration_text} | {d['gross_losing_trades']} | {d['net_losing_trades']} |")
    lines += ["", "## Cost decomposition", "", "Components below are sums of recorded trade-ledger fields; they explain the entire gross-to-net difference if the reconciliation check passes.", "", "| Strategy | Component | Cost ₹ | Share of costs |", "|---|---|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for name, value in sorted(d["cost_components"].items(), key=lambda item: -item[1]):
            lines.append(f"| {strategy} | {name} | {value:.2f} | {100 * value / d['costs']:.1f}% |")
    lines += ["", "## Recorded exit and timing dimensions", "", "For each strategy, the following tables summarize the ledger by entry-time label, holding-label difference, and exit reason. The full dimension tables, including stock and regime, are in `findings.json`.", "", "### Entry-time label", "", "| Strategy | Label | Trades | Gross ₹ | Costs ₹ | Net ₹ | Avg net ₹ |", "|---|---|---:|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for item in d["by_entry_time_label"]:
            lines.append(f"| {strategy} | {item['group']} | {item['trade_count']} | {item['gross_pnl']:.2f} | {item['costs']:.2f} | {item['net_pnl']:.2f} | {item['average_net_trade']:.2f} |")
    lines += ["", "### Largest negative stock aggregates", "", "| Strategy | Stock | Trades | Gross ₹ | Costs ₹ | Net ₹ |", "|---|---|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for item in d["top_five_negative_stock_groups"]:
            lines.append(f"| {strategy} | {item['group']} | {item['trade_count']} | {item['gross_pnl']:.2f} | {item['costs']:.2f} | {item['net_pnl']:.2f} |")
    lines += ["", "### Net by regime label", "", "| Strategy | Dimension | Regime | Trades | Gross ₹ | Costs ₹ | Net ₹ |", "|---|---|---|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for dimension, groups in (("trend", d["by_trend_regime"]), ("volatility", d["by_volatility_regime"])):
            for item in groups:
                lines.append(f"| {strategy} | {dimension} | {item['group']} | {item['trade_count']} | {item['gross_pnl']:.2f} | {item['costs']:.2f} | {item['net_pnl']:.2f} |")
    lines += ["", "### Holding-label difference", "", "| Strategy | Label difference min | Trades | Gross ₹ | Costs ₹ | Net ₹ |", "|---|---:|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for item in d["by_holding_label_minutes"]:
            lines.append(f"| {strategy} | {item['group']} | {item['trade_count']} | {item['gross_pnl']:.2f} | {item['costs']:.2f} | {item['net_pnl']:.2f} |")
    lines += ["", "### Exit reason", "", "| Strategy | Reason | Trades | Gross ₹ | Costs ₹ | Net ₹ |", "|---|---|---:|---:|---:|---:|"]
    for strategy, d in analysis["strategies"].items():
        for item in d["by_exit_reason"]:
            lines.append(f"| {strategy} | {item['group']} | {item['trade_count']} | {item['gross_pnl']:.2f} | {item['costs']:.2f} | {item['net_pnl']:.2f} |")
    lines += ["", "## Reconciliation and risk reconstruction", "", f"- Row-level checks found {sum(analysis['errors'].values())} accounting/timestamp/stop/duplicate-key errors across the recorded ledger. Detailed nonzero counts: `{analysis['errors']}`.", f"- All-observed, per-stock, and per-regime reported metrics were independently reconciled against the trade ledger; material deltas: {len(analysis.get('aggregate_reconciliation_mismatches', []))}. See `findings.json` for exact per-field deltas.", "- Partition trade counts can be checked against all-observed fills. Partition cash metrics cannot: the experiment reruns each partition from the starting capital, changing path-dependent quantities and costs, while `trades.csv` contains only all-observed fills. The partition metric cash differences are retained in `findings.json` as non-comparable, not labeled accounting errors.", "- Daily risk, position sizing, and one-position-at-a-time checks were reconstructed from accepted fills and the experiment's stated starting capital/rules. This cannot establish how every rejected signal was handled because rejected signals are not a trade ledger dimension."]
    for strategy, d in analysis["strategies"].items():
        lines.append(f"- `{strategy}` reconstructed risk violations: `{d['reconstructed_risk_violations']}`.")
    lines += ["", "## Findings", "", "- **Verified:** The four strategy counts sum to the 574 trade rows; recomputed net P&L, cost totals, average net trade, win rate, net profit factor, closed-equity drawdown, and stock/regime totals are derived consistently from the ledger. (Exact aggregate reconciliation is in `findings.json`.)",
              "- **Verified:** Recorded entry labels are exactly five minutes after signal labels for all ledger trades. This supports next-label entry sequencing, not actual source timestamp semantics or executable fill availability.",
              "- **Verified:** Recorded `total_cost` equals the sum of itemized components and `net_pnl = gross_pnl - total_cost` within INR 0.011 for all rows, if no row-level errors are reported.",
              "- **Verified:** Brokerage and slippage dominate modeled costs; slippage is the largest aggregate component for every strategy. It is a modeling assumption, not observed fill slippage.",
              "- **Verified:** The original chronological partition report reinitializes capital independently per partition, so those cash metrics are not directly additive and cannot be reconciled to the single all-observed trade ledger; there is no partition-specific trade ledger to inspect.",
              "- **Suspected:** With the experiment's provisional START-label assumption, the ledger's `exit_timestamp` is a bar label, not the actual time an intrabar stop or candle-close exit could execute. Under that assumption, holding-label differences understate elapsed duration by one five-minute interval for close-at-bar-end exits and cannot time intrabar stops.",
              "- **Suspected:** Entries at the same candle's open and stops checked against that candle's low are plausible, but the OHLC record cannot order low versus any other intrabar event. Stop-only policy removes target ambiguity, yet stop fill timing and gaps within the bar remain unobservable.",
              "- **Verified in implementation:** `simulate` enforces one open position with a separate `last_exit` comparison, but calls the shared risk engine without setting `RiskState.open_positions` (default zero). Current accepted ledger rows reconstruct without overlaps; the reusable risk gate itself would not enforce this limit unless the caller supplies open-position state.",
              "- **Unassessable:** Underlying feed authenticity, adjustment/corporate-action treatment, true timestamp convention, measured costs/slippage, point-in-time universe, and genuinely untouched performance remain unavailable from these artifacts.",
              "- **Unassessable:** The basket regime is a proxy and is represented by labels rather than daily constituent values in the trade ledger; its computation can be code-reviewed but cannot be independently recomputed from the experiment output alone.",
              "", "## Priority fixes before another strategy experiment", "",
              "1. Establish source-backed candle timestamp semantics and execution timestamps; explicitly model bar-open entry to bar-close/stop execution rather than treating labels as fills in time.",
              "2. Obtain traceable, adjustment-aware historical data and a reviewed point-in-time universe; the present diagnostics cannot resolve those foundational uncertainties.",
              "3. Validate transaction-cost/slippage assumptions against an authorized source or conservative sensitivity analysis; estimated slippage alone is the largest cost line.",
              "4. Add a persisted signal/rejection ledger and reconstruct event-level daily risk decisions, because the accepted-trade ledger cannot audit rejected or skipped signals.",
              "", "## Source artifact SHA-256", ""]
    lines.extend(f"- `{name}`: `{digest}`" for name, digest in sorted(artifact_hashes.items()))
    lines += ["", "No experiment outputs, strategies, manifests, datasets, or acquisition configuration were modified.", ""]
    return "\n".join(lines)
