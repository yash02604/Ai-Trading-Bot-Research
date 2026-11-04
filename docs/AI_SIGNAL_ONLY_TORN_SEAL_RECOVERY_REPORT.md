# AI_SIGNAL_ONLY Torn Seal Recovery Report

Date: 3 November 2026 (Asia/Calcutta).

**Ready for another independent read-only audit only. This remediation authorizes no experiment. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO.**

## Root cause and verified original locations

Before this change, `evaluate_experiment()` loaded the entire future seal ledger through `AppendOnlyJsonl` at `app/research/signal_observations.py:220`. The generic reader parsed every line with `json.loads()` at `app/research/deepseek_prospective.py:334`. A malformed, incomplete final line raised before the existing missing-seal branch could run. The seal append was at `signal_observations.py:295`.

An interruption after raw response, normalized snapshot and observation publication could therefore leave valid saved artifacts and a spent reservation, but replay returned a parsing failure instead of explicit partial-publication accounting.

## Scope and exact code changes

Only one existing application file changed: `app/research/signal_observations.py`.

1. Added `_read_future_seals()` at line 205. This read-only helper takes one byte snapshot of the ledger, returns its complete valid prefix, and separately identifies a recognized incomplete final append.
2. `evaluate_experiment()`, now at line 240, reads seals once at line 250. Complete prefix seals retain all existing identity/hash/receipt verification. Published observations without a complete matching seal take the existing `UNRECOVERABLE_PARTIAL_PUBLICATION` branch and are excluded from numerical outcomes.
3. A recognized tail is exposed as `summary["seal_ledger_state"]` at line 294. The metadata includes status, reason, line number, byte offset, byte count, SHA-256 and complete-record count. It never includes the raw tail text.
4. `run_followup()`, now at line 298, inspects the ledger before entering capture. At line 313, a recognized torn tail prevents the entire acquisition loop, including any still-unspent future job. Internal corruption raises before acquisition instead.
5. The append at line 333 is unchanged. No code truncates, repairs, completes, rewrites or appends a replacement seal.

The shared `AppendOnlyJsonl` implementation was **not modified**. Its strict behavior for provider/market budgets, send markers, receipts and other ledgers remains unchanged. No transport, request-limit, reservation, strategy, capital, risk, prompt or model setting changed.

Added `tests/test_signal_torn_seal_recovery.py` and this new report. No previous report was overwritten.

## Torn final record versus internal corruption

A line is treated as a possible torn append only when all of these hold:

- It is the physical final line.
- It has no LF or CR line terminator.
- After leading whitespace, it begins an object.
- The decoder reports an error at EOF/after trailing whitespace, or an unterminated JSON string.

This covers the actual ASCII object/string seal format, including truncation inside a key or hash string, after a colon or comma, and after trailing spaces.

The following fail closed with `future_seal_ledger_corrupt`:

- A malformed record before the physical final line.
- A malformed final record that already has a line terminator.
- Invalid final syntax that is not a recognized incomplete prefix.
- Invalid UTF-8.
- Non-object records, missing/empty/non-string request IDs, or duplicate complete request IDs.

A complete valid JSON object at EOF remains readable even without a final newline, preserving the previous reader's behavior. This change does not add a general-purpose log repair or append-framing migration.

The helper never silently drops bytes. An incomplete tail remains intact on disk and is identified by offset, size and hash in the returned summary. Valid records before it remain readable and unchanged.

## Recovery behavior

| Evidence state | Result |
|---|---|
| Normal complete seal ledger | Existing validated replay behavior |
| Empty/missing seal ledger with an already published observation | Explicit missing-seal partial publication; no invented seal |
| Published observation with a recognized torn final seal | `UNRECOVERABLE_PARTIAL_PUBLICATION`; observation remains missing |
| Complete valid prefix seals followed by a torn tail | Valid prefix remains usable after normal chain verification; tail is explicitly reported |
| Torn tail with another future job still unspent | Capture loop is disabled; no new reservation or send |
| Malformed middle/committed record | Fail closed before any capture |

The original expected evaluation denominator remains present. An affected unsealed observation is not passed into the numeric evaluator, so its row remains `MISSING_FUTURE_DATA`, completion count stays zero, and no positive or negative outcome is inferred.

Previously complete, independently verified prefix observations may retain their existing outcomes. That does not classify the incomplete tail as an outcome.

Repeated replay at the same cutoff returns the same explicit result. Capture after a torn tail returns the same evaluation without sending another request. Spent reservations remain spent. No raw response, snapshot, observation, manifest, receipt or seal is replaced.

## Offline safety verification

Before tests, inspected `tests/conftest.py`, the shared mocked HTTP/SDK setup and future-observation fixtures. Dotenv loading is disabled before application imports, and the service credential environment variables are unavailable. The final focused runner additionally removes all UPSTOX_, DEEPSEEK_ and OPENAI_ environment settings before fixtures provide synthetic settings.

Both runners:

- Block socket connections, DNS and datagram sends before test collection.
- Disable third-party pytest plugin autoload and pytest addopts.
- Use synthetic candles, mock credentials, mocked HTTP/SDK responses and, where applicable, SDK MockTransport.
- Disable subprocesses during tests after caching Python's local Windows platform probe.
- Disable bytecode, pytest cache and logging-file output.
- Use a fresh temporary directory outside the project.
- Install an audit hook rejecting writes outside that temporary directory.

No genuine experiment, external service request, broker order, paper position or fill was created. Existing signal fixtures explicitly reject paper-account lifecycle calls. Temporary test artifacts are synthetic evidence only.

## Tests and exact results

