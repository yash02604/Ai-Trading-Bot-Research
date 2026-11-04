# DeepSeek Provider Diagnostic Readiness

Date: 2026-11-04. Scope: conservative provider-error retention and one separately guarded provider-only smoke path. No external request or live smoke command was executed.

## A. Audit and exact retained fields

The installed SDK keeps the decoded provider error in `APIError.body` (or non-JSON text when decoding fails), and exposes status/code/type metadata. Its status-error factory unwraps the provider's `error` object. Useful fields therefore exist in memory before the reviewed binding's former fixed-message redaction. The original exception also carries private response/request objects; those objects must never be serialized.

`provider_error_diagnostic()` in [provider.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/app/v2/provider.py:60) now projects only these exact fields:

| Field | Allowed retained value |
| --- | --- |
| http_status | Integer 100–599, or null; booleans/invalid values become null |
| category | One of invalid_request, authentication_failure, insufficient_balance, rate_limit, server_error, timeout, transport_failure, http_error |
| provider_error_type | A canonical allowlisted code/type, or null |
| provider_error_code | A canonical allowlisted code/type, or null |
| provider_error_message | An entire canonical allowlisted sentence, or null |
| body_status | json, non_json, missing, oversized |
| message_status | retained, missing, omitted, oversized |

Here `json` means a decoded object is available; `non_json` means no decoded error object is available from the SDK (including raw/non-object bodies). This is not independent authentication of the provider body.

The coarse category mapping is 400 → invalid_request; 401/403 → authentication_failure; 402 → insufficient_balance; 408 → timeout; 429 → rate_limit; 5xx → server_error; other HTTP statuses → http_error. Without a status, built-in timeout/SDK APITimeoutError → timeout, otherwise transport_failure. These are status/transport categories, not invented detailed provider reasons.

The reviewed receipt's existing category/status/fixed redacted message remain compatible. The safe projection is added under `provider_diagnostic`. Thus old top-level categories can remain generic while the nested diagnostic distinguishes balance/server/timeout cases. A second validator, `retained_provider_diagnostic()`, rechecks exact keys, scalar types and allowlists before the attachment is copied into a receipt.

## B. Exact conservative redaction policy

Only `message`, `code` and `type` from the decoded error object are considered, with code/type SDK metadata used as a fallback. No headers, Authorization, bearer value, arbitrary exception message, request payload, provider-supplied URL or extra response field is copied.

All code/type values must match one of these finite constants, ignoring case and surrounding whitespace:

`invalid_request, invalid_request_error, invalid_request_format, invalid_parameter, authentication_error, authentication_failure, invalid_api_key, insufficient_balance, rate_limit, rate_limit_error, server_error, internal_server_error, model_not_found`.

All retained message values must match one of these finite full sentences, ignoring case and surrounding whitespace:

- Invalid request body
- Invalid request body.
- Invalid request format
- Invalid request format.
- Invalid request
- Invalid request.
- Authentication Fails
- Invalid Authentication
- Insufficient Balance
- Rate Limit Reached
- Server Error
- Server Overloaded
- Unknown parameter: max_completion_tokens
- Unrecognized request argument supplied: max_completion_tokens
- Invalid response_format
- Invalid model
- Response format requires one of the messages to contain the word 'json' in some form.

The returned value is the known constant, never a substring copied out of arbitrary provider text. Any value containing the actual configured API key, case-insensitively, is rejected even if it would otherwise match an allowlist. Secret-bearing and unknown messages are omitted entirely, rather than attempting partial regex masking. This prevents secret fragments and echoed request bodies from surviving a sanitizer.

Messages above 256 characters are omitted with message_status=oversized. If the SDK's original response bytes exceed 8192 bytes, code/type/message extraction is suppressed and body_status=oversized. Missing/malformed/non-object bodies retain only available safe coarse metadata. No exact reason is invented when none is supplied or safely retainable.

These limits bound retained diagnostics and extraction; they do not claim to prevent the SDK from initially buffering a response in private memory.

## C. Raw provider-body persistence

**No raw provider error body is persisted.** No raw error-body hash, unrestricted provider text, request headers or Authorization value is added to evidence. Original SDK exceptions remain private at the catch boundary; the reviewed binding continues to raise its fixed safe failure.

Failed signal responses remain PROVIDER_FAILURE with a null prediction/core response and consumed reservation, never a fabricated NO_TRADE. Failure receipts keep response_sha256=null. This change does not rewrite the historical _02 receipt or recover its lost error reason.

## D. Prepared provider-only command — NOT EXECUTED

After separate operator authorization, the prepared command is:

```powershell
& 'C:/Users/Priyanka/Desktop/ai-trading-bot-research/.venv/Scripts/python.exe' -B 'C:/Users/Priyanka/Desktop/ai-trading-bot-research/scripts/run_deepseek_provider_smoke.py' --run-id provider-smoke-01 --enable-provider-smoke
```

