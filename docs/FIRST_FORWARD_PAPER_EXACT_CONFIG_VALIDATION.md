# First-session exact-config offline validation

Verification started 2026-11-15 and continued 2026-11-16 (Asia/Calcutta).
Offline evidence only; production remains NO-GO.

## Original readiness deadlock

`scheduled_forward_paper.validate_config` parses the versioned runtime config.
`preflight` delegates to `v2_transport_contract.readiness`, which first requires
market-data and session-start opt-ins, and provider opt-in for startup/entry.
Only afterward does it accept a source/config-bound review with both approval
assertions true, verify client attestation/calendar and validate credentials.
`run_scheduled` refuses a failed preflight before initialization. The controller's
`run_cycle` also checks readiness before constructing `LiveTransport`, and the
live transport independently checks authorization before requests. V2.2 begins
at the controller's engine/selection/observation calls. Injecting mock responses
alone therefore cannot get the exact disabled canonical configuration past
production readiness. Enabling its switches or forging approval would change
the very identity being validated and would be unsafe.

## Dedicated architecture and authority boundary

`scripts/validate_forward_paper_config.py` invokes the dedicated
`app/research/exact_config_validation.py` entry. The public function accepts only
the canonical path, a closed scenario set and a new absolute output directory.
It exposes no supplied transport, arbitrary callback, approval bypass or live
mode. It removes credential environment variables before spawning a guarded
child. The child installs its guard before importing the project execution code.

The child parses the actual canonical bytes and verifies the pending review
identity, all 26 reviewed dependency hashes, client attestation and manifest.
It creates a private namespace for the existing controller function code, bound
to a locally defined exact-type offline port and a scoped offline context.
The production controller globals, readiness function, config and approval are
unchanged. The private readiness result is only usable with that context's exact
config and fixed offline port. No production READY approval is created.

The actual transport normalizers, provider parser, bounded market-data retry
loop, controller acquisition/ranking, V2.2 risk/cost/entry/monitor/settlement/replay,
scheduler projections and observer publication code are exercised. The transport
callback passed by the production normalizer is never invoked. The retry loop's
lowest I/O function receives only fixed synthetic responses in another private
namespace. Live transport constructors and provider SDK constructors are never
called by the validation lifecycle. There is no environment-controlled fallback.

Financial storage binds the canonical identity **and** validation domain,
harness hash and scenario. Production `_configuration` rejects this binding.
The ordinary production parser also rejects a validation report presented as a
review artifact, even in a negative test with all opt-ins true.

## Exact canonical identity

