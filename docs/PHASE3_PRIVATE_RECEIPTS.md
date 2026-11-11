# Phase 3 — independent owner receipts + private Drive publication

**Status:** implementation and synthetic tests. Real market rows remain **NOT_AUTHORIZED**. No change to CryptoResearchLab or frozen Universe.

## Three separate identities/processes (mandatory for real use)

1. **Owner controller** (trusted workstation, not public CI/Colab): holds the offline Ed25519 read-ACK signing key and local SQLite owner journal. This journal must reside on a private, persistent owner disk, NEVER a Google Drive mounted folder, cloud-sync directory or disposable Colab disk. No analyst gets the key or SQLite database. Owner verifies signed Development grant and exact FrozenIndex partition before creating a `DevelopmentPermit`. In its separate publisher subprocess, owner can access **only** the private research-events target with upload capabilities.
2. **Source broker** (trusted, isolated process): holds an OAuth token with effective `drive.readonly` scope and no write token. It is allowed to fetch exactly pinned Development partition bytes only **after** verifying the signed `OWNER_READ_ACK` and re-reading its Drive `READ_STARTED` record. The analyst does **not** receive source mount/token. Do not use full-owner `drive` scope, and do not share or modify original source folder ACLs.
3. **Analyst and results uploader**: analyst receives only an approved private Development staging or features artifact, with **no** original source mount/token. The results uploader runs with a **different identity** and the least-capable `drive.file` app access limited to its output; it must not be granted CryptoResearchLab access. For actual Drive sharing, independently verify inherited permissions and actual token scopes. The connected owner account is currently **not** adequate evidence of isolation.

Owner identity and signer separation is a process/credential trust boundary, not a claim Python dataclasses enforce OS-level access control. `RuntimeRoles` is a **checklist**, not an account-permission detector. Effective ACL/scope checks and sandbox process tests are mandatory before any real row read.

## Durable owner READ_STARTED protocol

- Owner uses `permit_from_signed_development_grant` to verify signed authorization against frozen partitions, Development role and expiration; denies all Validation/OOS periods.
- Owner SQLite (stdlib, local `synchronous=FULL`) begins `BEGIN IMMEDIATE`, reserves unique `(grant_id, partition_sha)` and `attempt_id`, and **commits PENDING before any cloud upload**. A crash never automatically refunds an attempt.
- Publisher uses only Drive **files.create** (not update/delete/copy) with explicit pinned private events-folder ID and one unique JSON name. Before creation, it checks for duplicates; after, it checks exact parent, name, MIME, length, SHA-256 bytes and unique matching filename with Drive API readback. Drive supports same-name duplicates; these checks require an independent single trusted writer and are not a distributed lock.
- After verified Drive readback, owner SQLite transaction changes `PENDING` -> `ACKED`. Only then owner signs a separate `OWNER_READ_ACK` blob with an offline Ed25519 key. That **signature is not uploaded to Drive**; it is handed to the trusted source broker out-of-band.
- Broker verifies owner public key by an independently pinned SHA-256 fingerprint, exact signed ACK fields, grant/partition binding, and re-fetches Drive record. If anything fails, source streams remain CLOSED.
- If Drive create/readback or SQLite ACK is ambiguous, the state remains `PENDING` and no signed ACK is issued; fail closed. No automatic retry, dedupe, deletion, reconciliation or recovery while status is ambiguous. The private owner must adjudicate separately.
- The SQLite owner DB is an independent anchor from Drive, but `SQLite FULL` and the Python mocks are **not** equivalent to a proven multi-machine crash/power-loss/permission model. Owner backup/restore, file-system behavior, token expiry, lost ACK, race/crash, and live credential separation need acceptance on the intended equipment.

## Private Drive results

`VerifiedDriveWriter` is a bounded, create-only adapter. It rejects unsafe names, oversized files, known source destination IDs, duplicate names, wrong parents, missing bytes and hash mismatch; on any uncertain outcome it **does not retry** or delete. `GoogleDriveV3Port` uses a separately injected authenticated Google Drive v3 service, without hardcoded tokens/paths or default Google credentials. `VerifiedDriveReader` and `GoogleDriveV3ReadPort` have no create method and can use read-only credentials. This is deliberately a small adapter, not a new server.

Private Drive parent IDs and signer keys belong ONLY in operator-controlled external configuration, never in public GitHub, Actions secrets for public PR builds, notebooks, examples or issue comments. Even a public repo's branch and commit history are public. `.gitignore` is insufficient by itself; track only pure code and synthetic tests.

## Remaining NO_GO gates before first C1+C2 Development read

- Owner must establish separate real Google identities/OAuth scopes, test effective permissions without attempting to write to original source, and ensure analyst has no source access. This cannot be established just by a connector on the owner account.
- Owner workstation must create its own secure database and signing key; acceptance must demonstrate durable events and readback with real scoped Drive identities. Never generate keys in CI or share keys in chat.
- The trusted broker must be integrated with existing `GovernedRealReader` using a **lazy** source opener called only after verification. No unrestricted `manifest_stream` or `parquet_stream` should be opened first. Any ACK replay must require independently durable one-time consumption for a new read (or reuse previously verified staged artifacts without another source read).
- Private result uploader must be tested with its **own** identity and source-denial proof; the connected-owner API test alone cannot establish least privilege.
- No C1/C2 market rows, Confirmation 2025 or Final OOS 2026 may be read until these gates pass and the owner explicitly approves one bounded Development pilot.

## Verification

Run local synthetic suite: `PYTHONPATH=src python -m unittest discover -s tests -v`.

The public test suite uses a fake Drive backend; no live Drive or source credentials. Manual private Drive connector smoke tests, if done, are isolated from the source dataset and never count as owner-identity separation.
