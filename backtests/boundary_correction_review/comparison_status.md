# Frozen V1 Boundary Correction Review

Status: complete. The original decline interval was verified from the report and saved summary as 1.0% inclusive to 2.0% exclusive; with signed `decline_pct`, that contract is `-2.0 < decline_pct <= -1.0`.

## Isolated comparison

The validation runner writes `VALIDATION_REPORT.md` both to `--output` and beside the runner script. Baseline and corrected source copies were placed in separate workspaces under this review artifact, so both report writes stayed inside their respective workspaces. They used the same data, capital, stop, horizon, and slippage defaults. The baseline copy differed from the corrected copy only at the decline predicate in `app/reversal_detector.py`.

| Metric | Baseline | Corrected | Change |
|---|---:|---:|---:|
| Frozen signals | 58 | 58 | 0 |
| Risk accepted entries | 46 | 46 | 0 |
| Risk rejected signals | 12 | 12 | 0 |
| Data rejected signals | 0 | 0 | 0 |
| Gross P&L | -₹2,514.17 | -₹2,514.17 | ₹0.00 |
| Estimated costs | ₹7,994.62 | ₹7,994.62 | ₹0.00 |
| Net P&L | -₹10,508.79 | -₹10,508.79 | ₹0.00 |
| Win rate | 30.43% | 30.43% | 0.00 pp |
| Profit factor | 0.375052 | 0.375052 | 0 |
| Portfolio trade count | 46 | 46 | 0 |

Trade rows, per-symbol signal counts, and metrics CSV rows compare exactly equal. The endpoint audit found no data observations exactly at -2.0% or -1.0%; consequently no signals or trades changed and there are no affected timestamps to enumerate. The saved pre-existing summary matches the isolated baseline on these metrics.

Machine-readable comparisons and endpoint audits are in `comparison.json` and the `baseline/workspace/results` and `corrected/workspace/results` folders.

## Integrity

The original root and validation reports, summary, metrics, signal counts, trade file, dataset audit, and input candles retain their pre-run SHA-256 hashes. The exact hashes are in `original_baseline_hashes.json`.
