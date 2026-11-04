# Project completion and viability review

Date: 4 November 2026 (IST)

## Executive assessment

This project is a substantial research and paper-trading toolkit, not a finished profitable trading business. The central problem is that software correctness, predictive skill, realistic execution and profitability have become interleaved milestones. Completing more safeguards cannot establish a trading edge; obtaining an AI response cannot establish profit.

My recommendation is to freeze feature expansion, choose one canonical prospective workflow, and run a bounded evidence programme with a written go/no-go decision. A negative strategy decision is a valid completion outcome. There is no defensible deadline for achieving profitability.

Planning estimate: 2–4 weeks for an operational, supervised signal-research workflow; 4–8 weeks for a credible paper-trading MVP if suitable market evidence is available; 8–12 weeks for an initial research decision. Statistical confidence may require many more months or years at the existing strategy's low event rate. Broker deployment is separate, conditional work, provisionally another 4–8 weeks after the preceding gates pass. These are engineering estimates, not promises.

## Scope and evidence quality

Reviewed the working tree, main status/readme, selected execution and transport code, test isolation, historical provenance report, exploratory strategy results, and recent calendar, paper-fill and signal-observation reports. This is a targeted architecture/evidence review, not a line-by-line security audit. Existing modifications and evidence were preserved; no broker order or provider request was made. Credentials were not read. Historical performance was inspected from saved reports, not independently recalculated.

Evidence is distinguished below as saved historical results, previously reported verification, direct code/file observations, or recommendations. The large existing modified/untracked working tree means conclusions concern this workspace, not a clean released commit.

## Where we are

| Area | Assessment | Evidence and implication |
|---|---|---|
| Quantitative strategy | Implemented; economic case unsupported | Saved 46-trade baseline loses money before and after costs. |
| AI confirmation | Implemented behind quantitative/risk gates in V1 | `app/trading_engine.py` returns before AI when no strategy signal; a provider skip can be correct behaviour. |
| Independent AI signals | Implemented as a distinct research mode | `docs/GUARDED_AI_SIGNAL_ONLY_REPORT.md`: AI can propose independently, but records no positions or execution profit. |
| Risk/account controls | Substantial offline implementation | Position sizing, capital limits, request budgets, recovery and ledger tests exist. These establish software behaviour, not an edge. |
| Historical data | Structural checks exist; acquisition provenance unresolved | `backtests/provenance_integrity_v1/report.md` and later reports retain unresolved lineage/adjustment/timestamp limitations. |
| Session/source evidence | Significant repair completed | `docs/OFFICIAL_NSE_CALENDAR_CLIENT_ATTESTATION_REPORT.md` supersedes the older calendar blocker with v3 evidence and separate source attestations; future amendments still need checking. |
| Reviewed transports | Implemented | `app/research/ai_led_reviewed.py` performs mode-specific preflight and bounded requests; old saved-response-only limitations must not be repeated as current implementation facts. |
| Prospective outcomes | Offline machinery present; inspected report lacks genuine future-acquisition demonstration | `docs/SIGNAL_FUTURE_OBSERVATION_EVIDENCE_REPORT.md` explicitly identifies this limitation and manual follow-up. |
| Realistic paper fills | Not complete for real quote-based execution | `docs/PROSPECTIVE_PAPER_FILL_VALIDATOR_REPORT.md`: synthetic validator, unknown authentic depth freshness, settlement unsupported. |
| Broker execution | Not implemented as a deployable order system | No completed broker executor/reconciliation evidence in the inspected workflows. |
| Release reproducibility | Incomplete | Unpinned `requirements.txt`, many untracked source/test files, and status pages with multiple older test counts. |

The inspected `backtests/ai_signal_chain_trial_20261104_01` contained only `.prospective.lock`, not a completed prediction/outcome bundle. This does not prove why it stopped or whether another process owns it. Do not delete the lock or reset budgets without diagnosing ownership and recovery state.

## Why completion keeps moving

