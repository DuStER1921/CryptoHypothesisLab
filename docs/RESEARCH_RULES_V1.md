# Research Rules V1 — synthetic implementation, not market-data authorization

## Decision: half-open UTC banks (do not optimize them after seeing results)

- DEVELOPMENT: `[2020-01-01, 2025-01-01)` = 1,827 calendar days.
- VALIDATION: `[2025-01-01, 2026-01-01)` = 365 days, **sealed**.
- FINAL_OOS: `[2026-01-01, 2026-09-01)` = 243 days, **sealed**.

The dates are approved as a project design. They grant **no** real-data access. The core package rejects all real and protected dataset read requests. Any alteration requires a new versioned rule decision, never retroactive relabeling after outcomes were seen.

## Zero writes to source

The immutable original source warehouse is external and not a Git submodule. No source paths, source credentials, production manifests, research outputs or market rows are committed here. No copy/promotion/deletion/mount/write code to the source is provided. The V1 DataGateway only reads small pinned **synthetic fixtures**. A future production adapter must be independently approved, mounted through a genuinely read-only identity, and verified against the frozen universe/calendar/partition ledger and actual file SHA-256; merely using this Python class or a copied JSON is not an authorization boundary.

Only the separate private output location may receive research artifacts. The public CI has no access to either source or output Drive account and uses synthetic fixtures only.

## Time safety

- UTC only; all date intervals are half-open.
- Complete candles and aggregated trades must have explicit `available_at` values no later than `decision_at`.
- Features use only available information at decision time; no future rolling window, centered filters or backfilled values.
- Labels begin *strictly after* decision; their end must remain inside the same allowed bank.
- Purge prior training samples with labels overlapping a later evaluation window, accounting for overlap and configurable embargo. Internal chronological evaluation is NOT a sealed validation.
- For C3 (future phase): only at-or-before available sampled snapshots, PASS quality, no full-L2 or interpolation claims; gaps reset dependent state.
- Current discovery returns **descriptive returns from known closes**, not executable after-close fills.

## Search and evidence rules

- Register the complete batch/spec/horizon grid **before** calculating its results.
- Save every attempted variant, failures and insufficient samples; no cherry-picked "best only" evidence.
- Account for correlated labels, effective sample size, regime changes, multiple tests and selection effects before calling anything an edge.
- Keep Validation sealed until a frozen batch + independently durable read-start grant; keep Final OOS sealed until independent approval and one-time read-start. Reusing an exposed holdout is NOT independent validation.
- Avoid raw sample rows or numeric research results in public CI/commits/logs; outputs remain private.
- Future real evaluation needs a trusted owner-controlled grant and independent durable ledger anchor. The local POSIX hash-chain is not tamper-proof: deleting the end can be detected only if its head is anchored elsewhere. It must never be treated as a cloud/FUSE durable grant authority.

## Not yet implemented

- Real frozen warehouse adapter, independent grant authority, protected role access.
- Colab/OAuth integration, private output uploader and trusted ledger anchor.
- Full C1+C2 temporal alignment and complete C3 quality admission.
- Dependence-aware inference (block bootstrap, effective sample size, multiplicity adjustment), walk-forward statistical significance, and power analysis.
- Production performance and large-file streaming (synthetic gateway deliberately limits reads to 8 MiB).

Until these items are implemented and accepted, all V1 results remain `SYNTHETIC_DESCRIPTIVE_ONLY`, not evidence of market edge, not permission to run real research.
