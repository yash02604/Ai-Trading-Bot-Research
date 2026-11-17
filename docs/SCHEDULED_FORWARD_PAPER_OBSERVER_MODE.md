# Scheduled NSE forward-paper infrastructure and Trading Hours Observer Mode

## 1. Architecture

**Preparation scope selected by the user:** build V2.2 scheduling and observer infrastructure; keep production starts blocked until a separate transport integration is reviewed. No schedule or experiment is activated by this change.

### Discovery, recorded before implementation

The existing reviewed path is `scripts/run_ai_led_prospective_paper.py` → `ai_led_reviewed.run_reviewed_paper` → `ai_led_guarded._capture_batch/_finish` → `ai_led_offline.run_offline_batch`. It is the older guarded paper account, not V2.2. Its provider and GET-only market transports require separate explicit opt-ins, current client attestation/calendar/source identity and durable budgets/send markers. Quotes have a five-second response-time freshness check; candle batches require 35 contiguous same-day completed five-minute candles, so an ordinary 09:05 wake-up cannot authorize a reviewed batch before approximately 12:10.

Risk/cost/ranking are already implemented in the core, risk engine and cost model. The reviewed settlement API only advances an accepted OPEN position; PENDING is handled by a subsequent batch or explicit local expiry. A position outside its acquisition window is unresolved, not permission to invent a closing price. Logical request budgets in this legacy path do not imply the signal-only physical-attempt accounting applies to it.

`CausalForwardPaperV2` has no transport binding and accepts only offline provenance. It accepts already selected upstream proposals and explicit observations; it does not invoke ranking or AI. No reviewed data adapter currently reaches it. Reusing the older guarded path would change accounting/recovery semantics, so it is not silently substituted.

V2 construction and its apparent `history/account/performance` readers call verification/recovery that may update anchors/quarantine/cleanup. Likewise, `summarize_guarded` obtains a lock and can create account directories. None is a strict observer API.

The current V1 `app.paper_session` is a separate persistent scanner with its own synchronous loop and quote-based simulation. It is not a replacement for the audited causal engine. Its shutdown retains state; scheduling it here would bypass the intended V2.2 preparation boundary.

The implemented architecture separates: Task Scheduler → fixed paper launcher → ownership/identity/calendar/preflight → **NO_GO: V2_TRANSPORT_NOT_REVIEWED**. The private `_run_synthetic_fixture` harness exercises the existing V2 engine for offline tests only and is absent from CLI options. Runner-owned sanitized snapshots/events/reports → read-only observer → human/Codex. No observer response participates in decisions. This is the specifically approved preparation scope; it is not a completed real-market V2 transport integration.

## 2. Launcher flow

Entry point: `scripts/run_scheduled_forward_paper.py`, implemented by `app/research/scheduled_forward_paper.py`. It validates an exact non-secret config schema and FORWARD_PAPER mode, evaluates the actual clock/calendar and local client attestation, then obtains nonblocking project-wide ownership before creating dedicated control metadata. Invalid modes/configurations return NO_GO before creating state. A recognized closed day returns NO_SESSION. Other valid launches remain NO_GO because transport integration and experiment authorization are absent. Neither market/provider permission nor a production adapter can be supplied in configuration.

Identity is bound to trading date, config and code hashes. Each invocation has a separate immutable `.begin.json`/`.final.json` record and a link to the previous attempt. Same-day invocations retain the logical run ID, get distinct attempt IDs and flag duplicate launch. Reports are operational metadata, not substitutes for V2's financial identity or a trusted completion witness.

The dedicated control root accepts only its marker, runs, engine and launcher-log directories; it refuses unrelated contents, symlink/junction roots, changed identity and conflicting unfinished prior dates. Production ownership is keyed by the canonical project path in a separate OS-backed lock, so changing control roots cannot bypass it. A local thread lock plus Windows `msvcrt`/POSIX `flock` gives immediate second-instance refusal; process death releases OS ownership even though its one-byte lock artifact remains. No PID/name-based stale-owner deletion is used. Existing V2's own directory lock remains unchanged.

Layout:

```text
control-root/
  .scheduler-control.json
  engine/                         # only explicit synthetic harness creates V2 here
  runs/YYYY-MM-DD/
    identity.json
    attempts/<id>.begin.json
    attempts/<id>.final.json
    observer.json
    events.jsonl
    summary.json
    summary.md
  launcher-logs/
```

