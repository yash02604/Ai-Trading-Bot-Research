# First controlled FORWARD_PAPER operating policy

Status: operating values established by offline simulations; **session readiness NO-GO**. Target date 2026-11-16, supervised manual preparation around 09:05–09:10 Asia/Kolkata; retained session 09:15–15:15 and entry cutoff 15:10. No network request, session, schedule activation or broker action was performed.

## Formal operating values and ordering

Market-data logical cap: **1100**. Provider logical cap: **36**. Initial paper capital: **₹1000**, matching the explicit current environment value. All three network/session opt-ins remain false. All 14 registry members remain active, in the literal insertion order of reviewed `strategy_backtest.STOCKS`:

RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, BHARTIARTL, ITC, KOTAKBANK, LT, AXISBANK, MARUTI, SUNPHARMA, TITAN.

`instrument_priority` was already an explicit validated subset/order mechanism. This policy selects the complete registry rather than shrinking it. Registry order is a deterministic acquisition/tie-breaking convention, not a liquidity or profitability ranking. Provider proposals are still ranked by descending score, then this priority, and only one selected proposal is admitted at a time. An expensive symbol is not filtered from the registry; the current capital/risk/cost engine decides whether any quantity is affordable.

## Actual request graph and budget derivation

`run_cycle` requests no network data before warmup. Initial snapshots are the per-symbol candle responses, not a separate fetch class. In a new candidate epoch it acquires **14 separate candle logical requests**, builds the complete batch, then **14 separate quote logical requests**, then **one provider batch call for all 14**. There is no single multi-symbol market-data request. A partial invalid candle/quote batch may stop earlier. The provider gets one batch, not 14 independent calls; NO_TRADE, schema rejection or another failed response still consumes its reservation. Decision replay/done markers and cached results do not spend another logical slot for the same identity.

A selected ENTRY_PENDING proposal does not hold a known fill quantity yet. Each due entry or monitoring observation requires one candle request plus one fresh quote request for the selected instrument, purpose-bound separately from candidate acquisition. Thus one complete 14-stock candidate plus one entry observation plus one exit observation costs **32 market-data / 1 provider**. Maximum supported holding_bars is 12, so one candidate plus entry and 12 monitoring bars costs **54 / 1**. Settlement has no extra hidden endpoint; the final observation feeds authoritative V2 accounting. Observer reads/health do not acquire data.

The first legal complete 35-bar batch is available at 12:10 (candidate label 12:05). The last candidate epoch before the 15:10 cutoff starts at 15:05: **36 five-minute epochs**. Fully formed NO_TRADE day: 36×28 = **1008 market-data** and **36 provider** reservations. This is a deterministic planning case, not an empirically measured average or promise of throughput. Scanning pauses while an entry/position is active; risk rejection does not justify assuming scanning stops for the day.

Conservative complete-session market bound: **1008 + 3×2×(1+12) = 1086**. This deliberately double-counts broad scan epochs displaced by up to three permitted trades' entry/monitoring observations. **1100** adds **14** above that conservative bound and **92** above a full no-opportunity day. There is no requirement to consume the margin. Provider **36** is the minimum ceiling covering every one of the 36 eligible batch epochs, with zero extra epochs added for retries; fewer proposals or exposure can reduce usage. No arbitrary maximum cap or historical example value was adopted.

Technical minima differ by scenario: before warmup zero requests; a complete initial batch needs 28 market-data/1 provider; one one-bar completed trade needs 32/1; maximum-horizon single trade 54/1; full NO_TRADE coverage 1008/36. The proposed 1100/36 supports the conservative normal-session envelope without a planned increase. Partial failures may consume reservations without producing an accepted response, but identities and candidate windows bound legitimate acquisitions. Actual remote delays may instead cause rejection or recovery uncertainty; budgets do not guarantee successful data, a trade or settlement.

## Persistence, retries and exhaustion

Caps are persistent **lifetime totals for this configured control ledger**, not automatically renewed daily allowances. The first session starts from the explicitly empty new control directory; any later continuation must deduct prior usage. Changing cap/config identity cannot silently adopt old state. Durable reservations/send/physical/results/failures remain reconciled by LiveTransport. At most 3 Upstox physical attempts use one logical reservation, with retry indices 0,1,2; provider has one physical attempt and zero SDK retries. Physical upper bounds at the caps are 3300 market-data attempts and 36 provider attempts, not planned consumption. Retry delays and wall/monotonic/session revalidation are unchanged.

At zero remaining capacity, a new acquisition is rejected BUDGET_EXHAUSTED without an unreserved send or reset. Candidate paths record rejection under existing policy. An exhausted market-data cap can also prevent a required entry/monitoring observation: no budget is reserved specially for exits. Existing lifecycle records missing evidence, retains unresolved OPEN exposure/reservations and fails closed for recovery; **it does not fabricate a settlement, increase budgets or release exposure**. A consumed provider cap is not itself a prohibition on an already-open position's market-data monitoring. Genuine uncertain request evidence blocks instead of retrying silently.

## Preserved authorities and limitations

Registry, strategy, scores, prompts, 1% per-trade risk, 2% daily-loss policy, current-equity sizing, reservations, fee/slippage models and expected-net-profit acceptance remain unchanged. The daily-loss reference remains the existing initial-capital reference. Provider fields cannot override quantity/risk. Five-second quote freshness, forming-candle exclusion, completed availability clocks, candidate expiry, deadline/entry/session rules and replay/settlement are unchanged. Existing source-bound default transport options are used exactly except for the designated approval path; poll 5s, max_cycles 10000, timeout 10s, total deadline 20s, max retry delay 1s, min_turnover 100000, model deepseek-flash.

