# BASELINE_REPORT.md — Read-Only Repository Audit

- **Date:** 2026-10-25
- **Branch:** `main` (HEAD `a7fc8b1`, "Add bounded synthetic OpenRouter decision check")
- **Mode:** Inspection only. No files were modified, no broker orders placed, no external data acquired, and `.env` was never read or printed.
- **Method:** `git status`/`git log`/`git diff --stat` review, full-file review of `app/`, `app/v2/`, `scripts/`, `schemas/`, `docs/`, `tests/`, backtest artifacts, baseline test run with the project virtualenv, and a line-ending/compile sanity check.

---

## 1. Repository snapshot

### 1.1 Git status (abridged)

```
 D .env.example
 M app/v2/ai_led_research.py
 M app/v2/provider.py
 M app/v2/synthetic_provider_check.py
 M tests/test_synthetic_provider_check.py
?? app/offline_market_data_v1.py
?? app/provenance_audit.py
?? app/session_manifest_v2.py
?? backtests/openrouter_synthetic_decision_check/run_*.json   (7 untracked runs)
?? backtests/provenance_integrity_v1/
?? data/acquisition_pilots/
?? data/audits/nse_cm_session_manifest_v2.json
?? docs/ACQUISITION_PILOT_EXECUTION_GATE_V1.md
?? docs/ACQUISITION_PILOT_READINESS_V1.md
?? docs/OFFLINE_MARKET_DATA_PIPELINE_V1.md
?? schemas/
?? scripts/create_nse_session_manifest_v2.py
?? scripts/run_acquisition_pilot_v1.py
?? scripts/run_provenance_integrity_audit.py
?? tests/test_acquisition_pilot_plan.py
?? tests/test_acquisition_pilot_runner.py
?? tests/test_offline_market_data_v1.py
?? tests/test_provenance_audit.py
```

**Uncommitted work is significant:** the entire offline market-data pipeline (module, schemas, tests, manifest v2, pilot scripts, provenance audit) plus provider hardening in `app/v2/` exists only in the working tree. `git diff --stat` for tracked files:

```
 .env.example                           |  13 ----
 app/v2/ai_led_research.py              |  38 ++++++++++-
 app/v2/provider.py                     | 108 ++++++++++++++++++++++++++-----
 app/v2/synthetic_provider_check.py     |  19 +++++-
 tests/test_synthetic_provider_check.py | 114 ++++++++++++++++++++++++++++++++-
 5 files changed, 258 insertions(+), 34 deletions(-)
```

Until this is committed, the audit trail that `docs/OFFLINE_MARKET_DATA_PIPELINE_V1.md` and the synthetic-check docs describe is not reproducible from history.

### 1.2 Secret hygiene (verified, no contents read)

- `.env` exists on disk (untracked) and `.gitignore` line 1 is `.env`; `logs/` (which holds the SQLite state) is also ignored. `git ls-files --error-unmatch .env` fails — **`.env` is not tracked by git.** Good.
- However, `.env.example` is **deleted** in the working tree while `README.md` (lines 25, 56) and `RESEARCH_COPY_STATUS.md` still instruct: *"Create a local `.env` based on `.env.example`."* Setup instructions are broken for a fresh clone. If the deletion was unintentional, restore it; if intentional, the docs must be updated.

### 1.3 Environment

- System `python` (3.13.2) has **no pytest**; the project `.venv` does. All commands below use `./.venv/Scripts/python.exe`. README's `python -m pytest -q` works only with the venv activated — worth stating explicitly in the README.
- `requirements.txt` lists `upstox-python-sdk`, `ta`, `backtesting`, `tenacity` — none of which appear to be imported by current `app/` code paths reviewed (Upstox calls use `requests` directly). Dead or aspirational dependencies; harmless but misleading.

---

## 2. Baseline test results

```
$ ./.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
============================= 315 passed in 9.93s =============================
```

