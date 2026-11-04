# Root Causes

Signal preflight inherited the reviewed paper prompt hash instead of hashing its own prompt. The evaluator summary iterated only supplied, completed outcome rows, allowing omitted evaluations to disappear from its rate denominators without coverage accounting.

# Prompt Hash Correction

Signal preflight now hashes the exact `PROMPT` text as UTF-8 bytes. Both the signal manifest's prompt identity and its inherited account metadata use that hash. Saved-input verification compares the saved request's exact prompt, its hash, both manifest identities and the request digest before authorization or recovery.

The existing immutable configuration comparison rejects changed prompts, conflicting hashes and old source-bound manifests. No historical manifest, request, response or seal is migrated. The quantitative and reviewed paper prompts remain unchanged.

# Evaluation Reconciliation Changes

Expected evaluations come from each valid prediction's complete instrument universe and frozen forecast window. An omitted evaluation or instrument row becomes an explicit missing outcome. With an explicit `as_of` before the window ends, it becomes pending instead. No wall clock is inferred.

The evaluator distinguishes pending horizons, absent/insufficient observations, invalid observations, completed price movement and ambiguous stop/target ordering. Genuine NO_TRADE decisions retain universe-wide movement evaluations for descriptive baseline comparisons. Failed provider decisions remain failures, not NO_TRADE.

The summarizer rejects duplicate/unknown identities, mismatched decision or policy hashes, changed selections, contradictory movement labels, invalid timing and inconsistent outcome metadata. Conflicting summaries fail closed; invalid future-data rows are counted separately. Every expected instrument/window appears in `evaluation_accounting`.

# Prediction Denominators and Coverage

The summary reports recorded decisions, valid BUY decisions, NO_TRADE decisions, expected instrument evaluations, expected selected BUY evaluations, completed, pending, missing/insufficient, invalid and ambiguous counts. `total_valid_buy_predictions` counts valid records whose action is BUY; `total_expected_buy_evaluations` counts their selected instrument proposals.

Completed, pending, missing and invalid counts partition the expected evaluation set. Ambiguity is a subset of completed movement evaluations, not an additional completed outcome or an execution result.

`rate_denominators` names the denominator for each overall rate; every confidence bin also carries its upward-frequency denominator. Evaluation coverage is completed / expected * 100. Conditional upward movement rates remain descriptive and are always accompanied by coverage and unevaluated counts.

In the regression example, two outcomes (one positive and one negative) yield 50% upward frequency with denominator two and 100% coverage. Omitting the negative evaluation yields conditional upward frequency 100% with denominator one, coverage 50%, one missing outcome and one unevaluated prediction. No missing outcome is assigned a positive or negative label.

# Future Observation Provenance

The evaluator reuses canonical `_digest`, candle validation and aware timestamp validation. It retains a canonical body hash, supplied source, observation timestamp, symbol and instrument key alongside each consumed future body. Completed-row summaries cross-check that identity, hash and observation timing.

These inputs remain caller-supplied numeric bodies. This API has no retained acquisition envelope, original file-byte identity or independently authenticated provider receipt to verify. It explicitly labels provenance `UNVERIFIED_CALLER_SUPPLIED`; source labels are not authentication. This is a separate readiness concern for measuring genuine forecast quality. Preserve original future observations and verify their acquisition/integrity chain before making research claims. No new acquisition mechanism or invented provenance was added.

# Files Changed

- `app/research/ai_signal_only.py`: correct and verify exact prompt identity.
- `app/research/signal_evaluation.py`: outcome states, reconciliation, coverage, denominators and explicit provenance limits.
- `tests/test_ai_signal_only.py`: prompt mismatch/resume regressions and pending-status assertion.
- `tests/test_signal_evaluation_accounting.py`: synthetic evaluation-accounting regressions.
- This new report; prior reports remain unchanged.

# Tests Added

Thirty additional cases cover prompt agreement/conflicts/immutable recovery; two complete outcomes; omitted negative outcomes and instrument rows; pending horizons; missing, empty, short, corrupt and incorrectly identified observations; ambiguity; duplicate/unknown/conflicting evaluations; NO_TRADE; denominators; deterministic summaries; retained observation identity and rejected cutoff changes. Existing mocked collection tests continue to verify account isolation and disabled real transports.

# Verification Results

Clean focused run: **62 passed in 69.69 seconds**, without exclusions. The first development run crossed a source edit and failed one source-bound fixture; the clean rerun used fixed source bytes.

Full relevant regression: **784 passed in 327.36 seconds**, without failures, skips or deselections. This is the original 24-file selection (754 cases before these fixes), plus the new accounting module and 30 added cases. It is not a claim that every unrelated repository test was run.

Test invocations disable `.env` loading, use mock credentials, block socket connections and put temporary artifacts outside repository evidence. All four changed/new Python files passed in-memory compilation; all five task files passed targeted CRLF-aware whitespace checks. The final task diff includes untracked source files omitted by ordinary Git diff.

Task-start SHA-256 verification preserves all 386 pre-existing documentation, data, backtest and report files, including the protected historical evidence, Sessions 02–05, budgets and original attestations. Of 505 inventoried existing files, only the two signal modules and existing signal test file changed. The accounting test module and this report are new. Shared guarded/offline/reviewed code, CLI, Upstox transport and original prompts remain byte-identical.

# Backward Compatibility

The six-bar horizon, reference-close movement definition, baseline predicate and existing rate definitions remain unchanged. Summary fields are additive. Pending and invalid observations now have explicit statuses instead of being collapsed into missing data. Older evaluation objects lacking the new cutoff/evidence metadata must be recomputed offline from preserved observations; they are not silently trusted or rewritten.

Collection/recovery remain source-hash bound. Old experiment manifests are not upgraded. Shared paper/quantitative workflows, budgets, attestations and CLI authorization flags are unchanged.

# Remaining Risks

The caller must supply the complete set of integrity-checked saved predictions; the summarizer cannot detect prediction records withheld from its input. For a common reporting cutoff, pass `as_of` explicitly; supplied evaluations must match that cutoff. Conflicting evidence raises an error instead of producing partial statistics.

Future observation authenticity remains unverified. Movement is measured relative to the earlier decision-batch close, not an executable fill or exact readiness-to-horizon return. Scores are not established probabilities and the evaluator never computes execution P&L.

# Signal-Only Trial Readiness

The two implementation defects are corrected and the relevant offline regression passes. The implementation is ready for a final read-only safety review of a bounded prediction-only trial. This task does not authorize network requests. Genuine outcome-provenance readiness remains separate from prediction-only collection. Real-time paper fills and broker execution receive no approval.

# Exact Recommended Next Step

Perform a read-only review of these changes and verification results. Define how all immutable predictions and retained future observations will be supplied and verified for offline evaluation. Only then consider separately authorizing one fresh-directory, bounded signal-only collection. Do not start or resume a genuine experiment as part of this correction task.
