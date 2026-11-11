"""V3: fixed, offline Development-only pre-move case/control research.

Observations are *completed* 5s trade buckets. No venue quotes, observed feed
latency, actual fills, true onset, validation years or market-data source access.
A fixed decision grid and pre-signal quiet requirement prevent post-hoc selection
of picturesque explosions. All negative and unmatched decisions are reported.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

from .micro_bundle import inspect_micro_bundle, MicroBundleError
from .micro_warning import _asia_levels, _feature_at, _sha

ENGINE = 'PREMOVE_V3_FIXED_CASE_CONTROL'
RULES = (
    ('c1_near_asia', 'C1'),
    ('c1_wick_rejection', 'C1'),
    ('c2_flow_surge', 'C2'),
    ('c2_impact_divergence_fade', 'C2'),
    ('fusion_asia_flow', 'C1+C2'),
    ('fusion_compression_flow', 'C1+C2'),
    ('stale_c2_surge_60m', 'PLACEBO'),
)
THRESHOLDS = (15, 30)
HORIZONS = (60, 300)
DECISION_SECOND_OFFSETS = (25, 55)  # within each minute, aligned on a 30s grid
QUIET_PAST_SECONDS = 60
QUIET_MAX_RANGE_BPS = 8.0
ONSET_PROXY_BPS = 5
COOLDOWN_SECONDS = 330  # longer than the 300s label plus next-bin entry
MIN_MATCHED = 100
MIN_MATCHED_DAYS = 8
BOOTSTRAP_ROUNDS = 500
SEED = 20261111
TRAIN_DAYS = 21
MAX_GALLERY = 6


def _canonical(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def make_v3_plan(path:Path)->bytes:
    meta,_,_=inspect_micro_bundle(path,collect_5s=False)
    return _canonical({
        'schema':ENGINE,'role':'DEVELOPMENT','month':meta['month'],
        'bundle_sha256':_sha(path),'producer_code_commit':meta['producer_code_commit'],
        'source_c1_sha256':meta['c1_sha256'],'source_c2_sha256':meta['c2_sha256'],
        'rules':[{'name':name,'family':fam} for name,fam in RULES],
        'thresholds_bps':list(THRESHOLDS),'horizons_seconds':list(HORIZONS),
        'decision_second_offsets':list(DECISION_SECOND_OFFSETS),
        'quiet_past_seconds':QUIET_PAST_SECONDS,'quiet_max_intra_bucket_price_range_bps':QUIET_MAX_RANGE_BPS,
        'onset_proxy_bps':ONSET_PROXY_BPS,'cooldown_seconds':COOLDOWN_SECONDS,
        'matching':'same UTC day/hour, trailing C1 range regime, Asia proximity, C1 compression; only earlier non-alert controls >= 330s before a case; outcome-blind',
        'train_days':TRAIN_DAYS,'bootstrap_day_clusters':BOOTSTRAP_ROUNDS,'seed':SEED,
        'price_proxy':'next complete 5s executed-trade close; NONEXECUTABLE',
        'scope':'SAME-MONTH EXPLORATORY; July already inspected; no causal/OOS validation',
        'missing_c3':'UNKNOWN_NOT_INCLUDED'})


def _bar_context(c1):
    """Every bar sees only the previous 20 already completed C1 bars."""
    out={}
    for i in range(20,len(c1)):
        current=c1[i];history=c1[i-20:i]
        if int(current['open_time_ms']) != int(history[-1]['open_time_ms'])+60000:
            continue
        cp=float(current['close']);op=float(current['open'])
        high=float(current['high']);low=float(current['low'])
        prev_ranges=[(float(r['high'])/float(r['low'])-1)*10000 for r in history]
        median_range=statistics.median(prev_ranges)
        current_range=(high/low-1)*10000
        median_volume=statistics.median(float(r['volume']) for r in history)
        upper=(high-max(op,cp))/cp*10000
        lower=(min(op,cp)-low)/cp*10000
        out[int(current['open_time_ms'])+60000]={
            'range_bps':current_range,'median_range_bps':median_range,
            'regime':0 if current_range<max(1.,.7*median_range) else
                     2 if current_range>max(3.,1.4*median_range) else 1,
            'compression':median_range>0 and current_range<.7*median_range,
            'rel_volume':float(current['volume'])/median_volume if median_volume>0 else None,
            'wick_up_bps':upper,'wick_down_bps':lower,
            'close':cp,'bar_direction':1 if cp>op else -1 if cp<op else 0,
            'open_time_ms':int(current['open_time_ms']),
            'available_at_ms':int(current['available_at_ms'])}
    return out


def _near_asia(bar, levels, day, now_ms):
    # Asia wick levels are only usable after the FINAL bar closed and became
    # available. Before 08:00 UTC they are explicitly unknown.
    if not (now_ms%86400000>=8*3600*1000) or day not in levels:
        return 0
    high,low=levels[day]
    close=bar['close']
    high_diff=(close/high-1)*10000
    low_diff=(close/low-1)*10000
    if -10<=high_diff<=3:return 1
    if -3<=low_diff<=10:return -1
    return 0


def _raw_signals(f,bar,asia_sign,stale):
    """No forward observations or fitted thresholds in this function."""
    out={}
    if asia_sign:
        out['c1_near_asia']=asia_sign
    if (bar['median_range_bps']>0 and bar['range_bps']>=bar['median_range_bps']
            and max(bar['wick_up_bps'],bar['wick_down_bps'])>=.55*bar['range_bps']):
        if bar['wick_up_bps']>bar['wick_down_bps']:out['c1_wick_rejection']=-1
        elif bar['wick_down_bps']>bar['wick_up_bps']:out['c1_wick_rejection']=1
    side=1 if f['imbalance']>0 else -1 if f['imbalance']<0 else 0
    surge=bool(side and abs(f['imbalance'])>=.5 and f['rel_quote']>=2.
               and f['rel_count']>=1.5 and side*f['imbalance_acceleration']>=.1)
    if surge:out['c2_flow_surge']=side
    if (side and abs(f['imbalance'])>=.65 and f['rel_quote']>=2.
            and abs(f['impact_bps'])<=1.5):
        out['c2_impact_divergence_fade']=-side
    if surge and asia_sign and asia_sign==side:out['fusion_asia_flow']=side
    if surge and bar['compression']:out['fusion_compression_flow']=side
    if stale:
        # Strictly PAST one-hour-old C2 feature: timing negative control.
        out['stale_c2_surge_60m']=stale
    return out


def _quiet_range(rows,i):
    """Full inclusive 60s past window, not just first-to-last displacement."""
    if i<12:return False
    t=int(rows[i]['start_ms']);lows=[];highs=[]
    for k in range(i-12,i+1):
        b=rows[k]
        if int(b['start_ms']) != t-(i-k)*5000:return False
        lows.append(float(b['min_trade_price']))
        highs.append(float(b['max_trade_price']))
    return (max(highs)/min(lows)-1)*10000<=QUIET_MAX_RANGE_BPS


def _future_event(rows,index,threshold,horizon,latency):
    """First +/- barrier after a NEXT completed 5s close, or UNKNOWN.

    Entire horizon must be continuous, same-day, and strictly after the
    assumed data availability. The earliest >=5bps is only an ONSET PROXY.
    """
    entry=index+1;end=entry+horizon//5
    if end>=len(rows):return {'status':'UNKNOWN'}
    t=int(rows[index]['start_ms']);day=t//86400000
    if (int(rows[index]['available_at_ms'])>=int(rows[entry]['start_ms'])+5000
            or int(rows[entry]['start_ms'])!=t+5000
            or int(rows[end]['start_ms'])//86400000!=day):return {'status':'UNKNOWN'}
    prices=[]
    for k in range(entry,end+1):
        row=rows[k]
        if int(row['start_ms'])!=t+(k-index)*5000:
            return {'status':'UNKNOWN'}
        prices.append(float(row['last_trade_price']))
    base=prices[0]
    first_up=first_down=None
    onset_up=onset_down=None
    for i,p in enumerate(prices[1:],start=1):
        bps=(p/base-1)*10000
        if onset_up is None and bps>=ONSET_PROXY_BPS:onset_up=i*5
        if onset_down is None and bps<=-ONSET_PROXY_BPS:onset_down=i*5
        if first_up is None and bps>=threshold:first_up=i*5
        if first_down is None and bps<=-threshold:first_down=i*5
    if first_up is None and first_down is None:
        return {'status':'NO_EVENT','direction':0,'onset_proxy_s':None,'threshold_hit_s':None}
    sign=1 if first_down is None or first_up is not None and first_up<first_down else -1
    return {'status':'EVENT','direction':sign,
            'onset_proxy_s':onset_up if sign>0 else onset_down,
            'threshold_hit_s':first_up if sign>0 else first_down}


def _match(rule, decisions):
    """Greedy one-to-one matched controls chosen without reading ANY outcome."""
    strata=defaultdict(list);candidates=[]
    def key(d):
        # Near-Asia is the EXPOSED C1 variable for this particular C1 rule:
        # controls must be able to be away from the level, so do not match it
        # away. For fusion, condition on near-Asia to measure incremental flow.
        x=d['stratum']
        return x[:3]+x[4:] if rule=='c1_near_asia' else x
    for d in decisions:
        if rule in d['signals']:candidates.append(d)
        elif rule not in d['raw_signals']:strata[key(d)].append(d)
    # Stable per-timestamp ordering: the presence of FUTURE control times
    # cannot rearrange earlier candidates (unlike seeded shuffle).
    for pool in strata.values():
        pool.sort(key=lambda d:hashlib.sha256(
            f'{SEED}:{rule}:{d["t"]}'.encode('ascii')).digest())
    pairs=[];unmatched=0;used=set()
    for cand in candidates:
        pool=strata.get(key(cand),())
        found=None
        for ctrl in pool:
            if ctrl['index'] not in used and ctrl['t'] <= cand['t']-COOLDOWN_SECONDS*1000:
                found=ctrl;break
        if found is None:unmatched+=1
        else:
            used.add(found['index']);pairs.append((cand,found))
    return pairs,unmatched


def _split_summary(pairs, outcomes, threshold, horizon, split):
    dsum=defaultdict(lambda:[0,0,0,0,0,0])
    evaluable=0;not_evaluable=0;correct=wrong=0;lead=[]
    for cand,ctrl in pairs:
        if (cand['day_in_month']<=TRAIN_DAYS) != (split=='train'):
            continue
        a=outcomes[cand['index'],threshold,horizon]
        b=outcomes[ctrl['index'],threshold,horizon]
        if a['status']=='UNKNOWN' or b['status']=='UNKNOWN':
            not_evaluable+=1;continue
        evaluable+=1
        ac=int(a['status']=='EVENT');bc=int(b['status']=='EVENT')
        day=cand['day']; v=dsum[day]
        v[0]+=1;v[1]+=ac;v[2]+=bc
        if ac and a['direction']==cand['signals'][cand['active_rule']]:
            correct+=1
            if a['onset_proxy_s'] is not None:lead.append(a['onset_proxy_s'])
        elif ac:wrong+=1
        # 3=directionally correct candidate, 4=directionally correct control
        # using the candidate side as an explicitly hypothetical baseline.
        v[3]+=int(ac and a['direction']==cand['signals'][cand['active_rule']])
        v[4]+=int(bc and b['direction']==cand['signals'][cand['active_rule']])
        v[5]+=int(ac and a['direction']!=cand['signals'][cand['active_rule']])
    if not evaluable:
        return {'pairs':0,'unknown_pairs':not_evaluable,'days':0,
                'alert_event_rate':None,'control_event_rate':None,
                'event_risk_difference':None,'event_risk_ratio':None,
                'daily_bootstrap_95pct_risk_difference':None,
                'correct_direction_rate':None,'wrong_direction_rate':None,
                'control_same_direction_rate':None,'mean_onset_proxy_seconds_after_entry':None,
                'status':'INSUFFICIENT_MATCHED_EVIDENCE'}
    cand_events=sum(v[1] for v in dsum.values())
    ctrl_events=sum(v[2] for v in dsum.values())
    c_rate=cand_events/evaluable; b_rate=ctrl_events/evaluable
    days=len(dsum)
    interval=None
    if evaluable>=MIN_MATCHED and days>=MIN_MATCHED_DAYS:
        # Cluster by calendar day. NOT a validation confidence interval: same
        # month/selection and serial clustering persist.
        keys=sorted(dsum)
        rng=random.Random(f'{SEED}:{threshold}:{horizon}:{split}:{keys[0]}:{evaluable}')
        vals=[]
        for _ in range(BOOTSTRAP_ROUNDS):
            chosen=[keys[rng.randrange(days)] for _ in keys]
            n=sum(dsum[k][0] for k in chosen)
            vals.append((sum(dsum[k][1]-dsum[k][2] for k in chosen))/n)
        vals.sort()
        interval=[vals[int(.025*(BOOTSTRAP_ROUNDS-1))],
                  vals[int(.975*(BOOTSTRAP_ROUNDS-1))]]
    return {'pairs':evaluable,'unknown_pairs':not_evaluable,'days':days,
            'alert_event_rate':c_rate,'control_event_rate':b_rate,
            'event_risk_difference':c_rate-b_rate,
            'event_risk_ratio':c_rate/b_rate if b_rate else None,
            'daily_bootstrap_95pct_risk_difference':interval,
            'correct_direction_rate':correct/evaluable,
            'wrong_direction_rate':wrong/evaluable,
            'control_same_direction_rate':sum(v[4] for v in dsum.values())/evaluable,
            'mean_onset_proxy_seconds_after_entry':sum(lead)/len(lead) if lead else None,
            'status':('EXPLORATORY_DESCRIPTIVE_ONLY' if interval is not None
                      else 'INSUFFICIENT_MATCHED_EVIDENCE')}


def _episodes(rows, alerts):
    """All ex-post trailing 60s 30bps reference crossings, NOT true onset."""
    occurrences=[];last_ms=-10**30
    by_rule={name:[] for name,_ in RULES}
    for name,alist in alerts.items():
        by_rule[name]=sorted((a['t'],a['signals'][name],a['available']) for a in alist)
    missed={r:0 for r,_ in RULES};detected={r:0 for r,_ in RULES}
    from bisect import bisect_left
    starts={name:[x[0] for x in a] for name,a in by_rule.items()}
    for i in range(12,len(rows)):
        t=int(rows[i]['start_ms']);start=t-60000
        if t-last_ms<=300000 or int(rows[i-12]['start_ms'])!=start:
            continue
        if start//86400000!=t//86400000:continue
        if any(int(rows[k]['start_ms'])!=start+5000*(k-(i-12)) for k in range(i-12,i+1)):
            continue
        move=(float(rows[i]['last_trade_price'])/float(rows[i-12]['last_trade_price'])-1)*10000
        if abs(move)<30:continue
        direction=1 if move>0 else -1
        occurrences.append({'reference_ms':t,'direction':direction})
        for name,_ in RULES:
            ss=starts[name];alarms=by_rule[name]
            k=bisect_left(ss,start)
            ok=False
            while k<len(alarms) and alarms[k][0]<t:
                if alarms[k][1]==direction and alarms[k][2]<=t:ok=True;break
                k+=1
            if ok:detected[name]+=1
            else:missed[name]+=1
        last_ms=t
    return {'reference_count':len(occurrences),'caught_by_rule':detected,
            'missed_by_rule':missed,
            'definition':'ex-post trailing 60s >=30bps reference, 300s cooldown; cannot infer true onset'}


def run_v3(path:Path, plan:bytes):
    if type(plan) is not bytes or plan!=make_v3_plan(path):
        raise MicroBundleError('exact complete pre-registered V3 plan required')
    meta,c1,rows=inspect_micro_bundle(path,collect_5s=True)
    if len(rows)<100:raise MicroBundleError('too few observed source bins')
    latency=meta['assumed_latency_ms']
    c1_by_time=_bar_context(c1)
    c1_full={int(r['open_time_ms']):r for r in c1}
    asia=_asia_levels(c1)
    decisions=[];all_signal_alerts={n:[] for n,_ in RULES}
    last_alert={n:None for n,_ in RULES}
    past_flow={};eligible=0;nonquiet=0;missing_context=0
    for i,row in enumerate(rows):
        t=int(row['start_ms'])
        # The decisions are predeclared, independent of price outcomes.
        if (t//1000)%60 not in DECISION_SECOND_OFFSETS:
            continue
        bar=c1_by_time.get(t-t%60000)
        if bar is None or bar['available_at_ms']>int(row['available_at_ms']):
            missing_context+=1;continue
        f=_feature_at(rows,i,asia,c1_full)
        if f is None or not _quiet_range(rows,i):
            nonquiet+=1;continue
        eligible+=1;day=t//86400000
        levels=_near_asia(bar,asia,day,int(row['available_at_ms']))
        # An available 60-minute-old signal is a timing placebo, not novel alpha.
        stale=past_flow.get(t-3600000)
        raw=_raw_signals(f,bar,levels,stale)
        regime=bar['regime'];near=bool(levels)
        stratum=(day,(t%86400000)//3600000,regime,near,bool(bar['compression']))
        d={'index':i,'t':t,'day':day,'day_in_month':datetime.fromtimestamp(t/1000,timezone.utc).day,
           'available':int(row['available_at_ms']), 'stratum':stratum,'signals':{},
           'raw_signals':raw,
           'asia_sign':levels,'quiet_range_bps':QUIET_MAX_RANGE_BPS,
           'delta':f['delta_base'],'rel_quote':f['rel_quote'],'rel_count':f['rel_count']}
        for name,sign in raw.items():
            last=last_alert[name]
            if last is not None and last[0]==day and t-last[1]<=COOLDOWN_SECONDS*1000:
                continue
            d['signals'][name]=sign
            last_alert[name]=(day,t)
            all_signal_alerts[name].append(d)
        decisions.append(d)
        if 'c2_flow_surge' in raw:
            past_flow[t]=raw['c2_flow_surge']
        # NOTE: retaining just signals for stale placebo is bounded by month.
    # For the stale placebo, early current loop would miss 1hr-old signal
    # if that past decision was outside the risk set; by design this placebo
    # compares exactly the same eligible decision grid, no future use.
    pairs={}; pair_counts={}
    for name,_ in RULES:
        pp,unmatched=_match(name,decisions)
        pairs[name]=pp
        pair_counts[name]={'all_alerts':len(all_signal_alerts[name]),
                           'matched_before_future_checks':len(pp),
                           'unmatched_due_to_controls':unmatched}
    # Evaluate labels only for the union of case/control indices. No outcome
    # participates in the signal decisions or the deterministic matching.
    index_set={d['index'] for pp in pairs.values() for pair in pp for d in pair}
    outcomes={}
    for ix in sorted(index_set):
        for thresh in THRESHOLDS:
            for horizon in HORIZONS:
                outcomes[ix,thresh,horizon]=_future_event(rows,ix,thresh,horizon,latency)
    trials=[]
    for name,family in RULES:
        for threshold in THRESHOLDS:
            for horizon in HORIZONS:
                pp=[]
                for a,b in pairs[name]:
                    # Keep active rule only in shallow ephemeral copy; original
                    # decisions and signals remain unchanged.
                    aa={**a,'active_rule':name}
                    pp.append((aa,b))
                train=_split_summary(pp,outcomes,threshold,horizon,'train')
                forward=_split_summary(pp,outcomes,threshold,horizon,'forward')
                trials.append({'rule':name,'family':family,'threshold_bps':threshold,
                               'horizon_seconds':horizon,'train':train,
                               'later_development_diagnostic':forward})
    refs=_episodes(rows,all_signal_alerts)
    # Chronologically FIRST cases for each outcome class; gallery is entirely
    # retrospective and never affects any model, matching, or trial result.
    gallery=[]
    by_time=sorted(((a,b,name) for name,ps in pairs.items() for a,b in ps
                    if name in ('c2_flow_surge','fusion_asia_flow')),
                   key=lambda it:(it[0]['t'],it[2]))
    used_cases=set()
    for a,b,name in by_time:
        label=outcomes.get((a['index'],15,300))
        if not label or label['status']=='UNKNOWN':continue
        outcome_class='event' if label['status']=='EVENT' else 'no_event'
        if outcome_class in used_cases:continue
        i=a['index'];t=a['t']
        timeline=[]
        for k in range(max(0,i-24),min(len(rows),i+13)):
            rr=rows[k];bt=int(rr['start_ms'])
            if bt//86400000!=a['day'] or abs(bt-t)>120000:continue
            timeline.append({'offset_seconds':(bt-t)//1000,
                'last_trade_price':float(rr['last_trade_price']),
                'delta_btc':float(rr['taker_delta_base']),
                'executed_quote_notional':float(rr['executed_quote_notional']),
                'aggregate_rows':int(rr['aggregate_rows'])})
        gallery.append({'rule':name,'class':outcome_class,'signal_ms':t,
                        'future_event':label,'timeline':timeline})
        used_cases.add(outcome_class)
        if len(gallery)>=2:break
    return {'schema':ENGINE,'role':'DEVELOPMENT','month':meta['month'],
            'bundle_sha256':_sha(path),'plan_sha256':hashlib.sha256(plan).hexdigest(),
            'producer_code_commit':meta['producer_code_commit'],
            'source_counts':meta['counts'],'c3_book_depth':'UNKNOWN_NOT_INCLUDED',
            'assumed_latency_ms':latency,'decision_grid_seconds':list(DECISION_SECOND_OFFSETS),
            'quiet_gate_max_60s_trade_high_low_range_bps':QUIET_MAX_RANGE_BPS,
            'eligible_risk_decisions':eligible,'excluded_nonquiet_or_missing_past':nonquiet,
            'excluded_missing_c1_context':missing_context,
            'pair_accounting':pair_counts,'trials':trials,'retrospective_references':refs,
            'retrospective_gallery':gallery,
            'limitations':[
                'same July Development month has already been inspected; no independent evidence',
                'matched controls chosen without labels, but observational confounding and serial clustering remain',
                'C1-only comparison is conditional on observable C2 5s grid/quiet trade-price gate, not a pure standalone C1 model',
                'future labels begin after a next-complete trade-price close, not a fill',
                'onset proxy is first 5bps closed-trade change after price reference; NOT true onset',
                'no executed bid/ask, spread, queue, market impact, observed latency, or trading PnL',
                'bootstrap is descriptive within reused Development; no OOS test, no p-values or alpha claim',
                'missing C2 buckets UNKNOWN; C3 book depth not included'
            ],'inference':'DESCRIPTIVE_ONLY_REUSED_DEVELOPMENT_NOT_VALIDATED'}
