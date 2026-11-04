# AI_SIGNAL_ONLY Request-Budget and Recovery Remediation

Date: 3 November 2026 (Asia/Calcutta).

**Readiness: ready for another independent read-only audit only. No real experiment is authorized. Broker placement remains disabled. Real-time paper execution and causal trade settlement remain NO-GO.**

## 1. Original defects

The initial signal-only command capped logical reservations, while the Upstox client permitted three physical HTTP attempts per reservation. Two reservations could therefore produce six GET attempts. The provider disabled SDK retries but inherited redirect following. A later initial-command restart could spend the future capture's remaining budget. A future observation published without its seal blocked replay rather than reporting a deterministic incomplete state.

## 2. Root causes

- Logical reservations and transport send markers were above the Upstox retry loop, without a separately persisted physical-attempt checkpoint.
- The DeepSeek adapter relied on the SDK's default HTTP client, which followed redirects.
- The initial runner matched completed work against the current five-minute candidate and did not hold separate initial/future purpose slots.
- Observation publication and seal append were separate writes; replay required a complete pair and capture did not classify spent-but-unpublished evidence.

## 3. Files changed

Modified existing application files:

- `app/upstox_client.py`
- `app/v2/provider.py`
- `app/client_attestation.py`
- `app/research/ai_led_reviewed.py`
- `app/research/ai_signal_only.py`
- `app/research/signal_observations.py`

Modified existing tests:

- `tests/test_ai_signal_only.py`
- `tests/test_signal_observation_chain.py`
- `tests/test_readiness_evidence.py`
- `tests/test_synthetic_provider_check.py`

Added:

- `app/research/signal_request_budget.py`
- `tests/test_signal_request_remediation.py`
- `data/attestations/upstox_client_v3/attestation.json`
- `data/attestations/upstox_client_v3/current_client.crlf.py.snapshot`
- `data/attestations/upstox_client_v3/reviewed_diff.patch`
- This new report.
- `reports/ai_signal_only_request_budget_remediation_20261103/preservation.json`

No installed SDK or virtual-environment package was modified. No previous report or experiment artifact was overwritten.

## 4. Exact implementation changes

### Physical Upstox attempts

`request_window()` now accepts a validated `max_attempts` and `before_send` callback. Its defaults preserve the existing three-attempt behavior for other callers. `_get()` uses the window's attempt limit and calls `before_send` after its authorization/deadline checks and immediately before the bounded GET worker.

The reviewed signal and future-observation paths set `max_attempts=1`. Their frozen transport policy records zero Upstox retries. The worker still disables requests/adapter retries and HTTP redirects.

The new signal-budget module holds fixed purpose slots, binds each slot immutably to one request ID, and appends `physical_request_attempts.jsonl` before I/O. The append is flushed and fsynced. The callback requires the existing logical reservation and send checkpoint, rejects an already-spent physical request ID, and checks the physical ledger against the saved cap. Saved initial and completed future evidence also verify their physical checkpoint.

Key locations: `app/upstox_client.py:21,155`; `app/research/ai_led_reviewed.py:_send_market`; `app/research/signal_request_budget.py:12,20,49,56,73`.

### DeepSeek redirects

`DeepSeekProvider._get_client()` supplies an application-configured `DefaultHttpxClient(follow_redirects=False, timeout=...)` to OpenAI. SDK `max_retries=0` remains unchanged. In the installed transport, connection retries default to zero. The real installed SDK, using only a mocked HTTP transport, rejects 301/302/307/308 responses without following Location.

The requested model, prompt, completion-token cap, response-byte cap, schema checks and returned-model validation are unchanged. Injected test clients remain a testing interface; the reviewed production binding does not expose caller-supplied clients.

Key location: `app/v2/provider.py:82`.

### Purpose reservations and initial restart

Before any send, signal initialization freezes `request_slot_policy` in its manifest and publishes `signal_request_slots.json`, bound to the manifest digest. For one instrument without quotes it contains exactly:

1. INITIAL_SNAPSHOT.
2. FUTURE_OBSERVATION.

