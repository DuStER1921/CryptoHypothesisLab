# Systematic C1+C2 Discovery V1 — code-only research contract

## Why this replaces idea-first strategy writing

Failed Auction V2 and Emergence Continuation V1 are *individual hypotheses*, not evidence of profit. This separate experimental engine asks **where, if anywhere, pre-decision C1/C2 states predict a future executed-trade price return that exceeds a matched temporal control and a hypothetical friction hurdle**. It does **not** perform stop/target optimization, search execution parameters, or enter orders.

## Data boundary and chronology

- Only an owner-uploaded, SHA-256-verified private MICRO ZIP containing 1-minute C1 and 5-second C2. No Drive mount, no live network source reads, no modifications to CryptoResearchLab, no 2025 Validation, no 2026 OOS.
- One deterministic observation per UTC minute: first completed 5s C2 bucket (`start_ms % 60000 == 0`). All 12 lookback C2 buckets are contiguous and confined to one UTC day; previous 60 C1 minute candles are complete, contiguous and `available_at_ms <= decision data availability`. No C1 current-minute OHLC.
- Exactly **12** as-of indicators: 5s and 60s taker imbalance, 5s relative trade-count and quote notional, 5s price response, 60s C2 return and executed-price range, 5s largest aggregate fraction, previous C1 return/range/relative volume, and position below the previous known 60m high.
- Freeze **train-only** unconditional 33rd/67th feature quantiles from July 1–21. **Never** fit on future returns or on August.
- For the forward proxy, the first executed trade in the **third subsequent completed 5s bucket** is the reference entry, after assumed latency. Horizons exactly 300/900/1800/3600 seconds, ending with a future 5s trade-close; every missing or cross-day path is UNKNOWN, not filled or profit-imputed. Neither price is an executable quote or proof of an achievable fill.

## Fixed multiplicity

- 12 single-feature three-bin memberships = **36**, 66 two-feature 3×3 memberships = **594**. Total **630** memberships × 4 horizons = **2,520 fixed hypotheses**.
- Direction is **not preselected by looking at August**. The sign of the TRAIN July 1–21 daily excess return gives a single frozen LONG or SHORT orientation, with a **two-sided** test (no uncounted direction search).
- Outcome-only ex-post control: within each UTC day/hour subtract the average future price return of all eligible risk-set observations. This is a **descriptive matched-hour benchmark**, never a feature in the live decision. Similar day/hour does not match all confounders, and overlapping forward returns are correlated.
- Unit for inference is a **UTC-day mean**. A hypothesis is eligible for statistical screening with ≥150 training observations across ≥10 active UTC days. A synchronized Rademacher sign flip of whole UTC days gives 1,000 Monte Carlo **max-|t|** replicates across the entire 2,520-member family; same day sign for all hypotheses preserves cross-hypothesis dependence. The resulting p-values are an *approximate exploratory screen*, subject to assumptions about sign symmetry, limited days, serial dependence, and earlier July exploration; **not** an independent OOS statistical proof.
- To proceed, a candidate must pass *all* precommitted gates: approximate train adjusted p ≤ .05, both train and July 22–31 descriptive gross return minus **16 bps** assumed round-trip friction positive in TRAIN-selected direction, late-July excess return in the same direction, and late-July sample coverage ≥75 observations over ≥5 days. Keep ≤3 candidates by (corrected p, magnitude, stable ID). **If none pass, abort August under this protocol. Do not rescue by lowering thresholds.**

## One-shot replay

- July is repeatedly inspected Development. Days 22–31 are *internal diagnostic only*, not true independent validation. The whole month is now research-contaminated.
- The owner saves (a) canonical July **feature-only** plan *before computing outcomes*, (b) full July report and (c) deterministic sealed-ish candidate manifest. It is a source/code hash chain, **not a cryptographic attestation of an untampered real-world run**.
- Only after the July shortlist is finalized can a separately owner-authorized read-only preparation of August 2024 occur via Colab 04. The offline Colab 10 requires unchanged algorithm/ZIP schema and the three saved private July files, checks manifest against report deterministically, and evaluates **only frozen selected candidates**, without scanning August's 2,520 hypotheses or refitting bins.
- August remains Development, not protected Validation/OOS; one month is insufficient for operational proof. Any post-August re-selection would require a new future untouched month before confirmatory claims.
- The **16/20/24 bps** round-trip cost values are sensitivity assumptions, not measured actual fees/spread/fill impact. Returns use no stop or liquidation path. A positive result is a predictive proxy to investigate, **not** a profitable, executable strategy.

## Reasons to stop or downgrade confidence

No eligible observations, missing C2 bins, C1 freshness failure, incorrect ZIP digest, code/protocol drift, no eligible hypotheses, maxT not significant, negative late-July proxy, short list empty, any Aug mismatch, unavailable bid/ask, and unstable day effects all block a claim of profitable trading. Every attempted hypothesis (including negative/null/underpowered) is retained in the private report; no results published to the public code repository.