This command is recorded for review only. No real smoke command was invoked in this task, including no live CLI invocation with the enable flag.

Future output is fixed beneath:

`C:/Users/Priyanka/Desktop/ai-trading-bot-research/diagnostics/deepseek_provider/provider-smoke-01/`

The CLI accepts a restricted run ID rather than an arbitrary output path. Linked/junction diagnostic roots are rejected. It cannot target _02, _03, Sessions 02–05 or a trading output directory. Repeating an existing run ID blocks; it never creates a replacement run ID automatically.

[provider_smoke.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/app/v2/provider_smoke.py:88) calls the same application-owned `DeepSeekProvider` transport used by the reviewed binding. It deliberately does not construct the trading `_ReviewedRun`, whose preflight includes market/account concerns. Its imports and execution path have no Upstox client, market capture, calendar, order, account, position, fill or settlement operation. Package initialization imports only pure V2 contracts/research/safety definitions, not a broker/market transport.

Request policy:

- Current provider base URL constant: https://api.deepseek.com, unchanged and shared with the reviewed provider.
- Configured model must equal deepseek-flash.
- Final wire JSON contains exactly model, messages, max_tokens=64 and response_format={"type":"json_object"}.
- System: `Return exactly this JSON object and no other text: {"status":"OK"}.`
- User: `Return JSON: {"status":"OK"}`.
- No stream, sampling override, thinking/reasoning or unsupported OpenAI body parameter is added.
- SDK retries=0; redirects disabled; phase timeout=min(configured timeout, 5 seconds).
- One spawned worker and one provider invocation. A 10-second deadline includes worker-start elapsed time; the parent terminates a timed-out worker, with bounded 0.5-second join and kill/join cleanup. No timeout/failure retry is authorized.
- Successful evidence retains only the validated fixed object {"status":"OK"}, never arbitrary model output. Request determinism does not guarantee model output; a different or malformed response fails the smoke check.
- execution_enabled, trade_placement_enabled and paper_execution_enabled are always false.

Exclusive output creation claims the run before any request. Files are written exclusively and fsynced:

1. request.json — harmless exact messages, wire settings and disabled-execution policy.
2. request_budget_config.json — max_total_requests exactly 1, with no CLI override.
3. request_budget.json — request identity and requests_consumed=1.
4. send_authorization.json — conservative single-send authorization, explicitly not proof of transmission.
5. result.json — bounded result and sanitized diagnostic, if publication completes.

No child is launched until the first four files are published. Every existing output directory, including an empty/partial/corrupt one, blocks another send. A crash before transmission can therefore consume the opportunity without sending; no refund/replacement follows. Two concurrent claims on the same run ID permit only one caller to enter the worker. A new run ID is a separate operator action; this task does not authorize repeated diagnostic runs.

Credentials are loaded only after the explicit enable flag and run-ID validation. They are passed to the child in memory, not stored in the request or result. The smoke path does not require Upstox credentials or a trading authorization. Its stdout contains only the bounded summary and output path.

## E. Files changed and scoped Git diff/stat

| File | Insertions | Deletions |
| --- | ---: | ---: |
| app/v2/provider.py | 80 | 25 |
| app/research/ai_led_reviewed.py | 6 | 2 |
| app/v2/provider_smoke.py (new) | 124 | 0 |
| scripts/run_deepseek_provider_smoke.py (new) | 29 | 0 |
| tests/test_provider_diagnostics.py (new) | 269 | 0 |

This report is a sixth new/changed task file. The only changed pre-existing files are provider.py and ai_led_reviewed.py. Source/prompt/risk/budget changes from prior tasks were not reverted. No strategy, signal prompt, model selection, trading capital, trading request ceiling, purpose slot, attestation or calendar was changed here.

The pre-change workspace already contained tracked modifications and untracked research files. Before editing, byte copies of the two affected sources and a 604-file hash baseline were saved externally. Task-scoped Git no-index comparisons against those original copies avoid attributing unrelated HEAD differences to this work. New files are compared against an external empty file.

External patch, per-file numstat and preservation checks:

`C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/provider-diagnostics-ed20e9ccd6ca45cb81f20454d2d0fdef/task-diff.patch`
`C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/provider-diagnostics-ed20e9ccd6ca45cb81f20454d2d0fdef/final-preservation.json`

## F. Offline tests and checks

The external runner disables dotenv before application imports, blocks .env reads, removes inherited UPSTOX_/DEEPSEEK_/OPENAI_ variables, blanks target credentials, blocks socket/DNS/datagram calls and subprocess launches, disables pytest plugin autoload/cache/logging, and rejects writes outside its external isolation directory. Python uses -B; each run uses a fresh UUID temporary directory.