Path: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\logs\forward-paper-v2\runtime-config.json`.

- Configuration identity: `cb28365631d301aeac08e46e61d76bbcc87e5b54a8851e8e05e80d9e27f1e206`.
- Canonical file SHA-256: `be4e86e1e80153901cffaef7c7ca71b3a745adeb7c6653d5016093c70257d039`.
- Reviewed integration identity: `8d854dbf1073aab6971da707d152484691416edcc78a1d78caddd025d45d1342`.
- Session manifest SHA-256: `4f0524db0b16c2dfedf354704cac7954b14af3936cbbff76f53d1848b4938d03`.
- Capital ₹1,000; logical market-data cap 1,100; logical provider cap 36.
- All 14 instruments in canonical registry order: RELIANCE, HDFCBANK, ICICIBANK,
  INFY, TCS, SBIN, BHARTIARTL, ITC, KOTAKBANK, LT, AXISBANK, MARUTI, SUNPHARMA, TITAN.
- All three production opt-ins remain false. The canonical JSON mode remains
  FORWARD_PAPER because its bytes are the object being validated; the dedicated
  invocation and evidence domain are OFFLINE_VALIDATION_ONLY.

No copied or value-modified config drives the mocked lifecycle. Risk/cost,
five-second quote freshness, deadlines/retries and session/calendar policies are
the existing implementations bound by the 26-file identity. The fixture clock
uses the verified 2026-11-16 regular session. It does not authorize that session.

## Network guard and broker boundary

An irreversible child-process audit hook rejects socket events, subprocesses,
link creation and writes outside the marked validation root. Socket connect,
sendto and DNS helpers are also denied. Requests and synchronous/asynchronous
HTTPX constructors are blocked; dotenv loading and `.env` reads are blocked.
Eight named probes cover direct sockets, DNS, Requests, HTTPX, async HTTPX,
standard-library HTTP, process start and a production-control write. Actual
rejections are recorded in `guard-checks.json`.

The fresh reviewer traced 39 local modules in the import closure and found no
broker place/modify/cancel interface or broker endpoint. Production modules
remain observation/proposal-only. Validation neither imports a broker executor
nor runs a Windows scheduler installer/launcher. Process creation is denied in
the worker. This is trusted-code Python confinement, not a claim of an OS
security sandbox against hostile native extensions or malicious code changes.

## Scenario expectations and request accounting

| Scenario | Result | Market-data logical | Provider logical |
|---|---|---:|---:|
| A: 36 no-opportunity epochs | NO_OPPORTUNITY | 1008 | 36 |
| B: one valid candidate | COMPLETED, one settlement | 32 | 1 |
| C: stale quote | Rejected, no entry | 15 | 0 |
| D: candidate expiry | Rejected, no entry | 28 | 1 |
| E: provider failure | No fabricated entry | 28 | 1 |
| F: market-data failure | No entry | 1 | 0 |
| G: provider exhaustion | Monitoring and settlement continue | 32 | 36 |
| H: market-data exhaustion | FAILED_CLOSED; open exposure/reservation retained | 1100 | 1 |
| I: observer failure | Same financial result as B; warning | 32 | 1 |
| J: recovery | One settlement, no reacquisition | 32 | 1 |
| Retry | Three attempts share first logical reservation | 32 | 1 |
| Serial slow quotes | First quote becomes stale; no provider call | 28 | 0 |

Cap-exhaustion fixtures explicitly reserve remaining capacity without pretending
physical requests occurred. PersistentRequestBudget is used with the exact caps;
reopening reconciles its consumption against the offline reservation journal.
Retry uses 35 total physical mock attempts for 33 logical requests. No automatic
budget reset is introduced. The 36-epoch case exercises all available decision
epochs after warmup, without a live scheduler wait loop.

Observed existing reason mappings: no opportunity → NO_VALID_PROPOSAL; stale
quote → QUOTE_INVALID; candidate expiry → RESPONSE_STALE; provider exception →
TRANSPORT_FAILURE; market response failure → CANDLE_INVALID. Slow serial
acquisition maps to PROVIDER_INVALID at the controller's payload/freshness
boundary with zero provider requests. Market-cap loss of monitoring evidence
retains the engine's missing_expected_candle and summary RECOVERY_REQUIRED.
These are existing reason mappings, not newly introduced validation codes.

## Recovery, observer, finances and redaction

Recovery interrupts after durable provider response/before selection, reopens
with an open position, and replays after settlement. Counts and physical attempts
must remain unchanged at replay boundaries; one financial settlement remains.
The observer failure case must have exactly the same account as the successful
candidate case and expose OBSERVER_UNAVAILABLE. Native reports include cash,
equity, reservations, positions, P&L, costs and reason codes. Detailed engine
history retains risk/cost rejection and lifecycle evidence.

The successful mock has gross P&L ₹8, modeled costs ₹2.244409008 (including
modeled slippage ₹0.604), realized P&L ₹5.755590992, ending available cash/equity
₹1,005.755590992 and zero remaining exposure/reservations. This is synthetic
evidence, not a profitability estimate.

Recognizable fake credential markers are injected only inside the child, used
in headers and simulated exceptions, then all generated scenario files are
searched byte-for-byte. No real credential is loaded or printed. A failed child
exports sanitized exception types and stack locations, never exception strings.

## Evidence isolation and production preservation

Final evidence: `logs/forward-paper-v2/validation/exact-final-20261115/`.
Earlier complete and one-candidate smoke runs are retained separately; neither
is production attempt history.
The root/scenario markers, OFFLINE_VALIDATION_ONLY path and artifact inventory
identify all native working files as offline evidence. Native schemas remain
unchanged for real engine replay, and native scheduler records retain
`mode=FORWARD_PAPER` with `simulation_only=true`; individual native records do
not all embed the validation tag. They must be interpreted with their marked
root and inventory. They cannot be adopted as production engine/controller
state because their storage binding differs.

Canonical approval stays pending with independent_review_passed=false and
network_exercise_authorized=false. Its exact before/after hashes and config hash
are recorded in the main report. The production control root must start and
remain empty. Normal preflight is run before and after and must remain NO_GO
with NETWORK_DISABLED, PRODUCTION_START_DISABLED and PROVIDER_DISABLED.
No task registration, schedule activation or production attempt is performed.

## Tests, fresh review and remaining limits

Verification results and preservation reconciliation are recorded below.
The fresh non-author reviewer performed read-only review, including
the final guard/hash-report changes, and found no confirmed authorization bypass.
The same fresh non-author reviewer subsequently checked the final runtime
evidence read-only: all 989 artifact hashes, 26 dependency hashes and current
config/approval/harness identities matched; all 12 scenarios were present;
recovery/observer financial accounts matched the successful case; exposure
remained explicit on market-cap exhaustion; all eight probes were blocked;
no fake secret leaked; canonical control stayed empty and preflight stayed NO_GO.

Final focused command:
`python -B -m pytest tests/test_exact_config_validation.py -q -p no:cacheprovider --junitxml=logs/forward-paper-v2/validation/preservation-20261115/focused-suite.xml`.
Result: **24 passed in 290.21 seconds**, exit 0. This includes the 39-module
broker import-closure regression, exact identities, all scenarios, guard probes,
approval immutability, output isolation and rejection of validation as approval.

The initial sandboxed collection encountered four PowerShell
`AuthorizationManager check failed` errors in scheduler preview/invalid-config
and copied-project wrapper tests. Diagnostic reruns reproduced the failures
before the scripts executed.
The unchanged scheduler module then passed **14/14 in 8.86 seconds** outside the
sandbox; the copied-project wrapper separately passed **1/1 in 9.29 seconds**
outside the sandbox. No execution-policy setting or production script was changed. The
failed run is retained; it is not counted as clean. A complete unfiltered final
run uses the recorded `validation/preservation-20261115/run_full_suite.py`
runner outside the sandbox, with dotenv disabled, empty credential settings and
external socket guards, plus the existing per-test offline fixtures.
Initial sandbox result: **2,127 passed, 4 failed in 1,825.10 seconds**, all four
failures being the reproduced PowerShell authorization errors. No other test
failed. The five historical stage11 tests were also checked separately and
passed; the final unfiltered run includes them directly.

Final complete unfiltered command:
`python -B logs/forward-paper-v2/validation/preservation-20261115/run_full_suite.py`.
Result: **2,136 passed in 1,496.82 seconds (24m 56s), exit 0**. JUnit:
**2,136 tests, 0 failures, 0 errors, 0 skips**, 1,496.374 seconds. Outer runner:
1,497.6740129 seconds. No deselection/filter was applied; the 2,136 collected
tests all passed. This reconciles the 2,112 baseline plus 24 dedicated validation
regressions. The final unfiltered run supersedes the sandbox execution failure.

Post-suite preservation: 375 inventoried files reconciled, 374 byte-identical
and only the intended new harness edited. No protected file was added or
removed; no unexpected change occurred. All 26 production dependencies also
match their reported hashes, including dependencies outside that inventory.
Canonical config/approval hashes remain identical, all opt-ins remain false,
canonical control remains empty, and a fresh default-time normal production
preflight still returns NO_GO with the three disabled-opt-in reasons.

Verification records under `logs/forward-paper-v2/validation/preservation-20261115/`:
`full-final-suite.xml`, `full-final-run.json`, `preservation-result.json`,
`before.json`, `after.json`, `fresh-review.json`, `broker-closure.json`,
`final-artifact-check.json` and focused/diagnostic JUnit reports. Earlier failures
are retained in `full-suite.xml` and `full-suite-run.json`.

LIVE FEASIBILITY UNPROVEN: serial mocked quotes at 0.4 seconds each make the
first quote 5.2 seconds old when all 14 are ready, and the actual controller
freshness check rejects the batch before provider acquisition. The current
controller uses serial per-symbol acquisition; no batch interface was added.
Zero-latency mock success cannot establish real entitlement, latency, quotas,
provider quality, execution quality or profitability. The harness exercises
controller cycles and scheduler reporting helpers, not a running Windows task
or a real-time session. Offline validation provides no production authority.

**EXACT-CONFIG OFFLINE VALIDATION VERIFIED.** The exact canonical configuration
completed the required mocked lifecycle in the dedicated offline-only path.
Production approval, opt-ins, session authorization, scheduler state and
production attempt history were not changed. Production remains NO-GO and broker
execution remains disabled.
