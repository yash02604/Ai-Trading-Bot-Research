# Scheduled V2.2 paper infrastructure implementation plan

**Goal:** Prepare scheduler ownership, identity, fail-closed preflight, offline V2.2 orchestration and an independent read-only observer without activating any experiment.

**Architecture:** Production entry is structurally blocked until a separately reviewed V2.2 transport integration exists. This implements the user's explicit clarification, not an assumed live adapter. The private synthetic test harness drives the unchanged V2.2 engine; observer readers consume sanitized scheduler-owned publications and never construct an engine.

**Tech stack:** Python standard library, existing pandas/calendar/V2 APIs, existing pytest offline guards, Windows PowerShell Task Scheduler artifacts.

**Specification:** User attachment `C:/Users/Priyanka/.codex/attachments/0a678cb2-4124-46b4-b030-1c1422996d35/Pasted text.txt`, with subsequent user selection to keep production starts blocked pending separate integration review.

## Constraints

- No provider, market-data or broker requests; no real-time or causal experiment; no task registration/activation.
- Preserve all existing 640 inventoried files, Sessions 02–05, historical reports and real logs/state.
- No engine, strategy, risk, cost, calendar or broker source edits; no reinterpretation of real data as synthetic provenance.
- Existing retained calendar is authoritative. Missing/unverified dates fail closed; holidays are clean NO_SESSION.
- No observer capability to mutate positions, budgets, recovery, risk or settlements.
- No new financial recovery algorithm or claim that another local file solves rollback freshness.

## Tasks

1. Record actual flow and integration gaps in the requested documentation before implementation.
2. Add failing deterministic tests for mode/calendar/preflight, OS-backed ownership, identity/duplicates, read-only snapshots, sanitization and failure isolation.
3. Implement new scheduler/observer modules and CLIs. Keep runtime metadata outside V2's dedicated ledger directories; block production transport unconditionally.
4. Exercise unchanged V2 propose/observe/recovery on explicit synthetic fixtures. On unavailable due slots publish existing missing-observation semantics; preserve unresolved exposure and fail closed, never fabricate settlement.
5. Prepare disabled scheduler XML/registration preview and stable wrapper; test only previews, syntax and blocked child invocation.
6. Run focused suites and existing V2/seal regressions, then a fresh read-only diff review. Address confirmed in-scope findings only.
7. Run the complete offline suite after focused verification; retain exact commands/counts/durations externally.
8. Compare all starting file hashes, finalize requested 16-section documentation and stop.

## Review focus

- Control-root changes must not permit two concurrent scheduled instances of this project.
- Repeated same-day launches and interrupted lifecycle recovery must not repeat economic effects.
- Malformed/stale observer publications must neither leak arbitrary text nor affect engine decisions.
- Calendar cutoffs must come from existing rules; explicit missing slots must retain unresolved positions.
- Production CLI must not offer a synthetic/provenance override or dynamic backend escape hatch.
