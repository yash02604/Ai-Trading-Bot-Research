"""Generate read-only diagnostics for the committed sharp-reversal outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.sharp_reversal_diagnostics import audit_rows, read_csv, report_markdown, summarize

ARTIFACTS = (
    "REPORT.md", "experiment_spec.json", "run_manifest.json", "trades.csv",
    "metrics_by_symbol.csv", "metrics_by_regime.csv", "metrics_by_partition.csv",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(root: Path, output: Path) -> None:
    source = root / "backtests/sharp_reversal_exploratory_v1_1_final"
    missing = [name for name in ARTIFACTS if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"required experiment artifacts missing: {missing}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite diagnostic output: {output}")
    spec = json.loads((source / "experiment_spec.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "run_manifest.json").read_text(encoding="utf-8"))
    trades = read_csv(source / "trades.csv")
    analysis = audit_rows(trades, float(spec["starting_capital_inr"]))
    by_symbol = read_csv(source / "metrics_by_symbol.csv")
    by_regime = read_csv(source / "metrics_by_regime.csv")
    by_partition = read_csv(source / "metrics_by_partition.csv")
    # Reconcile recorded total rows and subgroup counts to the ledger, without replacing the originals.
    recorded_total = {r["strategy_id"]: r for r in by_partition if r["partition_id"] == "all_observed"}
    reconciliation = {}
    report_fields = {
        "trade_count": ("trade_count", "int"), "gross_pnl": ("gross_pnl_inr", "float"),
        "costs": ("estimated_costs_inr", "float"), "net_pnl": ("net_pnl_inr", "float"),
        "win_rate_pct": ("win_rate_pct", "float"), "net_profit_factor": ("net_profit_factor", "float"),
        "max_closed_drawdown_pct": ("max_closed_equity_drawdown_pct", "float"),
        "average_net_trade": ("average_net_trade_inr", "float"), "turnover": ("turnover_inr", "float"),
    }

    def compare_record(row: dict, computed: dict) -> dict:
        deltas = {}
        for calc_field, (source_field, kind) in report_fields.items():
            if row.get(source_field, "") in ("", "None") or computed.get(calc_field) is None:
                deltas[calc_field] = None
            else:
                source_value = int(row[source_field]) if kind == "int" else float(row[source_field])
                computed_value = int(computed[calc_field]) if kind == "int" else float(computed[calc_field])
                deltas[calc_field] = source_value - computed_value
        return deltas

    for strategy, calc in analysis["strategies"].items():
        row = recorded_total.get(strategy)
        reconciliation[strategy] = {
            "recorded_all_observed_row_present": row is not None,
            "all_observed_deltas_recorded_minus_ledger": None if row is None else compare_record(row, calc),
        }
        observed = [r for r in trades if r["strategy_id"] == strategy]
        group_checks = []
        for metric_row in by_symbol:
            if metric_row["strategy_id"] != strategy:
                continue
            selection = [r for r in observed if r["symbol"] == metric_row["symbol"]]
            group_checks.append({"group": metric_row["symbol"], "deltas_recorded_minus_ledger": compare_record(metric_row, summarize(selection, float(spec["starting_capital_inr"])))})
        reconciliation[strategy]["stock"] = group_checks
        regime_checks = []
        for metric_row in by_regime:
            if metric_row["strategy_id"] != strategy:
                continue
            dim_field = "trend_regime" if metric_row["regime_dimension"] == "trend_regime" else "volatility_regime"
            selection = [r for r in observed if r[dim_field] == metric_row["regime"]]
            regime_checks.append({"dimension": metric_row["regime_dimension"], "group": metric_row["regime"], "deltas_recorded_minus_ledger": compare_record(metric_row, summarize(selection, float(spec["starting_capital_inr"])))})
        reconciliation[strategy]["regime"] = regime_checks
        partition_checks = []
        partition_status = []
        for metric_row in by_partition:
            if metric_row["strategy_id"] != strategy:
                continue
            partition_id = metric_row["partition_id"]
            if partition_id == "all_observed":
                selection = observed
            else:
                part = next(p for p in spec["partitions"] if p["partition_id"] == partition_id)
                start, end = part["start"], part["end"]
                selection = [r for r in observed if start <= r["signal_timestamp"][:10] <= end]
            computed = summarize(selection, float(spec["starting_capital_inr"]))
            partition_checks.append({"group": partition_id, "trade_count_delta_recorded_minus_all_observed_ledger": int(metric_row["trade_count"]) - computed["trade_count"],
                                     "cash_metric_deltas_recorded_minus_all_observed_ledger": compare_record(metric_row, computed)})
        # The experiment reruns each time window from the original starting capital;
        # its fills are not represented by a separate partition trade ledger.
        reconciliation[strategy]["partition_metrics_comparability"] = {
            "status": "cash metrics are independent partition replays; do not reconcile to all_observed trade fills",
            "reason": "capital and position sizes are reinitialized for each window, while trades.csv stores only all_observed fills",
            "rows": partition_checks,
        }
    analysis["aggregate_reconciliation"] = reconciliation
    mismatches = []
    for strategy, sections in reconciliation.items():
        for scope, records in sections.items():
            if scope == "recorded_all_observed_row_present":
                continue
            if scope == "all_observed_deltas_recorded_minus_ledger":
                records = [{"group": "all_observed", "deltas_recorded_minus_ledger": records}]
            if scope == "partition_metrics_comparability":
                continue
            for record in records or []:
                for field, delta in (record.get("deltas_recorded_minus_ledger") or {}).items():
                    if delta is not None and abs(delta) > (0 if field == "trade_count" else 0.011):
                        mismatches.append({"strategy_id": strategy, "scope": scope, "group": record.get("group"), "field": field, "delta": delta})
    analysis["aggregate_reconciliation_mismatches"] = mismatches
    artifact_hashes = {name: sha256(source / name) for name in ARTIFACTS}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    output.mkdir(parents=True)
    (output / "findings.json").write_text(json.dumps({
        "diagnostic_version": 1, "source_commit": head,
        "experiment_source_commit": manifest["source_commit_before_experiment"],
        "source_artifact_hashes_sha256": artifact_hashes,
        "diagnostic_code_hashes_sha256": {
            "app/research/sharp_reversal_diagnostics.py": sha256(root / "app/research/sharp_reversal_diagnostics.py"),
            "scripts/run_sharp_reversal_diagnostics.py": sha256(root / "scripts/run_sharp_reversal_diagnostics.py"),
        },
        "analysis": analysis,
        "available_dimensions": ["symbol", "trend_regime", "volatility_regime", "entry_time_label", "holding_label_minutes", "exit_reason", *["itemized_cost:" + c for c in ("brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst", "slippage")]],
        "unavailable_dimensions": ["execution timestamps within bars", "rejected-signal event ledger", "independent session/calendar evidence", "source-verified candle timestamp convention", "measured market spread/fill slippage"],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(report_markdown(analysis, artifact_hashes, head), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("backtests/sharp_reversal_diagnostics_v1"))
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else args.root / args.output
    run(args.root.resolve(), output.resolve())


if __name__ == "__main__":
    main()
