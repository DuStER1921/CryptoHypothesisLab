# V3.1 — Missed references, every 5-second decision, frozen Development sequence

**Scope:** Pure offline owner-provided, SHA-verified `HYPOLAB_MICRO_DEV_*.zip`. This implementation does not connect to Google Drive, CryptoResearchLab, canonical storage, Binance, or any broker. Validation 2025 and Final OOS 2026 remain protected. A staged MICRO ZIP contains real-derived private trading statistics; never commit it, a private plan, or an outcome report to public GitHub.

## Why this is a diagnostic, not a demonstrated edge

Earlier V3 experiments used a restricted quiet-price decision grid and retrospective trailing-60-second crossing references. This methodological document deliberately does **not** publish private observed counts, outcomes or experimental rankings. The reference is not a measured physical onset or prospective executable opportunity. The Development month has been inspected repeatedly, so any newly selected method incurs selection bias.

V3's `_feature_at` also has an internal **15-bps start-to-end filter**, separate from the published 8-bps maximum *intra-bin* high-low gate. V3.1 computes a new as-of C2 feature from the current completed 5s aggregate plus exactly 12 earlier contiguous 5s bars **without** that hidden endpoint filter. The primary V3.1 12-bps gate is explicitly a new exploratory proposal, not proof that relaxing the gate improves outcomes.

## Fixed, inspectable protocol

The protocol is serialized as deterministic canonical JSON by `protocol_bytes()`. Its SHA-256, the SHA-256 of `premove_v31.py`, `premove_v3.py`, `micro_bundle.py`, `micro_warning.py`, and the Colab 07 notebook are combined into one `freeze_token_sha256()`. Every private plan and private report carries the token. **New-month Colab 07 requires the owner to paste the saved first-run token**, rejecting any code/protocol drift. A full new authorization is needed for every canonical month staging operation, and none is implied by this offline module.

1. **Retrospective references**: V3-equivalent first trailing-60s close-price crossing of ±30 bps, 300-second cooldown, full contiguous 5-second grid and same UTC day. We anchor each reference to the **start** of its terminal 5s bucket. Physical onset, when intra-bin trades arrived, and bid/ask at that moment are unknown.
2. For **all** references, inspect exactly **5, 15, 30, 60 and 120 seconds before** their start. For each reference and V3 rule, an additional ordered diagnostic categorizes legacy warnings as `WARNED`, `MISSED_BY_25_55_GRID`, `BLOCKED_BY_8BPS_QUIET_GATE`, `COOLDOWN_OR_RULE_STATE`, `ONLY_OPPOSITE_DIRECTION_TRIGGER`, `NOT_AVAILABLE_AT_REFERENCE_START`, `NO_ASOF_OBSERVATION` or `NO_RAW_TRIGGER`. This categorization is descriptive; stale-placebo raw conditions may depend on the eligibility of the scenario that generated them. Each snapshot contains observed executed delta BTC, imbalance, aggregate trade count, quote notional, trailing 60s max high/min low range, an as-of availability flag, and a derived stage: `QUIET_8BPS`, `EMERGING_8_TO_20BPS`, `ACTIVE_OVER_20BPS`, `UNKNOWN_GAP_OR_BOUNDARY`. Stage labels are **descriptive past-range classes**, not the onset of a price move. Specifically, with a nominal 1.5-second delay, the interval starting only five seconds prior is **not yet available** when the reference bucket begins; the report flags this. Missing samples and earlier C1 context remain UNKNOWN; no forward filling.
3. **Six fixed sensitivity scenarios**: (a) decisions on seconds 25 and 55/minute with 8bps, 12bps or no past-range cap, versus (b) **every completed 5s bin** with 8bps, 12bps or no cap. All scenarios require full contiguous 12-bin past C2 history, past-only C1, available prior bar and the exact same seven V3 candidate families (including 60m timing placebo). For each scenario report eligible decisions, raw triggers, post-330s-cooldown alarms and direction-matching alarms in the 60 seconds before each reference. These are **multiple diagnostic views**; comparing them to choose the best would invalidate independent significance claims.
4. **One fixed primary risk set** for matched outcome analysis: all completed 5s bins with trailing 60s within-bin executed-price range <=12bps, previous completed C1 bar + its 20-bar reference, C2 flow current completed bucket and older only. Apply exactly the original V3 rule thresholds, 330s per-rule cooldown, six core rules plus stale-flow placebo. Match each case one-to-one to the **earliest earlier** non-triggering control at least 330 seconds before the signal, in the same UTC day/hour, past-only C1 range regime, Asia proximity and C1 compression; near-Asia matching is omitted only for the C1 near-Asia exposure itself. Match before future outcomes. Controls are never a suppressed raw trigger for that rule. Log unmatched cases, no fabricated pairs.
5. Labels unchanged from V3: next **fully completed** 5s last executed trade is a **NONEXECUTABLE** baseline; strict continuous and same-day closed-trade ±15/30bps first crossing at 60/300s. Report all 7×2×2=28 trials, first 21 days vs last days *inside Development*, case/control event-rate difference, direction, UNKNOWN paths and day-cluster descriptive bootstrap. No multiple-testing p-values, causal claims or fill estimates.
6. Never change fixed rules or calendars based on the July results before a chronological Development check. If code changes, it creates a **new protocol** rather than silently extending the old token. There is no access to protected 2025/2026 splits.

