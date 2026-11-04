# Existing Runner Inspection

Review date: 2026-11-02. This task used local evidence only. No Upstox, DeepSeek,
NSE or other external requests were performed.

The existing request-capable research entry points are:

| Route | Actual request location | Mandatory integration |
| --- | --- | --- |
| `capture_upstox_snapshot` and its unlocked implementation | `get_intraday_candles` | Before market-data budget construction/reservation; validate response before writing a snapshot |
| `record_prospective_snapshot` and its unlocked implementation | `provider.complete` for an eligible frozen baseline candidate | After existing snapshot validation, before experiment/budget processing; recheck immediately before reservation |
| `run_provider_smoke_test` and its unlocked implementation | Non-trading `provider.complete` | After snapshot validation, before budget processing; recheck immediately before reservation |
| `scripts/run_deepseek_prospective_research.py` | Delegates to those functions | Also checks intended downstream provider cap before combined Upstox capture |

`run_ai_led_offline.py`, `run_offline_batch`, and replay remain saved-data-only.
`run_offline_batch` requires the exact `SavedResponseProvider` class, rejecting
live providers and subclasses. Its existing complete-universe, aligned timestamp,
deterministic proposal ranking and shared-account logic were reused unchanged.

The repository also contains separate network-capable utilities: the legacy V1
`app/main.py` -> `app/paper_session.py::run` scans stock/index candles and quotes;
`app/ai_engine.py::analyze_market` is a generic AI client; the historical evaluator,
synthetic provider connectivity utility and historical downloader have their own
entry points. They do not delegate through this prospective research workflow.
**These are not certified by this task's experiment-bound gate.** Direct SDK/client
calls are not a repository-wide network firewall. Do not substitute those routes
for the guarded experiment. Extending immutable experiment budgets to the legacy
multi-stock/index scanner requires a separate adapter, rather than modifying its
account or strategy as part of this change.

The pre-existing dirty/untracked Git state was inspected and retained. Task diffs
were reviewed against saved task-start copies, because the prospective files were
already untracked. No historical-live evaluation was run.

# Pre-Request Validation Design

Added `app/research/prospective_preflight.py`. It has no transport, order or budget
reservation capability. Each request-capable research path invokes it internally;
calling a public wrapper or its unlocked implementation cannot omit the gate.

Preflight revalidates:

1. Explicit paper mode, timezone-aware request clock and supported calendar entry interval.
2. Exact current client bytes and the existing versioned attestation's full consistency checks.
3. The pinned v3 calendar and its underlying source, receipt and archive-member evidence checks.
4. A versioned immutable request policy: capital, model, singleton instrument mapping/priority, calendar/attestation pins and all three request caps.
5. The policy's canonical SHA-256 against the initial experiment manifest, the saved experiment specification and current source hashes.
6. Existing persistent budget configurations and ledger identities without reserving or rewriting them.

Preflight returns fixed safe errors, never arbitrary exception text. Neither a file's
existence nor a previous successful preflight is a reusable authorization token.
Every entry/restart validates again, and provider routes recheck at reservation.
Ordinary preflight failures do not construct a request budget. A late recheck
failure can leave an empty budget configuration but cannot reserve a request.
Existing budget exhaustion still uses `PersistentRequestBudget.reserve`, retaining
its established diagnostic/accounting behavior.

Explicit policy initialization accepts a fresh output only. Repeated initialization
must match. It writes configuration, never requests or budget reservations. A
partially interrupted initialization fails closed; use a fresh directory rather
than deleting or repairing historical artifacts to get past it.

The frozen baseline runner consumes one snapshot per decision. Its policy requires
exactly one instrument and `single_configured_instrument` priority. A multi-stock
policy is rejected; there is no claim that sequential single-stock calls implement
an AI-led complete batch. Multi-stock live collection remains a separate step.

# Files Changed

Only these seven project files were added or changed during this task:

| File | Change |
| --- | --- |
| `app/research/prospective_preflight.py` | New offline policy/evidence/budget/snapshot validators |
| `app/research/deepseek_prospective.py` | Mandatory research gates; optional policy hash at fresh manifest creation; gate source hashes; existing AI account checks moved before provider use |
| `scripts/check_prospective_preflight.py` | New offline-only explicit initialization/check CLI |
| `scripts/run_deepseek_prospective_research.py` | Combined-command provider-cap preflight; safe BLOCKED handling for preflight errors |
| `tests/test_prospective_preflight.py` | 51 new real-gate, mocked-transport integration cases |
| `tests/test_deepseek_prospective.py` | Explicit gate mocks at legacy lifecycle/diagnostic unit boundary |
| `docs/PROSPECTIVE_REQUEST_PREFLIGHT_REPORT.md` | This implementation and verification report |