Added **34 parametrized pytest cases across nine test functions**, including an exhaustive loop over every nonempty truncated byte prefix of an actual compact ASCII seal.

Coverage includes normal LF/CRLF/non-newline JSON records; absent/empty ledgers; truncated objects and strings; trailing-space truncation; a valid prefix before a torn tail; malformed middle/committed records; duplicate request IDs; actual interruption during seal append; repeated replay and capture; no replacement request/refund; retained missing denominators; and byte-for-byte preservation of all published fixture files.

### Broader offline regression

**162 passed, 0 failed, 0 errors, 0 skipped, 0 deselected in 562.10 seconds. Exit code 0.**

This run contained the existing 132-test remediation selection plus the initial 30 torn-seal cases. It ran **before** the final trailing-whitespace refinement and four additional parametrized cases. It must not be represented as a rerun of the final source state.

Exact command, executed from `C:\Users\Priyanka\Desktop\ai-trading-bot-research`:

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
base = Path(r'C:\Users\Priyanka\.codex\visualizations\2026\11\03\01a1a0fd-3308-7ce2-8029-33d126edc703') / ('torn-test-' + uuid.uuid4().hex)
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
    'tests/test_signal_torn_seal_recovery.py', 'tests/test_signal_request_remediation.py', 'tests/test_ai_signal_only.py',
    'tests/test_signal_observation_chain.py', 'tests/test_signal_evaluation_accounting.py',
    'tests/test_upstox_deadlines.py',
    'tests/test_synthetic_provider_check.py::test_provider_timeout_is_bounded_and_retry_is_disabled',
    'tests/test_readiness_evidence.py::test_historical_client_and_current_attestation_are_distinct',
    'tests/test_readiness_evidence.py::test_modified_current_or_historical_client_evidence_is_detected']))
'@ | & .\.venv\Scripts\python.exe -B -
```

### Final focused verification

After refining the EOF comparison to accept trailing whitespace and adding four cases:

**34 passed, 0 failed, 0 errors, 0 skipped, 0 deselected in 43.67 seconds. Exit code 0.**

This run tested the final application source and final focused test file. The broader selection and full repository suite were not rerun after that final one-line refinement.

Exact command:

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
for key in tuple(os.environ):
    if key.startswith(('UPSTOX_', 'DEEPSEEK_', 'OPENAI_')):
        os.environ.pop(key, None)
os.environ.update(PYTEST_ADDOPTS='', UPSTOX_ACCESS_TOKEN='', DEEPSEEK_API_KEY='')
base = Path(r'C:\Users\Priyanka\.codex\visualizations\2026\11\03\01a1a0fd-3308-7ce2-8029-33d126edc703') / ('torn-test-' + uuid.uuid4().hex)
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
    'tests/test_signal_torn_seal_recovery.py']))
'@ | & .\.venv\Scripts\python.exe -B -
```

The first attempt at the new selection stopped with 25 passed and one setup error because the imported fixture's `setup` dependency was not imported into the new test module. That fixture import was corrected before the successful runs above.

Both final changed/new Python files parsed in memory and passed trailing-whitespace checks.

## Evidence preservation results

The task-start SHA-256 inventory covered **576 existing files**, excluding .env, Git/virtual-environment files, bytecode and prior test temporary/cache directories.

Post-change verification found:

- **398 existing files under backtests/data/docs/reports/experiments/logs unchanged.**
- Sessions 02–05 preserved: 7, 4, 3 and 8 files respectively.
- No missing inventoried files.
- Exactly one existing file changed: `app/research/signal_observations.py`.
- Every other inventoried existing source, test and evidence file remained unchanged.
- The prior remediation report, its preservation inventory, all existing attestations and historical experiment artifacts remained intact.

The preservation baseline is stored outside the project at:

`C:\Users\Priyanka\.codex\visualizations\2026\11\03\01a1a0fd-3308-7ce2-8029-33d126edc703\torn-seal-baseline.json`

Baseline SHA-256: `514ae93ce1f45ab17689da5cd942513032d8dd834c1237b0f73b2e1b9f7bbbb6`.

Final tested source identities:

| File | SHA-256 |
|---|---|
| app/research/signal_observations.py | 5a4a26bc91602a57a88ad88c30462c6cb184b8472ea8501865b3372793a5faa3 |
| tests/test_signal_torn_seal_recovery.py | 6477e4f184d5c3cec86bd6e9afa1a7901479ffb2d4096c8e4c0b77a7b2ded28b |

Fixture-level tests also compare all saved file bytes and file inventories before and after repeated replay/capture, proving that no missing seal was fabricated and no published raw response or observation was modified.

## Remaining limitations and final decision

- A recognized incomplete prefix cannot prove whether interruption or deliberate truncation caused it. Both remain unavailable for outcome classification.
- Unknown or internal corruption remains blocked rather than repaired. This helper does not relax other JSONL ledger readers.
- The recognized truncation shapes are tested exhaustively against the current ASCII seal writer. Arbitrary foreign encodings or unrelated record formats are not recovered.
- No genuine experiment artifact chain, credentials, provider availability or live timing was tested.
- Existing source-bound experiment manifests may reject changed implementation hashes. No historical experiment was migrated, rewritten or rehashed.
- Durability still depends on the filesystem. This change reports incomplete evidence; it does not reconstruct missing receipts, seals or observations.
- The final focused tests passed. The broader regression passed before the final whitespace refinement; the full repository suite was not run.

**Decision: ready for another independent read-only audit only. No experiment is authorized. Broker execution remains disabled; real-time paper trading and causal settlement remain NO-GO.**