## 3. Scheduler behavior

`scripts/install_forward_paper_task.ps1` defaults to a mutation-free JSON/XML preview. Its task definition has weekday 09:05 wake-up, `IgnoreNew`, least privilege/interactive-user execution, an explicit project working directory, hidden noninteractive PowerShell and **Enabled=false**. The wrapper resolves the project's venv/interpreter and uses fixed `--mode FORWARD_PAPER`; it preserves child exit status and isolates stdout/stderr under launcher-logs. It never accepts credentials as arguments or dumps environment variables.

Explicit `-Register` is available only for a separately authorized registration step; it refuses a mismatched host timezone and an existing task instead of overwriting it. It still registers a disabled task. No code enables or starts the task, changes host timezone or changes/bypasses execution policy. Actual registration and scheduler invocation were deliberately not exercised. `StartWhenAvailable=false` does not grant permission for missed-session catch-up. Any eventual invocation still uses internal policy, not weekday scheduling as trading-day evidence.

## 4. Calendar authority

The retained `OfflineSessionCalendar.intervals/entry_allowed` remains authoritative. Missing or unverified evidence produces NO_GO; verified holidays/weekends and unsupported special/Muhurat sessions produce clean NO_SESSION. The launcher classifies BEFORE_SESSION, ENTRY_WINDOW, NO_NEW_ENTRIES and AFTER_CLOSE without granting entry authority. Early/late starts do not change engine policy.

**Observed retained policy:** the fixtures' 2026-11-02 execution interval is 09:15–15:15, with the existing derived entry cutoff at 15:10. This code preserves that retained execution policy rather than substituting a nominal 15:30 exchange-close assumption. Tests include both sides of the actual cutoff, 15:15, 15:30 and later. Any reconciliation of retained execution intervals with exchange hours requires a separate evidence/policy review; calendar files were not changed. The calendar's coverage ends at its retained dates; future uncovered days fail closed.

## 5. Paper-only enforcement

Only FORWARD_PAPER is accepted by both config and CLI. `run_scheduled` has no engine constructor, provider/client parameter, callback, arbitrary URL or fixture/provenance override. Its only outcomes are blocked/closed-session operational reporting. Only the private function requiring the exact explicit SYNTHETIC_TEST_DATA fixture schema constructs V2. Real captured data must never be relabeled synthetic or supplied to this harness.

No broker-order implementation was found in the inspected app/scripts boundary; existing Upstox calls are read requests and existing paper classes simulate fills. No such source was modified. Removing the production block requires separately reviewed source/integration work, not a flag or schedule activation. All real-time budget, warmup, quote, ranking and acquisition checks must remain authoritative in that future work. Configured request caps here record intended identity; they create no request budget or permission. Actual current request counts are zero because no request path exists.

## 6. Observer boundaries

`app/research/paper_observer.py` and `scripts/observe_forward_paper.py` read runner publications only. They have no engine, settings/provider, order, fill, risk, position or settlement mutation APIs. Snapshot/event JSON requires a strict schema, unique keys, finite numeric values, allowlisted statuses/reasons and the existing instrument registry read as a literal AST constant without importing its transport dependencies. Unknown fields and arbitrary reason text are rejected; raw provider/error/config text is not emitted.

Reads are bounded to 1 MiB and use read-only file modes, with no repair, directory creation or engine lock. Missing, malformed, oversized or future-dated snapshots return UNAVAILABLE; old snapshots return STALE. JSON and human summaries are supported, plus a bounded recent-event view. Snapshot publication/parsing errors, a crashed reader or disconnected Codex cannot influence engine choices: the runner never consumes observer files or acknowledgements.

## 7. Reason codes

Key fixed codes: V2_TRANSPORT_NOT_REVIEWED, EXPERIMENT_NOT_AUTHORIZED, MODE_NOT_PAPER, CONFIG_INVALID, INSTANCE_BUSY, CALENDAR_UNVERIFIED, NO_SESSION, CONTROL_STATE_INVALID, RECOVERY_REQUIRED, ENGINE_INVARIANT_FAILED, OBSERVER_UNAVAILABLE and INTERRUPTED. CALENDAR_UNVERIFIED is the combined local calendar/client-evidence gate failure; original exception text is not retained.