The existing prospective unit fixtures intentionally contain legacy directories and
synthetic incomplete candle times. Their request-gate boundary is mocked explicitly;
their account, cost, quota, rejection, settlement and diagnostic assertions remain
active. The new integration suite exercises the actual unpatched gates and all
request-capable research routes. No assertion was removed or test excluded.

# Calendar Enforcement

Reused the existing pinned v3 manifest and validator. Coverage remains
2023-12-01 through 2026-12-31, with explicit Asia/Kolkata session interpretation.
Weekends, holidays, unsupported dates/times, naive clocks and unsupported Muhurat
execution fail closed. The dated CAS common continuous-session cutoff remains
15:15 through 2026-12-06 and 15:30 from 2026-12-07. The existing entry-window rule
also reserves time for the next complete five-minute entry candle.

After capture and before DeepSeek use, the additional request-only gate requires
completed session-valid candles, 35 contiguous same-day recent bars, the configured
instrument identity, current-session timestamp consistency and existing 900-second
freshness bounds. Original candle parsing/filtering, indicators, data-quality
checks and frozen thresholds were not changed. Invalid responses are rejected
before successful snapshot/capture-event writing.

Calendar evidence remains the reviewed **as-of-2026-10-30** set. This task did not
check for later exchange notices. A later operational deployment needs its own
evidence-currency review; passing integrity checks does not establish that no
subsequent exchange amendment exists.

# Client Attestation Enforcement

Reused `verify_client_attestation` on every preflight. It checks the pinned
attestation, historical audit/snapshot, working current source versus reviewed
snapshot, source hashes/newlines, retained diff and endpoint/retry/request AST
invariants. Current-client SHA-256 remains
`27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb`.

No client, source snapshot, attestation, old audit, official evidence or calendar
artifact was edited. Attestation remains scoped source-byte evidence, not trading
approval or proof of historical dataset acquisition provenance.

# API Request Safety

All interactions during verification used mocked providers/responses. The full
final pytest process additionally denied socket connection operations before
loading tests. Invalid policy/evidence/session/configuration tests assert zero
Upstox calls, zero provider calls and unchanged budget files.

An Upstox attempt still reserves immediately before the existing client call. A
post-request schema/candle/session failure keeps that reservation consumed. A
failed response cannot produce a successful snapshot or DeepSeek request. Caps,
retry behavior and request identities were not reset or increased.

The AI-assisted arm's existing open-position, daily quota/loss and cost checks now
run before provider use. They use the same values and functions as previously;
ineligible account state skips the request. Baseline generation, maximum quantity,
actual-entry revalidation and accepted-trade settlement remain unchanged.

The provider smoke route stays non-trading. It validates the snapshot/session and
its separate cap, and cannot create a paper candidate, decision or order. Trade
risk/cost gates apply to candidate decision collection, not to interpreting a
non-trading connectivity acknowledgement as a trade.

# AI-Led Integration

No live AI-led adapter or new prediction feature was introduced. The smallest next
adapter should acquire the frozen complete instrument universe through guarded
budgeted capture, validate the whole batch and common candidate timestamp before
any DeepSeek request, and preserve point-in-time request/response lineage. It must
reuse the existing deterministic proposal processing and mandatory independent
RiskEngine, cost, cash and account transitions. The saved-only runner's exact
provider restriction must not simply be removed to admit a network client.

# Experiment Capital Handling

Capital remains configurable. Tests initialize independent policies/manifests at
INR 1,000, 2,500, 10,000 and 100,000. Initial capital and all request-policy fields
are frozen together. Capital, cap, model, priority and instrument changes fail
closed. Existing experiment specifications now include the gate and validator
source hashes for fresh experiments. Existing experiments are not migrated.

The current capital constraint still requires explicit `--paper-capital 1000`.
RiskEngine sizing, 1% trade risk, 2% daily loss, available cash, buy-side
affordability, round-trip cost risk and accepted-entry controls remain intact.
The new gate never increases a quantity. Prior ledgers remain readable and
summarizable, but old request-capable sessions without the new frozen policy
cannot silently resume requests under the new source version.

# Tests Added

