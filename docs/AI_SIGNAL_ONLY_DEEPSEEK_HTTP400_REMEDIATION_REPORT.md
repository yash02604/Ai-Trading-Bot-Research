# AI_SIGNAL_ONLY DeepSeek HTTP-400 and Failure-Evidence Remediation

Date: 2026-11-04. This is a local remediation report, not authorization for another experiment.

## A. Root cause: what is and is not established

The exact reason DeepSeek rejected the historical _02 request is **NOT VERIFIED**. The retained HTTP receipt has status 400 but no provider reason/body. A generic 400 does not establish which validation rule failed. No network request was made to recover or reproduce that server-side diagnosis, and no historical evidence was rewritten.

A specific local request deficiency is established: _02 requested `response_format: {"type":"json_object"}`, but neither saved message contained the word JSON or an explicit JSON-output instruction. This is consistent with the JSON-mode instruction requirement and is the leading explanation for the 400, rather than proof of the historical server reason. Current DeepSeek documentation was not fetched because the user prohibited network access. Endpoint/model/token-field/response-format comparison uses the contract supplied in the request. The JSON-mode instruction requirement has not been independently revalidated against current online documentation during this task.

The minimal correction adds the sentence `Return JSON.` to the signal-specific system prompt. This makes the required output format explicit without changing signal selection, prices, risk limits, model, capital, request ceilings or purpose slots. Prompt hashes change normally for new manifests; the change is not hidden in HTTP serialization.

**The suspected max_completion_tokens wire-name bug is ruled out for the inspected adapter and installed SDK.** No token-field change was made.

## B–C. Exact HTTP body before and after

The actual final serialized request was intercepted using the real application-owned client and installed OpenAI SDK 3.35.1, with HTTPX2 MockTransport replacing socket I/O. The pre-change probe used the saved _02 payload and fake credentials. It captured one mocked POST, zero retries and redirects disabled.

Before and after, the complete top-level JSON key set is exactly:

`max_tokens, messages, model, response_format`

Before:

```json
{
  "model": "deepseek-flash",
  "messages": [
    {"role": "system", "content": "<exact saved _02 system_prompt>"},
    {"role": "user", "content": "<exact saved _02 user_payload string>"}
  ],
  "response_format": {"type": "json_object"},
  "max_tokens": 512
}
```

After: the same key set, types and values, with the signal system prompt now including `Return JSON.` immediately before `Predictions only; no executable fills or account authority.`. The user message remains the exact saved payload string. The angle-bracket contents above abbreviate message values only; the offline test compares the complete actual decoded HTTP body to both saved messages, without abbreviation or partial-key assertions.

`max_completion_tokens` was **not** emitted by the inspected code. It remains the compatible internal constructor/policy name, mapped by `DeepSeekProvider.complete()` to wire `max_tokens`. The pre-change provider source hash matches the provider hash frozen in _02 and remains unchanged:
`cdf1e1af244016d03ecf96dca4c0edfa85e96cd6787ee5d53dc923d56e3d19fd`.

Historical original wire bytes and the historical SDK version were not retained in _02. Consequently, the pre-change result is an exact reconstruction using the saved messages, matching adapter source and currently installed SDK, not a claim that _02 retained a literal wire capture.

| Contract item | Final serialized behavior |
| --- | --- |
| Endpoint | POST https://api.deepseek.com/chat/completions |
| Model | deepseek-flash, unchanged; valid according to the user-supplied current contract |
| Messages | Exactly system and user, with string content preserved from the saved request |
| Token limit | max_tokens: 512; no max_completion_tokens field |
| JSON output | response_format: {"type":"json_object"} |
| stream / stream_options | Both absent; no streaming requested |
| temperature / top_p | Both absent; no sampling change |
| thinking / reasoning | No thinking/reasoning/reasoning_effort field |
| Other OpenAI-specific parameters | None emitted; exact body equality rejects additions |
| Null/empty parameters | None among the four emitted top-level values; optional SDK values are omitted |
| Headers | Authorization: Bearer credential in memory only; Content-Type: application/json |

The path inspected is:

