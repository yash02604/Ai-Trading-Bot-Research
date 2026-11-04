# 1. Existing transport infrastructure

Implementation date: 2026-11-02. All verification used mocked HTTP/SDK responses
with socket connections denied. **No real Upstox, DeepSeek or NSE requests were
made, no provider tokens were spent, and no current market data was captured.**

Reused the existing authenticated read-only Upstox functions
`get_intraday_candles` and `get_quote`, the existing bounded `DeepSeekProvider`,
the pinned client attestation and calendar validators, persistent request budgets,
append-only ledgers and experiment locking. Reused the existing candle/quote
parsers and complete-batch validators. The original AI-led account engine remains
responsible for deterministic ranking, RiskEngine sizing, costs, actual entry,
rejection, settlement and shared cash. Its saved-only provider restriction remains.

The new binding extends the guarded adapter through a separate reviewed policy;
it does not replace accounting or add a second strategy. The mock entry points
retain their exact mock-type checks and remain usable offline.

# 2. Files changed

| File | Change |
| --- | --- |
| `app/research/ai_led_reviewed.py` | New opt-in read-only transport binding, trusted runtime clock, send guards, quote validation and sanitized transport evidence |
| `app/research/ai_led_guarded.py` | Separate prospective schema/policy/evidence mode, shared capture helper and reviewed binding scope; existing mock behavior retained |
| `scripts/run_ai_led_prospective_paper.py` | New default-disabled paper CLI and request-free preflight option |
| `tests/test_ai_led_reviewed.py` | 45 new offline transport/boundary tests |
| `docs/REVIEWED_PAPER_TRANSPORTS_REPORT.md` | This report |

Only `ai_led_guarded.py` changed among pre-existing source/report files. The
Upstox client, DeepSeek provider, settings/.env, candle parser, cost model,
RiskEngine, original quantitative strategy, offline account engine and historical
provenance code were not edited. All prior working-tree changes were preserved.

# 3. Upstox adapter behavior

For a frozen configured universe, the adapter selects the most recent completed
five-minute candle timestamp from the real UTC clock and acquires each instrument
in configured priority order. It requests only fixed, read-only existing endpoints;
there is no user-supplied URL, broker funds, order, modification or cancellation
interface.

Before a send, the binding rechecks paper mode, credentials' presence, configured
model/timeout, current source attestation, verified session, immutable experiment,
account integrity and persistent budget configuration. It checks that 35 completed
same-session bars could exist before spending a market-data reservation. Holidays,
unsupported Muhurat execution, insufficient warmup and unsupported calendar phases
or coverage block requests.

Responses require the successful response envelope, a candle list, aware valid
timestamps and valid OHLCV. The existing parsers are reused; invalid OHLCV rows
are rejected before the existing completed-candle filtering routine is used.
Current incomplete bars and bars newer than the batch's frozen candidate are not
passed to AI. All configured instruments must then pass the existing complete,
aligned, fresh, contiguous batch and turnover checks. No partial-universe fallback
or stale-data relaxation was added.

When a candle response echoes an instrument key, it must match the requested key.
The standard candle response can omit that echo: its identity is bound to the
attested request endpoint, not independently authenticated from candle values.
Raw response bytes/bodies are not retained; receipts keep only hashes/statuses.
Normalized validated market inputs are saved as immutable decision evidence.

`--include-quotes` requires one additional separately reserved quote per stock.
Its instrument token/key must match, its explicit provider timestamp must be fresh
within five seconds, and usable bid/ask depth must exist. Normalized quote context
is added to the AI request; malformed, extra, stale or non-finite cached quote
metadata fails closed before the provider request. The existing parser correctly
labels the timestamp as response-generated: an independent order-book timestamp
is not exposed. Quotes are evidence, not guaranteed executable fills or liquidity.

The existing Upstox retry contract is untouched: at most two retries and configured
per-attempt request timeout. Timeouts are capped at 20 seconds by this binding.
Authentication, rate-limit, HTTP, JSON and transport failures produce fixed safe
diagnostic labels/numeric status where available. Failed captures retain their
consumed reservations and cannot reach DeepSeek.

# 4. DeepSeek adapter behavior

Uses the existing settings and `DeepSeekProvider`; `DEEPSEEK_MODEL` must currently
be `deepseek-flash`. API keys are loaded through existing settings and never
included in request evidence, logs, hashes of settings or commands. The provider
keeps zero automatic retries, its bounded timeout, 512 completion-token limit and
16 KiB response limit.

Only a complete validated batch with an available valid account can reach the
provider. The transport independently rechecks the exact saved prompt/request,
universe, frozen input hashes, timestamps, quote context if enabled, account cash,
open-position/daily restrictions and its durable reservation. Direct method calls
cannot skip those checks or the experiment lock. No caller-supplied provider,
simulation clock or snapshot path is accepted by the public reviewed runner.

