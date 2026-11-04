# Existing Components Reused

Review date: 2026-11-02. This implementation used saved synthetic fixtures and
exact mock transport classes only. No external APIs, provider tokens, current
market data, Session 06 or broker orders were used.

Reused `ai_led_offline.py` for configuration, complete batch parsing, independent
proposal validation, deterministic ranking, shared-account state, RiskEngine
sizing, cost checks, actual-entry validation, rejection and settlement. Its public
`run_offline_batch` still accepts only the exact `SavedResponseProvider` class.

Reused the current prospective preflight's `_evidence`, `_budget_config` and
`validate_request_snapshot`, the full client attestation/calendar validators,
`PersistentRequestBudget`, `AppendOnlyJsonl` and the existing experiment lock.
`ProviderResult` supplies the existing provider response contract. The frozen
quantitative research runner, live SDK adapter and Upstox client were not changed.

The working tree already contained substantial changes and untracked research
files. Its tracked diff was saved before work and verified unchanged afterward.
Source/report hashes were captured before implementation to distinguish this
task's changes from earlier work.

# Adapter Architecture

Added an isolated adapter in `app/research/ai_led_guarded.py`:

```text
explicit instrument statuses OR simulated per-stock local capture
  -> paper mode + attestation + calendar + immutable configuration + budget checks
  -> every configured stock READY + complete validated point-in-time batch
  -> immutable normalized input copies + batch intent + exact provider request
  -> preview existing account settlement/restrictions
  -> persistent reservation -> exact mock provider -> untrusted response validation
  -> saved validated response -> existing AI-led paper engine
  -> atomic account state -> immutable batch evidence
```

Two entry points are available:

- `run_mock_batch`: supplied local snapshot statuses; no market-data reservation.
- `capture_mock_batch`: simulate acquiring each configured stock from local files;
  reserve a persistent market-data attempt before each exact mock client call.

Only exact `MockDecisionProvider` and `MockMarketDataClient` instances are accepted
at their applicable boundaries. Live SDK clients, arbitrary provider callbacks and
subclasses are rejected. The CLI exposes no live-provider, order or balance mode.
This supplies a testable future adapter contract without enabling network access.

# Files Changed

| File | Change and problem solved |
| --- | --- |
| `app/research/ai_led_guarded.py` | New complete-batch guard, immutable experiment/evidence, simulated budgets, proposal envelope, recovery and summary |
| `scripts/run_ai_led_guarded_mock.py` | New explicit offline-only multi-stock interface, including simulated capture |
| `tests/test_ai_led_guarded.py` | 65 new offline tests for guards, accounting integration, evidence and restart behavior |
| `app/research/ai_led_offline.py` | Startup check now permits its own `.prospective.lock` file in an otherwise empty account directory, matching the existing state loader |
| `docs/AI_LED_GUARDED_MULTI_STOCK_ADAPTER_REPORT.md` | This report |

The existing engine change is one condition, formatted over two lines. The first
crash-recovery test exposed that a failed first commit left a lock file that the
startup check rejected on restart, although `_load_state` already allowed it. The
fix permits that exact lock filename; all other unknown initial files still block
adoption. It does not change account arithmetic, selection or risk rules.

# Multi-Stock Batch Validation

The frozen completeness policy is `ALL_CONFIGURED_INSTRUMENTS_READY`. Every stock
needs an explicit status; missing, extra, failed, duplicate or incorrectly mapped
instruments block the whole batch. The adapter does not silently drop a stock or
ask AI to rank a partial universe.

Existing validators enforce schema, configured instrument identity, aware
timestamps, chronological/unique five-minute candles, finite OHLCV, completed
candles, current-session timestamps, 900-second freshness, 35 contiguous recent
same-session candles and the existing turnover/positive-volume requirement. All
stocks must share the same latest candidate timestamp.

The verified calendar and dated CAS entry cutoffs remain mandatory. Unsupported
Muhurat execution, holidays, weekends, unsupported phases and dates outside
coverage fail closed. The pinned calendar covers 2023-12-01 through 2026-12-31;
its evidence review remains as of 2026-10-30. No later exchange notices were fetched.

Configured priority determines iteration and tie breaking, independent of snapshot
submission order. Batch identity hashes the common candidate timestamp and frozen
priority universe. Revised data for an existing timestamp is rejected. Current
input hashes and original observation timestamps are retained per instrument.

Before provider use, normalized snapshots are saved under SHA-256 filenames.
Arbitrary source labels/extra source metadata are not copied into these files.
The source-byte hashes remain in validation/capture evidence. The provider and
account use the same frozen copies; changing an original input during a mock call
cannot change the accepted decision. A changed frozen copy blocks processing.

Invalid/incomplete batches receive an idempotent `batch_failures.jsonl` entry with
the configured universe, explicit safe status for every stock, a fixed reason
code, timestamp and disabled-order flag. No raw candle values, arbitrary messages
or response bodies are placed in these diagnostic failure records.

# DeepSeek Flash Decision Flow