**All 315 tests pass.** Notable passing suites: `test_paper_session.py` (fail-closed paper workflow, gap auditing, execution quotes), `test_synthetic_provider_check.py` (new provider hardening incl. no-message-leak classifications), `test_offline_market_data_v1.py` (manifest v2, holiday regression, quarantine partitioning), `test_nse_session_audit.py`, `test_sharp_reversal_*`, `test_provenance_audit.py`.

Additional safe checks:

```
$ git diff --stat                          → see §1.1 (5 files, +258/−34)
$ ./.venv/Scripts/python.exe -m compileall -q app scripts tests backtester.py
compile exit: 0                            → all Python compiles cleanly
```

Passing tests ≠ absence of defects: the highest-severity findings below sit in code paths the tests never exercise (replay of *realistic* ledger timestamps, day rollover in the legacy paper trader, malformed consumer filtering of integrity reports).

---

## 3. Findings by severity

Severity key: **P1** = wrong results / broken core flow / audit-integrity risk; **P2** = correctness or consistency defect with bounded impact; **P3** = hygiene, robustness, readability.

### P1 — High

| # | Location | Finding |
|---|----------|---------|
| P1-1 | `app/v2/evaluation.py:207-212` | **Replay window is effectively zero-width.** Lineage validation requires `recorded_at >= cutoff + 300s` (anti same-bar execution), but the subsequent `request.validate(now=recorded, max_data_age_seconds=300)` rejects anything where `recorded - cutoff > 300s`. Only `recorded == cutoff + 300.000s` exactly is replayable — which is exactly what the synthetic test uses (`tests/test_ai_v2_evaluation.py:26-27`). Any real ledger record written microseconds later is rejected as "stale". The offline evaluator cannot replay realistic decisions; fail-closed, but functionally broken. |
| P1-2 | `app/v2/evaluation.py:371-376, 567` | **Events misattributed to the wrong decision.** `advance_positions(recorded)` runs *before* `event_context` is rebuilt, and `emit()` spreads `**event_context` at call time. So `SIMULATED_FILL`, `EXIT_FILL`, `SIMULATED_EXIT`, `TRADE_OUTCOME`, and the kill-switch `ENTRY_REJECTED` emitted while advancing are stamped with the *previous* record's `rationale`, `uncertainty`, `key_evidence`, `evaluation_risk_result`. The append-only `events.jsonl` — the evidence ledger — carries wrong per-decision metadata. |
| P1-3 | `app/paper_trader.py:26-31, 145-149` | **"Daily" limits are lifetime limits in the legacy paper trader.** `RiskState.daily_loss` and `trades_today` are never reset on day rollover anywhere in `PaperTrader`/`TradingEngine`. After cumulative losses hit 2% (or 3 trades *ever*), all future trading is permanently blocked in any long-running process. (`app/paper_session.py` — the newer runner — handles rollover correctly; the legacy path does not.) |
| P1-4 | `app/candle_integrity.py:61-68` | **Naive timestamps silently shifted +5:30 in the returned frame.** `pd.to_datetime(..., utc=True)` treats a naive string as UTC, then `tz_convert("Asia/Kolkata")` relabels 09:15 as 14:45. The error row is flagged (`timezone_naive_timestamp`), and `market_analysis`/`historical_validation` filter errored rows — but any consumer that uses `report.frame` without filtering gets silently corrupted session times. Latent data-corruption trap; contrast with `offline_market_data_v1` which correctly classifies naive records as UNRESOLVED. |
| P1-5 | `app/v2/evaluation.py:313`, `app/v2/synthetic_provider_check.py:152`, `app/research/sharp_reversal_experiment.py:55-65` | **Session manifest trusted without v2 validation on replay paths.** `load_session_dates()` checks only `schema_version == 2` and `segment == "CM"`; the strict `offline_market_data_v1.validate_session_manifest` (holiday pin, source registry, coverage bounds) is never invoked on evaluation/synthetic-check paths — only the file SHA-256 is recorded. A tampered or truncated manifest silently changes session gating — the exact failure class the v2 manifest correction was built to prevent. |
| P1-6 | `backtests/openrouter_synthetic_decision_check/` | **OpenRouter integration has never produced a valid decision.** Latest run `run_20261023T091440829905Z.json` (model `openai/gpt-oss-20b:free`): all 4 scenarios failed with `provider_error`, HTTP 404 at request submission; earlier runs failed with ValueError / APIConnectionError; the 3 earliest runs were BLOCKED preflights with 0 requests. `docs/OPENROUTER_SYNTHETIC_DECISION_CHECK.md` still headlines "blocked before any external request… requests_made: 0", contradicted by 5 same-day runs that each made 4 requests. Fail-safe (no bad decision accepted), but the AI path is unverified end-to-end. |
| P1-7 | `scripts/run_openrouter_synthetic_decision_test.py:66-75` | **On exception the report is replaced with a stub claiming `requests_made: 0`.** If `run_synthetic_decisions` raises mid-run, up to 4 provider calls may already be consumed, but the persisted attestation asserts zero requests and drops scenario diagnostics — misleading exactly where the check exists to attest bounded usage. |