All 14 serial quote responses must still satisfy the existing freshness gate together. Budget capacity does not prove real-world collection fits that window, entitlement, provider availability, latency, depth age, live fill quality or real-market readiness. These are initial paper-session operating parameters, **not proven optimal strategy parameters**. Profitability is unproven. Joint local DB+anchor rollback still needs an independently trusted witness.

## Offline evidence and its limits

Final external simulation suite: **13 passed**, 0 failed/errors/skips/deselections; JUnit **532.929 seconds**, exit 0. It covers independent full-day request-graph/reservation modeling (1008/36), actual full-universe controller admission and settlement (32/1), competing reverse-ordered equal-score proposals selecting registry-first RELIANCE, stale quote, expired/late provider, wrong-model provider failure, bad market data, 3-physical/1-logical retry accounting, exact-cap exhaustion across re-instantiation, missing observation with OPEN exposure retained, unaffordable-symbol rejection and disabled-opt-in refusal. Exact-cap cases use benign copied synthetic reservation-only crash states, not real pending requests. The day model does not claim 1008 physical fake HTTP dispatches; actual controller/budget paths are sampled by the other cases.

All fixture credentials, clocks and network responses are fake, dotenv and sockets/DNS disabled. Full controller simulations use temporary test approvals/enabled fixture booleans outside the canonical root; they do **not** establish that the disabled canonical configuration is READY. External fixture caches readiness only for identical immutable config/source/approval/settings/clock/purpose inputs, calling the production check for changed inputs. Runtime code and integrity checks were not changed. Two preliminary attempts at the fully dispatched day were stopped without a completed result because identical checks/durable fixture I/O made that approach impractical; those incomplete attempts are not counted as passing tests. No test failure is hidden as a pass. No full repository regression rerun is claimed for the new operating config; prior 2112-case evidence concerns the unchanged source, not independent acceptance of this new config.

Exact final command, cwd `C:\Users\Priyanka\Desktop\ai-trading-bot-research`:

```powershell
& 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe' '-B' '-m' 'pytest' '-q' '-p' 'no:cacheprovider' '--basetemp' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\tmp-proof' '--junitxml' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\proof.xml' '--tb=short' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\test_operating_policy.py'
```

XML/probes/command/preservation evidence: `C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115`. No production test file was edited or added.

## Canonical artifacts and authorization conflict

Config: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\logs\forward-paper-v2\runtime-config.json`. Validated through production `validate_options` and scheduler parser with 1100/36/full registry, ₹1000, exact reviewed defaults and all opt-ins false. Credentials remain solely in environment/.env; none are in the JSON.

Approval: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\logs\forward-paper-v2\transport-review-approval.json`. Its source/config identity fields match the exact canonical configuration and the 26-file identity (including active session manifest). It is explicitly **PENDING_NOT_APPROVED**: `independent_review_passed=false`, `network_exercise_authorized=false`. These flags truthfully avoid claiming independent review of this newly established config or granting unrequested network authorization. An identity match is not approval. No prior positive approval was copied.

Control: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\logs\forward-paper-v2\control`. Empty, absolute, non-TEMP, no symlink/junction ancestor, accepted by the read-only production path validator; a temporary write probe succeeded and was removed. No trade history, engine, request journal, observer publication, owned run/attempt or mock state is created there. No actual single-instance ownership contention test is claimed for this canonical deployment; existing isolated ownership coverage remains supporting evidence.

Manifest: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\data\audits\nse_cm_session_manifest_v3.json`; SHA256 `4f0524db0b16c2dfedf354704cac7954b14af3936cbbff76f53d1848b4938d03`. Source identity `8d854dbf1073aab6971da707d152484691416edcc78a1d78caddd025d45d1342`; canonical config identity `cb28365631d301aeac08e46e61d76bbcc87e5b54a8851e8e05e80d9e27f1e206`. The source digest includes the manifest; no separate manifest copy or new date-locking feature is invented. Intended date is 2026-11-16; existing runtime calendar/clock remain authoritative.

**Exact canonical readiness/dry run: NO_GO**, with NETWORK_DISABLED, PROVIDER_DISABLED, PRODUCTION_START_DISABLED. This was checked without any production override. The observer correctly reports unavailable because there is no run. A positive acquisition→entry→settlement lifecycle at these paths cannot pass while the unchanged contract requires all three opt-ins true, and readiness cannot label an unapproved record approved. The request simultaneously requires READY and disabled opt-ins; those requirements cannot both be met by this implementation. No gate was bypassed and no mock financial history was written into real control state. The available result is a validated disabled config plus isolated operating-policy simulations, not the requested positive exact-config session dry run.

## Verdict

**NO-GO.** Operating values are now explicit and offline-tested, and canonical config/pending approval/empty control root exist. Independent exact-config review, truthful authorization/approval handling and an agreed isolated dry-run method that preserves disabled production opt-ins and uncontaminated control state remain required. No transport start, budget increase, strategy change, schedule activation or broker request is authorized. Do not automatically turn pending approval flags true or enable a switch.

## Supplemental provider-cap monitoring proof

One additional isolated test passed, 0 failed/errors/skips/deselections, JUnit 42.746s, exit 0. After actual mocked full-universe entry, 36 provider reservations exhaust the cap; monitoring still completes one settlement using market data only, ending at 32 market-data/36 provider reservations with no extra provider dispatch. Thus **14 unique external cases pass across two runs**; these are not added to the older 2112 repository-suite count.

Exact supplemental command (same cwd):

```powershell
& 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe' '-B' '-m' 'pytest' '-q' '-p' 'no:cacheprovider' '--basetemp' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\tmp-provider-monitor' '--junitxml' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\provider-monitor.xml' '--tb=short' 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\first-session-operating-policy-20261115\test_provider_cap_monitor.py'
```