1. `scripts/run_ai_led_prospective_paper.py:53` dispatches `--signal-only` directly to `run_signal_only()`.
2. `app/research/ai_signal_only.py:250` freezes/saves system and user payloads, reserves the provider budget, then calls the reviewed binding.
3. `app/research/ai_led_reviewed.py:420` validates authorization, writes the single-use send marker, instantiates `DeepSeekProvider`, and records its result/error receipt.
4. `app/v2/provider.py:82` constructs the application-owned client with base URL, timeout, `max_retries=0` and `follow_redirects=False`. Its `complete()` at line 92 passes exactly model, messages, response_format and max_tokens.
5. Installed SDK `openai/resources/chat/completions/completions.py:1319` builds POST /chat/completions; `_utils/_transform.py:265` removes omitted optional fields; `_base_client.py:653` uses `openapi_dumps()`; `_utils/_json.py:11` emits UTF-8 JSON bytes. MockTransport inspects those final request bytes, rather than merely inspecting SDK keyword arguments.
6. `app/research/deepseek_prospective.py` supplies `PersistentRequestBudget` and `AppendOnlyJsonl` on this path; it does not rewrite the HTTP body. Its separate smoke/prospective routines call an injected provider and are not the signal CLI branch.

## D. Locally generated NO_TRADE placeholder

Yes: the old failure's paper-schema `NO_TRADE` was locally generated by `guard._no_trade()`, assigned before the provider call in `run_signal_only()`. It was not a returned provider prediction.

The old _02 signal already had status PROVIDER_FAILURE, prediction null, taken false and execution disabled. Its evaluation status already excluded it from valid prediction statistics. The defect was misleading lower-level response evidence.

New unsuccessful signal evidence initializes `core_response` to null. Only validated proposals populate it. The shared validator now accepts this null failure contract only when the trusted signal publication path passes `signal_only=True`; paper/mock validation keeps its existing separate contract. Parsing is performed only on valid proposal evidence. The signal validator rejects legacy failure placeholders instead of silently converting them.

For HTTP failure, status remains PROVIDER_FAILURE, reservation_consumed remains true, prediction/rationale/response timing remain null, and no NO_TRADE prediction is fabricated. A genuine schema-valid provider NO_TRADE remains a valid prediction and is tested separately.

Compatibility is fail-closed: changed source/prompt identities prevent an old immutable manifest from being adopted. A temporary copy of _02 was tested for local recovery rejection with zero calls. The real _02 was only read and hashed, never opened through the experiment runner or lock.

## HTTP-400 diagnostics and redaction

`_error_fields()` now maps provider submission-stage HTTP 400 to the fixed category `invalid_request`, retaining numeric status 400 and the existing fixed redacted message. No provider message, provider-supplied code/type, header or credential is copied into the receipt. Authentication/rate-limit handling stays separate.

Failed receipts now store `response_sha256: null`. Previously, hashing the absent response produced the SHA-256 of JSON null, which could misleadingly imply response evidence. This is a new-receipt correction only.

These bounded classifications distinguish invalid requests from generic transport failures but do not preserve enough provider detail to establish an exact server validation reason. This limitation is deliberate: arbitrary provider text can echo credentials. Tests inject credential-bearing error messages/code/type and verify they are absent from every retained test artifact.

## E. Exact changed files and Git diff/stat

Only these three pre-existing source files changed:

| File | Insertions | Deletions |
| --- | ---: | ---: |
| app/research/ai_signal_only.py | 4 | 3 |
| app/research/ai_led_guarded.py | 4 | 3 |
| app/research/ai_led_reviewed.py | 3 | 1 |

New files:

- `tests/test_deepseek_signal_wire_contract.py`: 207 lines.
- `docs/AI_SIGNAL_ONLY_DEEPSEEK_HTTP400_REMEDIATION_REPORT.md`: this report.

`app/v2/provider.py`, `deepseek_prospective.py`, all budgets, send-marker logic, attestations, calendars and historical evidence were not changed. Existing unrelated workspace changes were preserved.

Because two of the edited application files are untracked in the existing checkout, ordinary HEAD diff is not a complete task diff. Pre-change byte copies were saved externally before editing. Git `diff --no-index` against those copies produced the scoped patch/stat; new files are compared against an external empty file. The complete task patch, per-file numstat and checks are retained externally.

## F. Offline tests and checks

External artifacts directory:

`C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/deepseek-wire-remediation-1679c7f03de542a79f95974c635fe7e2`

Before pytest collection, `run_offline.py` disables dotenv loading, blocks .env reads, removes inherited UPSTOX_/DEEPSEEK_/OPENAI_ environment variables, blanks target credentials, disables automatic pytest plugin loading, blocks socket/DNS/datagram access and subprocess launches, and rejects writes outside this external directory. Every run uses a fresh UUID temporary directory. Python uses -B and pytest cache/logging plugins are disabled. All transport tests use synthetic values, fake credentials and mocked HTTP/SDK boundaries. Synthetic account tests, where selected to protect the shared validator, use only isolated temporary fixtures.