1. **The target has changed between workflows.** Quantitative-plus-AI confirmation, independent AI proposals, paper accounts, and prediction-only evaluation are different products. A successful signal-only run does not complete executable trading.
2. **There is no demonstrated profitable strategy to deploy.** Continued engineering is addressing reliability while the economic hypothesis remains unsupported.
3. **Data issues are upstream of conclusions.** Valid OHLC shape and matching file hashes do not establish acquisition lineage, candle timing or corporate-action treatment. Missing historical evidence cannot be recreated by updating a hash.
4. **Too much progress is measured by synthetic checks.** The latest signal report records 815 passing tests in a selected regression suite, but explicitly says genuine future acquisition was not exercised. Test counts are valuable quality evidence, not independent market observations.
5. **One-shot requests do not create a study.** Narrow candidate rules, persistent small request caps and manual follow-ups can produce correct HOLDs and missing outcomes. The October status explains a skipped call from an ineligible quantitative candidate. Increasing calls without an experiment design will not fix that.
6. **Execution evidence is a separate unfinished dependency.** A fresh HTTP response is not proof of a fresh order book. Whole-candle highs/lows cannot establish post-entry stop ordering. The fill-validator report correctly keeps those claims blocked.
7. **Project state is difficult to identify.** Older status text coexists with later repairs and new modules. Extensive uncommitted work and unpinned dependencies make release reproduction and handover harder. This is a delivery risk, not evidence that all untracked work is defective.

These are inferred project-management causes supported by the repository structure and reports; they are not claims about the intentions of prior developers.

## What the financial evidence actually says

Saved baseline (`CURRENT_STATUS.md`, `VALIDATION_REPORT.md`): 46 trades, gross P&L -₹2,514.17, estimated costs ₹7,994.62, net -₹10,508.79 on ₹100,000, net win rate 30.43%, profit factor 0.375. Average estimated cost is about ₹173.80 per trade; average net loss about ₹228.45. Even eliminating all modeled costs would leave that saved baseline negative. Later fee corrections do not by themselves rescue it, and the original result is not a newly verified baseline.

The inspected exploratory report `backtests/sharp_reversal_exploratory_v1_1_final/REPORT.md` records:

| Candidate | Trades | Gross P&L | Costs | Net P&L |
|---|---:|---:|---:|---:|
| Frozen reversal | 73 | -₹891.22 | ₹12,454.53 | -₹13,345.75 |
| 3-bar decline / RSI30 / 6-bar horizon | 177 | -₹10,654.26 | ₹27,310.45 | -₹37,964.71 |
| 4-bar decline / RSI35 / 6-bar horizon | 269 | ₹1,675.32 | ₹41,777.53 | -₹40,102.21 |
| Larger decline / RSI30 / 12-bar horizon | 55 | -₹8,656.21 | ₹9,238.17 | -₹17,894.38 |

These samples overlap prior research; do not add their trade counts or call their previously examined partitions untouched out-of-sample evidence. The report also notes missing dates and unresolved provenance. They support refusing promotion today, not proof that every future strategy must fail.

AI must show incremental net value against matched baseline decisions, including rejected candidates and missing outcomes. Direction accuracy alone ignores payoff sizes, costs, opportunity selection and capital constraints. Include API/data/hosting expenses when measuring business profitability.

For context, SEBI's July 2024 study found seven in ten individual equity-cash intraday traders lost money. This is population context, not a forecast for this project: [SEBI primary source](https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html).

## Completion plan and acceptance gates

Estimates assume one experienced full-time Python developer with trading-research competence, daily operator availability, prompt credential/data access and no major provider change. Some activities overlap; do not sum every row mechanically.