The AI-led prompt allows independent BUY discovery or NO_TRADE, including structured
entry, stop, target, holding period, scores and allowlisted rationale codes. It does
not invoke the legacy frozen reversal predicate. Wrong timestamps/models, extra
fields including quantity, malformed/truncated responses, invalid prices or
unapproved rationale are rejected. Raw response text and arbitrary exception
messages are never persisted.

The response must arrive before the paper engine's expected next entry time;
otherwise it becomes a failed/not-taken provider evaluation. Validated proposals
still pass through the original saved-response pipeline and all independent
RiskEngine/cost/account checks. AI cannot set executable quantity or bypass gates.
The original quantitative comparison strategy remains unchanged.

# 5. Network activation controls

All three options are required, with defaults false:

```text
--enable-prospective-paper
--allow-market-data-requests
--allow-provider-requests
```

`PAPER_TRADING=true`, valid settings and a fresh or exactly matching reviewed
experiment are also required. Merely having credentials or a policy file does not
authorize a send. Imports, construction without opt-ins and the default CLI cannot
send requests. `--preflight-only` can freeze/check configuration without reserving
or sending anything, even when all three flags are supplied.

Every send acquires or inherits the experiment lock, requires a pre-existing
reservation and creates a single-use persistent send checkpoint. Reservation and
send files are flushed/fsynced before network I/O. A duplicate send ID is rejected.
The real path uses `datetime.now(timezone.utc)` internally, with no `--as-of` option.

These controls apply to the new reviewed adapter. They are not a repository-wide
firewall against arbitrary direct SDK/client use or separate legacy utilities.
Those utilities must not be substituted for this reviewed launch path.

# 6. Budget and evidence handling

Each experiment freezes initial capital, universe/priority, model, risk/cost
assumptions, source/prompt hashes, calendar/attestation references, quote policy,
timeouts/retries and lifetime caps. Capital remains configurable without an
INR 1,000 ceiling; tests include INR 1,000 and 10,000, with the earlier independent
accounts at INR 2,500 and 100,000 still covered by regression tests. Existing
experiments cannot silently change capital or adopt this implementation.

Market-data reservations count candles and quotes separately. Provider reservations
are made only after batch/account validation. A failed request, invalid response,
expiry or crash does not refund reservations. A reservation without a saved result
is never blindly retried. Completed batch replay makes no second SDK request, and
the replay result reports `provider_request_attempted=false`.

Evidence includes the configured universe; observation, batch, decision, send and
receipt timestamps; original/normalized input and response hashes; calendar and
attestation identities; provider/model; exact request identity and payload; safe
structured proposals/rationale; risk/cost results; paper action; account events,
settlement and realized P&L. Transport receipts retain safe failure categories and
validated numeric HTTP status, never headers, credentials or raw response bodies.

The reviewed mode uses `ai-led-guarded-prospective-paper-v1` and prospective provider
labels; mock mode retains its separate schema and synthetic labels. This task's
reviewed-path tests still use synthetic responses: their artifacts are test
fixtures, not genuine AI observations or performance evidence. Runtime mode labels
and hashes alone are not independent proof of provider authenticity.

`external_api_requests` counts conservative logical send checkpoints, not verified
physical network packets. A checkpoint can precede a local failure, and Upstox
retries can produce multiple physical attempts per reserved logical call. Existing
budget accounting was preserved rather than relabeled as a physical-wire cap.

All 359 original evidence files under `backtests`, `data` and `reports`, including
all 22 Session 02-05 files and budgets, remain unchanged. The proposed output and
Session 06 do not exist. Generated test accounts/evidence are outside the project.

# 7. Tests passed and failed

Final full relevant regression: **543 passed in 112.18 seconds**, exit 0. Zero
failures, exclusions or xfails: 45 new transport cases, all 435 previous tests,
and 63 additional quote/execution/paper-session regressions.

The 45 new cases cover default and individual opt-in denial; complete candle/quote
collection; actual existing HTTP/SDK interfaces with mocked responses; missing,
stale, future and invalid candles; instrument identity; authentication, timeout,
rate-limit and JSON errors; quote depth/freshness/cached schema; strict AI output,
wrong model, truncation, NO_TRADE and provider failure; budgets; duplicate/restart
and interrupted requests; calendar/attestation failure; direct-entry guards;
entry-time expiry; warmup; request-free preflight; capital/configuration and secrets.

Exact final command (PowerShell from the repository root):

