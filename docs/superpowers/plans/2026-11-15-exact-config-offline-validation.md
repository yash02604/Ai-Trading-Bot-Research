# Exact-config offline validation implementation plan

Goal: exercise the byte-exact canonical disabled configuration without changing production gates, approval or state.

Spec: user attachment 9cd0a64c-fbd7-476e-8599-e0c6f20e7f35, 2026-11-15.

Architecture: dedicated CLI launches a guarded child. A private instance of the unchanged controller functions receives sealed offline ports and an offline-only contract context. Production module globals/readiness remain untouched. Real parser, normalizers, ranking, V2 risk/cost/replay, scheduler projections and observer formats are reused. No live constructor or HTTP callback is called. Storage has a validation-domain binding, synthetic scheduler identity, OFFLINE_VALIDATION_ONLY root marker and exported artifact envelopes; it cannot match production state/approval.

Files: new app/research/exact_config_validation.py; new scripts/validate_forward_paper_config.py; new tests/test_exact_config_validation.py; new validation report. The 26 reviewed files and canonical config/approval/control remain unchanged.

Constraints: exact canonical JSON read-only (1000/1100/36/all14/default-off); pending approval identity verified, never promoted; no generic bypass option, user-defined transport class, real constructor, socket, subprocess fallback, schedule or broker surface. Child write audit confines all changes to a new absolute non-production output root; .env never loaded. Native V2 working files remain within the marked validation sandbox and are indexed as offline artifacts. Financial storage binding is deliberately domain-separated from production, while the exercised config identity stays canonical.

- [x] Inspect existing draft and regressions; map gates before editing. No missing-entry-point failure is claimed for this continuation.
- [x] Verify parent/worker isolation, guards, strict mock context and private dependency injection; add named guard probes and hash reporting.
- [x] Exercise actual controller cycles for all 36 no-opportunity epochs; one candidate/ranking/entry/monitor/settlement; stale/expired/provider/market faults; exact-cap exhaustion; observer failure; replay; retry; serial quote timing.
- [x] Prove unchanged production modules, flags, approval bytes/control, incompatible validation evidence, blocked network/client construction and live preflight NO_GO.
- [x] Fresh non-author read-only review, including guard/hash delta; no confirmed findings.
- [x] Complete unfiltered offline suite: 2,136 passed in 1,496.82s, zero failures/errors/skips/deselections, exit 0. Final focused suite: 24 passed in 290.21s. Retain initial sandbox PowerShell authorization failures and passing diagnostics; no production policy changed.
- [x] Run final dedicated CLI against actual canonical config; independently check 989 artifacts, all 26 hashes, fake-secret absence and NO_GO preflight. Post-suite reconciliation: 374 unchanged inventory files, only intended harness edit; no protected additions/removals; all 26 production hashes unchanged; canonical control empty and normal preflight NO_GO.
