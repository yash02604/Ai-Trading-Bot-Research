# DeepSeek prospective decision-quality research v1

## Evidence categories

| Category | What it contains | What it does not establish |
| --- | --- | --- |
| Synthetic engineering evidence | Deterministic fixtures for BUY, SELL, HOLD, malformed responses, timeout fallback, input ordering, caching, request budgets, and outcome accounting. | Model quality, market behavior, historical performance, or profitability. |
| Prospective observations | Timestamped snapshots supplied at decision time, exact request payloads, model/prompt/configuration metadata, redacted response, quantitative baseline, and separately appended later shadow outcomes. | A backtest, verified market-data source, real fill, or profitability. |
| Historical evaluation | Historical-live evaluator remains blocked by the provenance audit. | No DeepSeek historical evaluation has been run. |

The historical candle file's pinned hash and historical provenance gate are not changed by this workflow. Do not label prospective observations as historical or synthetic outcomes as historical trading performance.

## Offline verification

Run the network-free scenario matrix:

```powershell
.\.venv\Scripts\python.exe scripts/run_deepseek_prospective_research.py --mode offline-test --output backtests/deepseek_prospective_research_v1
```

This writes a uniquely timestamped `offline_synthetic_check_<UTC timestamp>.json`, labels its evidence `SYNTHETIC`, and reports zero API requests. Run reports and snapshots use collision-resistant timestamped names and exclusive creation; the append-only decision, outcome, budget, and event ledgers reject conflicting duplicate keys. The ordinary pytest suites use mocks and never call DeepSeek.

## Snapshot contract

Supply one JSON file per prospective capture using this shape:

```json
{
  "schema_version": "deepseek-prospective-observation-v1",
  "source": "operator source label",
  "symbol": "RELIANCE",
  "instrument_key": "NSE_EQ|INE002A01018",
  "observed_at": "2026-10-26T10:01:00+05:30",
  "candles": [
    {"timestamp":"2026-10-26T09:15:00+05:30","open":100,"high":101,"low":99,"close":100.5,"volume":12345}
  ]
}
```

Required top-level fields are `schema_version`, `source`, `symbol`, `instrument_key`, `observed_at`, and `candles`. `schema_version` must equal `deepseek-prospective-observation-v1`; `symbol` must be in `strategy_backtest.STOCKS` and its `instrument_key` must exactly match that mapping. `source` must be a non-empty operator-supplied string. Extra top-level or candle fields are accepted but are not added to the model payload. Each candle requires `timestamp`, `open`, `high`, `low`, `close`, and `volume`.

Include at least 35 candles for indicator warm-up. Timestamps must include explicit offsets, be strictly increasing, pass OHLCV integrity checks, and be no later than `observed_at`. Both `observed_at` and the latest candle must be no more than 15 minutes old at decision time; no future observation/candle is accepted. The operator must obtain the snapshot from an authorized market-data source and preserve its source details accurately; the local validator checks the supplied label but cannot authenticate the source.

The configured read-only source path is `app.upstox_client.get_intraday_candles(instrument_key, interval=5)`, with symbol/instrument mapping in `strategy_backtest.STOCKS`. The runner can capture directly with `--fetch-upstox --symbol ... --allow-market-data-requests`; it makes one logical Upstox V3 intraday client call and `app.paper_session.completed_frame` normalizes timestamps and excludes unfinished bars. The shared Upstox HTTP client retains bounded retries for transient GET failures, so actual HTTP attempts can exceed the number of persisted capture reservations. The capture is written under `snapshots/` with an explicit Asia/Kolkata retrieval timestamp, exact instrument key, completed candles, and content hash. This requires a valid authorized `UPSTOX_ACCESS_TOKEN` and an available intraday session. It is read-only; do not use the historical CSVs as a substitute for a current snapshot. The persistent market-data reservation cap is separate from the DeepSeek provider request budget, which caps provider calls exactly.

Run the local validation and frozen quantitative baseline check before the provider command:

```powershell
.\.venv\Scripts\python.exe scripts\validate_deepseek_prospective_snapshot.py --snapshot <snapshot.json>
```