Existing engine reasons map to ENTRY_WINDOW_CLOSED, INSUFFICIENT_EQUITY, RISK_LIMIT, EXPECTED_NET_PNL_NON_POSITIVE, OBSERVATION_MISSING or a safe ENGINE_REJECTED fallback. This changes presentation only, never the underlying decision. The snapshot exposes proposal symbol/time/BUY/score, actual outcome, safe reason, available equity, required entry cash, recorded expected gross/net target reward and planned cost-inclusive risk where V2 records them. Net-reward/planned-risk ratio is displayed only when those recorded inputs exist and risk is positive.

V2 accepts already selected proposals: upstream candidate rank, actual AI confidence, current quote/depth/age and separate target charges are unavailable and remain null. A proposal score is labeled `proposal_score`, not invented as AI confidence. No explanation claims an upstream rank or freshness measurement that this interface does not record.

## 8. Heartbeat

Observer snapshots record run/attempt IDs, date, PID, timestamps, STARTUP/PREFLIGHT/RECOVERY/PROPOSAL/OBSERVATION/STOPPED phase, safe status/reason, session/cutoffs and shutdown state. The producer reports its alive flag when publishing. The reader returns `process_alive=null` with `LAST_REPORTED_HEARTBEAT_NOT_OS_VERIFIED`, rather than falsely asserting current OS liveness from an old file. Freshness age and last reported flag remain available. No PID-only liveness result is used as ownership or trading authority.

Account fields include eligible paper equity, opening eligible equity, available/reserved cash, open/pending counts, daily entries, planned risk, realized P&L and modeled gross/cost/slippage values from verified V2 state. Maximum drawdown is null because no complete intraday equity series is measured. Market/provider timestamps are null and their counts zero in this preparation scope. Published observer events are diagnostic, not a sealed financial ledger or independently trusted rollback witness.

## 9. Daily reports

Each attempt has an immutable final JSON record, a latest daily `summary.json` and a human headline `summary.md`. Reports identify synthetic fixture evidence versus production NO_GO, startup/shutdown, recovery/duplicate relation, actual outcomes, recorded proposal/rejection counts, rejection reasons, actual settled exits, opening/closing eligible equity, gross/net P&L, modeled total costs/slippage/charges, observer failures, warnings and errors where available. JSON is the detailed machine-readable output; Markdown is a concise human summary.

Configured universe is not passed off as actual opportunity generation. Unavailable upstream opportunity counts, quote/rank/confidence, intraday drawdown and unsupported production metrics stay null. Fixture economic state lives in **one V2 ledger per control root across dates**; daily report folders do not reset capital or duplicate settlements. V2's existing risk, cost and source-bound configuration remains unchanged.

## 10. Failure behavior

Mandatory production gates return NO_GO before any engine/strategy/provider activity; verified closed days return NO_SESSION. Mode/config errors do not create control state. Ownership contention fails immediately. Incompatible control metadata and unfinished prior dates are not silently adopted or normalized. No exception bodies, environment dumps, tokens or raw AI rationale are serialized.

Observer construction/publication errors are caught independently of economic transitions. Existing V2 publication/ledger errors fail closed and retain its evidence for native recovery; uncertain account projections are null rather than asserted successful. Control/observer writes are not a new financial transaction or proof of hardware power-loss durability. KeyboardInterrupt at the CLI produces an explicit INTERRUPTED status/exit 130. Normal blocked invocations exit 2; NO_SESSION exits 0.

## 11. Recovery behavior

Only the explicit offline harness invokes the unchanged V2 constructor/verification recovery, under separate scheduler ownership. It then calls existing `propose/observe` methods; fixture commands later than the enclosing simulated clock are rejected. Interrupted runs resume the same ledger and exact engine identities. Completed same-day duplicates do not apply new commands. Fixture restarts after publication failure use the existing V2 pending intent/anchor recovery; no alternate economic history is synthesized.

If a due expected slot has no fixture observation, the controller explicitly invokes V2's existing `observe(..., observation=None)`. The result becomes OBSERVATION_MISSING, with unresolved exposure/reservation retained and FAILED_CLOSED/RECOVERY_REQUIRED reported. It does not fabricate a fill/exit, discard a position or let ENTRY_PENDING remain silently unreported. Unfinished prior dates block a new daily run. Production cannot open or recover any ledger in this phase; detecting an engine directory reports RECOVERY_REQUIRED without constructing V2.

## 12. Task Scheduler installation instructions

Use a dedicated absolute config path and control root outside historical evidence. The config contains no credentials. An illustrative **offline** config is:

```json
{"schema_version":"scheduled-forward-paper-v1","mode":"FORWARD_PAPER","initial_capital":1000,"instrument_priority":["RELIANCE"],"provider_cap":1,"market_data_cap":10}
```

Capital and caps are explicit values, not a new application default or authorization. They must be separately reviewed for any future experiment.

From PowerShell, previews only:

```powershell
$Repo = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research'
$Config = 'C:\absolute\isolated\paper-config.json'
$Control = 'C:\absolute\isolated\scheduled-paper-control'
& "$Repo\scripts\run_forward_paper_scheduled.ps1" -Config $Config -ControlRoot $Control -Preview
& "$Repo\scripts\install_forward_paper_task.ps1" -Config $Config -ControlRoot $Control
```

The config must exist and the project venv must resolve. Installation preview reports the required India Standard Time timezone and current match; it does not change timezone. Only after a **separate explicit registration authorization**, adding `-Register` to the installer command registers the task disabled. There is deliberately no enable/start command in these artifacts. A later integration review, operational readiness review and explicit experiment authorization are still required before activation. No task was registered/enabled/started in this task. Task registration itself remains untested by design.

## 13. Exact commands

Read-only simulated-clock preflight and observer commands:

```powershell
& "$Repo\.venv\Scripts\python.exe" -B "$Repo\scripts\run_scheduled_forward_paper.py" --mode FORWARD_PAPER --config $Config --control-root $Control --offline-preflight --as-of '2026-11-02T09:05:00+05:30'
& "$Repo\.venv\Scripts\python.exe" -B "$Repo\scripts\observe_forward_paper.py" --snapshot 'C:\absolute\isolated\scheduled-paper-control\runs\2026-11-02\observer.json' --format human
```

`--as-of` is accepted only for read-only preflight, never ordinary scheduled execution. The observer clock option changes freshness inspection only, not engine clocks. The Python entry has no synthetic fixture CLI option, provider opt-in, broker mode or transport parameter.

Validation used the repository `.venv` Python 3.13.2 and the previously inspected offline guard launcher. That launcher disables dotenv loading, clears credential-like environment variables, denies Python outbound sockets/DNS, disables bytecode and pytest cache. Native PowerShell previews required authorized execution outside the sandbox because sandbox execution returned AuthorizationManager failures. The same script invocations were rerun without changing execution policy or routing around that restriction. Python guards are not an OS-wide network sandbox; reviewed PS actions are previews or structurally blocked child checks only.

Exact aliases for recorded verification commands, from the repository root:

```powershell
$Python = "$Repo\.venv\Scripts\python.exe"
$Base = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Audit = "$Base\phase1-v22-audit"
$Run = "$Base\scheduled-forward-paper-infrastructure"
& $Python -B "$Audit\run_offline.py" tests/test_scheduled_forward_paper.py tests/test_paper_observer.py tests/test_forward_paper_scheduler_scripts.py -q -p no:cacheprovider --basetemp "$Run\focused-final-tmp" --junitxml "$Run\focused-final.xml" --tb=short
& $Python -B "$Run\run_full.py"
# Full capture driver executes:
& $Python -B "$Audit\run_offline.py" -q -p no:cacheprovider --basetemp "$Run\full-tmp" --junitxml "$Run\full.xml"
```

## 14. Tests

Baseline is 1,869 independently verified cases. The new focused suite passed **69 cases**, 61.64s console / 61.631s JUnit, with zero failures/errors/skips/deselections. The Windows-specific subset passed **14 cases**, 8.15s console / 8.155s JUnit; this includes native PS parsing, mutation-free previews, fixed-mode/absolute-path validation, disabled XML and an actual blocked child returning exit 2 with isolated logs.

The tests cover simulated 08:59/09:05/09:10/09:15/midday, both sides of the actual retained cutoff, 15:15/15:30/after-close, verified holiday/weekend/Muhurat, unknown future calendar, strict mode/config, global ownership contention and actual child-process death, run/attempt identity, duplicate launches, separate daily reports on one continuing V2 ledger, interrupted entry/open/monitor/publication, explicit missing observations, final candle, stale/wrong-symbol/bad-OHLC rejection, observer construction/publication failures, read-only readers, malformed/stale/secret-bearing snapshots and safe event reads.

