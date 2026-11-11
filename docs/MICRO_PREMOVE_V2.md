# Micro Pre-Move V2 — owner-approved C1 OHLCV + 5/15/60-second C2

**State:** implementation and synthetic CI only until an operator performs a new bounded Development month pilot. This is exploratory research, *not* validated alpha, observed HFT execution, true resting-order-book OFI, or market-neutral arbitrage.

## Problem addressed

The first minute-feature ZIP retained only `close` and aggregate taker volumes/counts. It discarded C1 `open/high/low/volume/count/taker` and most C2 within-minute sequencing. C1 close-based Asia high/low was not the actual high/low of the completed Asia session. These omissions can matter for identifying early price acceleration or exhaustion. V2 preserves price-range context and more granular **executed** taker-flow statistics with one Parquet iterator pass for C2 (source staging/hash verification is separate).

## Minimal two-notebook procedure; same Gmail

1. In a **new** owner prep Colab VM, run [`04_prepare_micro_development.ipynb`](../colab/04_prepare_micro_development.ipynb). It clones audited public code *before* Drive mount, verifies the frozen eight-file metadata proof using owner-pinned universe/calendar SHA-256, and accepts exactly one frozen `klines` + `aggTrades` **Development 2020–2024** month. No Validation 2025 or Final OOS 2026. The operator must **explicitly type** `AUTHORIZE_MICRO_DEVELOPMENT_YYYY-MM`; earlier authorization for a minute ZIP is **not reused**.
2. Before opening market-data bytes, record a distinct private create-only/readback `COLAB_PREP_STARTED` event with `purpose=MICRO_5S_15S_60S_V2`, explicit source hashes, code commit, latency assumption and attempt ID in the **separate** `CryptoHypothesisLab/research-events` folder. This is a Drive FUSE **manual owner trace** and **not** independently proven durable, atomic or a signed Phase-4 broker receipt. Never erase a failed prior attempt or automatically retry an uncertain one.
3. Open exact frozen-generation manifest and Parquet paths only `O_RDONLY`; stage both to protected local `/content` scratch using full original source manifest/Parquet SHA-256, then verify C1/aggTrades Arrow schemas. C1 is read in batches for full 1m OHLCV, including wick highs/lows, taker buy, trade count. C2 is **iterated once** through `iter_verified_staged_c2_micro_buckets` into **sparse** observed 5/15/60-second buckets with taker buy/sell base, raw/normalized delta, executed quote volume, aggregate-trade intensity, first/last/min/max executed price, maximum aggTrade notional and longest same-side aggregate run. `is_buyer_maker=true` means **taker sell**. aggTrade != an individual market order; notional concentration is an approximate transaction grouping measure.
4. Build a **private** create-only `HYPOLAB_MICRO_DEV_YYYY-MM_<attempt>.zip` containing `manifest.json`, `c1_1m.csv`, `c2_5s.csv`, `c2_15s.csv`, `c2_60s.csv`. Manifest includes pinned C1/C2, frozen universe/calendar, producer commit, owner start-event SHA, hypothetical positive latency, SHA-256 and exact bytes/row counts for each CSV. ZIP reader independently verifies every row, full C1 minute grid, sparse timestamp ordering, provenance, bounded uncompressed sizes and **cross-resolution C2 row-count/notional/volume/price consistency**. It rejects extra files, inconsistent 15/60 buckets or any protected period. Missing C2 buckets remain **UNKNOWN**, not zeros. Source originals are never modified. Keep the ZIP private, **never GitHub/Actions**.
5. Download the new ZIP to your computer and record its exact SHA-256. **Disconnect and delete the owner Colab runtime**, including mounted credentials. In a completely new, unmounted VM, run [`05_micro_warning_offline.ipynb`](../colab/05_micro_warning_offline.ipynb). Manually upload only the ZIP, confirm its independent SHA, then download the **exact registered plan before any labels**. The offline analysis runs with no Drive tokens or mounts. Download the private full report, including failures and negative candidates. Do not share real findings on the public repo.

## Causal interpretation

At each **fully completed** observed C2 5s interval, use **only past 12 contiguous observed buckets** for prior 60s intensity and delta baselines. Use the current complete bucket's executed buy/sell, normalized imbalance, raw delta acceleration, normalized imbalance acceleration, aggTrade counts, relative executed quote notional, maximum aggTrade concentration and executed-price impact per million quote. The optional C1 OHLC wick/range context is derived **only from the previous fully closed minute**, never the current incomplete 1m candle.

