# Public repository safety

This repository is intentionally public and contains **only source code, synthetic tests and non-sensitive documentation**.

Do not commit tokens, Drive folder IDs or paths, source manifests, raw or derived market data, research findings, notebook credentials or production ledger records. `.gitignore` is a convenience, not a security barrier: review the staged diff and run a secret scanner before pushing. Use separate, least-privilege identities for future source reads and private output writes.

No real market data is authorized in this V1. CI has no secrets and MUST remain synthetic-only. For a suspected leak, revoke affected credentials immediately and review Git history, Actions logs and caches.