Test-first failures were retained as evidence: 39 initial cases failed before the modules existed; three added invariants failed before dedicated-root/future-clock/daily-equity fixes. Two initial clock expectations assumed a nominal close instead of the existing retained interval and were corrected to that authoritative policy, with additional 15:30 coverage retained. A sandbox combined run reported 65 passed/2 failed solely at native PS AuthorizationManager; approved execution subsequently passed. No assertion was weakened to hide a production defect, and no test was skipped or xfailed.

A fresh read-only reviewer inspected the nine implementation/test files after implementation, specifically checking paper-only bypasses, duplicate sessions, calendar gates, recovery, observer mutation/secrets and unfinished lifecycles. It reported **no confirmed actionable finding** within the selected preparation scope. It did not execute tests and is not represented as independent runtime certification.

The complete fresh unfiltered offline suite passed **1,938 tests**, with **0 failures, 0 errors, 0 skips and 0 deselections**, exit **0**. Console duration: **1,030.14 seconds**; JUnit duration: **1,029.963 seconds**; capture-driver wall time: **1,032.4872766000335 seconds**. Execution began `2026-11-11T16:27:03.393704+00:00` and ended `2026-11-11T16:44:15.882300+00:00`. This is the 1,869-test baseline plus 69 new cases. No path, keyword or marker selection was applied to the full run. Final XML test-case totals and process metadata were checked separately; focused/full outputs are in `verification-results.json`, `full-metadata.json`, `full.log` and the XML files under `$Run`. The full run includes the existing V2/V2.1/V2.2 settlement/recovery/seal/accounting regressions without changing their implementation. These are freshly executed local results, not independent runtime certification by the source reviewer. No experiment output is used as historical trading evidence.

### File preservation and change inventory

The starting manifest contained 640 inventoried files and matched the preceding independently verified state. Its SHA-256 is `1232698381e796efb6e6d0668fac1217e5baf603ee39b65d628564a15379e21c`. Verification compares every original path and byte hash, not a clean-Git-tree assumption: the repository already contained prior work. All 640 remain unchanged, including 22 Sessions 02–05 files and five real log/database artifacts. Original V2/V2.2 settlement, strategy, risk/cost/calendar, broker boundary, CSV/candle fixes and historical audit reports are unchanged. Git HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`.

Only these 11 files are new:

- Production modules: `app/research/scheduled_forward_paper.py`, `app/research/paper_observer.py`.
- Python entries: `scripts/run_scheduled_forward_paper.py`, `scripts/observe_forward_paper.py`.
- Windows artifacts: `scripts/run_forward_paper_scheduled.ps1`, `scripts/install_forward_paper_task.ps1`.
- Tests: `tests/test_scheduled_forward_paper.py`, `tests/test_paper_observer.py`, `tests/test_forward_paper_scheduler_scripts.py`.
- Documentation: this file and `docs/superpowers/plans/2026-11-11-scheduled-forward-paper.md`.

`$Run/before.json`, `after.json` and `preservation.json` record the comparison, protected source hashes and exact added paths. Inventory covers root files except `.env`, and app/scripts/tests/docs/data/backtests/experiments/reports/diagnostics/logs/schemas; it excludes bytecode/cache. It is not a claim about excluded paths or a Git commit. No historical evidence was rewritten. Temporary fixtures/test reports are isolated outside the repository. The final manifest is regenerated after documentation completion.

## 15. Limitations

Reviewed real-data integration into V2.2 remains absent by explicit user choice. Profitability, real-market robustness, physical power-loss durability and universal spreadsheet behavior remain unproven. Matching older local database+anchor rollback still needs an independently trusted witness. Existing source-bound ledger compatibility is not migrated or weakened.

## 16. Authorization status

Verdict: **INFRASTRUCTURE READY FOR INDEPENDENT AUDIT**, within the explicitly selected blocked-production preparation scope. Production has no reviewed V2 transport binding and cannot start the economic engine. No provider/market/broker request, task registration, scheduled execution, real-time paper session or trading experiment was performed. Offline test subprocesses and native scheduler previews are not trading sessions. No next phase begins automatically. Production transport integration, operational readiness and any experiment require separate review and authorization.

**INFRASTRUCTURE READY FOR INDEPENDENT AUDIT. Scheduled NSE forward-paper execution and read-only Trading Hours Observer Mode are implemented and offline-tested. The Windows schedule has not been activated. No real-time paper experiment has been run. Broker execution remains disabled. Profitability and real-market readiness remain unproven.**