### P2 — Medium

**Risk / cost model**

- `app/risk_engine.py:55` vs `app/paper_session.py:166` vs `backtester` — **three different daily-loss-limit semantics.** `risk_engine.calculate_daily_loss_limit` measures against *current* (rolling) capital; `paper_session.daily_limit_reason` correctly uses `day_start_capital`; `backtester.run_reversal_backtest` accumulates *gross* loss while `historical_validation.simulate_book` accumulates *net*. Backtest and live paper validate different risk rules, weakening the "paper validates the backtested strategy" claim. Also no pre-trade check that `daily_loss + planned_risk <= limit` (`risk_engine.py:47-67`).
- `app/cost_model.py:12` — **SEBI turnover rate off by 10×.** Comment says ₹10/crore (1e-6) but the constant is `0.0000001` = ₹1/crore. Tiny absolute impact, but it contradicts the module's own comment and its "conservative published rates" test claim (`tests/test_historical_validation.py::test_cost_model_is_itemized_and_uses_conservative_published_nse_rate` passes with the understated rate).
- `app/cost_model.py:18-20` — `nse_transaction_rate(trade_date)` ignores `trade_date`; a fixed rate is applied across all history. GST base excludes SEBI fees (line 38), slight further understatement.
- `app/v2/paper.py:86-88` vs `app/v2/evaluation.py:386-391` — **inconsistent cost/PnL basis** between the live paper path and the offline evaluator (`entry_fill` vs `reference_entry` turnover for slippage), so the same conceptual trade yields different `net_pnl` in the two paths. No reconciliation test spans both.

**Audit / ledger integrity**

- `app/v2/audit.py:12-24` — `_safe_payload` raises (`NaN`, secret-like key) *out of* `append_*`, uncaught by callers. A NaN feature turns `append_decision` into an exception *after* the provider call was paid and the gate already consumed `signal_id` into `seen_signal_ids` (`app/v2/safety.py:223`) — the signal can never be retried. (`ai_led_research.py:620`, `controller.py:36`.)
- `app/v2/audit.py:40-44` — idempotency uniqueness covers only 4 event types; `PAPER_FAILURE` / `ENTRY_*` duplicates insert freely, so failure loops grow unbounded duplicate audit rows.
- `app/v2/safety.py:183-184` — gate failures persist raw `str(error)[:180]` into the ledger (can contain local file paths) in a codebase that otherwise bounds every diagnostic string.
- `app/v2/controller.py:30-37`, `ai_led_research.py:598-620` — TOCTOU on `audit.seen(signal_id)`: two concurrent evaluations both pass the check, both pay for a provider completion, the loser then raises duplicate errors with cost already spent.
- `app/paper_store.py:27` + `app/paper_session.py:310, 313` — explicit `BEGIN IMMEDIATE` can collide with the implicit transaction machinery (`isolation_level="IMMEDIATE"` + uncommitted `record_audit` calls → `cannot start a transaction within a transaction`, which would abort the position-close path). Works today only because of commit ordering. Related: those two `record_audit` calls are not followed by `commit()`, so audit rows can be lost on crash.

