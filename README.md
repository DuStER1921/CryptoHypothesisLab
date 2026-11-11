# CryptoHypothesisLab

Public code-only, synthetic-first research foundation for **causal event discovery**, not trading strategies.

**Current authorization: SYNTHETIC ONLY.** No real C1/C2/C3 market rows or original warehouse bytes are accessible from this project. The canonical source remains external and untouched; there is no source data submodule or source Drive credential in this repository.

## Five components

| Module | Responsibility | V1 boundary |
| --- | --- | --- |
| `gateway.py` / `DataGateway` | Byte-verified synthetic fixture loading, immutable allowlist, descriptor-anchored read | REAL data denied |
| `temporal.py` / `TimeGuard` | Completed feature availability, decisions, future labels, chronological purge | Cross-bank labels denied |
| `discovery.py` / `DiscoveryEngine` | Batch-preregistered imbalance event studies with sorted-threshold reuse | Synthetic descriptive results, not p-values |
| `ledger.py` / `ExperimentLedger` | Local POSIX SHA chain, exact trial IDs, exclusive cooperative writer and crash sentinel | Not a trusted Drive/FUSE grant ledger |
| `gate.py` / `ValidationGate` | Synthetic Development-only reads with read-start event | Validation / OOS sealed; all REAL reads denied |

## Research banks (UTC, end exclusive)

- Discovery: 2020-01-01 to 2025-01-01 (1,827 days)
- Validation: 2025-01-01 to 2026-01-01 (365 days, sealed)
- Final OOS: 2026-01-01 to 2026-09-01 (243 days, sealed)

## Tests

On Linux/macOS with Python 3.11+ (the local journal requires POSIX directory `fsync` and `flock`):

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/audit_public_tree.py
```

GitHub Actions runs the tests on Python 3.11 and 3.13, with no credentials and no market data. Windows imports the package but skips local POSIX journal tests; that is not a Windows acceptance result.

Read the [Research Rules](docs/RESEARCH_RULES_V1.md) and the [Security Review](docs/SECURITY_REVIEW_V1.md) before extending this repository. **Do not merge in production access**, run research against real frozen data, or present synthetic results as market edge until the missing independent grants, data-integrity adapter, durable journal and statistical inference have been accepted.

## Phase 2 data admission (real market rows still blocked)

The project now includes a separately reviewed `FrozenIndex` (eight-file freeze proof), streamed read-only private staging, exact-schema bounded Parquet window loader, signed Development grant verifier, C1+C2 minute feature builder and private create-only local result writer. **None of these objects alone authorize real data reads.** `GovernedRealReader` always denies until independently anchored, durable owner-controlled read-start authority and isolated credentials are implemented and accepted. Validation and OOS stay sealed.

See [`docs/PHASE2_DATA_ADMISSION.md`](docs/PHASE2_DATA_ADMISSION.md) for safety gates and remaining work. No original private files or observed market rows were committed.


## Phase 3 private receipts (code/tests, NOT market-data permission)

This release adds an independent owner-side SQLite `PENDING -> ACKED` exposure journal, Ed25519-signed `OWNER_READ_ACK` verification, a bounded create-only Google Drive JSON uploader with exact metadata/content readback, and role-isolation preflight. Tests are synthetic; no credentials, real Drive folder IDs or market rows are committed. The original `GovernedRealReader` continues to **deny every real read**.

A live connector smoke test stored only a **synthetic** JSON in the private results folder; this does not prove the actual output uploader's separate OAuth identity. Three real-world gates remain: set up independently scoped Google identities and effective ACL verification, prove owner SQLite durability/key custody and Drive ACK behavior on intended hosts, then integrate a one-use trusted broker with *lazy* source opening. No real C1/C2/C3 market rows may be opened yet. See [Phase 3 security protocol](docs/PHASE3_PRIVATE_RECEIPTS.md).

## Phase 4: one-Gmail owner airlock

`OwnerOneUseBroker` consumes a signed/cloud-verified `READ_STARTED` **once in local owner SQLite before opening a read-only source stream**. `GoogleDrivePinnedSource` reads only owner-pinned IDs in bounded chunks and `ReadOnlyStager` verifies SHA-256. The same Gmail may be used for two **separate OAuth tokens**, while the analyst receives neither. The Gmail account itself retains access to protected data; this is process isolation, not a Google ACL barrier. `GovernedRealReader` remains default-deny, and CI is synthetic-only. [Phase 4 procedure and remaining NO_GO checks](docs/PHASE4_ONE_GMAIL.md).