The mock request contains the full validated universe at one timestamp. It uses an
AI-led prompt, without the frozen reversal predicate. AI may propose independent
BUY opportunities or NO_TRADE. The declared model is `deepseek-flash`; no real
provider model identity or availability was tested.

The untrusted response envelope requires exactly:

- Adapter schema version and matching timezone-aware candidate timestamp.
- BUY or NO_TRADE with an internally consistent proposal list.
- Per-proposal symbol, score, entry, stop, target and holding bars.
- Structured rationale codes at batch/proposal levels from `trend`, `momentum`,
  `volume`, `risk_reward`, `no_edge`.

Duplicate JSON keys, extra fields (including executable quantity), unknown symbols,
wrong timestamps, non-finite values, invalid entry/stop/target relationships and
arbitrary rationale text are rejected. The existing parser enforces its current
price/stop/target/holding bounds. Ranking remains score descending, then configured
instrument priority; the first independently eligible proposal is selected.

Validated proposals are converted to the original saved-response schema and passed
through `run_offline_batch`. Raw provider text and arbitrary exception messages are
never saved. Evidence retains a response hash, validated numeric proposals and
allowlisted rationale codes. Invalid responses and mock failures become NO_TRADE
with fixed safe categories while retaining the consumed reservation.

# Risk and Capital Controls

Each output has an immutable `guarded_experiment.json` containing initial capital,
universe/priority, model, completeness policy, costs, RiskEngine configuration,
source/prompt hashes, calendar/attestation identities and both request caps.
Resume compares the expected configuration; capital, caps or source/version
changes cannot silently alter an existing experiment. A nonempty unrelated or
historical directory cannot be adopted.

Tests use independent INR 1,000, 2,500, 10,000 and 100,000 accounts. There is no
INR 1,000 global maximum. The user's current experiment still requires explicit
`--paper-capital 1000` on the new CLI.

Before requesting proposals, a copy of the existing account is advanced through
available settlement candles using the original settlement routine. Open/pending
positions, daily restrictions, invalid account state or unavailable cash block
provider use. This preview never substitutes a second accounting system; the
original engine performs the actual atomic state transition afterward.

After proposals, RiskEngine alone determines whole-share quantity from usable
cash. The existing gate checks entry notional plus buy costs, planned stop loss
plus round-trip costs, 1% per-trade risk and affordability. AI cannot supply or
increase quantity. Daily loss and position/quota rules remain unchanged: 2% daily
loss threshold, one position and three accepted trades per day under the existing
RiskConfig. Actual entries are revalidated by the original engine.

The existing conservative account rule remains: usable funding is the lower of
initial capital and realized balance. Losses reduce available funding; gains
increase account balance but do not automatically compound position funding.
This task did not change that rule or the frozen quantitative comparison strategy.

# Provider and Market-Data Budgets

Both budgets use the existing persistent reservation implementation in a new
isolated output. All reservations in this task are **simulated mock attempts**;
they did not spend an Upstox request or DeepSeek token.

Full preflight checks run before any mock capture and again before a provider
reservation. Invalid configuration/evidence/session/account state causes zero
transport calls and zero new reservations. A partial capture, failed response,
invalid proposal or storage error never refunds a consumed reservation.

Each capture has a timestamp/universe/instrument request identity and immutable
status record. All configured stocks receive a status, including failures and
cap exhaustion. Successful captures and cached capture metadata are revalidated
before reuse. Failed partial batches cannot reach the provider.

Exact request payloads and identities are persisted before provider reservation.
A saved validated response can resume without another call. A consumed reservation
without a saved result is never retried: it resolves as NO_TRADE with
`reserved_without_result`. Existing caps cannot be increased on restart.

No global shared cache or budget is used. The old research output directories and
their budgets are untouched. Reservations count logical client calls; the existing
Upstox retry contract was neither changed nor invoked.

# Paper Account Lifecycle

The original account engine remains authoritative:

```text
validated proposal -> PENDING
  -> actual-entry accepted -> OPEN -> stop/target/horizon/session exit -> SETTLED
  -> actual-entry rejected -> no position; rejected entry does not consume quota
```

Its existing expected-entry timing, gap handling, stop precedence, maximum holding
period, fees/slippage and realized net P&L remain intact. Both positive and negative
synthetic outcomes are tested. Continuing a pending/open account does not collect
another proposal unless settlement/rejection clears the applicable restriction.

Persistence uses the existing process lock plus atomic, fsynced immutable evidence
publication and the original atomic account commit. Budget reservation, response,
account and final batch record are separate checkpoints, rather than one database
transaction. Recovery tests cover failure before account commit, failure after
account commit but before batch publication, and an interrupted provider call.
Replaying a completed batch cannot create another decision, trade or settlement.

`summarize_guarded` reports accepted/rejected/completed trades, pending/open state,
gross P&L, costs, net P&L, balance and usable/available cash from the validated
account. Repeated summaries do not settle positions or consume budgets. No real
AI performance metrics are inferred from these mock scenarios.

# Tests and Verification Results

