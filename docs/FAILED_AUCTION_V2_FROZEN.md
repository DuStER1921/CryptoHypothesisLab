# Failed Auction V2 — frozen single-candidate trading research

## Status

**IMPLEMENTED / SYNTHETICALLY TESTED — profitability unproven.** No C1/C2 market rows were used to choose parameters for this rule. July 2024 has been repeatedly explored by earlier methods, so its next analysis is **not independent** and must not be used to tune this model. August 2024 remains an explicitly owner-approved, one-shot **Development** test (not reserved 2025 Validation or 2026 Final OOS). The code and operator workflow cannot authorize new canonical reads.

## Why this hypothesis, not the previously failed V1?

The earlier V1 5s momentum-following experiment suffered nearly zero gross edge and lost about the assumed transaction costs. Blindly reversing its trades also lost. V2 tests a **different, structural event**: an attempted breakout beyond an as-of known previous 60-minute C1 high/low with aggressive buying/selling, immediately reclaimed by price and opposing executions. This is a plausible failed-auction/absorption *hypothesis*, not empirical proof. The strategy is deliberately not optimized against known July outcomes.

## Fully specified causal rules

At each available **completed** C2 5-second bucket:

1. Calculate the preceding 60 **fully completed** C1 minute bars' wick high/low. Prior C1's `available_at_ms` must be no later than the C2's `available_at_ms` (and must not use an unfinished/current C1 bar). Require contiguous C1 and C2 history.
2. In one of the **previous two** contiguous closed C2 buckets, the trade-price high must have pierced the previous 60-minute high by at least **2bps** (for SHORT) or the low must have pierced the low by at least **2bps** (for LONG). That bucket must have an aggressive taker imbalance ≥+0.65 / ≤−0.65, and executed quote notional ≥1.5× its own twelve prior 5s buckets' mean. This is a *proxy*, not an orderbook absorption measurement.
3. The **current completed** C2 closes **1–10bps back inside** the pierced level, has an opposing taker imbalance of magnitude ≥0.30 and a completed first-to-last price response ≥1bp toward the reversal side. Only one emitted side per signal.
4. Submit a hypothetical market-order intent only after all those signal buckets became available. For an adverse-fill proxy, enter at the **first executed-trade price in the third subsequent 5s bucket** (`signal_start+15s`), **with no selection on the upcoming entry price**. If buckets or timestamp/latency conditions fail, count an `UNKNOWN` attempt, not a favorable fill.
5. Protective stop **30bps** from the entry price, target **60bps**, mandatory exit after **30 minutes**; one trade at a time, **15 minutes** cooldown after exit. Do not initiate a position that could cross a UTC day boundary at the maximum time horizon. For stop and target in the same 5s bin assume stop first. For stop gaps, assume the worse of stop level and first trade in the bin. If a path gap occurs **before** actual exit, mark UNKNOWN. A later gap **after** actual exit has no effect (important anti-look-ahead rule).
6. Assumed taker fee **5bps per side**, slippage/spread **3bps per side** = **16bps total**. Adverse stress **20bps / 24bps total**. These values are assumptions, not measured/account-confirmed historical fills. Funding, borrow, capacity and real latency/quotes are unknown.
7. Negative control: hypothetical **opposite side at identical precommitted case times** with its own stop/target/time path. Controls with unavailable future paths stay explicitly unknown; they must NOT silently delete an otherwise valid primary trade. The opposite side is **not a separately executable independently scheduled strategy**.

Nominal stopped trade after base friction: **−46bps**; nominal target after base friction: **+44bps**. Ignoring time exits and adverse gaps, a ≥**51.1%** target hit rate is required to break even; actual threshold depends on all exits. No parameter grid or search is permitted.

## Acceptance and falsification

**All** fixed criteria must pass: ≥30 completed modeled trades, ≥10 UTC days, positive modeled net mean, positive lower descriptive 95% day-cluster bootstrap bound, positive mean at **20 and 24bps total cost**, same-entry opposite-side comparison by ≥5bps and with all controls available, positive later days 22 onward, unit-bps arithmetic max drawdown ≤1,200, **zero UNKNOWN primary attempts**. Any failed criterion means `FAILS_PREDECLARED_MODEL_GATES`; none of these establish real executable profitability even when passed.

No source reads, algorithmic retuning, Jupyter outputs or owner-private market packets may be committed to public GitHub. Frozen plan is downloaded **before** running outcome analysis. Preserve July freeze token and the exact code commit; August runtime requires the saved exact July token. An algorithm change requires a *new* protocol version and must not be mixed with the August one-shot.

## Procedure

- Run new no-Drive `colab/09_failed_auction_v2_offline.ipynb` with the existing private July MICRO ZIP. Verify its SHA-256 and save the immutable plan and strategy V2 freeze token before evaluating labels. Download the full PRIVATE report, including losses/unknowns.
- Do **not** describe July as OOS or use it to pick a favorable variant. August 2024 requires the owner's new, explicit read grant in **separate** read-only Colab 04 to produce a private MICRO ZIP, then use Colab 09 offline, compare exact July token, and run the frozen one-shot test without altering code.
- Reserve 2025 Validation and 2026 Final OOS as protected. Even good August Development results justify only a later owner-authorized research review, not a trading claim.
