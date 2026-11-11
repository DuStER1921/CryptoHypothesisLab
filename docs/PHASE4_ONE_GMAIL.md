# Phase 4 — one-Gmail owner airlock (code, not production GO)

**Status: synthetic accepted only. Real market row reads still NO_GO.**

## Architecture: single Google account, separate process capabilities

Use **one Gmail** with **two distinct OAuth authorizations**, not two Google identities:

- Trusted owner source process: requests exactly `drive.readonly`, restricted in code to exact frozen Development partitions and signed grants. This OAuth scope **can also see Validation/OOS account-wide**; the code gate limits intentional reads but is NOT Google ACL-enforced blindness. Source token never enters the analyst process.
- Trusted owner output process: requests exactly `drive.file` and can write/readback app-created files. A pre-existing Drive folder is **not guaranteed visible** to a fresh `drive.file` token: select the output folder via Picker or explicitly test app access before relying on it. No broad `drive` write scope, no owner account token from a full-drive connector in automated research.
- Analyst: separate isolated, unprivileged OS process (ideally container/VM) with **no** source mount, OAuth credentials, user-profile token cache, owner SQLite/key or cloud host authorization. Only sanitized and verified Development-only staged inputs are passed to it. No Google Drive mount in the analyst.

The same Gmail remains owner of both folders, so these measures are operational isolation, **not cryptographic removal of the account's ability to see protected data**. If malicious code is run in the trusted source process, no Python guard can guarantee OOS blindness. Never execute untrusted notebooks there.

## Owner-side sequence

1. Independently pin the 8-byte-evidence frozen Universe, owner public key and signed Development grant in **private configuration**, outside the public repo. Explicitly approve only a small complete Development partition.
2. On owner persistent disk, create a **fresh schema 2** SQLite journal (phase 3 schema 1 is intentionally not auto-migrated). Never store it on Colab, Drive mount or synced folder. Use separate owner Ed25519 signing key; keep a protected backup and prevent restoring an older snapshot over the current head.
3. `OwnerReceiptAuthority.issue` (existing) validates the *actual signed grant* and creates a `READ_STARTED` Drive event, checks full readback and commits the owner `ACKED` state before releasing signed ACK. On ambiguous response remains `PENDING`; no retry.
4. `OwnerOneUseBroker.stage_once` **revalidates** the signed grant against exact FrozenIndex, verifies owner ACK signature and private Drive event bytes, then does a SQLite `BEGIN IMMEDIATE` one-use consumption. **Only after COMMIT** does it invoke the injected read-only pinned source opener and stream the verified partition into a private scratch directory. A failed source open or process crash burns the attempt; replay is denied across owner DB reopen.
5. The owner exports only verified Development data (prefer one reusable feature cache after source verification) to the private analyst workspace. Do not give the analyst the raw source OAuth token, mount, SQLite DB, signing key or original Drive path. Results upload via separate `drive.file` token, write-once with readback.

## Proofs still required for first real row

- Operator establishes two independently scoped tokens under the *same Gmail*, validates actual OAuth token scopes (not only Python-requested scopes) and output-folder permission. `TokenScopeDeclaration` is **configuration validation only**. The installed connected ChatGPT Drive app **cannot** stand in for this proof.
- Owner journal initialized on owner persistent disk with actual crash/restart and rollback-resistance procedure; private signing key custody and exact key-pinning proof.
- Read-only provider implementation for actual frozen source file IDs and streaming of multi-GB C2 partitions, verified without bulk memory reads; source path/IDs remain private. Test fail-closed behavior with synthetic provider first.
- Analyst process launched in a genuinely separate sandbox/user with no source token/mount/cache. `preflight_analyst_environment` is advisory only and cannot prove sandboxing.
- Small owner-approved Development-only experiment with exact period/caps, no source mutations; no Validation 2025, Final OOS 2026, or C3 without separate quality approval.

## Public code constraints

No token, key, file ID, Google Drive folder URL, market rows, derived private results or private journal in Git history, Actions logs or PR comments. Synthetic-only GitHub CI; no GitHub secrets required. Keep `GovernedRealReader` default-deny; only trusted owner can instantiate `OwnerOneUseBroker` after all external proofs.

## Large-file Drive reader

`GoogleDrivePinnedSource` provides a concrete optional Drive v3 read-only source
adapter. It takes a **private exact-ID map** supplied by the owner for the
frozen manifest and Parquet file; it does not search Drive and does not log IDs.
It demands `credentials.granted_scopes == {drive.readonly}` (unknown scopes fail
closed), checks provider metadata/size, and uses 4 MiB `MediaIoBaseDownload`
chunks into fresh local POSIX scratch files. `ReadOnlyStager` subsequently
verifies both complete SHA-256 identities and the manifest before any feature
consumer sees bytes. This uses temporary disk for download plus staging, so
budget at least two copies of the largest partition. Source bytes are deleted
from **only the adapter's temporary scratch** upon context exit; never from the
original Drive. Actual owner OAuth and large-C2 tests remain pending.

`ScopedSingleGmailDriveWriterPort` enforces a verified `drive.file`-only
OAuth granted-scope declaration before enabling the existing create-only
uploader. An unknown/elevated output token fails closed. No OAuth login
client, token or output-folder Picker grant is embedded in the public repo.