Final full regression: **435 passed in 68.24 seconds**, exit 0. Zero failures,
exclusions or xfails: 65 new adapter tests plus all 370 prior regressions,
including calendar, attestation, provenance and frozen quantitative coverage.

The 65 new cases cover complete batches; missing/failed/invalid instruments;
submission-order independence; discovery without the frozen predicate; malformed,
non-finite, secret-bearing and wrong-timestamp AI output; NO_TRADE; simulated
provider failure/caps; Upstox preflight and partial capture accounting; calendar
and attestation corruption; zero-call failures; RiskEngine/cost/zero-share
rejections; gains/losses and available cash; immutable capital above INR 1,000;
pending-position restrictions; actual-entry rejection and later candidates;
duplicate/resume behavior; missing/corrupt accounts; malformed cached metadata;
input changes during calls; crash boundaries; summaries; CLI mock-only execution;
broker disablement and preservation of historical sessions.

Exact final regression command (PowerShell, project root):

```powershell
@'
import socket,pytest

def denied(*a, **k):
    raise AssertionError('Network forbidden during guarded adapter verification')
socket.socket.connect=denied
socket.create_connection=denied
raise SystemExit(pytest.main(['tests/test_ai_led_guarded.py','tests/test_prospective_preflight.py','tests/test_readiness_evidence.py','tests/test_offline_market_data_v1.py','tests/test_ai_led_replay.py','tests/test_ai_led_offline.py','tests/test_deepseek_prospective.py','tests/test_deepseek_prospective_summary.py','tests/test_cost_model_execution_integrity.py','tests/test_core.py','tests/test_deepseek_eval_v1.py','tests/test_ai_v2.py','tests/test_ai_v2_evaluation.py','tests/test_provenance_audit.py','-q','-p','no:cacheprovider','--basetemp',r'C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-guarded-multistock-final']))
'@ | .venv\Scripts\python.exe -B -
```

Earlier runs: the first 52 adapter cases yielded 51 passes and the lock-only
restart failure described above. After that fix and expanded recovery coverage,
131 focused adapter/account/replay tests passed. An earlier full run passed 435
tests in 86.01 seconds; the final rerun also covers the later safe validation
failure ledger. Earlier counts overlap and are not additional independent tests.

All four affected Python files passed in-memory compilation. Targeted whitespace
checks cover all five files, using task-start bytes for the existing file and an
empty baseline for new files. The final task diff and new file contents were
reviewed. The prior tracked Git diff remains byte-for-byte unchanged.

All **359 original evidence files** under `backtests`, `data` and `reports`,
including all **22 Session 02-05 files and budgets**, remain SHA-256 unchanged.
The only changed pre-existing source/report file is the startup condition in
`ai_led_offline.py`. No Session 06 directory exists. All generated test accounts,
captures and evidence stayed in disposable scratch outside the repository.

# Remaining Limitations

- The adapter is intentionally mock-only. No real provider collection, Upstox
  authentication, broker cash fetching or order executor was enabled or tested.
- Saved market-data hashes and local checksums establish input consistency, not
  independent provider authenticity. All AI responses here are synthetic mocks.
- Fixed rationale codes are supported; unrestricted narrative text is rejected.
- The completeness policy supports all-ready batches only. No partial-universe
  fallback or scheduler was added. Existing liquidity checks do not establish
  executable depth or real fills.
- Current calendar evidence is reviewed as of 2026-10-30. Later amendments and
  real model-ID availability need verification before operational use.
- Cooperative process locking and process-crash recovery were tested. Hostile
  filesystem edits, storage power loss, broker reconciliation and distributed
  execution are outside this prototype's assurance.
- Source hashes deliberately block silent upgrades of old experiments. Use a new
  versioned output for a changed implementation; do not rewrite old manifests.
- Stops, estimated costs and realized-loss gates do not guarantee a maximum loss
  through gaps or execution uncertainty. Mock samples cannot prove profitability.
- Separate legacy V1/network utilities remain outside this adapter. They must not
  be used to bypass its experiment-bound guards.

# Readiness for Controlled DeepSeek Paper Trial

**GO for guarded offline mock validation. NO-GO for an actual DeepSeek paper
trial in this version.** The complete-batch/account/evidence integration is now
implemented and tested, but exact mock classes deliberately prevent real network
clients. A real trial requires a separately reviewed transport binding, trusted
current clock, fresh versioned experiment, current evidence review and explicit
request authorization. Removing the exact-type checks alone is not that binding.

# Broker Sandbox Readiness

**NO-GO.** No orders, real balances, partial fills, broker reconciliation or live
risk execution were introduced. Every affected mode/report keeps broker placement
disabled. This is not permission for real-money trading.

# Recommended Next Step

Perform a read-only security review of the adapter and its crash/restart evidence.
Then, as a separate task, bind reviewed real read-only Upstox and bounded DeepSeek
transports to these guards and mock-test the same failure scenarios before any
separately authorized prospective paper request. Preserve the saved-only runner,
frozen quantitative comparison, independent account/risk/cost checks and original
experiments. Do not start Session 06 or use current market data as part of this
completed offline task.
