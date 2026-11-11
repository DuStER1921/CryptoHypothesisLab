"""Single, fixed, executable-price-PROXY research candidate; no fitted parameters.

C2 emerging-flow momentum, CONDITIONAL on previous fully observed C1 1m direction.
All alerts use completed 5s and earlier observations; trade prices are NOT quotes.
Not a market order simulation, exchange-fill validation, or profitability proof.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import math
import random
import statistics

from .micro_bundle import inspect_micro_bundle, MicroBundleError
from .premove_v3 import _bar_context
from .premove_v31 import _range_60s, _flow_features

SCHEMA='HYPOLAB_FROZEN_TRADE_CANDIDATE_V1'
DIAGNOSTIC_MONTH='2024-07'
TEST_MONTH='2024-08'
# All values frozen BEFORE looking at any August 2024 market rows.
LOOKBACK_SECONDS=60
EMERGING_LO_BPS=8.0
EMERGING_HI_BPS=20.0
MIN_ABS_IMBALANCE=0.65
MIN_REL_QUOTE=2.0
MIN_REL_COUNT=1.5
MIN_SIGNED_IMBALANCE_ACCEL=0.10
MIN_SIGNED_5S_PRICE_RESPONSE_BPS=1.0
ENTRY_LAG_BINS=2  # last trade of second subsequent fully completed 5s bar
TARGET_BPS=36.0
STOP_BPS=18.0
MAX_HOLD_SECONDS=900
POST_EXIT_COOLDOWN_SECONDS=300
FEE_BPS_PER_SIDE=4.0         # HYPOTHETICAL, not a claimed Binance rate
SLIP_BPS_PER_SIDE=2.0        # includes hypothetical spread/adverse fill
STRESS_SLIP_BPS_PER_SIDE=(4.0, 6.0)
BOOTSTRAP_ROUNDS=1000
BOOTSTRAP_SEED=20261111
MIN_TRADES=120
MIN_TRADE_DAYS=15
MAX_DRAWDOWN_BPS=400.0


def _canonical(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',', ':'),allow_nan=False)+'\n').encode('utf8')


def protocol_bytes():
    return _canonical({
        'schema':SCHEMA,'months':{'already_inspected_development':DIAGNOSTIC_MONTH,
                                  'one_shot_future_development':TEST_MONTH},
        'signal':{'past_5s_lookback_seconds':LOOKBACK_SECONDS,
                  'inclusive_trailing_trade_range_bps':[EMERGING_LO_BPS,EMERGING_HI_BPS],
                  'min_abs_imbalance':MIN_ABS_IMBALANCE,'min_rel_quote':MIN_REL_QUOTE,
                  'min_rel_count':MIN_REL_COUNT,
                  'min_signed_imbalance_acceleration':MIN_SIGNED_IMBALANCE_ACCEL,
                  'min_signed_first_last_trade_price_bps':MIN_SIGNED_5S_PRICE_RESPONSE_BPS,
                  'previous_completed_C1_minute_direction_must_agree':True},
        'execution_proxy':{'entry_lag_complete_5s_bars':ENTRY_LAG_BINS,
            'first_outcome_bar':'first bar AFTER entry bar','target_bps':TARGET_BPS,
            'stop_bps':STOP_BPS,'max_hold_seconds':MAX_HOLD_SECONDS,
            'stop_first_on_dual_touch':True,'stop_gap_fills_worse_than_bar_open':True,
            'no_overnight_and_no_gap_fill':True,'cooldown_after_exit_s':POST_EXIT_COOLDOWN_SECONDS,
            'price_observation':'executed-trade close and high/low, NOT exchange executable quotes'},
        'cost_assumptions':{'fee_bps_per_side':FEE_BPS_PER_SIDE,
            'base_slippage_bps_per_side':SLIP_BPS_PER_SIDE,
            'stress_slippage_bps_per_side':list(STRESS_SLIP_BPS_PER_SIDE),
            'funding_borrow_and_real_fill_costs':'UNKNOWN - not modeled'},
        'controls':'same entry times and price, opposite side; frozen, not separately tradeable',
        'statistical_gate':{'min_completed_trades':MIN_TRADES,'min_active_days':MIN_TRADE_DAYS,
            'daily_bootstrap_rounds':BOOTSTRAP_ROUNDS,'seed':BOOTSTRAP_SEED,
            'lower_95pct_day_cluster_mean_net_bps_must_exceed_zero':True,
            'all_stressed_slippage_means_positive':True,
            'must_exceed_opposite_side_mean_bps':True,
            'later_month_days_22_onward_mean_positive':True,
            'max_arithmetic_drawdown_bps':MAX_DRAWDOWN_BPS,
            'zero_unknown_attempts_required':True},
        'status':'HYPOTHESIS_NOT_PROVEN; one month cannot establish real executed alpha',
        'forbidden':'parameter fitting, future rows in signal, new source reads without owner grant, protected Validation/OOS'})


def freeze_token_sha256():
    """One owner-stored token for August; changes if any algorithm/decoder changes."""
    root=Path(__file__).resolve().parents[2]
    code=[Path(__file__),
          Path(__file__).with_name('micro_bundle.py'),
          Path(__file__).with_name('premove_v3.py'),
          Path(__file__).with_name('premove_v31.py'),
          root/'colab'/'08_trade_candidate_offline.ipynb']
    digests={str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in code}
    return sha256(_canonical({'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
                              'source_code_hashes':digests})).hexdigest()


def make_strategy_plan(path:Path):
    meta,_,_=inspect_micro_bundle(path,collect_5s=False)
    month=meta['month']
    if month not in (DIAGNOSTIC_MONTH,TEST_MONTH):
        raise MicroBundleError('month is not in the frozen strategy V1 calendar')
    from .micro_warning import _sha
    return _canonical({'schema':SCHEMA,'role':'DEVELOPMENT',
      'month':month,'kind':'REUSED_EXPLORATORY' if month==DIAGNOSTIC_MONTH else 'ONE_SHOT_DEVELOPMENT_TEST',
      'bundle_sha256':_sha(path),'source_c1_sha256':meta['c1_sha256'],
      'source_c2_sha256':meta['c2_sha256'],'source_producer_code_commit':meta['producer_code_commit'],
      'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
      'freeze_token_sha256':freeze_token_sha256(),
      'assumed_latency_ms':meta['assumed_latency_ms'],
      'NO_CLAIM':'Observed trade prices only; fees/slippage assumed; not proven profit'})


def _signal(rows,i,rng,context):
    """Past-only predicate, returns (side, metrics) or None.

    `rows[i]` is completed before this is called. No future outcome is used.
    """
    if rng is None or rng<EMERGING_LO_BPS or rng>EMERGING_HI_BPS:
        return None
    t=int(rows[i]['start_ms'])
    bar=context.get(t-t%60000)
    if bar is None or int(bar['available_at_ms'])>int(rows[i]['available_at_ms']):
        return None
    f=_flow_features(rows,i)
    if f is None:return None
    side=1 if f['imbalance']>0 else -1 if f['imbalance']<0 else 0
    if side==0 or bar['bar_direction']!=side:return None
    if (abs(f['imbalance'])<MIN_ABS_IMBALANCE or
        f['rel_quote']<MIN_REL_QUOTE or f['rel_count']<MIN_REL_COUNT or
        side*f['imbalance_acceleration']<MIN_SIGNED_IMBALANCE_ACCEL or
        side*float(rows[i]['first_to_last_trade_bps'])<MIN_SIGNED_5S_PRICE_RESPONSE_BPS):
        return None
    return side, {'range_bps':round(rng,4),'imbalance':round(f['imbalance'],4),
          'rel_quote':round(f['rel_quote'],4),'rel_count':round(f['rel_count'],4),
          'c1_bar_direction':bar['bar_direction']}


def _simulate_trade(rows,index,side,slip_bps):
    """Pessimistic OHLC stop-first backtest with immutable model assumptions.

    A known signal at start+5000+latency is filled no earlier than a later
    bar's last-trade price. This price is a NON-EXECUTABLE proxy. Any missing
    bar or day rollover -> UNKNOWN, never an invented zero/good fill.
    """
    start=int(rows[index]['start_ms']);day=start//86400000
    entry_index=index+ENTRY_LAG_BINS
    horizon_bins=MAX_HOLD_SECONDS//5
    end_index=entry_index+horizon_bins
    if end_index>=len(rows) or (start+(ENTRY_LAG_BINS+horizon_bins)*5000)//86400000!=day:
        return {'status':'SKIP_DAY_BOUNDARY'},None
    availability=int(rows[index]['available_at_ms'])
    entry_ts=start+(ENTRY_LAG_BINS+1)*5000
    if availability>=entry_ts:
        return {'status':'UNKNOWN_SIGNAL_TOO_LATE'},None
    # Verify all intermediate 5s bins, including the entry bin and entire hold.
    for j in range(index+1,end_index+1):
        if int(rows[j]['start_ms'])!=start+(j-index)*5000:
            return {'status':'UNKNOWN_GAP'},None
    entry=float(rows[entry_index]['last_trade_price'])
    if not math.isfinite(entry) or entry<=0:
        return {'status':'UNKNOWN_BAD_PRICE'},None
    target=entry*(1+side*TARGET_BPS/10000)
    stop=entry*(1-side*STOP_BPS/10000)
    reason='TIME_EXIT';exit_price=None;exit_index=None
    for j in range(entry_index+1,end_index+1):
        b=rows[j]
        hi=float(b['max_trade_price']);lo=float(b['min_trade_price'])
        op=float(b['first_trade_price']);last=float(b['last_trade_price'])
        if not all(math.isfinite(p) and p>0 for p in (hi,lo,op,last)):
            return {'status':'UNKNOWN_BAD_PRICE'},None
        hit_stop=(lo<=stop if side==1 else hi>=stop)
        hit_target=(hi>=target if side==1 else lo<=target)
        # Both touched in a single 5s bin: pessimistically assume stop first.
        if hit_stop:
            exit_price=min(stop,op) if side==1 else max(stop,op)
            reason='STOP' if not hit_target else 'DUAL_TOUCH_STOP_FIRST'
        elif hit_target:
            exit_price=target  # no price improvement assumed on target
            reason='TARGET'
        elif j==end_index:
            exit_price=last
            reason='TIME_EXIT'
        if exit_price is not None:
            exit_index=j;break
    gross=side*(exit_price/entry-1)*10000
    net=gross-2*(FEE_BPS_PER_SIDE+slip_bps)
    return {'status':'COMPLETED','signal_ms':start,
      'entry_ms':int(rows[entry_index]['start_ms'])+5000,
      'exit_ms':int(rows[exit_index]['start_ms'])+5000,
      'exit_index':exit_index,'side':side,
      'reason':reason,'gross_bps':round(gross,6),
      'net_bps':round(net,6),'entry_price_proxy':round(entry,6),
      'exit_price_proxy':round(exit_price,6)},exit_index


def _mean(xs):return sum(xs)/len(xs) if xs else None


def _drawdown(pnls):
    peak=0.;equity=0.;maxdd=0.
    for p in pnls:
        equity+=p;peak=max(peak,equity);maxdd=max(maxdd,peak-equity)
    return maxdd


def _bootstrap_daily_mean(trades):
    """Descriptive day-cluster bootstrap weighted by trades, not independence."""
    days=defaultdict(list)
    for t in trades:
        days[t['entry_ms']//86400000].append(t['net_bps'])
    if len(days)<5:return None
    keys=sorted(days)
    rng=random.Random(BOOTSTRAP_SEED)
    values=[]
    for _ in range(BOOTSTRAP_ROUNDS):
        chosen=[keys[rng.randrange(len(keys))] for _ in keys]
        pnls=[p for day in chosen for p in days[day]]
        if pnls:values.append(sum(pnls)/len(pnls))
    if not values:return None
    values.sort()
    return [round(values[int(.025*(len(values)-1))],6),
            round(values[int(.975*(len(values)-1))],6)]


def _summary(trades):
    pnls=[t['net_bps'] for t in trades]
    rs=defaultdict(int)
    for t in trades:rs[t['reason']]+=1
    return {'trades':len(trades),'active_utc_days':len({t['entry_ms']//86400000 for t in trades}),
       'net_mean_bps':round(_mean(pnls),6) if pnls else None,
       'net_total_unit_bps':round(sum(pnls),6),
       'median_net_bps':round(statistics.median(pnls),6) if pnls else None,
       'net_wins':sum(p>0 for p in pnls),'net_losses_or_zero':sum(p<=0 for p in pnls),
       'max_arithmetic_drawdown_bps':round(_drawdown(pnls),6),
       'day_cluster_bootstrap_95pct_mean_net_bps':_bootstrap_daily_mean(trades),
       'exit_reasons':dict(rs)}


def run_strategy_v1(path:Path, plan:bytes):
    if type(plan) is not bytes or plan!=make_strategy_plan(path):
        raise MicroBundleError('canonical frozen strategy plan required')
    meta,c1,rows=inspect_micro_bundle(path,collect_5s=True)
    if len(rows)<500:raise MicroBundleError('too few 5s bins')
    ranges=_range_60s(rows)
    ctx=_bar_context(c1)
    all_signal_count=0;skips=defaultdict(int)
    primary=[];reverse=[];next_time=-1
    for i in range(len(rows)):
        sig=_signal(rows,i,ranges[i],ctx)
        if not sig:continue
        all_signal_count+=1
        ts=int(rows[i]['start_ms'])
        if ts<next_time:
            skips['OPEN_TRADE_OR_COOLDOWN']+=1
            continue
        side,metrics=sig
        trade,ix=_simulate_trade(rows,i,side,SLIP_BPS_PER_SIDE)
        if trade['status']!='COMPLETED':
            skips[trade['status']]+=1
            if trade['status'].startswith('UNKNOWN'):
                # Fail closed on source gaps: no new entries that UTC day.
                next_time=(ts//86400000+1)*86400000
            continue
        alt,alt_ix=_simulate_trade(rows,i,-side,SLIP_BPS_PER_SIDE)
        if alt['status']!='COMPLETED':raise MicroBundleError('same-input opposite control has inconsistent evaluability')
        trade['signature']=metrics
        primary.append(trade);reverse.append(alt)
        next_time=int(rows[ix]['start_ms'])+5000+POST_EXIT_COOLDOWN_SECONDS*1000
    p=_summary(primary);b=_summary(reverse)
    stressed={}
    for slip in STRESS_SLIP_BPS_PER_SIDE:
        adjusted=[{**t,'net_bps':round(t['net_bps']-2*(slip-SLIP_BPS_PER_SIDE),6)} for t in primary]
        stressed[str(slip)]={'assumed_slip_bps_per_side':slip,
                            'mean_net_bps':round(_mean([t['net_bps'] for t in adjusted]),6) if adjusted else None}
    later=[t['net_bps'] for t in primary if datetime.fromtimestamp(t['entry_ms']/1000,timezone.utc).day>=22]
    lower=(p['day_cluster_bootstrap_95pct_mean_net_bps'] or [None])[0]
    checks={
        'enough_trades':p['trades']>=MIN_TRADES,
        'enough_days':p['active_utc_days']>=MIN_TRADE_DAYS,
        'mean_net_positive':p['net_mean_bps'] is not None and p['net_mean_bps']>0,
        'cluster_lower_positive':lower is not None and lower>0,
        'all_stress_positive':bool(stressed) and all(x['mean_net_bps'] is not None and x['mean_net_bps']>0 for x in stressed.values()),
        'beats_opposite_side_same_entries':p['net_mean_bps'] is not None and b['net_mean_bps'] is not None and p['net_mean_bps']>b['net_mean_bps'],
        'later_days_positive':bool(later) and sum(later)/len(later)>0,
        'max_drawdown_within_budget':p['max_arithmetic_drawdown_bps']<=MAX_DRAWDOWN_BPS,
        'no_unknown_attempts':not any(k.startswith('UNKNOWN') and n>0 for k,n in skips.items()),
    }
    # Even if every artificial proxy gate passes, executable alpha unproven.
    model_status=('PASSES_PREDECLARED_MODEL_GATES_NOT_REAL_EXECUTION'
                  if all(checks.values()) else 'FAILS_PREDECLARED_MODEL_GATES')
    from .micro_warning import _sha
    return {'schema':SCHEMA,'role':'DEVELOPMENT','month':meta['month'],
      'bundle_sha256':_sha(path),'plan_sha256':sha256(plan).hexdigest(),
      'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
      'freeze_token_sha256':freeze_token_sha256(),
      'source_counts':meta['counts'],'signal_count_before_execution_constraints':all_signal_count,
      'skips':dict(skips),'primary':p,'opposite_side_control':b,
      'stress':stressed,'later_days_ge_22_trade_count':len(later),
      'later_days_ge_22_mean_net_bps':round(_mean(later),6) if later else None,
      'gates':checks,'model_status':model_status,
      'trades':primary,'opposite_control_trades':reverse,
      'limitations':['BID/ASK, real fill, queue, latency and impact unavailable; entry and exit are trade-price proxies',
         'fees and slippage explicitly assumed, not exchange confirmed; funding/borrow costs omitted',
         'July has been repeatedly inspected; August is new Development, not protected Validation/OOS',
         'even model gate success is NOT proven real-money profitability',
         'retrospective price high/low within 5s has unknown tick ordering: dual touches use STOP first',
         'all incomplete paths counted as UNKNOWN, no profit imputed']}
