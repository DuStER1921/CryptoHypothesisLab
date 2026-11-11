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