The 51 new cases cover valid evidence/configurable capital; changed client bytes;
missing/corrupt attestation; modified calendar/source evidence; out-of-coverage,
holiday, weekend, unsupported/naive clocks and Muhurat; dated CAS cutoffs; invalid
policy/capital/instrument/priority/model/pins/specification/budget configuration;
all public/unlocked research entry points; combined CLI, snapshot CLI and smoke
CLI; zero-request/zero-reservation failures; incomplete/missing/overnight/stale/
invalid-identity/OHLCV snapshots; spent reservation retention; unchanged exhaustion
classification; valid mocked capture/provider/smoke; restart/idempotent replay;
immutable experiment configuration; legacy output preservation; AI account checks
before provider use; offline-only CLI behavior and broker-disablement flags.

# Verification Results

Final full regression: **370 passed in 76.78 seconds**, exit 0, no exclusions,
xfails or unresolved failures. This includes 51 new integration tests plus all
319 previous regressions, including the previously failing provenance assertion.

Exact final command (PowerShell, repository root):

```powershell
@'
import socket,pytest

def denied(*a, **k):
    raise AssertionError('Network access forbidden during offline verification')
socket.socket.connect=denied
socket.create_connection=denied
raise SystemExit(pytest.main(['tests/test_prospective_preflight.py','tests/test_readiness_evidence.py','tests/test_offline_market_data_v1.py','tests/test_ai_led_replay.py','tests/test_ai_led_offline.py','tests/test_deepseek_prospective.py','tests/test_deepseek_prospective_summary.py','tests/test_cost_model_execution_integrity.py','tests/test_core.py','tests/test_deepseek_eval_v1.py','tests/test_ai_v2.py','tests/test_ai_v2_evaluation.py','tests/test_provenance_audit.py','-q','-p','no:cacheprovider','--basetemp',r'C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-prospective-preflight-final']))
'@ | .venv\Scripts\python.exe -B -
```

Earlier development results: 117 passed, then 120 passed as coverage expanded.
The first broad run reported 368 passed and two failures in new CLI tests because
their CLI clock used today's date against the October fixture. The test clock was
corrected, preserving freshness enforcement; the complete final run above passed.
These earlier runs are not additional independent test counts.

In-memory Python compilation passed for all six affected `.py` files, without
writing bytecode. Targeted Git whitespace checks passed for all seven files.
Task diffs and the full new files were inspected; no strategy, RiskEngine,
cost-model, retry, order-placement or historical-provenance implementation changed.

SHA-256 preservation checks verified **all 359 original files** under `backtests`,
`data` and `reports`, including all **22 Session 02-05 files and budgets**, unchanged.
Session 06 does not exist. All tests used disposable scratch outside the project.

# Remaining Risks

- This is application-entry-point enforcement, not protection against arbitrary
  direct SDK calls, malicious in-process monkeypatching or filesystem modification
  between validation and use. Protect reviewed deployments from concurrent edits.
- Legacy network utilities listed above remain outside this experiment adapter;
  do not use them as alternate prospective launch paths.
- AI-led live multi-stock collection, complete-batch budget orchestration and
  authenticated prospective decision evidence remain unimplemented.
- Calendar evidence is pinned to its review date, and expiry beyond 2026-12-31
  fails closed. Later exchange amendments require review, not guessed sessions.
- Interrupted initialization requires a fresh directory. No automatic artifact
  repair, budget reset or retroactive policy adoption was added.
- Historical dataset provenance remains unresolved. Mock responses prove pipeline
  behavior, not predictive performance or profitability. Stops and estimated costs
  do not guarantee a maximum realized loss through gaps or execution uncertainty.

# DeepSeek Prospective Paper Readiness

**GO for offline verification of the guarded single-instrument frozen research
workflow. NO-GO for unattended AI-led prospective trading.** The task did not
authorize or perform a real provider call. The AI-led runner is still deliberately
saved-response-only; a distinct guarded live batch adapter is required.

# Broker Sandbox Readiness

**NO-GO.** No order placement, live balance fetching, broker reconciliation,
partial-fill handling or executor was added or enabled. Every affected report and
policy retains `trade_placement_enabled: false`.

# Recommended Next Step

Review this diff and run offline preflight on a separately selected fresh output.
The offline-only CLI can explicitly initialize a frozen policy with
`--initialize --paper-capital 1000 --instrument-priority RELIANCE` and matching
market/provider caps. `--as-of` is an offline check clock, not permission to use
old market snapshots for a live request. No such experiment was initialized in
the project during this task.

For the AI-led objective, implement and mock-test the separate full-batch
prospective collector described above before considering any separately
authorized, tightly budgeted regular-session paper capture. Do not start Session
06, run legacy network utilities or enable broker orders as a side effect.
