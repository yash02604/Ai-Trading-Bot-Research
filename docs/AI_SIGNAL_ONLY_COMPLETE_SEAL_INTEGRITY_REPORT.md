# AI_SIGNAL_ONLY Complete-Seal Integrity Remediation

Date: 2026-11-03. Scope: the two complete-seal integrity findings only. This report records remediation and offline validation, not an independent audit or experiment authorization.

## Files changed

- Existing application file: `app/research/signal_observations.py`.
- New focused tests: `tests/test_signal_complete_seal_integrity.py`.
- New report: `docs/AI_SIGNAL_ONLY_COMPLETE_SEAL_INTEGRITY_REPORT.md`.

No other pre-existing inventoried file changed. Shared JSONL writing, CLI entrypoints, transports, budget persistence, purpose slots, signal logic, prompts, models, capital and risk settings were not modified.

## Root cause and duplicate-key strategy

Ordinary JSON decoding silently collapsed duplicate object keys. Complete seal records previously required primarily a nonempty request ID, and seals outside the expected artifact matching loop could escape validation.

At `signal_observations.py:208`, `_unique_seal_fields()` now rejects every repeated object key through `object_pairs_hook`, whether values are equal or conflicting and whether fields concern identity. It raises the existing `GuardedBatchError("future_seal_ledger_corrupt")`. That exception is not caught as JSON truncation, so complete duplicate-key objects never become torn-tail recovery.

## Exact complete-record schema

At `signal_observations.py:217`, `_read_future_seals()` requires the exact existing four-field `_seal()` contract:

- `request_id`
- `experiment_sha256`
- `observation_sha256`
- `saved_artifact_sha256`

Every value must be a string matching `[0-9a-f]{64}`. Missing/extra fields, wrong types, invalid hash shapes and repeated complete request IDs fail closed. No new evidence format or migration was introduced.

## Scheduled identity and evidence validation

At `signal_observations.py:254`, `evaluate_experiment()` derives legitimate jobs using the existing frozen configuration, validated predictions, calendar and `jobs()` policy. Every complete seal is checked against that job set before observation matching: its request must be scheduled, an observation artifact must exist, and its experiment hash must equal the derived job's experiment hash.

The existing artifact loop checks exact equality between observation identity and the derived job. This binds request, experiment, prediction ID/hash, instrument key/symbol, purpose and future window. Successful-capture verification reconstructs the observation using the saved raw response, normalized snapshot, reservation, physical-attempt checkpoint, send marker and receipt. Both seal hashes must equal the recomputed `_seal()`. Failed captures must match the existing exact `CAPTURE_FAILED` record. Unknown, foreign or conflicting complete seals are never filtered out or ignored.

At `signal_observations.py:329`, the locked follow-up path evaluates the existing ledger and evidence chain before entering acquisition or creating a reservation. At `signal_observations.py:84`, `future_preflight()` repeats validation at the mandatory transport boundary, covering direct future-fetch/send calls. Invalid complete seals raise locally. Recognized torn tails return the existing explicit partial result through follow-up; direct transport preflight rejects them.

## Duplicate seals and torn tails

The existing append contract is idempotent: asking the same `AppendOnlyJsonl` instance to append the identical record again returns false without adding bytes. This does not authorize two physical ledger rows. Exact duplicate rows and conflicting duplicate rows both fail closed.

The existing torn-tail classification condition was retained: only an unterminated physical final object with a recognized EOF/unterminated-string JSON error becomes `UNRECOVERABLE_PARTIAL_PUBLICATION`. Valid complete prefix records stay readable and unchanged. Complete schema/identity corruption preceding a torn tail still raises an integrity failure. Internal or newline-committed malformed JSON remains fatal. No bytes are removed, normalized, repaired or replaced.

Absent final seals, missing outcomes, the expected prediction denominator, deterministic replay and the prohibition on using unsealed observations as positive/negative outcomes retain existing behavior.

## Request-accounting invariants

No logical reservation, physical checkpoint, durable send marker, purpose binding, provider authorization, retry/redirect policy or ceiling changed. The bounded path retains at most two physical market-data attempts with separate `INITIAL_SNAPSHOT` and `FUTURE_OBSERVATION` purposes, and one physical provider attempt. Completed initial predictions remain reusable without reacquisition.

Invalid complete evidence produces zero additional mocked market/provider sends and no ledger changes. Tests also reserve the future slot before injecting invalid evidence and directly invoke fetch/send: the reservation remains spent, the physical counter does not advance, and no refund, replacement authorization or fabricated evidence is created.

## Offline test controls

Before collection, the external runner disables dotenv loading, removes inherited Upstox/DeepSeek/OpenAI credential variables and blanks target credentials, disables third-party pytest plugin autoload, blocks socket connect/DNS/datagram operations and subprocess launches, and installs a write audit hook allowing writes only beneath its external isolation root. Python runs with `-B`; pytest cache/logging plugins are disabled. Each invocation uses a fresh UUID temporary directory outside the project.

