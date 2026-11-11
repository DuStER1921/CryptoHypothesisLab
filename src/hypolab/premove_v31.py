"""V3.1: bounded, offline-only Development missed-reference diagnostics and fixed risk sets.

This module cannot open Drive or canonical data. It ONLY inspects an already
verified private MICRO ZIP. Its retrospective references are *closed*
trade-price proxies, NOT the beginning of physical price moves. All statements
and sensitivity comparisons remain exploratory. No protected calendar access.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .micro_bundle import inspect_micro_bundle, MicroBundleError
from .micro_warning import _asia_levels, _sha
from .premove_v3 import (_bar_context, _near_asia, _raw_signals, _future_event,
                         _split_summary, RULES, THRESHOLDS, HORIZONS,
                         COOLDOWN_SECONDS)

SCHEMA = 'PREMOVE_V31_FROZEN_DIAGNOSTIC_AND_MATCHED'
LEADS_SECONDS = (5, 15, 30, 60, 120)
SCENARIOS = (
    ('sparse_8bps', (25,55), 8),
    ('sparse_12bps', (25,55), 12),
    ('sparse_no_quiet', (25,55), None),
    ('all5_8bps', None, 8),
    ('all5_12bps', None, 12),
    ('all5_no_quiet', None, None),
)
PRIMARY = 'all5_12bps'
# Fixed BEFORE any new source reads. The source calendar must independently
# approve any monthly partition, including these months. No automatic staging.
FUTURE_DEVELOPMENT_MONTHS = ('2024-08','2024-09','2024-10')
DIAGNOSTIC_MONTH = '2024-07'
STAGE_EMERGING_BPS = 20
REFERENCE_BPS = 30
REFERENCE_TRAILING_SECONDS = 60
REFERENCE_COOLDOWN_SECONDS = 300


def _canonical(obj):
    return (json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8')


def protocol_bytes():
    """Content-addressed fixed protocol independent of any market-data packet."""
    return _canonical({
        'schema':SCHEMA,'development_diagnostic_month':DIAGNOSTIC_MONTH,
        'new_months_in_frozen_order':list(FUTURE_DEVELOPMENT_MONTHS),
        'scenario_grid':[{'name':n,'offsets':list(o) if o else 'ALL_5S',
                          'max_past_60s_trade_range_bps':q} for n,o,q in SCENARIOS],
        'primary_scenario':PRIMARY,'stages_bps':[8,STAGE_EMERGING_BPS],
        'lead_seconds':list(LEADS_SECONDS),'references':{
            'trailing_seconds':REFERENCE_TRAILING_SECONDS,
            'threshold_bps':REFERENCE_BPS,'cooldown_seconds':REFERENCE_COOLDOWN_SECONDS,
            'reference_time':'START of closed 5s reference bin, NOT physical onset',
            'availability_test':'source available_at_ms <= reference START',
        },'rules':[{'name':n,'family':family} for n,family in RULES],
        'label_thresholds_bps':list(THRESHOLDS),
        'horizons_seconds':list(HORIZONS),
        'primary_endpoint':{'threshold_bps':15,'horizon_seconds':300,
                            'statistic':'alert minus previous-only matched nonalert event rate'},
        'history':'12 prior contiguous completed 5s bins; no hidden 15bps endpoint gate',
        'match':'same UTC day/hour/as-of C1 regime/Asia vicinity/compression; '
                'one-to-one previous non-alert >=330s; outcome-blind',
        'constraints':'Development only, V3.1 is an exploratory method; new months '
                      'are chronological Development checks, NOT protected validation',
        'forbidden':'new data reads without owner approval; Validation 2025 and '
                    'Final OOS 2026; C3 execution/edge claims',
    })


def _code_sha256():
    # Printed privately and recorded in plan. If notebook/main code drifts,
    # future owner must compare against the already saved first-run code SHA.
    return sha256(Path(__file__).read_bytes()).hexdigest()


def freeze_token_sha256():
    """Freeze all algorithm/parser modules and analyst notebook, across months."""
    root=Path(__file__).resolve().parents[2]
    inputs=(
        Path(__file__),
        Path(__file__).with_name('premove_v3.py'),
        Path(__file__).with_name('micro_bundle.py'),
        Path(__file__).with_name('micro_warning.py'),
        root/'colab'/'07_premove_v31_offline.ipynb',
    )
    hashed={str(path.relative_to(root)):sha256(path.read_bytes()).hexdigest()
            for path in inputs}
    return sha256(_canonical({'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
                              'code_files_sha256':hashed})).hexdigest()


def _legacy_miss_reasons(refs, observations, legacy_alerts, names):
    """Retrospective explanation of WHY legacy 25/55-8bps did not warn.

    Observations are raw as-of signal conditions from the complete 5s grid,
    even when a sample fails the legacy price gate. No downstream outcome is
    read by any signal. Results are explanatory, never used for threshold tuning.
    """
    observed={}
    reasons={name:defaultdict(int) for name in names}
    indexed={name:(sorted(legacy_alerts[name]),
                    [x[0] for x in sorted(legacy_alerts[name])]) for name in names}
    for ref in refs:
        rt=ref['reference_start_ms'];direction=ref['direction']
        ticks=[observations.get(rt-k*5000) for k in range(1,13)]
        ticks=[x for x in ticks if x is not None]
        row={}
        for rule in names:
            alerts,stamps=indexed[rule]
            lo=bisect_left(stamps,rt-60000);hi=bisect_left(stamps,rt)
            warned=any(side==direction and available<=rt
                       for _,side,available in alerts[lo:hi])
            if warned:reason='WARNED'
            else:
                avail=[x for x in ticks if x['available']<=rt]
                matches=[x for x in avail if x['raw'].get(rule)==direction]
                if matches:
                    on_grid=[x for x in matches if x['grid']]
                    if not on_grid:reason='MISSED_BY_25_55_GRID'
                    elif not any(x['range']<=8 for x in on_grid):
                        reason='BLOCKED_BY_8BPS_QUIET_GATE'
                    else:reason='COOLDOWN_OR_RULE_STATE'
                elif any(x['raw'].get(rule)==direction for x in ticks):
                    reason='NOT_AVAILABLE_AT_REFERENCE_START'
                elif any(rule in x['raw'] for x in avail):
                    reason='ONLY_OPPOSITE_DIRECTION_TRIGGER'
                elif not avail:reason='NO_ASOF_OBSERVATION'
                else:reason='NO_RAW_TRIGGER'
            row[rule]=reason
            reasons[rule][reason]+=1
        observed[rt]=row
    return observed,{name:dict(counts) for name,counts in reasons.items()}


def make_v31_plan(path:Path):
    meta,_,_=inspect_micro_bundle(path,collect_5s=False)
    month=meta['month']
    if month != DIAGNOSTIC_MONTH and month not in FUTURE_DEVELOPMENT_MONTHS:
        raise MicroBundleError('month outside frozen V3.1 Development calendar')
    return _canonical({
        'schema':SCHEMA,'role':'DEVELOPMENT','month':month,
        'kind':'REUSED_DIAGNOSTIC' if month==DIAGNOSTIC_MONTH else 'NEW_CHRONOLOGICAL_DEVELOPMENT',
        'bundle_sha256':_sha(path),'source_c1_sha256':meta['c1_sha256'],
        'source_c2_sha256':meta['c2_sha256'],
        'source_producer_code_commit':meta['producer_code_commit'],
        'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
        'freeze_token_sha256':freeze_token_sha256(),
        'engine_sha256':_code_sha256(),
        'base_v3_sha256':sha256(Path(__file__).with_name('premove_v3.py').read_bytes()).hexdigest(),
        'assumed_latency_ms':meta['assumed_latency_ms'],
        'evaluation_contract':'NO_SOURCE_READ; NONE_OF_THESE_IS_AN_INDEPENDENT_VALIDATION_GRANT',
    })


def _range_60s(rows):
    """O(n) observed 12x5s trailing min/max, reset at gaps and day rollovers."""
    highs=deque();lows=deque();result=[None]*len(rows)
    prior=None;consecutive=0
    for i,row in enumerate(rows):
        t=int(row['start_ms'])
        if prior is None or t!=prior+5000 or prior//86400000!=t//86400000:
            highs.clear();lows.clear();consecutive=0
        prior=t;consecutive+=1
        h=float(row['max_trade_price']);l=float(row['min_trade_price'])
        while highs and highs[-1][1]<=h:highs.pop()
        while lows and lows[-1][1]>=l:lows.pop()
        highs.append((i,h));lows.append((i,l))
        while highs and highs[0][0]<i-11:highs.popleft()
        while lows and lows[0][0]<i-11:lows.popleft()
        if consecutive>=12:
            result[i]=(highs[0][1]/lows[0][1]-1)*10000
    return result


def _flow_features(rows,i):
    """No price-outcome filter. Completed current bucket + exactly 12 prior ones."""
    if i<12:return None
    b=rows[i];t=int(b['start_ms']);hist=rows[i-12:i]
    if hist[0]['start_ms']!=t-60000 or hist[0]['start_ms']//86400000!=t//86400000:
        return None
    if any(int(r['start_ms'])!=t-60000+5000*k for k,r in enumerate(hist)):
        return None
    prior_quote=sum(float(r['executed_quote_notional']) for r in hist)
    prior_count=sum(int(r['aggregate_rows']) for r in hist)
    if prior_quote<=0 or prior_count<=0:return None
    imb=float(b['taker_imbalance'])
    return {
        'imbalance':imb,
        'rel_quote':float(b['executed_quote_notional'])/(prior_quote/12),
        'rel_count':int(b['aggregate_rows'])/(prior_count/12),
        'imbalance_acceleration':imb-sum(float(r['taker_imbalance']) for r in hist)/12,
        'impact_bps':float(b['first_to_last_trade_bps']),
    }


def _stage(bps):
    if bps is None:return 'UNKNOWN_GAP_OR_BOUNDARY'
    if bps<=8:return 'QUIET_8BPS'
    if bps<=STAGE_EMERGING_BPS:return 'EMERGING_8_TO_20BPS'
    return 'ACTIVE_OVER_20BPS'


def _references(rows):
    """Exact ex-post V3 reference definition; no prior signals or price labels used."""
    out=[];last_ms=-10**30
    for i in range(12,len(rows)):
        t=int(rows[i]['start_ms']);start=t-60000
        if t-last_ms<=300000 or int(rows[i-12]['start_ms'])!=start:
            continue
        if start//86400000!=t//86400000:
            continue
        if any(int(rows[k]['start_ms'])!=start+5000*(k-(i-12)) for k in range(i-12,i+1)):
            continue
        move=(float(rows[i]['last_trade_price'])/float(rows[i-12]['last_trade_price'])-1)*10000
        if abs(move)<REFERENCE_BPS:continue
        out.append({'index':i,'reference_start_ms':t,
                    'direction':1 if move>0 else -1,
                    'trailing_60s_move_bps':round(move,4)})
        last_ms=t
    return out


def _match_previous(rule,decisions):
    """O(n) one-to-one outcome-BLIND previous-only control for each rule."""
    pools=defaultdict(list);cases=defaultdict(list)
    def key(d):
        s=d['stratum']
        return s[:3]+s[4:] if rule=='c1_near_asia' else s
    for d in decisions:
        k=key(d)
        if rule in d['signals']:
            cases[k].append(d)
        elif rule not in d['raw_signals']:
            pools[k].append(d)
    result=[];unmatched=0
    for k,clist in cases.items():
        clist.sort(key=lambda d:d['t'])
        ctrl=sorted(pools.get(k,()),key=lambda d:d['t'])
        q=deque();ptr=0
        for cand in clist:
            while ptr<len(ctrl) and ctrl[ptr]['t']<=cand['t']-COOLDOWN_SECONDS*1000:
                q.append(ctrl[ptr]);ptr+=1
            if q:result.append((cand,q.popleft()))
            else:unmatched+=1
    result.sort(key=lambda x:x[0]['t'])
    return result,unmatched


def _coverage(refs, scenarios, names):
    from bisect import bisect_left
    result={}
    for scenario in scenarios:
        signals=scenarios[scenario]['alerts']
        counts={};unwarned_by_rule={}
        for name in names:
            a=signals[name];stamps=[x[0] for x in a]
            hit=0
            for ref in refs:
                t=ref['reference_start_ms'];start=t-60000
                j=bisect_left(stamps,start)
                if any(ts<t and side==ref['direction'] and av<=t
                       for ts,side,av in a[j:bisect_left(stamps,t)]):
                    hit+=1
            counts[name]=hit
            unwarned_by_rule[name]=len(refs)-hit
        result[scenario]={
            'eligible_risk_decisions':scenarios[scenario]['eligible'],
            'excluded_not_on_grid':scenarios[scenario]['excluded_grid'],
            'excluded_not_quiet':scenarios[scenario]['excluded_quiet'],
            'raw_signal_counts':scenarios[scenario]['raw_counts'],
            'cooldown_alert_counts':{name:len(signals[name]) for name in names},
            'pre_reference_directional_caught':counts,
            'pre_reference_directional_missed':unwarned_by_rule,
        }
    return result


def run_v31(path:Path,plan:bytes):
    if type(plan) is not bytes or plan!=make_v31_plan(path):
        raise MicroBundleError('exact saved frozen V3.1 plan is required')
    meta,c1,rows=inspect_micro_bundle(path,collect_5s=True)
    if len(rows)<120:raise MicroBundleError('too few observed 5s rows')
    ranges=_range_60s(rows)
    refs=_references(rows)
    bar_context=_bar_context(c1)
    asia=_asia_levels(c1)
    times={int(b['start_ms']):i for i,b in enumerate(rows)}
    diagnostic_times={ref['reference_start_ms']-k*5000
                      for ref in refs for k in range(1,13)}
    raw_diagnostic_observations={}
    scenarios={name:{'offsets':offs,'quiet':quiet,
           'eligible':0,'excluded_grid':0,'excluded_quiet':0,
           'raw_counts':{n:0 for n,_ in RULES},
           'alerts':{n:[] for n,_ in RULES},
           'last_alert':{n:None for n,_ in RULES},
           'past_flow':{}} for name,offs,quiet in SCENARIOS}
    primary_decisions=[]
    omitted={'missing_prior_flow_or_gap':0,'missing_asof_c1':0}
    for i,row in enumerate(rows):
        t=int(row['start_ms']);rng=ranges[i]
        if rng is None:
            omitted['missing_prior_flow_or_gap']+=1;continue
        bar=bar_context.get(t-t%60000)
        if bar is None or bar['available_at_ms']>int(row['available_at_ms']):
            omitted['missing_asof_c1']+=1;continue
        f=_flow_features(rows,i)
        if f is None:
            omitted['missing_prior_flow_or_gap']+=1;continue
        day=t//86400000
        asia_sign=_near_asia(bar,asia,day,int(row['available_at_ms']))
        stratum=(day,(t%86400000)//3600000,bar['regime'],bool(asia_sign),bool(bar['compression']))
        sec=(t//1000)%60
        for name,s in scenarios.items():
            if s['offsets'] is not None and sec not in s['offsets']:
                s['excluded_grid']+=1;continue
            if s['quiet'] is not None and rng>s['quiet']:
                s['excluded_quiet']+=1;continue
            s['eligible']+=1
            stale=s['past_flow'].get(t-3600000)
            raw=_raw_signals(f,bar,asia_sign,stale)
            if name=='all5_no_quiet' and t in diagnostic_times:
                raw_diagnostic_observations[t]={'raw':raw,'range':rng,
                    'grid':sec in (25,55),'available':int(row['available_at_ms'])}
            d=None
            if name==PRIMARY:
                d={'index':i,'t':t,'day':day,
                   'day_in_month':datetime.fromtimestamp(t/1000,timezone.utc).day,
                   'available':int(row['available_at_ms']),
                   'stratum':stratum,'raw_signals':raw,'signals':{}}
            for rule,side in raw.items():
                s['raw_counts'][rule]+=1
                last=s['last_alert'][rule]
                if last is not None and last[0]==day and t-last[1]<=COOLDOWN_SECONDS*1000:
                    continue
                s['last_alert'][rule]=(day,t)
                s['alerts'][rule].append((t,side,int(row['available_at_ms'])))
                if d is not None:d['signals'][rule]=side
            if d is not None:primary_decisions.append(d)
            if 'c2_flow_surge' in raw:s['past_flow'][t]=raw['c2_flow_surge']
    # This experiment uses no future labels for risk-set eligibility, signals,
    # matching, or retrospective phase annotations.
    pp={};pair_counts={};needed=set()
    for name,_ in RULES:
        pairs,unmatched=_match_previous(name,primary_decisions)
        pp[name]=pairs
        pair_counts[name]={'all_alerts':len(scenarios[PRIMARY]['alerts'][name]),
                          'matched_before_future_checks':len(pairs),
                          'unmatched_due_to_controls':unmatched}
        for a,b in pairs:
            needed.add(a['index']);needed.add(b['index'])
    outcomes={}
    for i in sorted(needed):
        for threshold in THRESHOLDS:
            for horizon in HORIZONS:
                outcomes[i,threshold,horizon]=_future_event(rows,i,threshold,horizon,meta['assumed_latency_ms'])
    trials=[]
    for name,family in RULES:
        pairs=[({**a,'active_rule':name},b) for a,b in pp[name]]
        for threshold in THRESHOLDS:
            for horizon in HORIZONS:
                trials.append({'rule':name,'family':family,
                               'threshold_bps':threshold,'horizon_seconds':horizon,
                               'train':_split_summary(pairs,outcomes,threshold,horizon,'train'),
                               'later_development_diagnostic':_split_summary(pairs,outcomes,threshold,horizon,'forward')})
    miss_by_reference,miss_reason_counts=_legacy_miss_reasons(
        refs,raw_diagnostic_observations,scenarios['sparse_8bps']['alerts'],
        [name for name,_ in RULES])
    # Per-reference observations are calculated from ONLY earlier bins; their
    # selection is ex-post. They must NEVER be used to fit a signal threshold.
    lead_stage_counts={str(k):defaultdict(int) for k in LEADS_SECONDS}
    references=[]
    for ref in refs:
        rt=ref['reference_start_ms'];day=rt//86400000;recs=[]
        for lead in LEADS_SECONDS:
            sample_ms=rt-lead*1000
            ix=times.get(sample_ms)
            if ix is None or sample_ms//86400000!=day:
                info={'lead_seconds':lead,'stage':'UNKNOWN_GAP_OR_BOUNDARY',
                      'observable_before_reference':False,'flow_context_available':False,
                      'grid_25_55':None,'trade_range_bps':None,'delta_btc':None,
                      'taker_imbalance':None,'aggregate_rows':None,
                      'quote_notional':None}
            else:
                b=rows[ix];stage=_stage(ranges[ix])
                observable=int(b['available_at_ms'])<=rt
                f=_flow_features(rows,ix)
                bar=bar_context.get(sample_ms-sample_ms%60000)
                flow_known=bool(f and bar and bar['available_at_ms']<=int(b['available_at_ms']))
                info={'lead_seconds':lead,'stage':stage,
                      'observable_before_reference':observable,
                      'flow_context_available':flow_known,
                      'grid_25_55':(sample_ms//1000)%60 in (25,55),
                      'trade_range_bps':round(ranges[ix],4) if ranges[ix] is not None else None,
                      'delta_btc':float(b['taker_delta_base']),
                      'taker_imbalance':float(b['taker_imbalance']),
                      'aggregate_rows':int(b['aggregate_rows']),
                      'quote_notional':round(float(b['executed_quote_notional']),4)}
            lead_stage_counts[str(lead)][info['stage']]+=1
            if not info['observable_before_reference']:
                lead_stage_counts[str(lead)]['NOT_AVAILABLE_BEFORE_REFERENCE']+=1
            recs.append(info)
        references.append({**ref,'pre_reference_samples':recs,
                           'legacy_miss_reasons':miss_by_reference[rt]})
    # Do not serialize unbounded raw alarm timestamp streams or control rows.
    compact_scenarios=_coverage(refs,scenarios,[name for name,_ in RULES])
    return {'schema':SCHEMA,'role':'DEVELOPMENT','month':meta['month'],
        'kind':'REUSED_DIAGNOSTIC' if meta['month']==DIAGNOSTIC_MONTH else 'NEW_CHRONOLOGICAL_DEVELOPMENT',
        'bundle_sha256':_sha(path),'plan_sha256':sha256(plan).hexdigest(),
        'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
        'freeze_token_sha256':freeze_token_sha256(),
        'engine_sha256':_code_sha256(),
        'source_counts':meta['counts'],
        'reference':{'definition':'EX_POST first trailing 60s 30bps with 300s cooldown; reference time = 5s bucket START',
                     'count':len(refs),'lead_stage_counts':{k:dict(v) for k,v in lead_stage_counts.items()},
                     'legacy_miss_reason_counts':miss_reason_counts,
                     'all_references':references},
        'risk_sensitivity':compact_scenarios,'primary_scenario':PRIMARY,
        'primary_pair_accounting':pair_counts,'trials':trials,
        'omitted_for_asof':omitted,
        'inference':'EXPLORATORY_ONLY; neither true onset nor executable alpha',
        'limitations':[
            'Reference selection is ex-post; cannot locate actual microsecond onset',
            '5s lookback may not have been available before the reference due to hypothetical latency',
            'Sensitivity scenarios are diagnostics, NOT a selection-valid multiplicity correction',
            'Only owner-authorized Development packages; same July is repeatedly inspected',
            'C1 family remains conditional on observable C2 risk set',
            'C2 aggTrades are executed aggregates, not full order book or individual orders',
            'Previous-only matching is observational and can leave unmatched cases',
            'No C3 L2, fill data, bid/ask, spreads, fees, latency measurements or PnL',
            'Never infer robustness from one month; preserve all null and negative trials',
        ]}