This command makes no market-data or provider requests. It exits 0 only when the structure, freshness, project symbol mapping, and current frozen quantitative candidate pass; otherwise it prints a sanitized reason and exits 2.

The model request includes only bars available at decision time and the existing quantitative candidate must pass first. A future candle in the input snapshot is rejected, as is a stale observation timestamp. The runner appends an immutable observation with the exact system/user request, snapshot hash, quantitative baseline, model/configuration, redacted response, and a paper-only action. Provider failures and malformed responses are recorded and fall back to HOLD. Duplicate snapshot replay is rejected/idempotent. It never imports or calls a broker order path. Later invocations may append a separate outcome once the next entry bar and six-bar horizon or stop is observed. The outcome records paired baseline and AI-assisted branches for the same decision timestamp, including a baseline trade when AI vetoes it. Estimated costs come from `app.cost_model`; six explicit GST/SEBI/slippage scenarios are assumptions, and no broker fill is represented.

The frozen quantitative candidate is checked only at the latest candle. It requires a decline from the close three candles earlier satisfying `-2.0 < decline_pct <= -1.0` (a decline of at least 1% but less than 2%), RSI(14) in `[20, 25)`, and a latest close above the previous close. After that signal rule passes, the existing risk engine must permit the trade and position sizing must produce a positive quantity. The existing risk defaults are 1% maximum planned risk per trade, 2% daily loss limit, one open position, and three trades per day; the current paper account state also applies. These thresholds are frozen and must not be altered to obtain a provider call.

## Explicit opt-in run

To capture a fresh Upstox snapshot and conduct one controlled request (only if the frozen quantitative baseline is eligible), run during a valid market session. This example uses a new output directory; the previously used `deepseek_prospective_session_01` directory already has its one-call market-data reservation consumed, so do not reuse it:

```powershell
.\.venv\Scripts\python.exe scripts/run_deepseek_prospective_research.py --mode prospective-paper --enable-prospective --allow-provider-requests --fetch-upstox --symbol RELIANCE --allow-market-data-requests --output backtests/deepseek_prospective_session_02 --max-market-data-requests 1 --max-total-requests 1
```

Both `--enable-prospective` and `--allow-provider-requests` are required for provider access. The CLI also blocks unless `PAPER_TRADING=true`; `--fetch-upstox` additionally requires `--allow-market-data-requests`. This example sets both persistent lifetime caps to one. Each unique provider attempt is reserved in `request_budget.jsonl` before provider access. Repeated identical model inputs use the response cache and do not spend another provider request. Timeout, rate-limit/provider errors, malformed response, model mismatch, or exhausted budget defaults to HOLD. Provider retries are disabled and the configured timeout is bounded at 20 seconds. A model BUY can only confirm an already eligible quantitative candidate. If the baseline is ineligible, the AI request is skipped. Do not run this command during offline verification.

The provider cap is lifetime-persistent for the output directory. The one-request example is a smoke test, not a useful efficacy sample; a longer prospective study must predeclare an appropriate cap before its first run and keep that output directory's cap unchanged. Never edit or delete a budget ledger to expand it.

## Provider connectivity smoke test (non-trading)

Use this separate mode only to verify provider connectivity and strict JSON acknowledgement parsing when the current quantitative baseline is not eligible. It requires a user-supplied, current snapshot and validates schema, freshness, symbol/instrument mapping, timestamps, and candle integrity. It does not calculate or create a strategy decision, invoke the prospective observation/outcome ledgers, simulate a trade, or count as strategy-performance evidence. The provider is asked for exactly `{"status":"ok"}`; any trading-shaped or malformed response is marked invalid and never interpreted as a signal. This mode has a separate persistent provider request budget and must use a separate output directory. It does not fetch from Upstox. A smoke test does not require an eligible quantitative candidate; the regular snapshot validator is intended for strategy preflight and can return `BLOCKED` on an ineligible candidate.

To get a fresh snapshot using the configured read-only Upstox client, make one explicitly authorized capture call into the new smoke-test directory, then use the returned path. The capture reservation cap is separate; the existing Upstox client can retry transient GET failures. This does not read, reset, or modify session_02:

```powershell
$snapshot = & .\.venv\Scripts\python.exe -c "from app.research.deepseek_prospective import capture_upstox_snapshot; print(capture_upstox_snapshot('RELIANCE', output_dir=r'backtests\deepseek_provider_smoke_01', max_market_data_requests=1, allow_market_data_requests=True))"
if ($LASTEXITCODE -ne 0) { throw 'Upstox snapshot capture failed; do not retry in this directory without checking its persistent capture budget' }
.\.venv\Scripts\python.exe scripts/run_deepseek_prospective_research.py --mode provider-smoke-test --enable-provider-smoke-test --allow-provider-requests --snapshot $snapshot --output backtests/deepseek_provider_smoke_01 --max-total-requests 1
```

The smoke result is labeled `PROVIDER_CONNECTIVITY_TEST`, with `prospective_observation`, `strategy_performance_evidence`, `trading_decision_created`, `paper_trade_simulated`, and `trade_placement_enabled` all false. Its budget and events are stored as `provider_smoke_request_budget.jsonl`, `provider_smoke_request_budget_config.json`, and `provider_smoke_events.jsonl`. Do not reuse or reset an already-consumed smoke-test output directory.

Files under the output directory:

- `observations.jsonl`: append-only snapshots, exact model inputs, baseline and decision-time metadata.
- `outcomes.jsonl`: separately appended prospective shadow outcomes after enough later candles exist.
- `request_budget.jsonl`: persistent request reservations.
- `request_budget_config.json`: fixed lifetime request cap for this output directory.
- `response_cache.json`: request-keyed redacted responses.
- `prospective_run_<UTC timestamp>.json`: immutable per-invocation run summary.
- `market_data_request_budget.jsonl` and `market_data_request_budget_config.json`: persistent Upstox capture reservations and lifetime cap.
- `market_data_events.jsonl` and `market_data_failures.jsonl`: sanitized capture events and failures.
- `prospective_summary_<UTC timestamp>.json`: immutable paired directional accuracy and paper outcome metrics, separated by cost scenario.

Summarize matured paired outcomes without provider or market-data requests:

```powershell
.\.venv\Scripts\python.exe scripts/summarize_deepseek_prospective.py --output backtests/deepseek_prospective_session_02
```

Keep the output directory private because observations contain market data and exact prompts. Do not hand-edit or remove request-budget/ledger files to reopen a request allowance.

## Analysis plan and claim threshold

Before collecting observations, freeze the model identifier, prompt version, quantitative rule, stop/horizon, cost scenarios, snapshot source, and both request caps. Preserve every qualifying and non-qualifying capture so selection is visible. Review response validity, BUY/SELL/HOLD proportions, provider failures, veto rate, agreement with the quantitative baseline, net outcomes after estimated costs, drawdown, and symbol/time/regime breakdowns. The summary reports future-direction accuracy separately from simulated trading metrics and compares baseline and AI-assisted outcomes on the same completed decisions. Report eligible events, missing outcomes, and snapshot/data-quality exclusions.

Do not claim profitability from the synthetic run or from an early prospective sample. Any later performance statement requires enough independently sourced prospective observations, completed outcomes, estimated costs, uncertainty intervals, and a predeclared analysis; no numerical sufficiency threshold is asserted by this engineering change. Historical evaluation remains unavailable until its ten provenance findings are resolved with evidence and the exact dataset binding continues to pass.

## Verification record

Commands run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/run_deepseek_prospective_research.py --mode offline-test --output backtests/deepseek_prospective_research_v1_offline_check
.\.venv\Scripts\python.exe -m pytest tests/test_deepseek_prospective.py -q --basetemp .pytest-tmp-prospective
.\.venv\Scripts\python.exe -m pytest tests/test_deepseek_prospective.py tests/test_deepseek_eval_v1.py tests/test_synthetic_provider_check.py -q --basetemp .pytest-tmp-prospective
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-prospective
```

The latest reproducible results are recorded in `CURRENT_STATUS.md`. Offline verification must complete with zero provider requests, and its output is labeled `SYNTHETIC`. Provider capture commands are never part of pytest or the offline scenario matrix. The offline report is engineering evidence, not a market observation or historical result.