The inspected fixtures supply synthetic candles/predictions, fake credentials and mocked HTTP/SDK transports. Existing project evidence is read-only to these runs. No real experiment, provider request, broker order or operational paper position/fill was created.

External runner, original baseline and machine-readable final results:

`C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/complete-seal-remediation-409cba48a11d4cc0bd6d699629708a21`

### Exact commands executed

Working directory: `C:\Users\Priyanka\Desktop\ai-trading-bot-research`.

```powershell
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/complete-seal-remediation-409cba48a11d4cc0bd6d699629708a21/run_offline_tests_final.py' new
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/complete-seal-remediation-409cba48a11d4cc0bd6d699629708a21/run_offline_tests_final.py' torn
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/complete-seal-remediation-409cba48a11d4cc0bd6d699629708a21/run_offline_tests_final.py' prior
& .\.venv\Scripts\python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/complete-seal-remediation-409cba48a11d4cc0bd6d699629708a21/run_offline_tests_final.py' broader
```

The runner records the expanded pytest arguments, including the unique temporary directory, in each `*-final-result.json`. Result files are created exclusively; preserve them. Re-execution requires a fresh external runner/results directory.

### Final results on the completed source

| Selection | Passed | Failed | Errors | Skipped | Deselected | Duration (seconds) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| New complete-seal tests | 55 | 0 | 0 | 0 | 0 | 127.45 |
| Existing torn-seal focused tests | 34 | 0 | 0 | 0 | 0 | 26.29 |
| Prior budget/redirect/restart and transport selection | 69 | 0 | 0 | 0 | 0 | 162.92 |
| Broader relevant regression selection | 221 | 0 | 0 | 0 | 0 | 384.25 |

All exit codes were zero. Durations are recorded wall time inside each offline wrapper; the broader pytest terminal itself reported 383.57 seconds. Groups overlap; the broader row includes focused selections and is not an additional distinct-test count. Preliminary runs before the final transport-boundary check are excluded.

The 55 new tests cover duplicate identity/nonidentity JSON fields with/without a newline; all required fields and hash shapes; extra fields; idempotent append versus duplicate physical rows; incomplete/unknown/wrong-experiment seals before unspent capture; unknown complete prefixes before torn tails; missing observation artifacts; prediction/instrument/purpose/window/request mutations with recomputed local hashes; changed raw responses and seal hashes; exact/conflicting duplicates; deterministic repeated replay/capture; byte and budget preservation; valid evidence reuse; and direct transport bypass attempts.

The broader selection contains:

- `tests/test_signal_complete_seal_integrity.py`
- `tests/test_signal_torn_seal_recovery.py`
- `tests/test_signal_request_remediation.py`
- `tests/test_ai_signal_only.py`
- `tests/test_signal_observation_chain.py`
- `tests/test_signal_evaluation_accounting.py`
- `tests/test_upstox_deadlines.py`
- `tests/test_synthetic_provider_check.py::test_provider_timeout_is_bounded_and_retry_is_disabled`
- `tests/test_readiness_evidence.py::test_historical_client_and_current_attestation_are_distinct`
- `tests/test_readiness_evidence.py::test_modified_current_or_historical_client_evidence_is_detected`

The full repository suite was not run.

## Preservation verification

The pre-change external baseline contains 578 existing inventoried files, including 399 protected files under backtests/data/docs/reports/experiments/logs. After testing, all 399 protected hashes were unchanged; no baseline file was missing. The only changed pre-existing file was `app/research/signal_observations.py`. Sessions 02/03/04/05 retained all baseline hashes: 7/4/3/8 files respectively.

Application source SHA-256 before:
`5a4a26bc91602a57a88ad88c30462c6cb184b8472ea8501865b3372793a5faa3`.

Application source SHA-256 after:
`73e6a234ab22f760629ed3ac2cd503a845ce3b94f3eaa805c4fecbc0238155ac`.

The pre-change baseline was not regenerated. The new test and this new report are additions outside that baseline. Previous reports remain unchanged. Per-test byte snapshots verify rejected replay/capture attempts do not modify existing raw responses, observations, seals, receipts or budget records; valid complete evidence is reused unchanged. Preservation details are saved externally in `preservation-after-tests.json`. The inventory excludes .git, .venv, cache/bytecode directories, prior temporary test directories and .env; no claim is made to hashing those excluded files.

## Remaining limitations and decision

These tests validate synthetic local evidence chains, not a genuine real-data experiment or provider authenticity. No real experiment was started and no external request was made. This change adds no evidence migration or repair. Existing manifests binding earlier source hashes can fail closed after source changes; historical artifacts were not rewritten. Filesystem durability/locking assumptions and the recognized torn-tail grammar are unchanged. Stronger adversarial tamper authentication and additional power-loss durability are outside this remediation.

Real-time paper trading remains NO-GO. Causal settlement remains NO-GO. Broker execution remains disabled.

**Ready for independent read-only audit only. No experiment authorized.**

