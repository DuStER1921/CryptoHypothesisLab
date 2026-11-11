# Colab Airgap V1 — One Gmail / no custom OAuth client

**Scope:** owner-supervised Development-only preparer and separate exploratory analyst runtime. This is a *simplified manual pilot*, not the Phase-4 signed-broker/independently durable research-read authority. **The frozen source project's research authorization remains unchanged.** No automated access to Validation (2025) or Final OOS (Jan–Aug 2026).

## Exactly two fresh Colab runtimes

1. [01 — Prepare](../colab/01_prepare_development.ipynb) is the only notebook that mounts the owner's Drive. It clones the public code **before** mounting. The owner provides known SHA-256 pins (not embedded in the public notebook) and verifies the eight frozen metadata/evidence files. No market data is opened in preflight.
2. The owner **explicitly types** `AUTHORIZE_DEVELOPMENT_YYYY-MM` for one selected month (e.g. 2024-07). That is a new manual *pilot* approval for the separate lab, not an amendment to CryptoResearchLab's frozen governance status. The preparer checks both frozen C1 and C2 partition identities, writes a `COLAB_DEVELOPMENT_PREP_STARTED` record *only in the private CryptoHypothesisLab research-events folder* using exclusive creation and readback, and **only then** opens those exact source files in read-only mode. Source paths are constructed from the verified frozen period and generation; no generic arbitrary-path loader or current/latest partition fallback. Failed or uncertain state is **NO_GO** and is not retried silently. The Drive mount has account owner permissions; software cannot make that entire account read-only.
3. `ReadOnlyStager` copies C1/C2 to private `/content` scratch in bounded 4-MiB chunks, requires exact manifest/Parquet SHA-256, and the existing `C1C2MinuteBuilder` generates only Development minute features. Zero/unknown C2 minutes remain explicitly `C2_UNKNOWN`, not interpolated; time availability is hypothetical (`assumed_latency_ms`, default illustrative 1500 ms). Complete C1 grids are mandatory.
4. `airgap_bundle.create_bundle` produces a bounded ZIP with `manifest.json` and `features.csv` (exact 9-column schema). No raw source rows, trade-level data, labels, OOS data, file IDs, private paths, tokens or key material are included. The manifest binds frozen Universe SHA, calendar SHA, C1/C2 hashes, producer code commit, month, latency assumption, row count and data SHA. Download to your own computer via `google.colab.files.download`. **Do not add this ZIP or result reports to public GitHub.**
5. Choose **Runtime > Disconnect and delete runtime** to discard Drive mount and owner credentials. Start a **new** VM, open [02 — Explore offline](../colab/02_discover_offline.ipynb), and upload the ZIP **from your computer** using `files.upload()`. The notebook does not mount Drive, request Drive OAuth, or receive owner tokens. Its preflight rejects common residual mounts/credential env vars; it cannot prove no hidden credentials exist in an arbitrary untrusted runtime. A fresh VM and trusted code are essential.
6. Analysis validates the ZIP structure, SHA-256, schema, every UTC minute, hypothetical arrival time, C2 status and exact Development boundaries. One illustrative conditional 5-minute return summary is **DESCRIPTIVE_ONLY_NOT_VALIDATED** — no statistical significance, no trade-entry assumptions, no independent OOS. Download **every** experiment report to the computer (and optionally upload it manually to the private `research-results` folder). The analyst notebook must not get a Drive mount just to save reports.

## Important security / quality limitations

- **Account vs. runtime:** one Gmail retains full account-level access to all market data. The *analyst VM*, not the Gmail account, is isolated. The operator prep VM intentionally has source access, including potentially protected material; the audited path only permits C1+C2 Development, and it must never be used to inspect OOS. Cannot stop deliberate owner modification or arbitrary Python in the prep runtime.
- **Drive FUSE logging:** an exclusive file create + fsync/readback is an *operational* owner trace. Drive mount sync is not independently atomic/durable and is not a signed owner-protected audit authority. Concurrent preparer VMs or crash recovery are **not approved**. For rigorous multi-writer and protected-real-data authorizations use Phase 4 owner broker + distinct authoritative receipt backend. Do not claim this pilot as full production `READ_STARTED` acceptance.
- **Provenance:** frozen identity is checked with the eight-file freeze proof, and each source partition's full manifest and canonical Parquet hash is checked. SHA does not prove *completeness* of original Binance trades or actual historical network arrival latency.
- **Multiple testing:** discovery-only trials can be exploratory but every variant, family and negative result must be retained. If a user edits thresholds without keeping prior attempts, the resulting significance assessment is invalid. No Validation or Final OOS is exposed.
- **Resource bounds:** source C2 may exceed several GiB; free Colab disk/RAM/quota are not guaranteed. A failed transfer leaves its start record and must not be hidden. No automatic retries or data deletion from original Drive. Test one month, then scale by month and cache verified features, *not* by rereading C2 for every hypothesis.
- **Privacy:** public code notebooks contain no real data, tokens, private folder IDs or literal frozen SHA. Keep notebook outputs excluded when saving/sharing. The owner-supplied pins, private results and prepared ZIP stay private.

## What is accepted now / not accepted

- Synthetic tests and public notebook audit in CI; no real-market source read performed by this delivery.
- `01` can validate frozen metadata; processing one real month requires **owner typing an exact approval** in Colab and accepting the documented manual-pilot risk profile.
- Real Colab runtime, Drive FUSE, private `READ_STARTED` write/readback, source I/O, Python/PyArrow version and analyst VM isolation **still require an owner-run acceptance**. If these checks fail, the pilot is NO_GO and no result should be treated as evidence.
- Upstream `GovernedRealReader` remains default-deny; Colab manual owner-pilot is a separate, explicitly lower-assurance pathway, **not a silent grant bypass**.

## July 2024 C1 V1 manifest compatibility (2026-11-11)

The original C1 `klines` 1m `manifest_version=1` **does not contain** a
`generation` key. Phase 5 initially rejected this valid source in
`ReadOnlyStager._check_manifest`; frozen preflight succeeded, but the read-only
stager raised `StageError: manifest disagrees with frozen ledger` before C2
was staged. C2 `aggTrades` July 2024 uses manifest V3 and **does contain**
`generation`. The verifier now respects these different source contracts:

- C1 V1 requires provider, market, symbol, logical dataset identity, canonical
  name, interval/schema version, source hash, and full pinned manifest SHA-256.
  Its generation is bound by the frozen partition ledger and exact
  `generations/<SHA>/...` source path, not an invented JSON member.
- C2 retains the strict explicit generation comparison (and all previous
  SHA-256, period, validation, and readback checks).
- Local read-only acceptance against the actual July 2024 C1/C2 manifest
  bytes passed with matching manifest SHA-256. This does **not** constitute
  a real C2 Parquet processing or Colab-runtime acceptance.

**Safe retry after this exact error:** the first attempt has already written
`COLAB_PREP_STARTED` to the separate private research-events folder; it is a
real historical attempt, **do not delete or edit it**. In Colab choose
**Runtime > Disconnect and delete runtime**, reopen notebook 01 from the
updated `main` branch, repeat frozen preflight, and explicitly authorize the
same Development month. The notebook generates a **new** random attempt ID.
Do not rerun cell 3 within the previous runtime: its private scratch folders
were intentionally created once, and the old checkout would still have the bug.
If the new attempt fails, retain its event and the traceback; do not silently
repeat or copy original source files.
