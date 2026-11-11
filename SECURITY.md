# Public repository safety

This repository intentionally publishes only code, synthetic tests and general documentation. **Never publish** raw/derived market rows, private research findings, any Google Drive folder ID/path, access token, credentials, original warehouse manifest, production journal or research grant.

Run `python scripts/audit_public_tree.py` and inspect the staged diff before pushing. The built-in audit checks obvious file types and secret patterns, but it is not a complete secret scanner. `.gitignore` is not a security boundary; previously tracked files remain public in history. Keep GitHub Actions free of Drive secrets and read-only (`permissions: contents: read`).

Do not give this public CI runner access to the canonical data store. Future market-data readers must use a separate least-privilege read-only identity; private research outputs use another isolated writer. No real market data or protected role is authorized in V1.

If an exposure occurs, revoke affected credentials, investigate Git history, Actions logs and caches and consider the data permanently disclosed.
