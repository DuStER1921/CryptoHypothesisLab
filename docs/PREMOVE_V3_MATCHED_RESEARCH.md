# V3 — Pre-Move Research: causal risk sets and matched controls

**Scope: offline Development-only descriptive testing of an existing private, already verified `HYPOLAB_MICRO_DEV_*.zip`. No C1/C2/C3 source reads, writes, Drive mounts, new OAuth, live orders, Validation 2025, or Final OOS 2026.**

## Why V2's attractive examples are insufficient

A spike in C2 delta *at* an ex-post 30bps 60s reference is an observed accompaniment, not proof of prediction. V2's mean seconds until a threshold hit is **not seconds before actual onset**. V3 must measure the numerator and denominator at *all* eligible decision times, not just selected successful moves, and use controls drawn without looking at labels. The same July 2024 Development month has been previously inspected many times; no result from it can count as independent confirmation.

## Deterministic experimental contract

1. **Separate clean Colab 06**: Drive never mounted. Owner uploads the private V2 micro ZIP from their computer, enters the **micro ZIP's** expected SHA-256, validates manifest + *every* C1/C2 5/15/60s row and cross-resolution totals, then downloads an **exact canonical fixed plan** with packet/code SHA before any outcome evaluation. Missing or tampered packet / modified plan fail closed.
2. Evaluate **only completed 5s C2 buckets starting at seconds 25 and 55 of each UTC minute** (two decisions/minute). Require a full contiguous preceding 60-second C2 trail and a maximum observed **intra-bin high/low** range of **8bps** over that window. This excludes many moves already under way but does **not** establish a true physical onset. Restrict C1 to **the previous completed 1m bar** with its latency boundary, including 20 *earlier* bars for as-of range-compression reference. Asia high/low is from complete C1 wicks 00:00–07:59 UTC and is unknown until the session closes.
3. Seven fixed predeclared signals (not threshold-tuned on July outcomes): two C1-only candidate rules (`near Asia`, `prior 1m wick rejection`), two C2-only (`executed-flow surge`, `heavy taker flow/weak price response`), two C1+C2 combinations (`Asia+flow`, `compression+flow`), and **one timing placebo** (`C2 flow from exactly 60 minutes earlier`, only when observable). Every rule has a **330-second cooldown** and each candidate is recorded independently of future prices.
4. **One-to-one matched controls** are selected without accessing future labels. Match candidates to a non-signal decision on the same UTC day, hour, as-of C1 price-range regime, Asia proximity and C1 compression, using only **earlier** non-alert controls at least **330s before** each signal. For `c1_near_asia` specifically, proximity is the exposure and must **not** be matched away. For `fusion_asia_flow`, near-level status **is** held constant to test the added C2 condition. Suppressed raw signals cannot masquerade as controls. Insufficient matches are explicitly reported and NOT backfilled with dissimilar times.
5. **Outcomes**: use the **NEXT completed 5-second last-trade price** as a **non-executable reference price**, then first +/-15bps or +/-30bps **closed-bucket** crossing over 60s or 300s. Entire future horizon must be contiguous and inside the same UTC day; otherwise the pair is **UNKNOWN and excluded**, never a `no_move`. Separately count direction matches/mismatches. First +/-5bps crossing from the new entry-price proxy is only a **proxy for departure**, not true onset. This metric measures elapsed seconds **after** the entry proxy, not pre-onset lead time; we explicitly avoid an unsupported timing claim.
6. The first **21 UTC calendar days** are an exploratory training segment and remaining days are a *later Development diagnostic*, **not Validation/OOS**. Retain **all 7×2×2 = 28 rule/threshold/horizon evaluations**, including empty rules, unmatched cases, unknown paths, negative controls and nulls. For matched cases report alert event probability, baseline event probability, risk difference, ratio (null when zero controls), directional comparison and number of active days. A **500-draw day-cluster bootstrap** reports a descriptive range for risk difference only if at least 100 evaluable matched pairs spanning at least 8 calendar days. It is *not* a significance p-value, an independent confidence claim or a validated edge. Daily cluster resampling does not cure selection/observational confounding.
7. Independently report retrospective first 30bps trailing-60s references with a 5-minute cooldown and whether each rule warned in the preceding minute. These references are **NOT actual onset**. First chronological positive/negative cases are displayed in a **retrospective** gallery for diagnostic understanding, not model selection.

## Engineering boundary / interpretation

- Comparison on the **same eligible observed C2 grid** is fair as a feature ablation but the `C1` family is *not* a pure C1 standalone strategy: eligibility/quiet-gate itself uses C2 observed prices. A strict standalone C1 strategy would require a separate C1-only risk-set study.
- Exact matching on as-of covariates is not a randomized causal experiment. Intraday volatility shocks, funding, economic announcements, persistent order flow and venue-specific mechanisms can confound results. Some near-Asia cells may have no suitable control and are reported unmatched.
- `C2 aggTrade` intensity tracks **aggregated executions**, not individual market orders or resting-order-book inserts/cancels. `C3` is explicitly **UNKNOWN_NOT_INCLUDED**. There is no observed latency, spread, queue position, fills, slippage, fees or real HFT market-making/arbitrage profit.
- If timing placebo performs similarly to fresh C2 signals, treat the alleged pattern as possibly just market-regime exposure. If C1+C2 appears better than C1 alone, require **new chronological Development months**, paired regime controls, trading costs and independent later permissioned validation before making any prediction or profitable alpha claim.
- The report and packet include historical-derived private market data. **Do not publish their rows, hashes linked to private source, plans or reports in public GitHub/Actions.** Keep source `CryptoResearchLab` completely unchanged.

## Acceptance gates

| Gate | Status |
|---|---|
| Canonical code-only V3; no Drive/source APIs | CI synthetic suite required |
| Negative, tampering, as-of, gap, same-day, control matching tests | CI synthetic suite required |
| Notebook 06 safe and plan downloaded before labels | Public audit and tests required |
| Real owner July 2024 V3 run on existing private ZIP | **Not independently run by code-only PR** |
| New-month Development walk-forward | **Not started; separately owner approved for source reads** |
| Validation/OOS, C3, executable profit | **NOT AUTHORIZED / NOT ESTABLISHED** |
