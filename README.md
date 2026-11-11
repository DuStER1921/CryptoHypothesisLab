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


## Phase 4: one-Gmail operator airlock

`OwnerOneUseBroker` consumes a previously signed and cloud-verified `READ_STARTED` ACK **once in owner SQLite before opening a read-only source stream**. `TokenScopeDeclaration` and analyst-environment preflight support a **single Gmail using two separate tokens**; they do not pretend to revoke Gmail owner's access to Validation/OOS. The real-source gateway remains default-deny and no live market rows are read by CI. See [`docs/PHASE4_ONE_GMAIL.md`](docs/PHASE4_ONE_GMAIL.md) for the exact owner setup and remaining NO_GO gates.

## Two-notebook Colab airgap (manual owner pilot)

For a single Gmail without creating a Google Cloud OAuth client, use **[01 — Prepare Development](colab/01_prepare_development.ipynb)**, download the bounded SHA-verified feature ZIP, fully delete that runtime, and then open **[02 — Explore offline](colab/02_discover_offline.ipynb)** and upload the ZIP from your computer. The offline analyst does not mount Google Drive. Public notebooks are code-only with empty outputs; real frozen IDs, raw data and private reports are never in GitHub.

**Read [Colab Airgap V1](docs/COLAB_AIRGAP_V1.md) before a real run.** It documents strict 2020–2024 bounds, private start evidence, scope of manual owner approval, the weaker Drive FUSE durability properties vs. the signed Phase 4 broker, and separate-VM constraints. No real source bytes are opened in CI or by this setup alone; the user must explicitly approve a selected Development pilot.

## Phase 6: exploratory quant screening (Development only)

The offline [Colab 02](colab/02_discover_offline.ipynb) now offers an optional fixed-grid **1,440-trial research screen** using an already verified single-month C1+C2 Development ZIP (no new Drive mount, raw market data or custom OAuth client). It downloads a complete plan **before** evaluation, then a private report covering **every trial**, both signs, cost-sensitivity scenarios and a 21-day / remaining-days chronological diagnostic. The selection test is a daily-block sign-flip maxT screen, **not independent statistical validation**; its assumptions may not hold in market time series. Never call the result HFT arbitrage or a profitable executable strategy.

Read the [Quant Screen V1 contract](docs/QUANT_SCREEN_V1.md). No plan/report/market artifacts belong in public GitHub.

## Phase 7 — Pre-Move Early Warning (Development-only)

[`Colab 03 — Pre-Move Research`](colab/03_premove_offline.ipynb) uses an already verified private minute-feature ZIP, **never mounts Drive**, and tests **three causal pre-move mechanisms across 18 fixed warning/label definitions** on every closed minute. It reports alarms, **false alarms**, first-close lead times and ex-post event timelines, with a close-based Asia 00:00–07:59 UTC proxy. No HFT execution, causal edge, true OHLC Asia extremes or C3 book depth is inferred. A pure, one-pass, already-authorized C2 5/15/60s rollup helper exists for future finer-resolution preparer work; its real-source integration remains PENDING. [Research method and limitations](docs/PREMOVE_RESEARCH_V1.md).