**Paper session / engine robustness**

- `app/paper_session.py:389` — `now = max(now, quote["observed_at"])` mutates the shared scan clock inside the candidate loop, potentially keeping later symbols' entry windows "open" after they actually closed.
- `app/paper_session.py:94-104` — `completed_frame` silently drops malformed/duplicate rows with no audit record, inconsistent with the module's otherwise fail-closed posture (there *is* a test asserting the drop, but no audit trail).
- `app/paper_session.py:562-674` — `run()` has **no market-hours gate**: loops 24/7 including weekends, hitting the API every 60s and relying on downstream freshness checks to no-op. Wasted quota and noisy audit trail.
- `app/trading_engine.py:40-45` — no guard for `close=None`; `check_stop_loss(None, ...)` raises `TypeError` that escapes `process_market` uncaught — one malformed candle kills the tick. Also `trading_engine.py:66-69` logs only the exception *type* on AI failure, no diagnostic detail.
- `app/upstox_client.py:24-33` — retry policy never retries read timeouts (`read=0`, the most common transient market-data failure); fresh `Session` per call (no pooling).
- `app/paper_store.py:135-141` — `daily_state` silently keeps stale `day_start_capital` if the store is reused with different `initial_capital` (INSERT OR IGNORE, no warning).
- `app/v2/provider.py:121-130` — only `finish_reason == "length"` is rejected; `content_filter` passes through to `missing_content` (mislabeled diagnostic); `latency_ms=None` on provider errors; `response_bytes` measures decoded content only.

**Offline pipeline / provenance**