Asia high/low is computed from **actual C1 high/low wicks** between 00:00–07:59 UTC only after the session has finished and the assumed bar latency has elapsed. Before then, the Asia levels are **UNKNOWN**; no hindsight or current incomplete candle is used.

Five **fixed** direction rules, *not optimized on the test outcomes*:

1. `flow_surge_follow`: aggressive-side flow dominates, relative notional and aggTrade-rate surge, and signed imbalance accelerates.
2. `flow_surge_fade`: same surge but hypothesize short-term exhaustion.
3. `low_price_response_fade`: heavy one-sided taker flow has little first-to-last executed-price effect (**absorption proxy**, not confirmed resting liquidity).
4. `large_agg_cluster_follow`: one aggTrade dominates short-bin notional, plus high flow and consistent aggressive direction (not proof of an institutional/iceberg order).
5. `asia_wick_pressure_follow`: executed flow pushes toward the **completed C1 wick** Asia high/low after 08:00 UTC.

For each closed 5s signal: apply a 60-second **time-based cooldown**, use the **next fully closed 5s executed-trade price as a NON-EXECUTABLE entry proxy**, and evaluate *first* +/−15 or +/−30 bps crossing at horizons 60/300 seconds. All labels stay inside the same UTC day and Development month. Missing future 5s bins are `UNKNOWN`, **excluded from the eligible denominator**, never counted as either success or false alarm. Count `correct`, `wrong_way`, `no_move`, `unknown`, false alerts, valid active days and successful lead to the first closed-bin hit. At least 50 evaluable warnings on 10 days are needed even for a *descriptive* sufficiency label; no significance/edge is claimed.

Separately, ex-post reference episodes are first *trailing 60-second* closed-trade-price crossings of 30 bps, separated by a 300-second time cooldown. Report how many had same-direction warnings in the preceding minute and how many did not. These are **retrospective references**, NOT true onset times; there may be an unobserved move inside a bucket. The report includes an explicitly retrospective 5s timeline before/during/after each of up to 8 episodes and a four-panel price/delta/notional/aggTrade concentration plot. It never treats attractive examples as sufficient research evidence.

## Market microstructure limits and next scientific gates

- **No C3 BookDepth in V2 ZIP.** `UNKNOWN_NOT_INCLUDED` is explicit; it does not imply zero resting orders. C3 source snapshots are daily, sampled and quality-gated; selected days are `WARN` and must not be silently considered valid. Even `PASS` sampled depth cannot identify all order insertions, cancellations, bid/ask quotes and queue dynamics needed for true book-order-flow imbalance.
- C2 source `transact_time` is an exchange event timestamp, *not* arrival time at the future live trading machine. A hypothetical 1500ms availability delay is a **scenario**, not a measured latency distribution.
- The `last_trade_price` of a completed bin is **not** an actual executable next bid or ask quote. This project reports neither realizable fees/spread/market impact nor order fills or a tradeable HFT profit estimate.
- **Same-month selection bias:** a month previously inspected under V1 cannot be used as independent evidence for V2. A single July result cannot establish an edge. A bounded pilot first validates source/data-plane mechanics and the false-alarm methodology. Then freeze the fixed hypothesis families and run new Development months in chronological walk-forward evaluation; preserve every trial, code SHA and negative finding. Protect Validation 2025 and Final OOS 2026 until independently authorized, and do not auto-open them.
- Build reports with immutable packet/plan hashes and never suppress warnings without a documented independent reason. A future confirmation phase must add effective cost/latency/quote data and statistics appropriate for serial dependence and multiple testing.

## Acceptance matrix

| Proof | State after code merge |
|---|---|
| Pure synthetic 5/15/60s aggregation and tamper/lookahead tests | CI expected |
| Two Colab notebook compilations and public-file audit | CI expected |
| Genuine owner Colab 04 C1/C2 source processing and output SHA | **NOT YET RUN / needs new owner consent** |
| Clean Colab 05 real V2 package analysis | **NOT YET RUN** |
| Genuine C3 quality-filtered as-of depth features | **NOT IMPLEMENTED** |
| Executable HFT or validated/independent edge | **NOT ESTABLISHED** |