## Predeclared next months — one by one, no automatic source reads

`2024-08` → `2024-09` → `2024-10`.

These months have historically been marked with both C1 and C2 daily family availability in the frozen calendar, **but that is not an assertion that the separate owner source, SHA or real run has passed**. The month must exist and pass `FrozenIndex.load`/pinned-partition validation at each preparation. If a chosen month fails integrity or owner authorization, record the failure and **stop rather than substitute an attractive month**. Do not backfill alternate months without a separately frozen protocol revision.

- **July diagnostic**: use existing verified private July MICRO ZIP in a fresh no-Drive Colab 07. Save exact ZIP SHA, plan, full private report and the printed V3.1 freeze token. The report is exploratory, not a source of production thresholds.
- **Before each new month**: open a **separate fresh owner Colab 04**; verify Universe and Calendar frozen hashes, manually select the next fixed Development month and type an entirely new owner confirmation. A fresh pinned read-start attempt and full source validation are mandatory. Download the new private ZIP and delete owner runtime.
- **For each approved month**: open a fresh no-Drive **Colab 07**, upload exactly one ZIP and verify its exact SHA. When prompted, enter the **July-saved freeze token** before any outcome calculation. Download canonical plan BEFORE running labels, then download full report. Preserve null/negative results. Never upload private packets, plans, results, original paths or Drive IDs to GitHub or Actions.
- **Cross-month evaluation**: consider the three predetermined months in chronological order with *all* trials retained; do not pick only months/cases that appear favorable. A post-hoc pooled summary may be descriptive, but it is not protected Validation or Final OOS. To claim a trading advantage, additional independent design, real fill/quote/fees/latency evidence, serial dependence and multiple-testing correction are still needed.

## Acceptance gates

| Gate | Code-delivery result |
|---|---|
| Exact protocol/no source API/clean notebook safety | Must pass static/synthetic checks and CI |
| 5/15/30/60/120 lookback including missing/unavailable flags | Synthetic tests required |
| 5s/sparse × 8/12/no-quiet scenarios and same-day fences | Synthetic tests required |
| Primary matched controls; all 28 fixed outcomes | Synthetic integration + CI required |
| Real July V3.1 execution on owner's MICRO ZIP | Not performed by code-only CI |
| New 2024-08/09/10 real reads and analysis | **NOT PERFORMED; requires new explicit owner authorization per month** |
| Protected Validation 2025 / OOS 2026 / fills / L2 / confirmed alpha | **NOT AUTHORIZED / NOT ESTABLISHED** |