- `schemas/*.schema.json` — **never enforced at runtime.** `scripts/run_acquisition_pilot_v1.py:31` defines `AUTH_SCHEMA_RELATIVE` but never reads it; authorization/manifests/candles are validated by hand-rolled Python validators that have already drifted from the schemas (e.g., quarantined UNRESOLVED rows get `timestamp_ist: null` at `offline_market_data_v1.py:480`, violating candle-record-v1's `type: string`). The schemas are documentation, not a gate — and there is a test *asserting they parse*, giving false confidence.
- `app/offline_market_data_v1.py:470-477` — a structurally broken candle on an untrusted date is classified UNRESOLVED rather than VERIFIED_INVALID (precedence masking), and `timestamp_timezone_unresolved` is detected via a fragile string match on the error message ("explicit UTC offset", line 428).
- `app/offline_market_data_v1.py:100-134` — secret redaction misses bare `sk-…`/token values under non-secret key names.
- `scripts/run_provenance_integrity_audit.py` — module-level side effects (hashing, artifact writes at import), duplicate `ROOT` assignment (lines 4 and 8), crashes raw on missing `data/research/*.csv` leaving half-written outputs. **Zero tests.**
- `app/session_manifest_v2.py:52` — the 2024-11-20 correction *replaces* `source_ids` with `["NSE_2024", SOURCE_ID]` rather than appending; v1 sources for that date are silently dropped.
- `app/nse_session_audit.py:24` — hard-requires `schema_version == 1`, so the v1 audit module can never validate the repo's current v2 manifest; the two audit stacks cannot cross-check each other.

**Backtester consistency**

- `backtester.py:197-204` — sizing capital `max(capital, 0.01)` lets reported `ending_capital` go arbitrarily negative; drawdown stats on a negative base are meaningless.
- `historical_validation.py:163` vs `backtester.py:221` — gap-exit label boundary differs (`open <= stop` vs `open < stop`), confusing cross-run comparison.
- `backtester.py:91-131` — forward-movement windows cross overnight/session boundaries unchecked, mixing overnight gaps into "intraday" stats.
- `reversal_detector.py:49-55` — the standalone `detect_reversal_candidates` has no session-gap guard (callers patch it afterward); the 3-candle decline can span a weekend.

### P3 — Low / hygiene

- `app/simulator.py.save` — **stray backup file committed to git** (older copy with different fixture values); repo clutter, no doc references it.
- `app/config.py:35` — `paper_initial_capital` unvalidated (accepts 0/negative/NaN); default `openrouter_model="openrouter/free"` is not a real OpenRouter slug, so unconfigured deployments burn one failed request per signal.
- Naive `datetime.now()` in `paper_trader.py:76,117` and `simulator.py:40,54` (host-local timestamps vs the session's IST ledger).
- `app/v2/contracts.py:63` — `self.request_id.strip()` runs before the `isinstance(str)` check → `AttributeError` instead of the intended `ValueError`; line 92 is dead code; `features` unbounded in size.
- `app/v2/safety.py:68-76` — `FileKillSwitch` docstring says absent path is invalid, but `None`/empty is silently accepted and the switch is permanently off.
- `app/v2/ai_led_research.py:436-440` — local `request_validation` failures are recorded with code `provider_failure`; line 551 bypasses the ledger API via `self.audit.db.execute` directly; empty `hypotheses` list counts as successful discovery (line 491).
- `app/research/sharp_reversal_diagnostics.py:182` — `report_markdown` crashes (`None:.2f`) for a zero-trade strategy.
- `app/research/sharp_reversal_experiment.py:401-407` — execution-integrity validation failures silently become reject counters instead of failing the run; hardcoded `git rev-parse` (line 568) aborts the write if git is absent; `sha256_file` exists in three drifting copies across modules.
- `app/v2/synthetic_provider_check.py:95-109` — evidence anchoring is substring + any-number matching over `rationale`; one feature name plus one supplied number anywhere in the text passes for the whole decision.
- `app/market_analysis.py:26-33` — `candles_to_dataframe` filters only 5 error reasons; rows with `nonpositive_price`/`invalid_ohlc_relationship`/`negative_volume` pass through to the caller.

### Documentation conflicts

1. **`.env.example` deleted; README (lines 25, 56) and `RESEARCH_COPY_STATUS.md` (line 8) still instruct copying it** — fresh-clone setup is broken (see §1.2).
2. **`RESEARCH_COPY_STATUS.md` claims "No `.env` exists in this research copy"** — an `.env` now exists (untracked, contents not inspected). The reconciliation record is stale.
3. **`RESEARCH_COPY_SHA256.csv` manifest fails 3 entries:** `.env.example` (listed, file gone), `.gitignore` (41 vs 195 bytes), `RESEARCH_COPY_STATUS.md` (1,672 vs 2,239 bytes). The manifest no longer certifies the copy it describes.
4. **`docs/OPENROUTER_SYNTHETIC_DECISION_CHECK.md` headline vs newer run artifacts** — "blocked, 0 requests" vs five same-day runs of 4 requests each (see P1-6).
5. **`docs/ACQUISITION_PILOT_READINESS_V1.md` says "the repository still has no dedicated acquisition runner"** — `scripts/run_acquisition_pilot_v1.py` exists and `docs/ACQUISITION_PILOT_EXECUTION_GATE_V1.md` documents invoking it. Readiness doc (Oct 19) stale vs gate doc (Oct 23).
6. **Conflicting same-day suite counts:** `AI_PAPER_SESSION_SYNTHETIC_VERIFICATION.md` says "298 passed"; `OPENROUTER_SYNTHETIC_DECISION_CHECK.md` says "309 passed" (today's baseline is **315**). No supersession notes.
7. **`CURRENT_STATUS.md` predates the whole V2/offline-pipeline effort** (audit dated 2026-10-02) yet README points to it as "the audit checklist and latest validation state." Both README and CURRENT_STATUS also reference `cd .\ai-tarding-assistant` (typo + wrong name; actual dir is `ai-trading-bot-research`).
8. Docs verified **consistent** with code: risk limits (1%/2%/1 position/3 trades), ₹100,000 paper capital, 1% stop, six-candle horizon, frozen reversal band, 3-of-4 index veto, V2 safety bounds (25% allocation, stop 0.05–1.00%, horizon 1–12 bars), and the `backtests/validation/summary.json` figures quoted in `VALIDATION_REPORT.md`.

### Data-provenance state (from artifacts, not re-acquired)

- `backtests/provenance_integrity_v1/report.md`: dataset status **UNRESOLVED** — 15 findings (3 VERIFIED_INVALID incl. 3 malformed OHLC rows on 2024-06-25 09:15 and the manifest's originally missing 2024-11-20 holiday; 10 UNRESOLVED incl. pipeline provenance, corporate-action adjustment, survivorship). Conclusion stands: cached candles are **not yet eligible** for execution-based research claims.
- `data/acquisition_pilots/pilot_v1.json`: correctly fail-closed (`BLOCKED_PENDING_SOURCE_AUTHORIZATION_AND_RETENTION_REVIEW`, `retention_permission_confirmed: false`).

---

## 4. Missing tests

Modules with **no dedicated tests**: `app/simulator.py`, `app/mock_ai.py`, `app/trade_logger.py`, `app/config.py` (only incidental coverage), `scripts/run_provenance_integrity_audit.py`, `scripts/run_openrouter_synthetic_decision_test.py` (incl. the report-replacement path, P1-7), `scripts/create_nse_session_manifest_v2.py` (builder is tested indirectly).

Weak/negative-path gaps in otherwise-tested modules:

- `app/v2/evaluation.py`: the `recorded_at == cutoff + 300s` boundary is tested at exactly one instant (which is why P1-1 went unnoticed); `_split_round_trip_costs` reconciliation failure; malformed/unreadable ledger rows; AI-exit vs intrabar stop precedence on the same bar (`same_bar_precedence` asserted nowhere); abort path in `_write_output`.
- `app/v2/audit.py`: NaN/secret propagation out of `append_decision`; duplicate generic-event insertion.
- `app/v2/provider.py`: constructor bound rejections, `content_filter` finish reason, latency-on-error.
- `app/upstox_client.py`: retry policy / read-timeout behavior (only the 401 path is tested).
- `app/reversal_detector.py`: candidate math (decline/RSI boundaries) tested only via `signal_monitor` frozen band, not the standalone detector.
- `app/paper_trader.py`: **no day-rollover test** (which is why P1-3 exists).
- `app/v2/paper.py` vs `app/v2/evaluation.py`: no cross-module cost/PnL reconciliation test.
- `app/research/sharp_reversal_diagnostics.py`: `report_markdown` entirely (would catch the zero-trade crash).

---

## 5. Phased implementation plan (proposed; not started)

**Phase 0 — Provenance and hygiene (no behavior change).**
1. Commit or consciously stage the uncommitted offline-pipeline work (module, schemas, scripts, tests, manifest v2, docs, backtest artifacts) so the audit trail is reproducible.
2. Restore `.env.example` (or rewrite README/`RESEARCH_COPY_STATUS.md` setup sections) and refresh `RESEARCH_COPY_SHA256.csv`.
3. Remove `app/simulator.py.save` from git; reconcile the stale docs (§3 Documentation conflicts 4–7) and add supersession notes with dates.
4. Delete dead dependencies from `requirements.txt` or justify them.

**Phase 1 — P1 correctness fixes (each with a failing-test-first regression test).**
1. Widen the V2 replay window to a coherent rule (e.g., validate data age against the signal candle close, not the recorded instant) and add boundary tests at `cutoff+300s+ε`.
2. Rebuild `event_context` before `advance_positions` emits; add a test asserting emitted events carry the acting decision's metadata.
3. Add day-rollover reset to `PaperTrader`/`TradingEngine` `RiskState` + test.
4. Make `candle_integrity` classify naive timestamps without mutating the returned frame's values (match `offline_market_data_v1` semantics) + test.
5. Route evaluation/synthetic-check session loading through `offline_market_data_v1.validate_session_manifest` + tamper test.
6. Fix the synthetic-check CLI so an exception mid-run preserves partial-run artifacts with an honest request count.
7. Decide the OpenRouter path: fix the 404 (model slug/endpoint), or demote the doc claims to "blocked" explicitly.

**Phase 2 — Risk and cost consistency.**
1. One daily-loss semantics: day-start capital, net-of-cost accumulation, shared by `risk_engine`, `paper_session`, `backtester`, `historical_validation`; add the pre-trade `daily_loss + planned_risk <= limit` check.
2. Fix the SEBI rate constant (and GST base); re-run cost sensitivity; update affected backtest numbers or annotate that historical artifacts predate the correction.
3. Unify `app/v2/paper.py` and `app/v2/evaluation.py` cost basis; add a cross-module reconciliation test.

**Phase 3 — Ledger and runtime hardening.**
1. Make `_safe_payload` failures a pre-provider validation (fail before paying for the call, don't consume `seen_signal_ids`); bound gate exception text; add uniqueness for remaining event types.
2. Fix the `BEGIN IMMEDIATE`/implicit-transaction pattern; commit audit rows at the point of record.
3. Stop clock mutation in `paper_session`'s candidate loop; add a market-hours gate to `run()`; guard `check_stop_loss(None)`; retry read timeouts in `upstox_client`.
4. Enforce or retire the JSON Schemas (pick one: runtime-validate against them, or generate the Python validators from them).

**Phase 4 — Test-gap closure.** Add the tests listed in §4, prioritizing the replay-boundary family, paper-trader rollover, and the schema enforcement decision.

**Phase 5 — Data provenance.** Resolve the 10 UNRESOLVED findings in `backtests/provenance_integrity_v1/` (or formally accept and document them) before any execution-based research claim; only then revisit the acquisition pilot gate.

---

## 6. Verification record

| Check | Command | Result |
|---|---|---|
| Full baseline suite | `./.venv/Scripts/python.exe -m pytest tests/ -v --tb=short` | **315 passed in 9.93s** |
| Uncommitted diff | `git diff --stat` | 5 files, +258/−34 (see §1.1) |
| Secret-file tracking | `git ls-files --error-unmatch .env` | not tracked (error) ✔ |
| `.gitignore` coverage | `grep -nE "\.env\|sqlite\|logs/" .gitignore` | `.env` (line 1), `logs/` (line 6) present ✔ |
| Byte-compile | `./.venv/Scripts/python.exe -m compileall -q app scripts tests backtester.py` | exit 0 ✔ |
| External calls / orders / data acquisition | — | none performed ✔ |

All existing work preserved: this audit created only `BASELINE_REPORT.md` and modified nothing else.

## Reverification addendum — 2026-10-28

This addendum records a second, scoped audit after the earlier repository audit. It does not replace the prior findings or imply that every finding was rechecked. No Git/GitHub commands, external network requests, market-data requests, DeepSeek calls, broker actions, or historical evaluation were intentionally performed during this update. The test suite may import normal project configuration; no `.env` contents or credentials were displayed or printed.

### Verified baseline and changes

- Project venv: Python 3.13.2, pip 24.3.1. Dependency declarations in `requirements.txt` remain unpinned; clean-environment installation was not attempted.
- Full offline suite: `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-final2` — **348 passed**.
- Related AI replay suite: `.\.venv\Scripts\python.exe -m pytest tests\test_ai_v2_evaluation.py tests\test_ai_led_research.py tests\test_synthetic_provider_check.py -q --basetemp .pytest-p1fix` — **70 passed**.
- Compilation: `.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py` — **passed**.
- Confirmed replay freshness defect: the evaluator required a recorded timestamp at or before exactly 300 seconds after a five-minute START-labeled candle, despite separately requiring the candle to have completed. A one-second post-close delay failed freshness. Offline replay now allows up to 600 seconds from the start label, corresponding to bar formation plus at most five minutes of bounded processing delay. A regression test verifies 09:25:01 after a 09:20 start label is replayable and a 09:25:01 record for a 09:15 label remains stale.
- This freshness allowance is scoped to the offline recorded-decision evaluator. The global `RiskLimits` and `AIResearchDecisionService` defaults remain 300 seconds. An attempted change to the shared safety default was rejected by automatic review because it widened freshness for every caller; it was not bypassed. Consequently, the live AI research path may still fail closed for a delayed START-labeled candle unless separately configured and reviewed.
- Fixed the README setup path and documented the observed Python/dependency limitations. Corrected the acquisition readiness note: the bounded runner exists, is fail-closed, and remains blocked pending source/retention/request-limit/instrument-master evidence.
- The historical `VALIDATION_REPORT.md` is pinned as an input to the provenance-preservation test. An attempted note edit caused the test to fail; the note was removed, the pinned hash was restored, and the preservation regression passed before the full-suite rerun.

### Current data and safety disposition

The latest local provenance summary contains 18 findings: 5 `VERIFIED_VALID`, 3 `VERIFIED_INVALID`, and 10 `UNRESOLVED`. The pinned main-candle SHA-256 is verified for byte identity only. Overall provenance remains `UNRESOLVED` and historical-live remains blocked. Historical metrics in the 2026-10-02 validation report are preserved as reported; they were not reproduced during this audit. No current prospective snapshot or live observation was created, and no profitability claim is supported. Real broker orders remain disabled.

### Files changed in this update

`README.md`, `CURRENT_STATUS.md`, `docs/ACQUISITION_PILOT_READINESS_V1.md`, `docs/AI_V2_EVALUATION_V1.md`, `app/v2/evaluation.py`, `tests/test_ai_v2_evaluation.py`, and this report addendum. No files were removed. Other existing project changes and artifacts were preserved.

### Cost-model correction — 2026-10-28

Follow-up verification found `app/cost_model.py` used `0.0000001` for SEBI turnover charges while its comment said ₹10/crore. ₹10 divided by ₹1 crore is `0.000001`; the constant and small-turnover arithmetic regression were corrected. The current [Upstox charges schedule](https://upstox.com/brokerage-charges/) lists ₹10/crore for equity intraday. This correction is limited to the modeled SEBI fee component.

The GST base was not changed: current Upstox charge documentation describes GST over brokerage, transaction and IPFT charges, while an [NSE circular](https://archives.nseindia.com/content/circulars/FATAX52993.pdf) states GST is to be levied on SEBI fees from 2022-07-18. The applicable treatment for the historical dataset and modeled broker was not established from dated contract-note evidence. The fixed transaction-rate assumption also remains a model limitation.

No historical report or result was regenerated. Previously reported costs and metrics predate this correction and must not be treated as recalculated with the corrected fee. Provenance remains unresolved and historical-live evaluation remains blocked.

- Focused cost/historical-validation tests: `.\.venv\Scripts\python.exe -m pytest tests\test_cost_model_execution_integrity.py tests\test_historical_validation.py -q --basetemp .pytest-cost` — **12 passed**.
- Full offline suite after correction: `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-cost-full` — **348 passed**.
- Compilation: `.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py` — **passed**.

Files modified in this follow-up: `app/cost_model.py`, `tests/test_cost_model_execution_integrity.py`, `CURRENT_STATUS.md`, and this report. The pinned `VALIDATION_REPORT.md` remains unchanged.