| Stage | Work and responsible role | Estimated effort | Definition of done |
|---|---|---|---|
| 1. Freeze scope and release | Developer + owner: choose one primary experiment; inventory existing work; review and checkpoint a release; lock dependencies; update one authoritative status | 2–4 working days | Fresh environment reproduces tests; one documented entry point, owner and decision deadline |
| 2. Establish usable evidence | Researcher + developer: resolve historical provenance where possible or acquire a new documented dataset; verify sessions, timestamps, adjustments and universe; preserve old records | 5–10 working days plus external waits | Dataset manifest and exclusions verified, or historical claims explicitly abandoned in favour of forward evidence |
| 3. Complete signal collection | Developer/operator: exercise initial capture and later outcome retrieval; diagnose locks; budget a whole study; add a minimal supervised scheduling/recovery path | 5–10 working days | Genuine frozen prediction and future observation reconcile; every attempted event, failure and missing outcome is counted; restart works |
| 4. Complete paper execution evidence | Developer/researcher: establish permissible fill proxy, freshness, causal entry/exit timing, gap handling and account reconciliation | 10–20 working days, partly parallel | Complete entry-to-exit paper lifecycle with honest observed-versus-modeled labels and no invented fills |
| 5. Evaluate and decide | Researcher: freeze hypotheses and costs, reserve untouched data, compare AI and baseline, assess dependence/uncertainty, publish go/no-go | 3–5 analysis days plus at least 6–12 weeks collection | Recorded decision against predetermined economic and operational criteria; inconclusive is allowed |
| 6. Conditional broker work | Developer/operator, only after evidence gates: verify applicable broker/exchange requirements, order idempotency, partial fills, reconciliation, restart and emergency controls | Additional 4–8 weeks | Broker-supported testing and tightly limited rollout review; no automatic promotion from paper results |

Calendar time for stages 2 and 5 is not controlled by coding speed. The existing 46 entries over roughly two years imply about 23 trades/year for that configuration. At that rate even 100 entries would take around 4.3 years. This is an illustration, not a required sample size or forecast. Do not widen the strategy just to hit a trade target; changes require a new hypothesis and independent evaluation.

## How to test whether profitability is achievable

Before observing new results, specify a minimum economically useful net expectancy, capital and drawdown budget, evaluation date, treatment of missing outcomes, and costs. Size the study using observed variance and dependence by trading day/symbol, rather than choosing a universal trade-count threshold.

Use a small fixed set of economically motivated hypotheses. Test causal inputs and realistic fills, include failed/abstained decisions, and compare AI-assisted outcomes to the same opportunities without AI. Preserve model/prompt versions and provider latency. Current-model answers to historical prompts can contain later knowledge and cannot substitute for point-in-time AI records.

Promotion should require positive net expectancy after realistic costs and service overhead, uncertainty compatible with the predeclared minimum edge, acceptable drawdown, and stability across reasonable cost stress and time blocks. Report correlated samples honestly. A positive point estimate or a few winning trades is insufficient. Thresholds must be fixed before results; none is asserted as already met.

Stop or redesign the strategy if fresh evidence fails its economic gate. If evidence is too sparse at the decision date, finish the review as inconclusive and explicitly choose whether further collection is worth its cost. This prevents an endless project without pretending that software can guarantee profits.

## Immediate priorities

For the next week: designate a single owner and canonical signal workflow; reconcile current status with later reports; checkpoint and reproduce the workspace; investigate the incomplete trial without touching its budgets; freeze the prospective study and cost assumptions. Then complete one genuine prediction-to-outcome chain under the existing controls before scaling collection.

Do not add another AI model, dashboard, strategy sweep or broker executor until the chosen milestone needs it. Keep current risk checks and evidence guards. They are exposing missing proof rather than causing the negative strategy economics.

## Verification for this review

Existing reports' test totals are historical claims and explicitly scoped selections, not a fresh full-suite result. A current offline run of the `tests` directory was initiated with `.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider --basetemp .audit-completion-20261104`; its final result is recorded below when available. Tests disable dotenv loading and external socket access through `tests/conftest.py`. No historical evaluator or paid API experiment was run.

Fresh verification result: **1,062 passed in 1,046.77 seconds (17 minutes 26 seconds), exit code 0**. The command covered the entire `tests` directory without exclusions; it did not include separate tests located under `backtests`. This verifies the existing local environment, not a fresh dependency installation or real-market operation.
