# Security review V1 — before PR #1 merge

**Scope:** Synthetic-only research foundation. No market-data access, source Drive permissions, actual production mounts, Colab/OAuth or sealed-window grants. GitHub is public; all sample input and output are synthetic.

## Finding -> correction -> proof

| Finding | Correction | Regression |
| --- | --- | --- |
| Concurrent `ExperimentLedger.append` operations could collide | Advisory POSIX `flock`, one exclusive writer; concurrent reads refuse instead of observing partial data | `test_multiprocess_style_exclusive_lock_refuses_other_reader_and_writer`, `test_separate_process_cannot_append_during_exclusive_lock` |
| Mid-batch crash lacked a safe interrupted-state contract | Create-only `.incomplete-transaction` sentinel, file/directory fsync, readback; never silently recover after failure | `test_interrupted_multi_append_blocks_all_future_reads` |
| Full replay on every trial scales quadratically | `append_many` validates and prepares up to 5,000 events; single full replay per batch; Discovery records results in 1,000-event chunks | `test_bounded_bulk_append_and_expected_head` |
| Registered trial *count* did not pin trial identities | Batch captures exact ordered `trial_ids`; unknown/repeated trial results fail closed | `test_exact_registered_trial_id_required` |
| Path checks alone could follow a replaced symlink | POSIX `openat`-style directory descriptors and `O_NOFOLLOW` for each component; `fstat`, regular file/link count and bounded immutable SHA read | `test_directory_symlink_denied`, `test_special_file_rejected_without_hanging` |
| Re-reading the same bars for each threshold was costly | Cache each horizon's returns and sorted imbalance distribution; answer variants by `bisect` | `test_sorted_threshold_equivalent_to_reference` |
| Public repo could accidentally accept market-data artifacts | Non-mutating tracked-file audit and GitHub CI checks | `test_public_repo_audit` |

## Known limits — admission blockers

1. **Real frozen C1/C2/C3 reader:** not implemented. The public `ValidationGate` always rejects `scope='REAL'` before reading bytes.
2. **Production ledger/authorization:** not implemented. This is a **local POSIX** cooperating-writer journal; advisory flock is not a security boundary against a hostile writer and has not been proved over Drive/FUSE, network filesystems, Windows or distributed writers. Independent, immutable head anchoring is missing. An untrusted user able to rewrite the entire journal can forge an unanchored chain.
3. **Crash recovery:** the interrupted marker intentionally blocks automatic replay. Owner must inspect and recover the synthetic journal manually or recreate it; never blindly remove the marker for protected data. No production durable approval is claimed.
4. **Scientific inference:** findings are descriptive *synthetic* means, not statistical significance. No autocorrelation-adjusted confidence intervals, block bootstrap, FDR, walk-forward evidence or market-edge claims.
5. **Source protection:** no physical original source path is present in this repo. OS/OAuth least-privilege read-only access and a separate private uploader still need independent acceptance before any real read.
6. **Public source guard:** the tracked-file scanner detects obvious banned files/strings only and operates **after a commit is created on CI**. Run it before pushing locally; manual diff review and a dedicated secret scanner remain required. Public commits cannot be unexposed by deleting a file later.

The existing Development `[2020-01-01, 2025-01-01)`, Validation `[2025-01-01, 2026-01-01)` and Final OOS `[2026-01-01, 2026-09-01)` roles remain fixed and **do not authorize real research**.

## Local reproducibility

On a POSIX machine with Python 3.11 or 3.13:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/audit_public_tree.py
```

On Windows, time-only checks work but POSIX journal and fixture gateway tests are explicitly skipped; do not interpret skipped tests as accepted Windows/Drive durability.