```powershell
@'
import socket,pytest

def denied(*a,**k): raise AssertionError('All external networking forbidden')
socket.socket.connect=denied
socket.create_connection=denied
raise SystemExit(pytest.main(['tests/test_ai_led_reviewed.py','tests/test_ai_led_guarded.py','tests/test_ai_led_offline.py','tests/test_ai_led_replay.py','tests/test_prospective_preflight.py','tests/test_readiness_evidence.py','tests/test_offline_market_data_v1.py','tests/test_deepseek_prospective.py','tests/test_deepseek_prospective_summary.py','tests/test_cost_model_execution_integrity.py','tests/test_core.py','tests/test_deepseek_eval_v1.py','tests/test_ai_v2.py','tests/test_ai_v2_evaluation.py','tests/test_provenance_audit.py','tests/test_execution_observations.py','tests/test_execution_integrity.py','tests/test_paper_session.py','-q','-p','no:cacheprovider','--basetemp',r'C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-reviewed-transports-verified']))
'@ | .venv\Scripts\python.exe -B -
```

Earlier development runs passed 103 focused tests; then 478 relevant tests and 63
additional quote/execution/paper-session tests; then 541 together in 108.67 seconds.
The final run additionally covers exact cached-quote schema checks. Those runs
overlap and are not additive independent samples. No failing test was excluded.

All four affected Python files passed in-memory compilation. Targeted whitespace
checks cover all five changed files, including no-index diffs against task-start
bytes/empty baselines for untracked/new files. Full task diffs and the new code
were inspected. Pre-existing tracked Git modifications remain unchanged.

# 8. Remaining risks

- Real credentials, access-token expiry, live endpoint response shapes, actual
  model availability and latency were not tested. All tests blocked sockets and
  replaced HTTP/SDK responses. Fail-closed schema handling may reject a live shape.
- System UTC time must be operationally synchronized. The code removes caller
  simulation-clock injection, but does not establish NTP accuracy.
- Calendar evidence remains reviewed as of 2026-10-30 and expires after 2026-12-31.
  Later exchange amendments were not checked in this offline task.
- Socket timeouts/retry counts are bounded, but existing Upstox Retry-After
  behavior is unchanged; this is not a hard total wall-clock deadline. Post-call
  freshness/session/entry checks prevent a delayed result becoming an accepted
  late proposal. A logical budget is not a physical-attempt budget.
- Candle identity is endpoint-bound where the response omits an instrument echo.
  Quote timestamps describe response generation, not independently timed depth.
  Turnover/depth snapshots and modeled costs do not guarantee fills or profitability.
- Quote-enabled batches may expire while a multi-stock batch is being assembled;
  they fail closed without an automatic refresh/retry loop.
- One CLI invocation advances one batch. It is not a scheduler or unattended
  position monitor. Accepted paper positions need subsequent valid snapshots for
  entry/settlement. The current entry-session gate also restricts late-session
  capture, so a first trial should allow ample session time for later outcomes.
- Cooperative locks and process-crash recovery are covered. Hostile filesystem
  changes, power loss and distributed execution require separate deployment review.
- Broker funds/positions, order reconciliation, partial fills and real execution
  remain unimplemented. Stops and estimated costs do not guarantee maximum loss.

# 9. Readiness for first controlled real-data paper trial

**Transport wiring and offline verification are complete. Execution remains
NO-GO during this task: no real request was authorized.** After a final read-only
safety review, current calendar-evidence/clock/configuration checks and separate
explicit operator approval, this version can support one tightly budgeted
regular-session paper collection. It is not ready for unattended autonomous
trading or a broker-order sandbox.

Use a fresh reviewed output, explicit INR 1,000 capital for the user's current
experiment, and exactly the same configuration/caps on resume. Higher-capital
experiments remain supported independently. Do not migrate Sessions 02-05.

# 10. Exact operator command proposed for later approval

**PROPOSED ONLY — NOT EXECUTED OR AUTHORIZED BY THIS TASK.** Working directory:
`C:\Users\Priyanka\Desktop\ai-trading-bot-research`.

```powershell
.\.venv\Scripts\python.exe scripts/run_ai_led_prospective_paper.py --output backtests/ai_led_reviewed_paper_trial_01 --instrument-priority RELIANCE HDFCBANK --paper-capital 1000 --max-provider-requests 1 --max-market-data-requests 12 --include-quotes --enable-prospective-paper --allow-market-data-requests --allow-provider-requests
```

The two symbols are existing configured identities for a transport-validation
example, not screened recommendations or claims of affordability at INR 1,000.
Zero-share/affordability/cost gates may reject all proposed trades. No current
prices, liquidity, candidates or signals were obtained during this task.

With quotes, a complete two-stock capture normally reserves four logical market
calls and at most one provider evaluation. The lifetime market cap of 12 leaves
room for subsequent separately approved captures to observe entry/settlement;
resuming must not increase that cap. Real wire attempts may be higher due to the
unchanged Upstox retry policy. No loop or automatic follow-up is launched.

Before any approved invocation, establish a supported regular session after warmup
with enough time for outcomes, synchronized clock, current official calendar
evidence, appropriate credentials/model settings and a reviewed universe. Add
`--preflight-only` to the proposed command for a configuration-only check that
cannot send requests. No version of this command was run against real services.