Each slot allows one physical attempt. Optional quote mode has its own INITIAL_QUOTE slot and must budget that slot in addition to initial/future captures. Insufficient total capacity fails before a request. Slots are held at initialization; the specific future request ID is bound only after it can be derived from the saved prediction and fixed schedule.

`initial_capture.json` freezes the original candidate and universe. A changed candidate before prediction completion cannot create another initial capture in the same experiment. After an intent exists, the initial command verifies the saved configuration and resumes its original evidence before current-time acquisition checks. Completed predictions are returned even when the wall clock has advanced to another bucket. Multiple saved candidates are rejected. An altered saved candidate or input fails verification.

The request-ID and prediction-ID algorithms, prompt and prediction schema were not changed. Existing source-bound experiments are not migrated or rewritten.

Key locations: `app/research/ai_signal_only.py:26,98,177,249`; `app/research/signal_request_budget.py`.

### Client attestation

The modified Upstox source requires a new source identity. Added v3 evidence binds its exact bytes, the v2 parent attestation and the v2-to-v3 diff. The verifier continues checking preserved v1/v2 history, verifies current v3 source bytes, and checks that public GET endpoint/auth bodies remain unchanged.

All v1/v2 attestation artifacts remain byte-for-byte intact. V3 is an implementation source attestation, not independent approval; both independent-review and operational-trading approval flags are false.

## 5. Tests added and adjusted

Added **20 parametrized regression cases** in `tests/test_signal_request_remediation.py` covering:

- One physical initial attempt for authentication, rate-limit, server-error and redirect responses.
- No automatic retry sleep or replacement transmission.
- Physical and logical checkpoints present before the mocked I/O boundary.
- Persistent accounting and no refund on same-bucket/later-bucket restarts.
- Purpose slots held before any request and insufficient-capacity rejection.
- Initial-command restart after a saved prediction, without another capture.
- Changed saved candidate rejection.
- Immutable slot binding and rejection of an unreserved quote purpose.
- Separate initial/future physical attempts, success/failure reuse, and no replacement.
- Crashes after raw publication, before observation publication and before seal publication.
- Repeated replay/capture of partial evidence, stable denominators and no manufactured seals.
- The installed SDK rejecting 301/302/307/308 using MockTransport, with exactly one POST.

Existing signal tests continue forbidding paper-account/fill lifecycle calls. The observation fixture explicitly fails if signal capture or evaluation calls those paths. Completed captures still reuse saved evidence and tampering with sealed evidence remains rejected.

Existing two-instrument quote fixtures now hold six slots (two initial, two quote, two future), rather than consuming all four slots before outcome capture. This affects only synthetic fixture capacity; the proposed one-instrument experiment remains ₹1,000 with a market cap of two. Existing tests were updated for v3 attestation and the explicit partial-publication result.

## 6. Test configuration, exact command and results

Inspected `tests/conftest.py`, selected test fixtures, transport mocks and relevant module imports before execution. The successful command:

- Disabled dotenv loading before application imports and blanked both credential variables.
- Disabled third-party pytest plugin autoload and pytest addopts.
- Blocked socket connections, DNS and datagram sends before collection.
- Used HTTP/SDK mocks, including the installed provider SDK over MockTransport.
- Disabled subprocesses during collection/execution after caching the local Windows version probe.
- Disabled bytecode and pytest cache/log-file output.
- Used a fresh unique temporary test directory.
- Installed an audit hook rejecting writes outside that directory.

Run from `C:\Users\Priyanka\Desktop\ai-trading-bot-research`:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
@'
import os, socket, sys, uuid
from pathlib import Path
import dotenv

def denied(*args, **kwargs):
    raise AssertionError('Remediation tests: external network/process disabled')

socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
socket.create_connection = denied
socket.getaddrinfo = denied
dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ.update(PYTEST_ADDOPTS='', UPSTOX_ACCESS_TOKEN='', DEEPSEEK_API_KEY='')
base = Path.cwd() / ('.remediation-tmp-' + uuid.uuid4().hex)
assert not base.exists()

