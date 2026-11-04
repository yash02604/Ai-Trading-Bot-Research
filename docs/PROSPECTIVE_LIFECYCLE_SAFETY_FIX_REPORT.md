# Root Causes

The reviewed prospective adapter checked current quotes before recovering a saved decision, so a commit failure could strand a valid response after its five-second quote lifetime. Entry authorization also governed settlement acquisition, blocking the final completed candle. The HTTP adapter delegated retries to urllib3, which could honor a 21,600-second Retry-After. Seven HTTP test cases patched the configuration module rather than the client's imported settings reference.

This task used offline mocks only. No Upstox/DeepSeek requests, provider tokens, live captures, orders, Session 06 or real-data paper trial were performed.

# Recovery Fix

Added local-only response recovery before new acquisition and an explicit `--recover-only` CLI purpose. Recovery verifies immutable experiment/source/calendar identity, frozen input hashes, recorded request identity, original timestamps and quote validation at the original decision time, response evidence, budget state, send/receipt evidence and a separate append-only evidence seal. Missing, corrupt or conflicting evidence fails closed; missing decisions are never reconstructed.

Recovery reuses the original proposal and timestamps without new market/provider requests or reservations. It independently checks the current execution window. An expired window preserves the original BUY proposal as evidence while rejecting entry with `entry execution window expired during recovery`. Repeated completed recovery is idempotent. Existing experiments are never migrated or rewritten to match new source identities.

# Settlement-Only Acquisition Fix

Added `--settlement-only --allow-settlement-market-data-requests`, separate from new-entry/provider authorization. It requires a verified accepted OPEN position, current manifest/attestation, valid account and unchanged budget configuration. Only that position's instrument can acquire candles; quote and provider requests are forbidden.

The local acquisition window derives from the actual verified calendar interval containing the accepted entry. Its upper bound is the last START-labeled candle plus the unchanged 900-second freshness allowance: session end plus ten minutes, exclusive for new acquisition. This is an application read-authorization policy, not an additional NSE trading session or a guarantee of provider availability. Tests cover the verified 15:10-15:15 final candle and a different verified 15:25-15:30 close. Unsupported sessions do not acquire a window.

The intended completed candle is frozen across retries/clock boundaries. Missing latest candles, stale/future/invalid data, duplicate observations, pending/unverified positions and expired windows fail closed. Settlement retains the existing account advancement, cost and exit logic. A prepared transaction binds input, manifest, position and before/after account hashes; interrupted commits finish locally without another GET or duplicate outcome.

# Retry Deadline Fix

Upstox GETs now have an explicit total deadline, maximum three physical attempts and bounded delay. Defaults are 20 seconds total and one second maximum retry delay, configurable through `UPSTOX_TOTAL_DEADLINE_SECONDS` and `UPSTOX_MAX_RETRY_DELAY_SECONDS`. Validation allows a positive total up to 60 seconds and delay from zero through min(5 seconds, total). Each attempt uses the remaining deadline and revalidates its acquisition purpose/session before sending.

Long, malformed, negative or non-finite Retry-After values cancel retries. A retry must fit inside both the total deadline and acquisition window. A killable spawned worker performs one GET with no implicit retries or redirects and caps successful response bodies at 2 MiB. Parent deadline termination bounds slow DNS/headers/streaming; worker cleanup allows at most two half-second joins beyond the request budget. Structured authentication, HTTP, rate-limit, transport and JSON failure categories remain available without error bodies or arbitrary transport exception text.

GET-only endpoint bodies and get_headers are checked against the preserved reviewed source. Persistent reservations remain consumed after attempted acquisition; retrying never resets the ledger.

# HTTP Test Isolation Fix

The affected tests patch `app.upstox_client.get_settings` with dummy settings. Test collection disables dotenv loading and clears the two provider credential environment values in the test process. Autouse guards deny external socket connections and prevent an unmocked Upstox worker. Dedicated deadline tests mock clocks, workers, pipes and HTTP responses. The reviewed transport fixture supplies its mocked clock to the client too.

# Attestation Changes

Preserved all original v1 files and historical provenance evidence. Added a separately versioned v2 record, exact client-byte snapshot and exact reviewed v1-to-v2 diff. The verifier checks the full v1 chain, pinned v2 record, parent identity, actual bytes/SHA/CRLF count, recomputed diff and unchanged endpoint/auth ASTs. It rejects non-GET transport calls.

- Historical source SHA256: `59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3`
- Preserved reviewed v1 source SHA256: `27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb`
- Reviewed v2 source SHA256: `20bf9738577c5e4bf19b04587a609c352514f944bceb0cb0d6df3c5a1ea73580`
- Pinned v2 attestation SHA256: `9402ac71a85ed9ab7df6120260b21851a27bcf0b8381d692a94bb7da5c676709`