The new tests use fake credentials, synthetic inputs, the real SDK serializer with HTTPX2 MockTransport, and a mocked multiprocessing context. No actual child process or socket is used by the smoke tests. They call the worker target against the mocked transport and exercise deadline cleanup with a fake hung process.

Exact final commands executed from the project directory:

```powershell
& 'C:/Users/Priyanka/Desktop/ai-trading-bot-research/.venv/Scripts/python.exe' -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/provider-diagnostics-ed20e9ccd6ca45cb81f20454d2d0fdef/run_offline.py' focus-final
& 'C:/Users/Priyanka/Desktop/ai-trading-bot-research/.venv/Scripts/python.exe' -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/provider-diagnostics-ed20e9ccd6ca45cb81f20454d2d0fdef/run_offline.py' regression
& 'C:/Users/Priyanka/Desktop/ai-trading-bot-research/.venv/Scripts/python.exe' -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/provider-diagnostics-ed20e9ccd6ca45cb81f20454d2d0fdef/run_offline.py' compile
```

| Selection | Passed | Failed | Errors | Skipped | Deselected | Pytest duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Final focused provider diagnostics | 34 | 0 | 0 | 0 | 0 | 24.88 s |
| Relevant transport/provider/signal regressions | 399 | 0 | 0 | 0 | 0 | 493.98 s |

Both exit codes were zero. Recorded wrapper wall times were 25.49 s and 494.80 s respectively.

The 34 focused cases cover ordinary and secret-bearing 400 bodies; 401/402/429/500; missing/non-JSON/oversized/unknown bodies/messages; configured-secret exclusion even for otherwise allowlisted text; rejected malformed diagnostic attachments; timeout/transport distinctions; null signal predictions and consumed single-use provider reservations; no retries; exact smoke wire body; deadline cancellation; concurrent claims; publication crashes; repeat blocking; arbitrary successful-output suppression; and disabled execution flags.

The relevant regression selection includes these tests plus the previous DeepSeek wire-contract suite, AI_SIGNAL_ONLY, request-remediation, complete/torn seals, observation chain, evaluation accounting, guarded/reviewed adapter, synthetic provider, Upstox deadline modules and the historical/current client-attestation and tampered-evidence readiness nodes. It uses only offline fixtures; synthetic account checks remain inside isolated temporary directories. The full repository suite was not run. Group counts overlap.

Compilation passed for nine relevant files without writing bytecode. Scoped Git whitespace checks passed with existing CRLF respected, using:

`git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --no-index --check -- BEFORE AFTER`

No scoped diagnostics were emitted. Git no-index exit 1 represents expected file differences, not a failed whitespace check. No unrelated README whitespace or historical file was changed.

The preliminary 30-case focused run also passed before four extra tests and the final allowlist/documentation refinements were added; final results above are the completed-code results. All result JSON files are retained externally.

## G. Protected evidence integrity

The original baseline contains 604 existing inventoried files, including 423 protected files under backtests/data/docs/reports/experiments/logs. Final SHA-256 comparison found all 423 protected files unchanged and no baseline file missing. The only changed pre-existing files are app/v2/provider.py and app/research/ai_led_reviewed.py.

All 21 _02 files remain byte-for-byte unchanged, including request/market budgets, manifest, lock, inputs, request, response, signal, send/receipt ledgers and seals. Sessions 02/03/04/05 retain all 7/4/3/8 baseline hashes respectively. Historical budgets, attestations, calendar evidence and existing broker-order state were not modified. The exact per-file comparison, current source hashes and scoped Git checks are recorded in final-preservation.json.

No production diagnostic output directory was created. The future smoke output exists only as a prepared path; all test artifacts are external temporary fixtures. No real credentials were read or used.

The baseline excludes .git, .venv, bytecode/cache directories, prior temporary audit/remediation directories and .env. It was captured before edits and was not regenerated. The test write guard protects the entire project independently of that hash inventory. Previous reports remain unchanged; this report and the three new code/test files are explicit additions outside the baseline.

## H. Readiness verdict and limits

**READY_FOR_ONE_PROVIDER_SMOKE_TEST**

This is technical readiness for one separately authorized provider-only diagnostic request, not execution approval. No live request, provider smoke CLI, trading experiment, trial _03 or broker order was executed or authorized by this task.

Unknown messages/codes/types are intentionally omitted, so some future provider-specific details may remain unavailable. Successful live provider access, current server contract/availability and real OS process launching were not verified: the tests mock the process boundary and all HTTP I/O. The helper's safety policy remains fail-closed if worker launch, request, timeout or evidence publication fails. Filesystem immutability is enforced by exclusive application writes and existing-directory refusal, not by an external WORM store; power-loss durability remains subject to OS/filesystem guarantees.

Trial _02 remains immutable. Its historical exact HTTP-400 reason remains unrecoverable. Trial _03 remains NOT AUTHORIZED. Broker execution remains disabled. Real-time paper execution and causal settlement remain NO-GO.
