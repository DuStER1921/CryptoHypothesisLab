# Pre-Move Research V1 — causal minute alerts, false alarms, event context

**Status: DEVELOPMENT discovery only. Not an independently validated trading edge, executable fill, HFT/venue arbitrage, or a full order-book analysis.**

## Operator workflow (one Gmail, no custom OAuth)

1. Reuse a previously SHA-verified `HYPOLAB_DEV_YYYY-MM_*.zip`. Do **not** reopen the huge source C2 partition or the prep Drive mount merely to analyze that minute packet. In a **fresh** Colab session run [`03_premove_offline.ipynb`](../colab/03_premove_offline.ipynb). The notebook has **no Drive mount** and accepts only a manually uploaded private Development ZIP, with a separately typed expected SHA-256.
2. Save the exact canonical warning plan to your computer **before** running labels; the engine refuses edited plans. The plan, ZIP and the entire result report must remain private, and the same month's previous inspections mean this is **not independent preregistration**.
3. Run all **3 fixed mechanisms × 2 thresholds × 3 horizons = 18** rule/label combinations. Results preserve **every alert, failure and insufficient result**, including wrong-direction threshold hits and no-move false alarms.
4. Plot the *ex-post explanatory* 30-minute pre-reference timelines in the notebook. These are **not** proven lead indicators; the burst reference is constructed after seeing 15-minute trailing price returns and is not the true onset of an explosive move.
5. For a future higher-resolution dataset, the library includes `c2_microstructure.rollup_aggtrades`: a **single-pass**, bounded-state 5/15/60-second stream rollup of already-authorized/staged C2 aggTrades, without filling absent bins. It has no Drive access or source authorization API. **Real-source Colab wiring and full pilot acceptance for this raw-C2 path are not included.** No new source bytes were read to release this module.

## Exactly what is measured

**As-of** each completed minute `t`:
- `C2 taker delta ratio = (aggressive buy base − aggressive sell base) / (aggressive buy base + aggressive sell base)` using `is_buyer_maker` semantics from verified aggregate trade processing. This is *executed taker flow*, **not** L2 OFI (limit additions/cancellations are absent).
- `delta_change = imbalance[t] − mean(imbalance[t−5 ... t−1])`, provided all 5 previous C2 minutes are present.
- `relative_taker_volume = (taker buy + taker sell)[t] / mean(previous 60 valid-minute volumes)`. Require at least 55 valid historical minutes; C2_UNKNOWN is never imputed as zero. Aggregate trade row intensity can also be monitored but does not represent individual orders.
- `Asia High/Low proxy`: the maximum/minimum of **C1 minute CLOSES** between 00:00 and 07:59 **UTC**, usable from 08:00 UTC after the last Asia minute has completed. C1 ZIP V1 contains closes only — the proxy is **not the session's candle wick high/low**. No level is constructed before the session is complete or from the future.
- Quiet guard: the last 5-minute close return must be within ±15 bps. It limits detection of already-large moves (but cannot independently prove no intraminute move occurred).

**Fixed candidate rules (not adapted to hindsight):**
- `flow_acceleration`: |imbalance| ≥ 0.4; acceleration in its own direction ≥ 0.10; current relative executed taker volume ≥ 1.5; direction follows the aggressive side.
- `asia_pressure`: after 08:00 UTC, close is within 12 bps on the approach side, or at most 3 bps beyond the **close-based** Asia extreme; |imbalance| ≥ .30 in the direction of the relevant level, acceleration ≥ 0 and relative volume ≥ 1.2. Direction follows the aggressive side.
- `asia_absorption_proxy`: same Asia proximity, aggressive |imbalance| ≥ .45, relative volume ≥ 1.8, and current minute's close-to-close move ≤ 2 bps in magnitude; test the **opposite** direction. A high taker volume and small close change is **not proof of passive book absorption** — it is only a proxy. Very few alerts mean **INSUFFICIENT EVIDENCE** even if observed accuracy happens to be high.

A 15-minute **cooldown per rule and UTC day**, chosen without seeing future returns, prevents repetitive alerts from being counted as independent decisions. Different rules may still overlap. Both raw and cooldown-filtered candidate counts are reported. Any configuration with fewer than 50 evaluable alerts or fewer than 10 alert days is explicitly marked `INSUFFICIENT_SIGNAL_COUNTS`, regardless of its apparent success fraction.

**Outcome definition, independent from selection:** for an alert made using minute `t`, features are assumed available at minute close plus **hypothetical 1.5s** (the original ZIP's pinned latency). The **non-executable entry proxy** is **next completed minute close `t+1`**, and later completed closes `t+2..t+1+h` are examined for the **first** ±15 or ±30 bps threshold crossing relative to that proxy, for horizons of 5/15/30 minutes. First hit in warned direction = correct; first opposite-direction hit = wrong-way; no hit = no-move. Wrong-way and no-move count as **false alarms**. Outcomes never cross UTC day or Development month boundary. Report counts, fractions, day coverage, and lead to **first qualifying close after the entry proxy** (not latency to true price-move onset). The current-minute close **is never claimed as an executable entry**. No spread, adverse-selection, impact or queue fill data exist.

**Negative controls / caveats:** the report includes a basic directional-taker-imbalance reference from quiet Development minutes. This baseline is *not* fully matched to the alerts by hour, volatility and intensity, so no causal lift or p-value is asserted. Same-month conditional results are dependent and may be strongly selection-biased. This release deliberately reports descriptive outcomes, with **no significance inference and no strategy ranking**. Before any edge claim require fresh Development months, block bootstrap / calibrated multiple-testing correction, true execution models and an independently approved Validation gate.

## BookDepth coverage and limitations

The C1+C2 minute ZIP contains **no C3 field**. All `book_depth` values therefore remain `null`/`UNKNOWN_NOT_INCLUDED`, not zero and not manufactured bid/ask imbalance. Frozen C3 files are daily, quality-controlled and sampled (not full L2 incremental depth). Historical C3 quality `WARN` or `QUARANTINE` days must be rejected or marked UNKNOWN before any later as-of join, and no nearest-future snapshot is allowed. Even C3 PASS cannot identify within-second order submissions/cancellations, spread, queue position or true OFI without additional L2 data. The optional `c2_microstructure` module must not be confused with book depth.

## Safety and provenance

- `CryptoResearchLab` source data, source GitHub, frozen metadata and ACL are **immutable and read-only**. This release operates on the previously provided, independently SHA-verified private Development ZIP for exploratory acceptance; no historical source-row reads, Drive writes or new grants performed here.
- No Validation 2025 or Final OOS 2026 data, observed private outcomes or original drive identifiers belong in public code, notebook outputs or GitHub CI.
- The existing source preparer remains operator-gated; this new analyst never mounts Drive. The multi-resolution C2 helper is a pure library callable on an **already authorized** staged input, not a source-access bypass.
- No UI redesign, new service, market-data download, API key, order execution or background agent.