This is a documented technical review, not independent approval, provider-origin authentication or operational trading approval.

# Files Changed

Existing files changed in this task:

- `app/client_attestation.py`
- `app/config.py`
- `app/upstox_client.py`
- `app/research/ai_led_guarded.py`
- `app/research/ai_led_offline.py`
- `app/research/ai_led_reviewed.py`
- `app/research/prospective_preflight.py`
- `scripts/run_ai_led_prospective_paper.py`
- `tests/test_ai_led_reviewed.py`
- `tests/test_deepseek_prospective.py`
- `tests/test_readiness_evidence.py`

New files:

- `app/research/ai_led_recovery.py`
- `tests/conftest.py`
- `tests/test_prospective_lifecycle_fixes.py`
- `tests/test_upstox_deadlines.py`
- `data/attestations/upstox_client_v2/attestation.json`
- `data/attestations/upstox_client_v2/current_client.crlf.py.snapshot`
- `data/attestations/upstox_client_v2/reviewed_diff.patch`
- `docs/PROSPECTIVE_LIFECYCLE_SAFETY_FIX_REPORT.md`

All unrelated pre-existing Git changes were retained. No existing report was rewritten.

# Tests Added

Added 26 lifecycle cases and 11 deadline/worker cases, with existing attestation and HTTP tests updated rather than suppressed. Coverage includes expired quotes, expired entry, missing/corrupt evidence, repeated recovery, provider non-reexecution, final session candles, unsupported/closed acquisition, duplicate and partial settlement, commit recovery, missing latest candle, a clock-boundary crossing, absent DeepSeek credentials during settlement, purpose-flag conflicts, long/malformed retry headers, deadline/attempt bounds and GET-only worker behavior.

# Verification Results

Final regression command, from the repository root:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_upstox_deadlines.py tests/test_prospective_lifecycle_fixes.py tests/test_ai_led_reviewed.py tests/test_ai_led_guarded.py tests/test_ai_led_offline.py tests/test_ai_led_replay.py tests/test_prospective_preflight.py tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py tests/test_execution_observations.py tests/test_execution_integrity.py tests/test_paper_session.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-lifecycle-final
```

Result: **580 passed in 222.90 seconds; no exclusions and no failures.** The preceding full run passed 578 tests before the last two settlement regression cases were added. These totals overlap and must not be added together.

All 15 changed/new Python files passed in-memory compilation with `compile(path.read_bytes(), str(path), 'exec')` using `.venv\Scripts\python.exe -B -`. Targeted Git whitespace checks include untracked files and task-start comparisons, since ordinary `git diff` alone omits most pre-existing untracked implementation files. The command used per file was `git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,cr-at-eol diff --no-index --check <task-start-copy-or-empty> <current-file>`: 18 source, test, documentation and evidence files passed. The nineteenth file, the exact reviewed diff artifact, contains seven required single-space prefixes for blank context lines; these were verified as patch syntax and preserved. `git diff --check -- app/config.py app/upstox_client.py` also passed. No implementation whitespace errors remain.

SHA256 comparison against the preceding review's protected inventory confirms **359 existing evidence files unchanged**, including Sessions 02-05, their budgets, historical provenance and v1 attestation. The current task-start inventory independently identifies exactly the eleven existing source/test files listed above as changed. Capital remains configurable; no risk, sizing, cost, frozen strategy or broker enablement change was introduced.

# Remaining Risks

Mocked tests establish implementation behavior, not live transport availability, predictive skill or profitability. The actual Windows spawned-worker/OS termination path has not been exercised against a real provider. Local hashes and receipts cannot independently authenticate provider authorship or defend against coordinated malicious rewriting of all local evidence. Protect experiment directories and credentials operationally.

The settlement grace window remains limited by verified calendar coverage and existing freshness rules. It does not solve outages beyond that window; such positions must remain visibly unresolved rather than receive invented prices. Existing manifests pinned to earlier source bytes intentionally fail closed instead of being silently upgraded.

# Prospective Paper-Trading Readiness

The four identified lifecycle defects pass the offline regression checks. A real-data prospective trial has **not** been authorized or executed by this task. Obtain a final read-only review of these changes, current calendar coverage, attestation and proposed fresh experiment policy before any explicitly authorized provider request. Budget for settlement as well as discovery and preserve configurable initial capital (explicitly 1000 for the user's current experiment).

# Broker Sandbox Readiness

**NO-GO for broker execution.** Broker orders remain disabled. This task adds no broker balance integration, order transport, sandbox order validation or live execution approval.

# Recommended Next Step

Perform the final read-only review of recovery, settlement, deadline and v2 evidence, including the proposed request budgets and calendar date. Then obtain explicit authorization for a small fresh-directory paper-only trial with separate entry and settlement operations. Do not start Session 06 or run a real-data trial merely because offline tests pass.
