# CryptoHypothesisLab

Public **code-only**, synthetic-first research safety prototype for causal event studies. We do **not** copy raw market data, source warehouse code, credentials or results into this repository.

## Components

- `DataGateway` — pinned manifest + hash verification for bounded synthetic fixtures; real market reader deliberately **not authorized**.
- `TimeGuard` — UTC bank boundaries, feature availability, future-label separation and chronological purge.
- `DiscoveryEngine` — registers a complete synthetic event/horizon grid and returns descriptive conditional forward outcomes, **not** trading performance.
- `ExperimentLedger` — local POSIX create-only, SHA-linked events, readback; independently anchored durable production authority not yet built.
- `ValidationGate` — development-only synthetic fixture read policy. Validation and Final OOS cannot be opened in V1.

## Run local unit tests (no network, no market data, no credentials)

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The source project and its datasets remain **unchanged**. See [`docs/RESEARCH_RULES_V1.md`](docs/RESEARCH_RULES_V1.md) for selected time banks, causal invariants, explicit limitations and admission checklist.

CI executes only synthetic unit tests and does not authenticate to Google Drive. Do not publish private research outputs or any external canonical warehouse assets in commits, issues, PR descriptions or workflow logs.
