"""Frozen failed-auction C1/C2 research strategy: offline, exploratory only.

NO source access. Entry/exit first executed-trade price is a non-executable proxy.
A real-profit claim requires subsequent independent source and execution evidence.
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
from .premove_v31 import _flow_features
from .micro_warning import _sha

SCHEMA = 'HYPOLAB_FAILED_AUCTION_V2_FROZEN'
DIAGNOSTIC_MONTH = '2024-07'
ONE_SHOT_DEVELOPMENT_MONTH = '2024-08'
MINUTE_MS = 60_000
DAY_MS = 86_400_000
FIVE_MS = 5_000

# A single economic mechanism and SINGLE fixed rule set: a sweep of previously
# known one-hour extrema, aggressive flow exhaustion, then reclaim and flip.
PRIOR_C1_MINUTES = 60
SWEEP_MIN_BPS = 2.0
CLOSE_BACK_MIN_BPS = 1.0
CLOSE_BACK_MAX_BPS = 10.0
SWEEP_MIN_ABS_IMB = .65
SWEEP_MIN_REL_QUOTE = 1.5
CONFIRM_MIN_ABS_OPPOSITE_IMB = .30
CONFIRM_MIN_REVERSE_PRICE_BPS = 1.0
ENTRY_DELAY_BINS = 3  # Market intent occurs when signal available; entry proxy at t+15s
STOP_BPS = 30.0
TARGET_BPS = 60.0
MAX_HOLD_BINS = 360  # 30m
COOLDOWN_MS = 900_000
FEE_BPS_PER_SIDE = 5.0  # reference model, NOT account-specific proof
BASE_SLIP_BPS_PER_SIDE = 3.0
STRESS_SLIP_BPS_PER_SIDE = (5.0, 7.0)
MIN_TRADES = 30
MIN_DAYS = 10
MIN_MEAN_ADVANTAGE_OVER_REVERSE_BPS = 5.0
MAX_UNIT_DRAWDOWN_BPS = 1200.0
BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 20261112


def _canonical(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',', ':'),allow_nan=False)+'\n').encode('utf-8')


def protocol_bytes():
    return _canonical({
        'schema': SCHEMA, 'source':'verified owner-private 5s C2+1m C1 MICRO ZIP only',
        'development_july':'repeatedly seen exploratory',
        'development_august':'one-shot owner-authorized future source read only',
        'level':f'rolling {PRIOR_C1_MINUTES} fully closed contiguous 1m candles, known availability',
        'sweep':{'prior_5s_bins':2,'cross_level_bps':SWEEP_MIN_BPS,
                 'taker_imbalance':SWEEP_MIN_ABS_IMB,
                 'relative_quote_12_prior_5s':SWEEP_MIN_REL_QUOTE},
        'confirmation':{'price_back_inside_bps':[CLOSE_BACK_MIN_BPS,CLOSE_BACK_MAX_BPS],
                        'opposing_taker_imbalance':CONFIRM_MIN_ABS_OPPOSITE_IMB,
                        'same_5s_reverse_price_bps':CONFIRM_MIN_REVERSE_PRICE_BPS},
        'entry':{'delay_5s_bins':ENTRY_DELAY_BINS,
                 'proxy':'FIRST executed trade in third subsequent 5s bucket, not a fill',
                 'must_have_known_signal_before_entry':True},
        'exit':{'stop_bps':STOP_BPS,'target_bps':TARGET_BPS,
                'max_hold_5s_bins':MAX_HOLD_BINS,
                'same_bin_both_hit':'STOP FIRST','gap':'UNKNOWN at time encountered',
                'after_exit_cooldown_ms':COOLDOWN_MS,'same_day_only':True},
        'cost':{'fee_bps_each_side':FEE_BPS_PER_SIDE,
                 'assumed_spread_slippage_each_side':BASE_SLIP_BPS_PER_SIDE,
                 'stress_slippage_each_side':list(STRESS_SLIP_BPS_PER_SIDE)},
        'control':'opposite direction at the identical case entries; NOT independent executable schedule',
        'fixed_model_gates':{'min_trades':MIN_TRADES,'min_utc_days':MIN_DAYS,
                'mean_net_positive':True,'bootstrap_day_cluster_95pct_lower_positive':True,
                'stressed_means_positive':True,
                'primary_vs_opposite_net_bps_at_least':MIN_MEAN_ADVANTAGE_OVER_REVERSE_BPS,
                'later_days_22_onward_positive':True,
                'max_unit_drawdown_bps':MAX_UNIT_DRAWDOWN_BPS,'zero_unknown_attempts':True},
        'constraints':'Development only. DO NOT inspect Validation/OOS. Never retune from July/August.',
        'limits':'no observed bid/ask, latency, size, funding, market impact or live fills; profitability unproven'
    })


def freeze_token_sha256():
    root=Path(__file__).resolve().parents[2]
    # Pin every parser/algorithm module, including transitive imports. An
    # unrelated code edit intentionally requires a NEW owner-reviewed freeze.
    paths=tuple(sorted((root/'src'/'hypolab').glob('*.py'))) + (
           root/'colab'/'09_failed_auction_v2_offline.ipynb',)
    hashes={str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in paths}
    return sha256(_canonical({'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
                                'files':hashes})).hexdigest()


def make_strategy_v2_plan(path:Path):
    meta,_,_=inspect_micro_bundle(path,collect_5s=False)
    if meta['month'] not in (DIAGNOSTIC_MONTH,ONE_SHOT_DEVELOPMENT_MONTH):
        raise MicroBundleError('not an owner-approved V2 Development month')
    return _canonical({'schema':SCHEMA,'month':meta['month'],
                'role':'DEVELOPMENT_JULY_EXPLORATORY' if meta['month']==DIAGNOSTIC_MONTH
                       else 'ONE_SHOT_DEVELOPMENT_AUGUST',
                'bundle_sha256':_sha(path),
                'source_c1_sha256':meta['c1_sha256'],'source_c2_sha256':meta['c2_sha256'],
                'producer_code_commit':meta['producer_code_commit'],
                'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
                'freeze_token_sha256':freeze_token_sha256(),
                'c2_assumed_latency_ms':meta['assumed_latency_ms'],
                'source_read':'NONE; this is an existing, owner-approved offline ZIP'})


def _rolling_levels(c1):
    """Map UTC minute start -> previous 60 C1 extremes (as-of prior close).

    Each value references ONLY already closed candles; no current-minute OHLC.
    Source timestamps are CSV strings; normalize explicitly.
    """
    result={}
    # validated C1 is regular, but independently check chronology and continuity.
    hist_high=[];hist_low=[];hist_time=[]
    for bar in c1:
        t=int(bar['open_time_ms'])
        if hist_time and t!=hist_time[-1]+MINUTE_MS:
            hist_high.clear();hist_low.clear();hist_time.clear()
        if len(hist_time)==PRIOR_C1_MINUTES:
            hist_time.pop(0);hist_high.pop(0);hist_low.pop(0)
        hist_time.append(t);hist_high.append(float(bar['high']));hist_low.append(float(bar['low']))
        if len(hist_time)==PRIOR_C1_MINUTES:
            at=t+MINUTE_MS
            available=int(bar['available_at_ms'])
            if available>=at:
                result[at]=(max(hist_high),min(hist_low),available)
    return result


def _signal(rows,i,levels):
    """One closed 5s confirmation after a previous aggressive sweep.

    All inputs must have been available by this signal's time. No data at i+1.
    """
    if i<14:return None
    bar=rows[i];t=int(bar['start_ms']);today=t//DAY_MS
    if int(rows[i-2]['start_ms'])!=t-2*FIVE_MS or int(rows[i-1]['start_ms'])!=t-FIVE_MS:
        return None
    if int(rows[i-2]['start_ms'])//DAY_MS!=today:return None
    ref=levels.get(t-t%MINUTE_MS)
    if ref is None:return None
    hi,lo,known_at=ref
    if known_at>int(bar['available_at_ms']):return None
    if _flow_features(rows,i) is None:return None  # strict 60s contiguous flow
    close=float(bar['last_trade_price']);impact=float(bar['first_to_last_trade_bps'])
    imb=float(bar['taker_imbalance']);
    # Test both directions; never adapt threshold to observed outcome.
    for near,barrier,side in ((hi,'UPPER',-1),(lo,'LOWER',1)):
        if side==-1:
            inside=(near*(1-CLOSE_BACK_MAX_BPS/10000)<=close<=near*(1-CLOSE_BACK_MIN_BPS/10000))
            reverse=(imb<=-CONFIRM_MIN_ABS_OPPOSITE_IMB and impact<=-CONFIRM_MIN_REVERSE_PRICE_BPS)
        else:
            inside=(near*(1+CLOSE_BACK_MIN_BPS/10000)<=close<=near*(1+CLOSE_BACK_MAX_BPS/10000))
            reverse=(imb>=CONFIRM_MIN_ABS_OPPOSITE_IMB and impact>=CONFIRM_MIN_REVERSE_PRICE_BPS)
        if not (inside and reverse):continue
        for j in (i-2,i-1):
            sweep=rows[j]
            if int(sweep['available_at_ms'])>int(bar['available_at_ms']):continue
            if side==-1:
                crossed=float(sweep['max_trade_price'])>=near*(1+SWEEP_MIN_BPS/10000)
                bought=float(sweep['taker_imbalance'])>=SWEEP_MIN_ABS_IMB
            else:
                crossed=float(sweep['min_trade_price'])<=near*(1-SWEEP_MIN_BPS/10000)
                bought=float(sweep['taker_imbalance'])<=-SWEEP_MIN_ABS_IMB
            if not (crossed and bought):continue
            prev=rows[j-12:j]
            if len(prev)!=12 or int(prev[0]['start_ms'])!=int(sweep['start_ms'])-60_000:
                continue
            if any(int(x['start_ms'])!=int(sweep['start_ms'])-(12-k)*FIVE_MS for k,x in enumerate(prev)):
                continue
            avg_quote=sum(float(x['executed_quote_notional']) for x in prev)/12
            if avg_quote<=0 or float(sweep['executed_quote_notional'])<SWEEP_MIN_REL_QUOTE*avg_quote:
                continue
            return side,{'level_type':barrier,'known_extreme':round(near,6),
                 'sweep_imbalance':round(float(sweep['taker_imbalance']),4),
                 'confirm_imbalance':round(imb,4),'sweep_relative_quote':round(
                  float(sweep['executed_quote_notional'])/avg_quote,4)}
    return None


def _simulate(rows,i,side,slip):
    """Sequential, no future-based eligibility. Gap BEFORE exit = UNKNOWN;
    gap AFTER an achieved exit is irrelevant (prevents selection lookahead).
    """
    start=int(rows[i]['start_ms']);day=start//DAY_MS
    when=start+ENTRY_DELAY_BINS*FIVE_MS
    max_end=when+MAX_HOLD_BINS*FIVE_MS
    if max_end//DAY_MS!=day:return {'status':'SKIP_KNOWN_DAY_BOUNDARY'},None
    if when < int(rows[i]['available_at_ms']):
        return {'status':'UNKNOWN_LATE_SIGNAL'},None
    entry_idx=i+ENTRY_DELAY_BINS
    if entry_idx>=len(rows):return {'status':'UNKNOWN_ENTRY_NOT_PRESENT'},None
    for j in range(i+1,entry_idx+1):
        if int(rows[j]['start_ms'])!=start+(j-i)*FIVE_MS:
            return {'status':'UNKNOWN_ENTRY_GAP'},None
    entry=float(rows[entry_idx]['first_trade_price'])
    if not math.isfinite(entry) or entry<=0:return {'status':'UNKNOWN_PRICE'},None
    stop=entry*(1-side*STOP_BPS/10000)
    target=entry*(1+side*TARGET_BPS/10000)
    expected_start=when
    last_index=min(entry_idx+MAX_HOLD_BINS,len(rows)-1)
    for j in range(entry_idx,last_index+1):
        b=rows[j]
        if int(b['start_ms'])!=expected_start:
            return {'status':'UNKNOWN_PATH_GAP'},None
        expected_start+=FIVE_MS
        high=float(b['max_trade_price']);low=float(b['min_trade_price']);
        opened=float(b['first_trade_price']);closed=float(b['last_trade_price'])
        if not all(math.isfinite(p) and p>0 for p in (high,low,opened,closed)):
            return {'status':'UNKNOWN_PRICE'},None
        hit_stop=(low<=stop if side>0 else high>=stop)
        hit_target=(high>=target if side>0 else low<=target)
        if hit_stop:
            fill=min(stop,opened) if side>0 else max(stop,opened)
            reason='DUAL_TOUCH_STOP_FIRST' if hit_target else 'STOP'
        elif hit_target:
            fill=target
            reason='TARGET'
        elif j==entry_idx+MAX_HOLD_BINS:
            fill=closed
            reason='TIME_EXIT'
        else:
            continue
        gross=side*(fill/entry-1)*10000
        net=gross-2*(FEE_BPS_PER_SIDE+slip)
        return {'status':'COMPLETED','signal_ms':start,'entry_ms':when,
          'entry_price_proxy':round(entry,6),'exit_ms':int(b['start_ms'])+FIVE_MS,
          'exit_price_proxy':round(fill,6),'exit_index':j,'side':side,
          'reason':reason,'gross_bps':round(gross,6),'net_bps':round(net,6)},j
    return {'status':'UNKNOWN_PATH_END'},None


def _drawdown(xs):
    capital=0.;peak=0.;maxdd=0.
    for p in xs:
        capital+=p;peak=max(peak,capital);maxdd=max(maxdd,peak-capital)
    return maxdd


def _daily_bootstrap(trades):
    days=defaultdict(list)
    for t in trades:days[t['entry_ms']//DAY_MS].append(t['net_bps'])
    if len(days)<5:return None
    d=sorted(days);rnd=random.Random(BOOTSTRAP_SEED);results=[]
    for _ in range(BOOTSTRAP_ROUNDS):
        selected=[d[rnd.randrange(len(d))] for _ in d]
        data=[x for day in selected for x in days[day]]
        if data:results.append(sum(data)/len(data))
    if not results:return None
    results.sort()
    return [round(results[int((len(results)-1)*.025)],6),
            round(results[int((len(results)-1)*.975)],6)]


def _summary(trades):
    pnl=[t['net_bps'] for t in trades];days={t['entry_ms']//DAY_MS for t in trades}
    reason=defaultdict(int)
    for t in trades:reason[t['reason']]+=1
    return {'trades':len(trades),'active_utc_days':len(days),
        'mean_gross_bps':round(sum(t['gross_bps'] for t in trades)/len(trades),6) if trades else None,
        'mean_net_bps':round(sum(pnl)/len(pnl),6) if pnl else None,
        'net_unit_bps_sum':round(sum(pnl),6),
        'net_wins':sum(p>0 for p in pnl),
        'unit_bps_max_drawdown':round(_drawdown(pnl),6),
        'daily_cluster_95pct_net_mean':_daily_bootstrap(trades),
        'reasons':dict(reason)}


def run_strategy_v2(path:Path,plan:bytes):
    if type(plan) is not bytes or plan!=make_strategy_v2_plan(path):
        raise MicroBundleError('non-canonical frozen strategy plan')
    meta,c1,rows=inspect_micro_bundle(path,collect_5s=True)
    if len(rows)<500:raise MicroBundleError('too few C2 buckets')
    levels=_rolling_levels(c1)
    trials=[];opposite=[];matched_primary=[];attempts=[];skips=defaultdict(int)
    raw_signals=0;next_at=-1
    for i in range(len(rows)):
        candidate=_signal(rows,i,levels)
        if candidate is None:continue
        raw_signals+=1
        side,signature=candidate;t=int(rows[i]['start_ms'])
        if t<next_at:
            skips['POSITION_OR_COOLDOWN']+=1
            continue
        trade,exit_idx=_simulate(rows,i,side,BASE_SLIP_BPS_PER_SIDE)
        if trade['status']!='COMPLETED':
            skips[trade['status']]+=1
            attempts.append({'signal_ms':t,'status':trade['status'],'side':side})
            if trade['status'].startswith('UNKNOWN'):
                next_at=(t//DAY_MS+1)*DAY_MS
            continue
        contra,contra_ix=_simulate(rows,i,-side,BASE_SLIP_BPS_PER_SIDE)
        trade['signature']=signature
        trials.append(trade)
        if contra['status']=='COMPLETED':
            opposite.append(contra)
            matched_primary.append(trade)
        else:
            # A negative-control path may continue into a gap AFTER the
            # actual primary exit. Never erase the valid primary trade.
            skips['CONTROL_'+contra['status']]+=1
        next_at=int(rows[exit_idx]['start_ms'])+FIVE_MS+COOLDOWN_MS
    s=_summary(trials);alt=_summary(opposite);matched=_summary(matched_primary)
    stress={}
    for slip in STRESS_SLIP_BPS_PER_SIDE:
        stress[str(slip)]=round(s['mean_net_bps']-2*(slip-BASE_SLIP_BPS_PER_SIDE),6) if trials else None
    later=[x['net_bps'] for x in trials if datetime.fromtimestamp(x['entry_ms']/1000,timezone.utc).day>=22]
    lower=(s['daily_cluster_95pct_net_mean'] or [None])[0]
    gates={'enough_trades':s['trades']>=MIN_TRADES,'enough_days':s['active_utc_days']>=MIN_DAYS,
       'mean_net_positive':s['mean_net_bps'] is not None and s['mean_net_bps']>0,
       'day_cluster_lower_positive':lower is not None and lower>0,
       'stress_all_positive':bool(stress) and all(v is not None and v>0 for v in stress.values()),
       'beats_opposite_by_5bps':s['trades']==alt['trades'] and
                              matched['mean_net_bps'] is not None and alt['mean_net_bps'] is not None and
                              matched['mean_net_bps']-alt['mean_net_bps']>=MIN_MEAN_ADVANTAGE_OVER_REVERSE_BPS,
       'later_days_positive':bool(later) and sum(later)/len(later)>0,
       'drawdown_within_budget':s['unit_bps_max_drawdown']<=MAX_UNIT_DRAWDOWN_BPS,
       'no_unknown_attempts':not any(k.startswith('UNKNOWN') and v>0 for k,v in skips.items())}
    return {'schema':SCHEMA,'month':meta['month'],'role':'DEVELOPMENT',
      'bundle_sha256':_sha(path),'plan_sha256':sha256(plan).hexdigest(),
      'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
      'freeze_token_sha256':freeze_token_sha256(),
      'source_counts':meta['counts'], 'raw_signals':raw_signals,
      'attempts':attempts,'skips':dict(skips),'primary':s,
      'opposite_same_case_control':alt,
      'matched_primary_for_opposite_control':matched,
      'stress_mean_net_bps':stress,
      'later_days_22_onward':{'trades':len(later),'mean_net_bps':round(sum(later)/len(later),6) if later else None},
      'gates':gates,'model_status':'PASSES_MODEL_GATES_NOT_REAL_EXECUTION' if all(gates.values())
                    else 'FAILS_PREDECLARED_MODEL_GATES',
      'trades':trials,'opposite_side_control_trades':opposite,
      'limitations':['July is already-researched Development, not proof',
         'One forward August test is still Development, not protected validation',
         'C2 trades do not provide executable bid/ask, queue or real order fills',
         'Slippage, account fee and funding are not independently measured',
         'Stopped-first high/low ambiguity is a conservative proxy, not a tick replay',
         'No live performance or proven profitability claim']}