def protect(event, args):
    targets = []
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1], args[2]
        if (mode and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            targets = [args[0]]
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime'}:
        targets = [args[0]]
    elif event in {'os.rename', 'os.link', 'os.symlink'}:
        targets = args[:2]
    elif event in {'subprocess.Popen', 'os.system', 'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        denied()
    for target in targets:
        if Path(os.fsdecode(target)).name.upper() == 'NUL':
            continue
        if not Path(os.fsdecode(target)).resolve().is_relative_to(base):
            raise AssertionError('Test write outside isolated temporary directory blocked')

import platform
platform.uname()  # Cache the local Windows version probe before blocking subprocesses.
sys.addaudithook(protect)
import pytest
raise SystemExit(pytest.main(['-q', '--capture=sys', '--tb=short', '--maxfail=1', '-p', 'no:cacheprovider', '-p', 'no:logging', '--basetemp', str(base),
    'tests/test_signal_request_remediation.py', 'tests/test_ai_signal_only.py',
    'tests/test_signal_observation_chain.py', 'tests/test_signal_evaluation_accounting.py',
    'tests/test_upstox_deadlines.py',
    'tests/test_synthetic_provider_check.py::test_provider_timeout_is_bounded_and_retry_is_disabled',
    'tests/test_readiness_evidence.py::test_historical_client_and_current_attestation_are_distinct',
    'tests/test_readiness_evidence.py::test_modified_current_or_historical_client_evidence_is_detected']))
'@ | & .\.venv\Scripts\python.exe -B -
```

**Result: 132 passed, 0 failed, 0 errors, 0 skipped, 0 deselected in 282.10 seconds. Exit code 0.**

This was the exact listed regression selection, not the full repository suite. All test market data, credentials, provider outputs and HTTP responses were synthetic or mocked. No real Upstox/DeepSeek requests occurred.

Earlier harness attempts stopped before test execution because the write guard rejected pytest's logging sink and the process guard rejected Python's local Windows platform probe. Logging was disabled and the platform probe was cached before installing the process guard. Those setup/collection failures are not claimed as successful test runs.

Additional validation: all 12 changed/new Python files parsed in memory. Targeted Git whitespace checks passed for tracked edited files; Git emitted existing line-ending conversion notices.

## 7. Physical Upstox request-limit proof

For the proposed one-instrument, no-quote, cap-two experiment:

| Layer | Enforced bound |
|---|---|
| Initialized purpose slots | One initial snapshot plus one future observation |
| Slot binding | One immutable request ID per purpose |
| Persistent logical ledger | Saved cap two; no refunds |
| Persistent physical ledger | One checkpoint per request ID; total cannot exceed saved cap |
| Actual `_get()` window | One iteration; no automatic retries |
| HTTP worker | One GET; adapter retries zero; redirects false |
| Restart | Existing reservation/send/physical checkpoints remain authoritative |

Therefore the authorized upper bound is **two physical Upstox HTTP attempts over the experiment lifetime**, at most one per capture. This is a permission ceiling, not a requirement to transmit two requests: a failure or crash may leave fewer actual sends. A checkpoint is consumed conservatively before I/O even if the worker never transmits.

Other non-signal callers retain their previous retry defaults. Those paths are not authorized by this remediation.

## 8. Physical DeepSeek request-limit proof

With provider cap one, the saved provider budget permits one reservation. The durable single-use `transport_sent.jsonl` marker is written before SDK invocation, and repeat sends for that request ID are rejected. SDK retries are zero; the configured HTTP client does not follow redirects. The installed underlying transport's connection retry default is zero.

The four real-SDK/MockTransport redirect cases each observed one POST and a failure, without a second request. A redirect consumes the attempt; no code constructs a replacement provider request. Follow-up still cannot authorize or call the provider.

No live latency, server behavior, credentials or provider availability was tested.

## 9. Restart and reservation proof

A completed initial prediction is verified and reused independently of the current wall-clock candidate. No later initial snapshot can consume the future slot. An unfinished initial capture remains tied to its frozen candidate and purpose binding. A spent failed capture is not refunded; changing candidate/request ID conflicts with immutable evidence.

Follow-up derives its one job from the saved prediction and frozen schedule, binds only the designated future slot, and shares the original saved market cap. Existing completed observations skip acquisition. Spent requests without a finished observation also skip acquisition and produce the explicit partial state.

The lock serializes cooperating processes. Persistent files, not in-memory counters, enforce resumed accounting.

## 10. Partial-capture recovery behavior

Recovery is deliberately conservative; it never manufactures missing transport evidence:

| Saved state | Deterministic result |
|---|---|
| Reserved/spent future request, no observation artifact | UNRECOVERABLE_PARTIAL_CAPTURE |
| Integrity-readable observation with matching job, but no seal | UNRECOVERABLE_PARTIAL_PUBLICATION |
| Sealed CAPTURE_FAILED artifact | Reused failure; no replacement |
| Complete sealed observation and valid receipt/raw/snapshot chain | Verified saved outcome reused |
| Conflicting seal, identity, corrupted envelope or inconsistent sealed chain | Fail closed |

Partial states are included in `observation_bindings`. Their observations are never supplied to the numeric evaluator, so no positive/negative movement is recorded. The original expected evaluation remains in the missing denominator. Repeated offline replay produces the same result at the same cutoff and does not write a seal, receipt or replacement raw response.

A missing seal is indistinguishable from a seal deleted from an otherwise readable artifact. Both are conservatively unavailable; neither can become a positive/negative outcome.

## 11. Existing evidence preservation

A task-start SHA-256 inventory covered **569 existing files**, excluding .env, Git/virtual-environment files, bytecode and test temporary/cache directories. The post-change comparison found:

- **393 pre-existing files under backtests/data/docs/reports/experiments/logs unchanged.**
- No missing baseline files.
- Exactly the ten intended existing source/test files changed; every other inventoried existing file remained unchanged.
- Sessions 02–05 preserved: 7, 4, 3 and 8 files respectively.
- All prior reports, experiment artifacts, budgets, calendar/source evidence and v1/v2 attestations preserved.

The machine-readable inventory, expected modifications, current modified-file hashes and comparison results are saved in `reports/ai_signal_only_request_budget_remediation_20261103/preservation.json`.

Baseline inventory SHA-256: `1e022eb9fdf834a4248790278f5301f197e5dd6a670ae66e33e788a32599715f`.

No assertion is made that .env contents or installed packages were inventoried; .env was not loaded by tests, and no installed package was edited.

## 12. Remaining limitations

- No real experiment, Upstox call, DeepSeek call, broker order or operational paper trade was run. Regression artifacts are synthetic temporary data.
- No genuine prediction/outcome artifact chain was validated. The pre-existing proposed trial directory contained only its lock file and was left intact.
- Source-bound older manifests can fail closed against the new source/attestation/policy identities. No historical experiment is migrated, rehashed or relabeled.
- Interrupted spent captures can be permanently missing. The implementation reports that state rather than reconstructing missing receipts/seals or requesting replacement data.
- Local hashes and clocks establish local consistency, not independent provider authorship or exchange-time authenticity. Coherent malicious deletion/restoration of the entire local experiment directory is outside this guarantee.
- Durability assumes the filesystem honors flush/fsync. Reservation is conservative when transmission outcome is uncertain.
- Follow-up remains a manually invoked, fixed-window capture. Missing its window yields missing coverage.
- The caller must still choose correct mode, configuration and fresh output after a separate audit and authorization. This report grants neither.
- The full repository test suite was not run.

## 13. Updated readiness verdict

| Capability | Verdict |
|---|---|
| Remediation implementation and selected offline regression | Complete; ready for independent read-only audit |
| Offline synthetic validation and matching saved-evidence replay | Supported within the documented evidence constraints |
| Initial real-data signal collection | Not authorized; independent audit required |
| Future real-data observation capture | Not authorized; independent audit required |
| Real-time paper execution / causal trade settlement | NO-GO |
| Broker execution | Disabled and unauthorized |

Trading strategy, prediction logic, ₹1,000 trial capital, prompt, requested model, risk rules and executable sizing rules were not changed. No real execution authority was added.

