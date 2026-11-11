# Phase 2 — frozen source admission, private staging and Development-only research

**STATUS: CODE + SYNTHETIC TESTING; REAL DEVELOPMENT READS STILL DEFAULT-DENY.**

This is an implementation, not a permission to open any real market rows. The original source project's real-research status is not changed. No source GitHub or Google Drive files, folders, permissions or freeze receipts are edited.

## Data flow and trust boundaries

1. **Operator-owned metadata preflight (read-only):** obtain the exact eight *frozen metadata/evidence files* in an isolated private workspace; verify separately pinned `universe_sha256`, `calendar_sha256`, receipt, commit marker, every partition ledger SHA, C3 evidence ZIP and owner PASS-only decision. `FrozenIndex.load()` does not read market rows or authorize research. Never automatically select latest generations.
2. **Owner-only grant (not yet operational):** a strict Ed25519 grant candidate must be signed by an offline owner key whose public fingerprint is pinned **independently of the grant**. Permit at most explicitly listed **complete Development partitions**. Validation, OOS, expired/wrong-role/unsigned grants are rejected. A signed grant alone is NOT a research admission.
3. **Trusted read-start / data-source boundary (BLOCKED):** implement an independently anchored, durable, single-writer, private `READ_STARTED` receipt before any protected/real source read. Prove restart, lock, duplicate, torn ACK and revoke behavior on target storage. Existing POSIX synthetic journal and local create-only output are NOT cloud authorities. `GovernedRealReader.stage_real()` therefore **always raises** before reading bytes — even when passed a forged `durable_receipt_ack`.
4. **Verified private local staging (synthetic integration only):** provide two read-only byte streams. `ReadOnlyStager` copies 4 MiB chunks to a `0700` private POSIX scratch directory, verifies manifest bindings and full SHA-256 of each file, fsyncs, and rehashes. It has **no API for opening source paths or writing to source folders**. It is a low-level primitive; callers must not bypass step 3 with real data. The staging scratch path must be outside all mounts pointing to source data.
5. **Bounded data plane (synthetic integration only):** `ParquetWindowReader` validates exact family schema, requests an explicit half-open UTC slice and <=100,000 returned rows, streams in 16,384-row batches, and verifies SHA before/after. Development-only. `C1C2MinuteBuilder` scans each verified staged C1+C2 partition once to calculate reusable minute features, refusing malformed C1 grids and C2 ordering; C2-no-events means UNKNOWN rather than zero trading. It does not infer gap-free source coverage.
6. **Separate private results:** local `PrivateOutputStore` is create-only with explicit forbidden source roots, 8 MiB/report cap, restrictive permissions and fsync/readback. Cloud upload to a separate private Drive folder is **not implemented**, and requires a separately scoped `drive.file` identity, bounded create-only uploads, readback and a retry/duplicate protocol.
7. **Isolated research runtime:** after admission, the research analyst must run without original-source OAuth tokens or a mounted original source root. It receives only Development-only verified staged inputs. If the same interactive notebook has owner Drive credentials, Python gates alone cannot guarantee OOS blindness. A dedicated reader identity restricted to approved source material or a separate trusted staging process is necessary.

## Exact time policy

- Development: `[2020-01-01, 2025-01-01)` UTC (1,827 calendar days).
- Validation: `[2025-01-01, 2026-01-01)` UTC (365 days), SEALED.
- Final OOS: `[2026-01-01, 2026-09-01)` UTC (243 days), SEALED.
- Roles are fixed by source code and cannot be changed by runtime metadata.
- C1 `open_time` is a bar START, **not** proof of availability. A feature using a completed candle is available only after its close and conservative, explicitly declared latency overlay.
- Historical C1/C2 do **not** contain observed market-data arrival timestamps. `assumed_latency_ms` is an **assumption**, not actual latency. Vary and stress-test it before evaluating strategy implementations.
- C2 taker direction: `is_buyer_maker=False` means buyer initiated; the signed quantity is positive. One `aggTrade` is not one trade or independent observation.
- C2 absent minute is `C2_UNKNOWN`, never force-filled as buy=sell=0. Even present C2 ticks do not prove gap-free tick coverage.
- C3 is **sampled distance-band bookDepth**, not reconstructible full L2. Only explicitly verified owner PASS candidates can be considered; they do not independently grant research.
- Never use Development features whose assumed availability falls into Validation, or outcomes crossing split boundaries. Validation/OOS labels and per-sample results must remain unavailable until pre-registered release.

## Runtime and public repository

- Public repository contains **only code, tests on artificial data, generic schema rules and non-sensitive descriptions**. Do not commit source folder IDs, private file paths, market rows, private grants, offline private keys, output reports or derived feature values.
- CI executes with `contents: read`, no Drive/GitHub writing credentials, and synthetic fixtures generated inside test processes. Python 3.11 and 3.13; optional PyArrow 26.0.0 and cryptography for integration tests. The upstream source Python 3.14 runtime is NOT reused without a separate compatibility proof.
- `DataGateway` and `ValidationGate` from V1 remain default-deny for real data. New primitives do **not** change their behavior.
- Do not call low-level `ReadOnlyStager.stage()` with real streams outside approved owner admission, and do not interpret a valid owner signature as storage-backed `READ_STARTED`.

## Approval gates before first real Development read

1. Full frozen metadata preflight and real-manifest schema acceptance, with independently recorded hashes and exact Development partition selection.
2. Operator demonstrates source credential has **no write** access and research analyst has no source token/mount or effective Validation/OOS path. No attempt to write to the original source is permitted even as a test.
3. Private owner signature/key-fingerprint pin, single-writer durable research-event ledger with independent anchor, read-start ACK, expiry/revocation and crash recovery acceptance.
4. Private Drive uploader accepted with create-only receipt/result storage, complete readback and failure/retry handling; prevent public CI or Git history from receiving bytes.
5. Reproducible synthetic Parquet tests on the intended Python/PyArrow/Colab runtime, realistic bounded transfer/RAM/disk test, source metadata unchanged after read.
6. Owner explicitly authorizes one bounded Development-only pilot. NO Validation/OOS grants in this phase.

**GO** for continued offline/synthetic integration after CI PASS. **NO_GO** for real C1/C2/C3 market-row research until all independent gates above are met. Scientific significance / multiplicity and realistic trade execution are separate later phases; no edge is claimed here.
