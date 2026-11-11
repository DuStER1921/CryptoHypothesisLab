"""Microstructure V2: closed-5-second causal warnings and negative controls.

No HFT orders, no quotes or actual fills. First label price is the next *fully
closed* 5s executed-trade proxy, NOT an available exchange bid/ask. Retrospective
reference episodes are defined by later outcomes and never feed signal rules.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict

from .micro_bundle import inspect_micro_bundle, MicroBundleError

ENGINE='PREMOVE_MICRO_WARNING_V2'
RULES=('flow_surge_follow','flow_surge_fade',
       'low_price_response_fade','large_agg_cluster_follow',
       'asia_wick_pressure_follow')
THRESHOLDS_BPS=(15,30)
HORIZONS_SECONDS=(60,300)
HISTORY_BINS=12
COOLDOWN_SECONDS=60
MIN_EVALUABLE=50
MIN_ACTIVE_DAYS=10
EPISODE_THRESHOLD_BPS=30
EPISODE_TRAILING_SECONDS=60
EPISODE_COOLDOWN_SECONDS=300
EXAMPLE_LIMIT=8


def _canonical(x):
    return (json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode()


def make_micro_warning_plan(path:Path):
    manifest,_,_=inspect_micro_bundle(path,collect_5s=False)
    fields={'schema':ENGINE,'role':'DEVELOPMENT','month':manifest['month'],
            'bundle_sha256':_sha(path),'producer_code_commit':manifest['producer_code_commit'],
            'c1_sha256':manifest['c1_sha256'],'c2_sha256':manifest['c2_sha256'],
            'rules':list(RULES),'thresholds_bps':list(THRESHOLDS_BPS),
            'horizons_seconds':list(HORIZONS_SECONDS),'cooldown_seconds':COOLDOWN_SECONDS,
            'history_seconds':HISTORY_BINS*5,
            'c1_wick_context':'ONLY prior fully closed 1m C1 candle, never current minute',
            'quiet_last_60s_max_abs_bps':15,
            'flow_surge':{'min_abs_imbalance':.50,'min_relative_quote_notional':2.0,
                          'min_relative_aggregate_rate':1.5,
                          'min_directional_imbalance_acceleration':.10},
            'low_response':{'min_abs_imbalance':.65,'min_relative_quote_notional':2.0,
                            'max_first_last_price_abs_bps':1.5},
            'large_agg_cluster':{'min_abs_imbalance':.4,'min_relative_quote_notional':1.5,
                                 'min_max_agg_notional_fraction':.30},
            'asia_wick_pressure':{'min_abs_imbalance':.3,'min_relative_quote_notional':1.2,
                                  'max_distance_to_level_bps':10},
            'reference':{'price':'last observed executed aggTrade in each complete 5s bin',
                         'entry':'next fully closed 5s bucket (nonexecutable proxy)',
                         'target':'first later closed 5s price +/- fixed bps, within same day',
                         'missing_bucket':'UNKNOWN, not a zero-volume bar',
                         'costs':'not modeled, price proxy cannot justify executable PnL'},
            'no_c3':'UNKNOWN_NOT_INCLUDED',
            'inference':'EXPLORATORY_DEVELOPMENT_ONLY_NOT_HFT_OR_VALIDATED'}
    return _canonical(fields)


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(4*1024*1024),b''):
            h.update(part)
    return h.hexdigest()


def _asia_levels(c1):
    """For each day, as-of 08:00 UTC, full 00–07:59 candle WICKS only."""
    days=defaultdict(list)
    for r in c1:
        ms=int(r['open_time_ms'])
        days[ms//86400000].append(r)
    result={}
    for day, rows in days.items():
        asia=[r for r in rows if int(r['open_time_ms'])%86400000<8*3600000]
        if len(asia)!=480 or [int(x['open_time_ms'])%86400000 for x in asia]!=list(range(0,480*60000,60000)):
            continue
        result[day]=(max(float(r['high']) for r in asia),min(float(r['low']) for r in asia))
    return result


def _feature_at(rows,i,asia,c1_context=None):
    """Only source buckets <= i; contiguous past 60s and not already moving."""
    if i<HISTORY_BINS:return None
    bucket=rows[i];t=int(bucket['start_ms']);day=t//86400000
    history=rows[i-HISTORY_BINS:i]
    if (t%86400000<60_000 or
            any(int(r['start_ms'])!=t-(HISTORY_BINS-k)*5000 for k,r in enumerate(history)) or
            int(history[0]['start_ms'])//86400000!=day):
        return None
    previous_price=float(history[0]['last_trade_price']);current_price=float(bucket['last_trade_price'])
    if abs((current_price/previous_price-1)*10000)>15:
        return None
    previous_quote=sum(float(r['executed_quote_notional']) for r in history)
    previous_count=sum(int(r['aggregate_rows']) for r in history)
    if not previous_quote>0 or not previous_count>0:
        return None
    quote=float(bucket['executed_quote_notional'])
    imbalance=float(bucket['taker_imbalance'])
    rel_quote=quote/(previous_quote/HISTORY_BINS)
    rel_count=int(bucket['aggregate_rows'])/(previous_count/HISTORY_BINS)
    max_fraction=float(bucket['max_agg_trade_notional_quote'])/quote
    prev_imbalance=sum(float(r['taker_imbalance']) for r in history)/HISTORY_BINS
    delta=float(bucket['taker_delta_base'])
    delta_slope=delta-sum(float(r['taker_delta_base']) for r in history)/HISTORY_BINS
    imbalance_accel=imbalance-prev_imbalance
    impact=float(bucket['first_to_last_trade_bps'])
    signed_impact_per_1m_quote=(1 if imbalance>0 else -1)*impact/(quote/1_000_000)
    prev_bar=None
    if c1_context is not None:
        # C1 current minute OHLC is NEVER available before its close.
        prev_bar=c1_context.get(t-t%60000-60000)
    wick_up=wick_down=None
    prev_bar_range=None
    if prev_bar is not None:
        o,h,l,c=(float(prev_bar[key]) for key in ('open','high','low','close'))
        wick_up=(h-max(o,c))/c*10000
        wick_down=(min(o,c)-l)/c*10000
        prev_bar_range=(h/l-1)*10000
    levels=asia.get(day) if t%86400000>=8*3600000 else None
    near_hi=near_lo=False
    if levels:
        hi,lo=levels
        hi_diff=(current_price/hi-1)*10000
        lo_diff=(current_price/lo-1)*10000
        near_hi= -10<=hi_diff<=3
        near_lo= -3<=lo_diff<=10
    return {'t':t,'day':day,'price':current_price,'imbalance':imbalance,
            'rel_quote':rel_quote,'rel_count':rel_count,
            'impact_bps':float(bucket['first_to_last_trade_bps']),
            'max_agg_fraction':max_fraction,
            'delta_base':delta,'delta_acceleration_btc':delta_slope,
            'imbalance_acceleration':imbalance_accel,
            'signed_price_impact_bps_per_1m_quote':signed_impact_per_1m_quote,
            'prior_c1_upper_wick_bps':wick_up,
            'prior_c1_lower_wick_bps':wick_down,
            'prior_c1_range_bps':prev_bar_range,
            'quote':quote,'asia_hi':levels[0] if levels else None,
            'asia_lo':levels[1] if levels else None,
            'near_hi':near_hi,'near_lo':near_lo}


def _signals(f):
    if f is None:return {}
    s=1 if f['imbalance']>0 else -1 if f['imbalance']<0 else 0
    if not s:return {}
    imp=abs(f['imbalance']);rel=f['rel_quote'];intensity=f['rel_count']
    result={}
    if imp>=.5 and rel>=2 and intensity>=1.5 and s*f['imbalance_acceleration']>=.10:
        result['flow_surge_follow']=s
        result['flow_surge_fade']=-s
    if imp>=.65 and rel>=2 and abs(f['impact_bps'])<=1.5:
        result['low_price_response_fade']=-s
    if imp>=.4 and rel>=1.5 and f['max_agg_fraction']>=.30:
        result['large_agg_cluster_follow']=s
    if (s==1 and f['near_hi'] or s==-1 and f['near_lo']) and imp>=.3 and rel>=1.2 and s*f['imbalance_acceleration']>=0:
        result['asia_wick_pressure_follow']=s
    return result


def _label(rows,i,sign,threshold,horizon):
    """Returns outcome after a fresh next-bucket price proxy; no gap filling."""
    n=horizon//5
    entry=i+1
    last=entry+n
    if last>=len(rows):return None
    t=int(rows[i]['start_ms']);day=t//86400000
    if (int(rows[i]['available_at_ms']) >= int(rows[entry]['start_ms'])+5000 or
            int(rows[last]['start_ms'])//86400000!=day):
        return None
    for k in range(entry,last+1):
        if int(rows[k]['start_ms'])!=t+(k-i)*5000:
            return None
    base=float(rows[entry]['last_trade_price'])
    for k in range(entry+1,last+1):
        moved=sign*(float(rows[k]['last_trade_price'])/base-1)*10000
        if moved>=threshold:return ('correct',(k-entry)*5)
        if moved<=-threshold:return ('wrong_way',(k-entry)*5)
    return ('no_move',None)


def _referenced_episodes(rows,features,alarms):
    """Ex-post first trailing-60s crossing proxy, not true physical onset."""
    episodes=[];past=EPISODE_TRAILING_SECONDS//5
    last_reference_ms=None
    for i in range(past,len(rows)):
        current=rows[i];prev=rows[i-past]
        end=int(current['start_ms']);start=int(prev['start_ms'])
        if last_reference_ms is not None and end-last_reference_ms<=EPISODE_COOLDOWN_SECONDS*1000:
            continue
        if end-start!=past*5000 or start//86400000!=end//86400000:
            continue
        # strict contiguous 5s grid; do not infer from missing prints
        if any(int(rows[k]['start_ms'])!=start+(k-(i-past))*5000 for k in range(i-past,i+1)):
            continue
        r=(float(current['last_trade_price'])/float(prev['last_trade_price'])-1)*10000
        if abs(r)<EPISODE_THRESHOLD_BPS:continue
        sign=1 if r>0 else -1
        episode={'reference_ms':end,'direction':sign,'trailing_60s_move_bps':round(r,4)}
        hit={}
        for rule in RULES:
            # signal as-of must be before the reference timestamp, available
            hit[rule]=any(a['sign']==sign and start<=a['start_ms']<end and
                          a['available_at_ms']<=end for a in alarms[rule])
        episode['warning_before_reference']=hit
        # Gallery is explicitly ex-post and can contain future features.
        episodes.append((i,episode));last_reference_ms=end
    return episodes


def run_micro_warning_study(path:Path,plan:bytes):
    expected=make_micro_warning_plan(path)
    if type(plan) is not bytes or plan!=expected:
        raise MicroBundleError('must execute exactly complete pre-registered plan for this ZIP')
    meta,c1,rows=inspect_micro_bundle(path,collect_5s=True)
    if len(rows)<120:raise MicroBundleError('not enough 5s C2 coverage')
    asia=_asia_levels(c1)
    c1_context={int(r['open_time_ms']):r for r in c1}
    alerts={r:[] for r in RULES}
    last_alert={r:None for r in RULES}
    features=[None]*len(rows)
    eligible=0
    for i in range(len(rows)):
        f=_feature_at(rows,i,asia,c1_context)
        features[i]=f
        if f is None:continue
        eligible+=1
        for rule,sign in _signals(f).items():
            last=last_alert[rule]
            if last is not None and f['day']==last[0] and f['t']-last[1]<=COOLDOWN_SECONDS*1000:
                continue
            alerts[rule].append({'index':i,'start_ms':f['t'],
                                 'available_at_ms':int(rows[i]['available_at_ms']),
                                 'day':f['day'],'sign':sign,
                                 'rel_quote':round(f['rel_quote'],4),
                                 'rel_count':round(f['rel_count'],4),
                                 'imbalance':round(f['imbalance'],4),
                                 'price_impact_bps':round(f['impact_bps'],4),
                                 'delta_acceleration_btc':round(f['delta_acceleration_btc'],4),
                                 'imbalance_acceleration':round(f['imbalance_acceleration'],4),
                                 'prior_c1_upper_wick_bps':(round(f['prior_c1_upper_wick_bps'],4)
                                      if f['prior_c1_upper_wick_bps'] is not None else None),
                                 'prior_c1_lower_wick_bps':(round(f['prior_c1_lower_wick_bps'],4)
                                      if f['prior_c1_lower_wick_bps'] is not None else None),
                                 'signed_price_impact_bps_per_1m_quote':round(f['signed_price_impact_bps_per_1m_quote'],4),
                                 'max_agg_fraction':round(f['max_agg_fraction'],4)})
            last_alert[rule]=(f['day'],f['t'])
    trials=[]
    for rule in RULES:
        for threshold in THRESHOLDS_BPS:
            for horizon in HORIZONS_SECONDS:
                count={'correct':0,'wrong_way':0,'no_move':0,'unknown':0}
                lead=[];days=set()
                for a in alerts[rule]:
                    outcome=_label(rows,a['index'],a['sign'],threshold,horizon)
                    if outcome is None:
                        count['unknown']+=1
                    else:
                        name,seconds=outcome
                        count[name]+=1;days.add(a['day'])
                        if seconds is not None and name=='correct':lead.append(seconds)
                evals=count['correct']+count['wrong_way']+count['no_move']
                status=('DESCRIPTIVE_SUFFICIENT_COUNTS_NOT_VALIDATED'
                        if evals>=MIN_EVALUABLE and len(days)>=MIN_ACTIVE_DAYS
                        else 'INSUFFICIENT_EVIDENCE')
                trials.append({'rule':rule,'threshold_bps':threshold,'horizon_seconds':horizon,
                               'total_alerts':len(alerts[rule]),'outcomes':count,
                               'evaluable':evals,'active_days':len(days),
                               'correct_fraction':count['correct']/evals if evals else None,
                               'false_alarms':count['wrong_way']+count['no_move'],
                               'mean_correct_lead_seconds':sum(lead)/len(lead) if lead else None,
                               'status':status})
    episodes=_referenced_episodes(rows,features,alerts)
    examples=[]
    alert_positions={r:{a['index'] for a in alerts[r]} for r in RULES}
    for index,e in episodes[:EXAMPLE_LIMIT]:
        timeline=[]
        # all points are descriptive; can include post-reference outcome.
        for k in range(max(0,index-24),min(len(rows),index+13)):
            b=rows[k]
            if int(b['start_ms'])//86400000!=int(rows[index]['start_ms'])//86400000:
                continue
            timeline.append({'start_ms':int(b['start_ms']),
                             'last_trade_price':float(b['last_trade_price']),
                             'delta_base':float(b['taker_delta_base']),
                             'quote_notional':float(b['executed_quote_notional']),
                             'aggregate_rows':int(b['aggregate_rows']),
                             'max_agg_fraction':float(b['max_agg_trade_notional_quote'])/
                                                float(b['executed_quote_notional']),
                             'signalled_rules':[rule for rule in RULES if k in alert_positions[rule]]})
        examples.append({**e,'timeline':timeline})
    coverage={r:sum(int(e['warning_before_reference'][r]) for _,e in episodes) for r in RULES}
    return {'schema':ENGINE,'role':'DEVELOPMENT','month':meta['month'],
            'bundle_sha256':_sha(path),'plan_sha256':hashlib.sha256(plan).hexdigest(),
            'assumed_latency_ms':meta['assumed_latency_ms'],
            'c3_book_depth':'UNKNOWN_NOT_INCLUDED',
            'source_counts':meta['counts'],'eligible_closed_5s_buckets':eligible,
            'rules':list(RULES),'trials':trials,
            'reference':{'definition':'ex-post first 30bps trailing-60s 5s trade-price crossing, 300s cooldown; NOT true onset',
                         'episodes':len(episodes),'prior_60s_directional_alarm_counts':coverage,
                         'missed_by_rule':{r:len(episodes)-coverage[r] for r in RULES}},
            'gallery':examples,
            'inference':'EXPLORATORY_ONLY_NOT_VALIDATED_NO_L2_NO_EXECUTABLE_FILLS',
            'limitations':['one-month reused Development, selected episodes are ex-post',
                           'missing 5s bins are UNKNOWN, no zero/forward-fill',
                           '5s trade prices are not bid/ask/queue execution quotes',
                           'timestamp latency is hypothetical, no observed feed delay',
                           'alerts overlap across rules and no multiple-testing p-value',
                           'no C3, funding, true L2 OFI or order cancellations']}
