"""Causal, Development-only pre-move warnings and independent false-alarm audit.

Only verified C1-close / C2 taker-volume minute features are available here.
Asia levels are *CLOSE extrema*, not exchange/wick high/low. C2 is not L2 OFI.
All warnings use data available by the completed decision minute; burst labels
are built later from a next-completed-close proxy, never for signal selection.
No execution, fill, PnL, arbitrage, reliable latency, or edge is asserted.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from .airgap_bundle import BundleError, inspect_bundle, _month_bounds

ENGINE = "PREMOVE_CAUSAL_WARNING_V1"
RULES = ("flow_acceleration", "asia_pressure", "asia_absorption_proxy")
THRESHOLDS_BPS = (15, 30)
HORIZONS_MIN = (5, 15, 30)
COOLDOWN_MIN = 15
ASIA_UTC_START_HOUR = 0
ASIA_UTC_END_HOUR = 8
PRE_EVENT_MINUTES = 30
EPISODE_BPS = 30
EPISODE_LOOKBACK_MIN = 15
EPISODE_COOLDOWN_MIN = 45
MAX_EPISODES_IN_REPORT = 12
MIN_HISTORY = 60
MIN_EVALUABLE_ALERTS = 50
MIN_ALERT_DAYS = 10


def _canonical(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def make_premove_plan(packet: bytes) -> bytes:
    """Freeze exact rules before any warning outcomes are evaluated."""
    meta, _ = inspect_bundle(packet)
    plan = {
        "schema": ENGINE,
        "role": "DEVELOPMENT",
        "bundle_sha256": _sha(packet),
        "month": meta["month"],
        "frozen_universe_sha256": meta["universe_sha256"],
        "source_c1_sha256": meta["c1_sha256"],
        "source_c2_sha256": meta["c2_sha256"],
        "rules": list(RULES),
        "thresholds_bps": list(THRESHOLDS_BPS),
        "horizons_minutes": list(HORIZONS_MIN),
        "cooldown_minutes": COOLDOWN_MIN,
        "asia_window_utc": [ASIA_UTC_START_HOUR, ASIA_UTC_END_HOUR],
        "asia_reference": "closed-minute CLOSE extrema (not candle wick or order book)",
        "min_history_minutes": MIN_HISTORY,
        "minimum_evaluable_alerts_for_description": MIN_EVALUABLE_ALERTS,
        "minimum_active_days_for_description": MIN_ALERT_DAYS,
        "quiet_previous_5m_max_absolute_bps": 15,
        "flow_acceleration": {"imbalance_min": .4, "delta_imbalance_5m_min": .10,
                              "relative_taker_volume_min": 1.5},
        "asia_pressure": {"distance_bps_below_level_max": 12,
                            "distance_bps_beyond_level_max": 3,
                            "imbalance_min": .30, "delta_imbalance_5m_min": 0.,
                            "relative_taker_volume_min": 1.2},
        "asia_absorption_proxy": {"distance_bps_below_level_max": 12,
                                   "distance_bps_beyond_level_max": 3,
                                   "imbalance_min": .45,
                                   "relative_taker_volume_min": 1.8,
                                   "current_close_return_abs_max_bps": 2.},
        "event_definition": "first future close crossing signed threshold measured from next completed minute close, within same UTC day",
        "c3_book_depth": "UNKNOWN_NOT_INCLUDED_IN_MINUTE_BUNDLE",
        "interpretation": "DESCRIPTIVE_DEVELOPMENT_ONLY_NO_EXECUTABLE_PNL_NO_HFT_FILL",
    }
    return _canonical(plan)


def _past_average(array, valid, n):
    """Past-only mean over *preceding* n minutes. Missing is unknown, not zero."""
    import numpy as np
    a = np.asarray(array, dtype=np.float64)
    v = np.asarray(valid, dtype=bool)
    counts = np.r_[0, np.cumsum(v.astype(np.int64))]
    sums = np.r_[0., np.cumsum(np.where(v, a, 0.))]
    idx = np.arange(len(a), dtype=np.int64)
    lo = np.maximum(idx - n, 0)
    ct = counts[idx] - counts[lo]
    total = sums[idx] - sums[lo]
    means = np.divide(total, ct, out=np.full(len(a), np.nan), where=ct > 0)
    return means, ct


def _features(rows):
    import numpy as np
    n = len(rows)
    ts = np.fromiter((int(r["open_time_ms"]) for r in rows), np.int64, count=n)
    av = np.fromiter((int(r["available_at_ms"]) for r in rows), np.int64, count=n)
    px = np.fromiter((float(r["close"]) for r in rows), np.float64, count=n)
    present = np.fromiter((r["c2_status"] == "C2_PRESENT" for r in rows), bool, count=n)
    vol = np.fromiter((float(r["taker_buy_base"]) + float(r["taker_sell_base"])
                       if r["c2_status"] == "C2_PRESENT" else float("nan") for r in rows),
                      np.float64, count=n)
    imb = np.fromiter((float(r["taker_imbalance"]) if r["c2_status"] == "C2_PRESENT"
                       else float("nan") for r in rows), np.float64, count=n)
    trades = np.fromiter((int(r["c2_aggregate_rows"]) for r in rows), np.int64, count=n)
    mean_imb5, n_imb5 = _past_average(imb, present, 5)
    mean_vol60, n_vol60 = _past_average(vol, present, 60)
    mean_trades60, _ = _past_average(trades, present, 60)
    rel_vol = np.divide(vol, mean_vol60, out=np.full(n, np.nan), where=mean_vol60 > 0)
    delta_accel = imb - mean_imb5
    rel_trades = np.divide(trades, mean_trades60,
                           out=np.full(n, np.nan), where=mean_trades60 > 0)
    previous5 = np.full(n, np.nan)
    previous1 = np.full(n, np.nan)
    previous5[5:] = (px[5:] / px[:-5] - 1.) * 10000.
    previous1[1:] = (px[1:] / px[:-1] - 1.) * 10000.
    day = ts // 86_400_000
    minute_in_day = (ts % 86_400_000) // 60_000
    asia_hi = np.full(n, np.nan)
    asia_lo = np.full(n, np.nan)
    for d in np.unique(day):
        indices = np.flatnonzero(day == d)
        session = indices[minute_in_day[indices] < 480]
        if (len(session) != 480 or not np.array_equal(minute_in_day[session], np.arange(480))):
            continue  # Incomplete Asia session -> no fabricated level
        later = indices[minute_in_day[indices] >= 480]
        asia_hi[later] = np.max(px[session])
        asia_lo[later] = np.min(px[session])
    near_hi = np.isfinite(asia_hi) & (((px / asia_hi) - 1.) * 10000. >= -12) & (
        ((px / asia_hi) - 1.) * 10000. <= 3)
    near_lo = np.isfinite(asia_lo) & (((px / asia_lo) - 1.) * 10000. <= 12) & (
        ((px / asia_lo) - 1.) * 10000. >= -3)
    history = (np.arange(n) >= MIN_HISTORY) & present & (n_imb5 == 5) & (n_vol60 >= 55)
    quiet = np.isfinite(previous5) & (np.abs(previous5) <= 15)
    usable = history & quiet & (rel_vol > 0)
    return dict(ts=ts, available=av, px=px, day=day, hour=minute_in_day // 60,
                minute_in_day=minute_in_day, vol=vol, imb=imb, present=present,
                rel_vol=rel_vol, rel_trades=rel_trades, accel=delta_accel,
                past_5m_bps=previous5, past_1m_bps=previous1,
                asia_hi=asia_hi, asia_lo=asia_lo, near_hi=near_hi, near_lo=near_lo,
                usable=usable)


def _rule_directions(features):
    """Signals depend ONLY on observed completed-minute features."""
    import numpy as np
    f = features
    a = np.asarray(f["imb"], dtype=np.float64)
    acceleration = f["accel"]
    high = f["near_hi"]
    low = f["near_lo"]
    v = f["rel_vol"]
    out = {}
    follow = (f["usable"] & (np.abs(a) >= .4) &
              (np.sign(a) * acceleration >= .10) & (v >= 1.5))
    out["flow_acceleration"] = np.where(follow, np.sign(a), 0).astype(np.int8)
    pressure_up = f["usable"] & high & (a >= .30) & (acceleration >= 0) & (v >= 1.2)
    pressure_down = f["usable"] & low & (a <= -.30) & (acceleration <= 0) & (v >= 1.2)
    out["asia_pressure"] = np.where(pressure_up & ~pressure_down, 1,
                                       np.where(pressure_down & ~pressure_up, -1, 0)).astype(np.int8)
    absorb_up = (f["usable"] & high & (a >= .45) & (v >= 1.8) &
                 (np.abs(f["past_1m_bps"]) <= 2))
    absorb_down = (f["usable"] & low & (a <= -.45) & (v >= 1.8) &
                   (np.abs(f["past_1m_bps"]) <= 2))
    out["asia_absorption_proxy"] = np.where(absorb_up & ~absorb_down, -1,
                                                np.where(absorb_down & ~absorb_up, 1, 0)).astype(np.int8)
    return out


def _cooldown_positions(directions, days):
    """One alert per 15m per rule/day; independent of all future outcomes."""
    import numpy as np
    candidates = np.flatnonzero(directions != 0)
    accepted = []
    last = -COOLDOWN_MIN - 1
    last_day = None
    for ix in candidates:
        i = int(ix)
        if last_day != int(days[i]):
            last = -COOLDOWN_MIN - 1
            last_day = int(days[i])
        if i - last > COOLDOWN_MIN:
            accepted.append(i)
            last = i
    return np.asarray(accepted, dtype=np.int64)


def _first_hit(f, i, sign, threshold, horizon):
    """No instant/same-close entry; first hit, right/wrong/none, after next CLOSE."""
    ts, available, px, day = f["ts"], f["available"], f["px"], f["day"]
    entry = i + 1
    last = entry + horizon
    if (not sign or last >= len(ts) or day[i] != day[last] or
            available[i] >= ts[entry] + 60_000):
        return None
    anchor = float(px[entry])
    for j in range(entry + 1, last + 1):
        signed_bps = sign * (float(px[j]) / anchor - 1.) * 10000.
        if signed_bps >= threshold:
            return ("correct", j - entry)
        if signed_bps <= -threshold:
            return ("wrong_way", j - entry)
    return ("no_large_move", None)


def _baseline(f, threshold, horizon, asia_only):
    """All quiet eligible minutes, direction = contemporaneous taker imbalance.

    This is a descriptive reference, not randomized hour-matched causal inference.
    """
    import numpy as np
    indices = np.flatnonzero(f["usable"] & np.isfinite(f["imb"]) & (f["imb"] != 0) &
                                 ((f["hour"] >= 8) if asia_only else True))
    cnt = 0
    success = 0
    for k in indices:
        i = int(k)
        result = _first_hit(f, i, 1 if f["imb"][i] > 0 else -1, threshold, horizon)
        if result is None:
            continue
        cnt += 1
        success += result[0] == "correct"
    return {"eligible_minutes": cnt, "directional_hit_fraction": success / cnt if cnt else None,
            "warning": "not alert-frequency or day/time matched; do not treat as causal lift"}


def _event_gallery(f, alerts):
    """EX-POST examples with only pre-anchor feature windows; not signal proof.

    The reference anchor is 15min BEFORE a 15min *trailing* return first
    exceeds 30bps. Onset cannot be identified from 1m CLOSE alone.
    """
    import numpy as np
    n = len(f["px"])
    last_end = -EPISODE_COOLDOWN_MIN - 1
    episodes = []
    coverage = {name:{'direction_matched_proxy_episodes':0,
                      'proxy_episodes_without_direction_matched_warning':0,
                      'pre_anchor_leads_minutes':[]} for name in alerts}
    for end in range(EPISODE_LOOKBACK_MIN + PRE_EVENT_MINUTES + MIN_HISTORY, n):
        anchor = end - EPISODE_LOOKBACK_MIN
        if f["day"][end] != f["day"][anchor] or end - last_end <= EPISODE_COOLDOWN_MIN:
            continue
        signed = (f["px"][end] / f["px"][anchor] - 1.) * 10000.
        if abs(signed) < EPISODE_BPS:
            continue
        last_end = end
        direction = 1 if signed > 0 else -1
        preceding = np.arange(anchor - PRE_EVENT_MINUTES, anchor)
        aligned = []
        for k in preceding:
            i = int(k)
            if f["day"][i] != f["day"][anchor]:
                aligned = []
                break
            aligned.append({"relative_minute": i-anchor,
                            "taker_imbalance": (float(f["imb"][i]) if np.isfinite(f["imb"][i]) else None),
                            "relative_taker_volume": (float(f["rel_vol"][i]) if np.isfinite(f["rel_vol"][i]) else None),
                            "delta_imbalance_vs_previous5": (float(f["accel"][i]) if np.isfinite(f["accel"][i]) else None),
                            "close_to_asia_high_bps": (float((f["px"][i] / f["asia_hi"][i]-1)*10000)
                                                         if np.isfinite(f["asia_hi"][i]) else None),
                            "close_to_asia_low_bps": (float((f["px"][i] / f["asia_lo"][i]-1)*10000)
                                                        if np.isfinite(f["asia_lo"][i]) else None),
                            "book_depth": None})
        if not aligned:
            continue
        before = {}
        for name, (indices, signs) in alerts.items():
            hit = [(int(k), int(signs[k])) for k in indices if anchor-15 <= k < anchor and signs[k] == direction]
            before[name] = {"matched_alerts_in_preceding_15m": len(hit),
                            "earliest_lead_minutes_to_proxy_anchor": anchor-hit[0][0] if hit else None}
        following = []
        for i in range(anchor, min(anchor+31, n)):
            if f['day'][i] != f['day'][anchor]:
                break
            following.append({"relative_minute":i-anchor,
                              "close_return_from_proxy_start_bps": float((f['px'][i]/f['px'][anchor]-1)*10000),
                              "taker_imbalance":float(f['imb'][i]) if np.isfinite(f['imb'][i]) else None,
                              "relative_taker_volume":float(f['rel_vol'][i]) if np.isfinite(f['rel_vol'][i]) else None,
                              "delta_imbalance_vs_previous5":float(f['accel'][i]) if np.isfinite(f['accel'][i]) else None,
                              "close_to_asia_high_bps":float((f['px'][i]/f['asia_hi'][i]-1)*10000)
                                                    if np.isfinite(f['asia_hi'][i]) else None,
                              "close_to_asia_low_bps":float((f['px'][i]/f['asia_lo'][i]-1)*10000)
                                                   if np.isfinite(f['asia_lo'][i]) else None,
                              "book_depth":None})
        if len(following) < 16:
            continue
        for name, stats in coverage.items():
            lead = before[name]['earliest_lead_minutes_to_proxy_anchor']
            if lead is None:
                stats['proxy_episodes_without_direction_matched_warning'] += 1
            else:
                stats['direction_matched_proxy_episodes'] += 1
                stats['pre_anchor_leads_minutes'].append(lead)
        episodes.append({"proxy_start_utc": datetime.fromtimestamp(f["ts"][anchor]/1000,timezone.utc).isoformat(),
                         "proxy_detection_end_utc": datetime.fromtimestamp(f["ts"][end]/1000,timezone.utc).isoformat(),
                         "15m_signed_close_return_bps": round(float(signed), 3),
                         "direction": "UP" if direction==1 else "DOWN",
                         "warning": "EX_POST rolling-return reference; not true explosive move onset",
                         "pre_reference_30min": aligned,
                         "event_and_post_30min": following,
                         "early_alerts": before})
    summaries = {}
    for name, stats in coverage.items():
        leads = stats.pop('pre_anchor_leads_minutes')
        summaries[name] = {**stats,
                           'direction_matched_proxy_episode_fraction':
                               stats['direction_matched_proxy_episodes']/len(episodes) if episodes else None,
                           'proxy_episodes_missed':stats['proxy_episodes_without_direction_matched_warning'],
                           'median_warning_minutes_before_proxy_anchor':float(np.median(leads)) if leads else None}
    return {"total_proxy_episodes": len(episodes),
            "event_proxy_warning_coverage": summaries,
            "gallery": episodes[:MAX_EPISODES_IN_REPORT],
            "sample_selection": "earliest qualifying 15m rolling return >=30bps per 45m cooldown; not top-profit selection",
            "warning": "EX-POST 15-minute trailing-close anchor cannot identify true move onset; coverage is descriptive and rules were studied on this same month"}


def run_premove_study(packet: bytes, registered_plan: bytes) -> dict:
    """Evaluate all minutes first, then future labels, preserving false alarms."""
    import numpy as np
    if type(registered_plan) is not bytes or registered_plan != make_premove_plan(packet):
        raise BundleError("altered/missing warning research plan")
    meta, rows = inspect_bundle(packet)
    month_lo, month_hi = _month_bounds(meta['month'])
    expected_count = (month_hi-month_lo)//60_000-1
    if (int(rows[0]['open_time_ms']) != month_lo or
            int(rows[-1]['open_time_ms']) != month_hi-120_000 or
            len(rows) != expected_count):
        raise BundleError('complete Development month except final unavailable minute required')
    f = _features(rows)
    signals = _rule_directions(f)
    alerts = {k: (_cooldown_positions(v,f["day"]),v) for k,v in signals.items()}
    by_rule = []
    baseline_cache = {}
    for name in RULES:
        positions, direction = alerts[name]
        for threshold in THRESHOLDS_BPS:
            for horizon in HORIZONS_MIN:
                evaluated = correct = wrong = no_move = 0
                lead = []
                successes_by_day = {}
                total_by_day = {}
                examples = []
                for i in positions:
                    k = int(i)
                    hit = _first_hit(f,k,int(direction[k]),threshold,horizon)
                    if hit is None:
                        continue
                    evaluated += 1
                    key = datetime.fromtimestamp(int(f["ts"][k])/1000,timezone.utc).date().isoformat()
                    total_by_day[key] = total_by_day.get(key,0)+1
                    if hit[0] == "correct":
                        correct += 1; lead.append(int(hit[1]));successes_by_day[key] = successes_by_day.get(key,0)+1
                    elif hit[0] == "wrong_way":
                        wrong += 1
                    else:
                        no_move += 1
                    if len(examples) < 6:
                        examples.append({"decision_minute_utc":datetime.fromtimestamp(f["ts"][k]/1000, timezone.utc).isoformat(),
                                         "direction":int(direction[k]),"outcome":hit[0],
                                         "first_threshold_cross_minutes_after_entry":hit[1],
                                         "imbalance":float(f["imb"][k]),
                                         "volume_ratio_to_previous60":float(f["rel_vol"][k]),
                                         "delta_imbalance_change_vs_previous5":float(f["accel"][k])})
                asia_only = name.startswith("asia_")
                basel = (threshold,horizon,asia_only)
                if basel not in baseline_cache:
                    baseline_cache[basel] = _baseline(f,threshold,horizon,asia_only)
                by_rule.append({"rule":name,"threshold_bps":threshold,"horizon_minutes":horizon,
                                "raw_candidate_minutes":int(np.count_nonzero(direction)),
                                "alerts_after_cooldown":len(positions),
                                "evaluated_alerts":evaluated,
                                "correct_direction_hit":correct,"wrong_direction_first_hit":wrong,
                                "no_threshold_hit":no_move,
                                "false_alarms_including_wrong_direction":wrong+no_move,
                                "precision":correct/evaluated if evaluated else None,
                                "false_alarm_fraction":(wrong+no_move)/evaluated if evaluated else None,
                                "mean_lead_minutes_after_entry":float(np.mean(lead)) if lead else None,
                                "median_lead_minutes_after_entry":float(np.median(lead)) if lead else None,
                                "successful_days":len(successes_by_day),
                                "days_with_alerts":len(total_by_day),
                                "daily_evaluated_alerts":total_by_day,
                                "daily_successful_alerts":successes_by_day,
                                "naive_imbalance_reference":baseline_cache[basel],
                                "first_six_alerts":examples,
                                "status":("NO_EVALUABLE_ALERTS" if not evaluated else
                                          "INSUFFICIENT_SIGNAL_COUNTS" if evaluated < MIN_EVALUABLE_ALERTS or len(total_by_day) < MIN_ALERT_DAYS else
                                          "DESCRIPTIVE_NO_SIGNIFICANCE_INFERENCE")})
    gallery = _event_gallery(f, alerts)
    return {"schema":ENGINE,"month":meta["month"],"scope":"DEVELOPMENT_ONLY",
            "bundle_sha256":_sha(packet),"plan_sha256":_sha(registered_plan),
            "feature_minutes":len(rows),"all_minute_decisions":len(rows),
            "c2_present_minutes":int(np.count_nonzero(f["present"])),
            "c2_unknown_minutes":int(np.count_nonzero(~f["present"])),
            "asia_close_proxy_definition":"00:00-07:59 UTC close max/min, active from 08:00; no OHLC/wick in ZIP",
            "volume_intensity_definition":"taker_buy_base+taker_sell_base / PREVIOUS 60 completed-minute mean; C2 unknown not imputed",
            "delta_change_definition":"current normalized taker imbalance minus mean of PREVIOUS 5 completed minutes",
            "latency":"source minute end + hypothetical 1500ms; entry NEXT completed close, NOT an exchange fill",
            "book_depth":"UNKNOWN_NOT_INCLUDED; no C3/L2 orderflow, cancel or queue evidence",
            "selection":"three predeclared warning mechanisms, 2 fixed move thresholds, 3 horizons; no outcome-driven alert tuning",
            "total_variant_evaluations":len(by_rule),
            "evaluations":by_rule,
            "ex_post_burst_gallery":gallery,
            "inference":"DESCRIPTIVE_DEVELOPMENT_ONLY_NOT_INDEPENDENT_EDGE",
            "warnings":["Warning alerts and labels are derived from the same month; no independent Validation/OOS.",
                        "Cooldown avoids same-rule alert storms, but different rules can overlap.",
                        "Naive directional reference is not an inferential control; effects must survive fresh months.",
                        "An alert hit is a next-minute-close proxy without spread, market impact or fill data.",
                        "Episode alignment is ex-post explanatory; never use it to choose alert conditions on this month.",
                        "Asia high/low refer to CLOSE extrema, not true high/low or order-book liquidity."]}
