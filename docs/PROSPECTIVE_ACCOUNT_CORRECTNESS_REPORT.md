# Prospective account correctness report

Date: 2026-10-30

## Problems addressed

The prospective paper workflow could size a candidate against the initial capital even after a shadow arm had realized a loss. It also needed a durable actual-entry decision so a rejected entry would not occupy an open position or a daily trade slot, and an accepted entry would not silently change when a later snapshot arrived. Concurrent runs could race while writing the same request budget or ledger. The summary needed stronger checks for duplicate records and inconsistent experiment capital.

## Changes and locations

| File | Change | Problem solved |
| --- | --- | --- |
| `app/research/deepseek_prospective.py` | Reconstructs available cash separately for the baseline and AI-assisted arms from settled default-scenario net P&L, with usable cash capped at the configured initial capital. The cost-aware gate also checks entry notional plus buy charges against that cash. | A position cannot reuse cash already lost or exceed the experiment's all-in funding. RiskEngine still sets quantity; this gate only rejects. |
| `app/research/deepseek_prospective.py` | Persists actual-entry checks in `entry_events.jsonl`. It requires the next five-minute entry candle, checks its open and RiskEngine size, and stores each arm's acceptance and cost reason. A baseline rejection immediately creates `PROSPECTIVE_ENTRY_REJECTED`; an AI-only rejection clears that arm while a baseline acceptance follows the normal stop or six-candle settlement. Accepted entry timestamp and price are checked on resume. | Rejected entries do not remain open, count as taken trades, or consume the daily accepted-trade quota. Accepted entries cannot silently change price on replay. |
| `app/research/deepseek_prospective.py` | Adds a shared-account clock guard, an output-directory lock around capture/provider/record operations, and atomic response-cache replacement. | Older observations cannot be appended after newer ones, cooperating processes cannot race on a directory's budget or ledgers, and a failed cache write preserves the prior cache. |
| `app/research/deepseek_prospective.py`, `scripts/run_deepseek_prospective_research.py` | Saves an experiment specification with model, frozen parameters, cost assumptions, and source hashes in the capital manifest, then checks it on resume. The CLI supplies the configured model. Legacy manifests and observations remain readable under their existing capital checks. | A future experiment cannot silently change capital, model, assumptions, or relevant code after initialization. |
| `scripts/summarize_deepseek_prospective.py` | Validates unique IDs, matching references, and consistent finite observation capital; separates rejected, awaiting-entry, awaiting-settlement, and completed decisions. | Duplicate or inconsistent records fail closed instead of inflating performance counts or silently falling back to a default capital. |
| `tests/test_deepseek_prospective.py`, `tests/test_deepseek_prospective_summary.py` | Adds offline tests for cash after losses, actual-entry state for both arms, resume, missing bars, capital/spec preservation, concurrent replay, atomic cache failure, and legacy summary behavior. | Guards the new account and ledger invariants without provider requests. |
| `tests/test_core.py` | Corrects the existing Upstox authentication assertion to the client's `UpstoxRequestError` contract, including category, stage, and HTTP status. | Removes a stale expectation of `PermissionError`; the Upstox client itself was not changed. |

The initial paper capital remains configurable. A ₹1,000 experiment must explicitly pass `--paper-capital 1000` with a **new** output directory. The frozen 1% stop, 1% per-trade risk, 2% daily loss limit, six-candle accepted-trade horizon, RiskEngine sizing, freshness and candle checks, and broker-order disablement were not changed.

## Verification

Offline regression command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-prospective-account-cache-final
```

Result: **129 passed in 20.53 seconds**. Tests used local mocks and scratch output only.

Python in-memory compilation and trailing-whitespace checks passed for all six changed Python files. `git diff --check -- tests/test_core.py` passed. Repository-wide `git diff --check` returned exit 1 for an existing `README.md:141` blank line at EOF; it reported no whitespace error in the files changed here. The other prospective source and test files were already untracked in this working tree, so Git's diff check does not inspect them; the explicit trailing-whitespace scan does.

Sessions 02, 03, 04, and 05 and their budgets were not modified by this work. Their current market-data budget ledger row counts are respectively 1, 1, 2, and 2. No Session 06 directory was created. No Upstox or DeepSeek request, market-data capture, broker order, budget reset, or historical evaluation was run.

## Remaining limits

- For different symbols at the **same** decision timestamp, candidate priority still follows the order snapshots are submitted. The shared account remains protected, but a research comparison across a stock universe needs a fixed batch order or documented priority rule before interpreting results.
- An accepted entry with too few contiguous future candles remains open and unsettled until valid continuation data arrives. The summary exposes that state and does not invent an exit.
- Available cash uses the saved default five-basis-point slippage net P&L. Alternative cost scenarios appear in performance summaries but do not drive the account cash ledger.
- The directory lock protects cooperating application processes, not manual edits to ledger files. The actual-entry open is a paper-market observation, not a broker fill. Stops can gap beyond the planned loss estimate.

The offline correctness checks pass. Before a multi-stock prospective comparison, define and freeze a deterministic policy for candidates arriving at the same timestamp. Any live current-market screening or provider call requires separate explicit authorization and a new experiment directory.