Exact final commands, run from the project directory:

```powershell
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/deepseek-wire-remediation-1679c7f03de542a79f95974c635fe7e2/run_offline.py' focus3
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/deepseek-wire-remediation-1679c7f03de542a79f95974c635fe7e2/run_offline.py' regression
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/deepseek-wire-remediation-1679c7f03de542a79f95974c635fe7e2/run_offline.py' compile
```

| Selection | Passed | Failed | Errors | Skipped | Deselected | Pytest duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Focused final run (focus3) | 13 | 0 | 0 | 0 | 0 | 17.27 s |
| Relevant regression | 365 | 0 | 0 | 0 | 0 | 513.06 s |

Both exit codes were zero. Wrapper wall times were 18.28 s and 513.68 s respectively. After the regression run, one trailing blank line was removed from the new test file; no executable test or application code changed. The focused suite and compilation were rerun after that whitespace-only cleanup.

The focused suite covers complete final wire-body equality; correct token field; JSON instruction; message preservation; no unsupported/null/stream/reasoning fields; headers; retry/redirect/timeout/response bounds; HTTP 400; consumed reservation and single send across replay/recovery; null prediction/core response; no fabricated NO_TRADE; paper-schema rejection; genuine NO_TRADE; secret exclusion; legacy failure rejection; and the untouched _02 directory.

The relevant regression selection includes the focused suite, ai_signal_only, request-remediation, complete-seal, torn-seal, observation-chain, evaluation-accounting, guarded adapter, reviewed transport, synthetic provider and Upstox deadline modules, plus the historical/current client-attestation and tampered-evidence readiness nodes. Expanded pytest arguments and exact results are saved in the external result JSON files. Focused/regression counts overlap and must not be added as distinct tests. The full repository suite was not run.

Compilation covers the provider, deepseek_prospective, reviewed binding, signal-only module, shared validator and new tests using compile() without writing bytecode: six files passed.

Scoped whitespace checks use Git no-index diff with existing CRLF respected:
`git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --no-index --check -- BEFORE AFTER`.
No scoped whitespace diagnostics were emitted. Git no-index exits 1 for expected file differences; this is not a whitespace failure. The repository-wide `git diff --check` separately reports the pre-existing `README.md:141: new blank line at EOF`; README was not modified.

The initial focused run had 1 pass and 1 failure before stopping: the new test exposed the pre-existing hash-of-null failure-receipt behavior. That was corrected; the final results above replace that incomplete preliminary run. Preliminary results were preserved externally.

## G. Protected-file integrity

The pre-change baseline was captured before edits and was not regenerated. It contains 602 inventoried existing files, including 422 protected files under backtests/data/docs/reports/experiments/logs.

All 422 protected hashes remain unchanged. No baseline file is missing. The only changed pre-existing files are the three source files listed above. All 21 _02 files, including its lock, budgets, manifests, send/receipt ledgers, inputs, responses, seals and signal, remain byte-for-byte unchanged. Sessions 02/03/04/05 retain their 7/4/3/8 baseline file hashes respectively. The two new files are additions outside the pre-change baseline.

Final per-file SHA-256 comparison and scoped Git diff/stat/check outputs are retained in `final-preservation.json` and `task-diff.patch` in the external directory. No historical budget, attestation, calendar, retained evidence or broker-order state was changed.

Inventory exclusions are .git, .venv, bytecode/cache directories, previous audit/remediation temporary directories and .env; no claim is made to hashing excluded credential/cache files. Test write restrictions protect the project independently of that inventory. No real credentials were loaded or used.

## H–I. Remaining risks and verdict

**NOT READY for a fresh bounded AI_SIGNAL_ONLY trial.** The local request/evidence corrections and offline tests do not establish the exact historical server-side rejection cause or independently verify the current live DeepSeek contract. The missing JSON instruction is the leading explanation, not a confirmed retrospective diagnosis. No successful real provider response or full genuine prediction-to-future-observation chain has been validated by this task.

The installed SDK was exercised offline; historical SDK identity/wire bytes and the original sanitized provider reason remain unavailable. Existing response size/time limits are unchanged, including their existing scope; no stronger transport-stream limit is claimed. Old manifests remain immutable and can fail closed after this source/prompt change; no migration was added.

Ready for another independent read-only review of these changes. Trial _03 is neither authorized nor executed. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO.
