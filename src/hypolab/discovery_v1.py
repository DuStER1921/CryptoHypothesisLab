"""Read-only C1/C2 systematic discovery, no strategy fitting or live trade claims.

All price and flow features are available at the completed 5s decision timestamp.
Labels are non-executable first-trade entry -> future executed-trade close proxies.
Private MICRO ZIP only; only July exploratory + preselected August Development replay.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import json
import math
import random

import numpy as np

from .micro_bundle import inspect_micro_bundle, MicroBundleError
from .micro_warning import _sha

SCHEMA = "HYPOLAB_SYSTEMATIC_DISCOVERY_V1"
FIVE_MS = 5_000
MINUTE_MS = 60_000
DAY_MS = 86_400_000
JULY = "2024-07"
AUGUST = "2024-08"
HORIZONS_S = (300, 900, 1800, 3600)
ENTRY_DELAY_BINS = 3  # first executed trade of 3rd following bin; not executable
COST_BPS = (16.0, 20.0, 24.0)  # hypothetical round trip all taker
TRAIN_DAYS = 21
BIN_QUANTILES = (1 / 3, 2 / 3)
MIN_TRAIN_ROWS = 150
MIN_TRAIN_DAYS = 10
MIN_INTERNAL_ROWS = 75
MIN_INTERNAL_DAYS = 5
MAX_CANDIDATES = 3
MAXT_REPETITIONS = 1000
MAXT_SEED = 20261112
FAMILY_ALPHA = 0.05
FEATURES = (
    'c2_imbalance_5s', 'c2_imbalance_60s', 'c2_rel_trades_5s',
    'c2_rel_notional_5s', 'c2_price_response_5s_bps', 'c2_return_60s_bps',
    'c2_trade_range_60s_bps', 'c2_largest_trade_share',
    'c1_return_1m_bps', 'c1_range_1m_bps', 'c1_rel_volume_20m',
    'c1_distance_to_known_60m_high_bps',
)


def _canonical(x):
    return (json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def protocol_bytes():
    return _canonical({
        'schema': SCHEMA, 'months': {'explored_discovery': JULY, 'one_shot_replay': AUGUST},
        'sampling': 'one closed 5s C2 bar starting at every UTC minute',
        'history': '12 contiguous closed 5s buckets plus 60 contiguous available prior C1 1m candles',
        'feature_names': FEATURES, 'bins': 'three train-only quantile bins, no outcome-tuned cutoffs',
        'hypotheses': 'all 12 singleton tercile bins + all 66 pairwise 3x3 bins; four fixed horizons',
        'entry': 'first executed trade in third subsequent 5s bucket, not a fill',
        'horizons_seconds': HORIZONS_S, 'train_days': TRAIN_DAYS,
        'outcomes': 'directionless linear future price return in bps, after closed forward horizon; direction assigned on train only',
        'contrast': 'same-UTC-day-and-hour all-risk-set mean return removed ex post for inference; never used as signal',
        'test': 'two-sided daily mean t, joint day-signflip maxT over entire searched family; exploratory only',
        'permutations': MAXT_REPETITIONS, 'seed': MAXT_SEED, 'alpha': FAMILY_ALPHA,
        'support': [MIN_TRAIN_ROWS,MIN_TRAIN_DAYS,MIN_INTERNAL_ROWS,MIN_INTERNAL_DAYS],
        'july_internal': 'last 10 days already-inspected Development, not validation',
        'candidates': 'up to three fixed selected using all gates; no manual cherry-picking',
        'proxy_costs_roundtrip_bps': COST_BPS,
        'status': 'not an executable strategy or proof of alpha; no quotes, fills, market impact, funding',
        'no_source_reads': 'user-supplied verified offline MICRO ZIP only; no protected roles',
    })


def freeze_sha256():
    """Bind algorithm, parser and fixed notebook to one auditable code version."""
    here = Path(__file__).resolve()
    root = here.parents[2]
    files = tuple(sorted((root/'src'/'hypolab').glob('*.py'))) + (root/'colab'/'10_systematic_discovery_offline.ipynb',)
    # In isolated synthetic tests, source tree has a minimal subset; that's fine.
    digests = {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in files}
    return sha256(_canonical({'protocol':sha256(protocol_bytes()).hexdigest(), 'files':digests})).hexdigest()


def _clean_f(v):
    x = float(v)
    if not math.isfinite(x):
        raise MicroBundleError('nonfinite feature value')
    return x


def _dataset(c1, rows):
    """Build deterministic, past-only 1-minute risk-set feature table.

    Assumes source bundle parser already checked CSV schemas and source digests;
    still verifies time alignment, whole 60min C1, whole 60sec C2, and as-of.
    """
    c1map = {int(b['open_time_ms']):i for i,b in enumerate(c1)}
    times=[];positions=[];feature_rows=[]
    for i in range(11,len(rows)):
        r=rows[i];t=int(r['start_ms'])
        if t%MINUTE_MS:continue
        history=rows[i-11:i+1]
        if any(int(x['start_ms'])!=t-(11-j)*FIVE_MS for j,x in enumerate(history)):
            continue
        if int(history[0]['start_ms'])//DAY_MS!=t//DAY_MS:continue
        available=int(r['available_at_ms'])
        last_c1_start=t-MINUTE_MS
        k=c1map.get(last_c1_start)
        if k is None or k<59 or int(c1[k]['available_at_ms'])>available:continue
        prior=c1[k-59:k+1]
        if any(int(x['open_time_ms'])!=last_c1_start-(59-j)*MINUTE_MS
               for j,x in enumerate(prior)):continue
        if any(int(x['available_at_ms'])>available for x in prior):continue
        buy=np.array([_clean_f(x['taker_buy_base']) for x in history])
        sell=np.array([_clean_f(x['taker_sell_base']) for x in history])
        quote=np.array([_clean_f(x['executed_quote_notional']) for x in history])
        counts=np.array([int(x['aggregate_rows']) for x in history],dtype=float)
        total_base=float(np.sum(buy+sell))
        last11_base=float(buy[-1]+sell[-1]);q0=float(quote[-1]);
        pq=float(quote[:-1].mean());pc=float(counts[:-1].mean())
        close_price=_clean_f(r['last_trade_price'])
        early_price=_clean_f(history[0]['last_trade_price'])
        min_price=min(_clean_f(x['min_trade_price']) for x in history)
        max_price=max(_clean_f(x['max_trade_price']) for x in history)
        recent_c1=c1[k]
        c1open=_clean_f(recent_c1['open']);c1close=_clean_f(recent_c1['close'])
        c1low=_clean_f(recent_c1['low']);c1high=_clean_f(recent_c1['high'])
        c1vol=_clean_f(recent_c1['volume'])
        prev_volumes=[_clean_f(x['volume']) for x in c1[k-20:k]]
        v_median=float(np.median(prev_volumes))
        high_60=max(_clean_f(x['high']) for x in prior)
        if not all(x>0 for x in (total_base,last11_base,pq,pc,early_price,min_price,c1open,c1low,v_median,high_60,q0)):
            continue
        v=[
            _clean_f(r['taker_imbalance']), float(np.sum(buy-sell)/total_base),
            float(counts[-1]/pc), q0/pq,
            _clean_f(r['first_to_last_trade_bps']), (close_price/early_price-1)*10000,
            (max_price/min_price-1)*10000,
            _clean_f(r['max_agg_trade_notional_quote'])/q0,
            (c1close/c1open-1)*10000, (c1high/c1low-1)*10000,
            c1vol/v_median, (close_price/high_60-1)*10000,
        ]
        if not all(math.isfinite(x) for x in v):continue
        times.append(t);positions.append(i);feature_rows.append(v)
    return (np.asarray(times,dtype=np.int64),np.asarray(positions,dtype=np.int64),
            np.asarray(feature_rows,dtype=np.float64).reshape((-1,len(FEATURES))))


def _thresholds(times,features):
    if len(times)<200 or len(features)!=len(times):raise MicroBundleError('not enough risk-set observations')
    train = ((times//DAY_MS - times[0]//DAY_MS) < TRAIN_DAYS)
    if np.sum(train)<100:raise MicroBundleError('not enough train decisions')
    cuts=np.quantile(features[train], BIN_QUANTILES,axis=0)
    # Nonidentical cutoffs mandatory: constant features must not spawn bogus bins.
    return {name:[float(cuts[0,i]),float(cuts[1,i])] for i,name in enumerate(FEATURES)}


def make_discovery_plan(zip_path:Path):
    meta,c1,rows=inspect_micro_bundle(zip_path,collect_5s=True)
    if meta['month']!=JULY:raise MicroBundleError('only already-inspected July can fit plan thresholds')
    times,_,features=_dataset(c1,rows)
    if not len(times):raise MicroBundleError('no eligible decisions')
    cuts=_thresholds(times,features)
    return _canonical({'schema':SCHEMA,'month':JULY,'role':'EXPLORED_DEVELOPMENT_ONLY',
                       'july_bundle_sha256':_sha(zip_path),
                       'july_source_c1_sha256':meta['c1_sha256'],
                       'july_source_c2_sha256':meta['c2_sha256'],
                       'feature_cuts':cuts,'frozen_code_token':freeze_sha256(),
                       'protocol_sha256':sha256(protocol_bytes()).hexdigest(),
                       'no_label_selection':'thresholds fitted on Jul 1-21 FEATURE data alone'} )


def _parse_plan(plan):
    if type(plan) is not bytes:raise MicroBundleError('canonical bytes required')
    try:p=json.loads(plan)
    except (TypeError,ValueError) as exc:raise MicroBundleError('invalid canonical plan') from exc
    if _canonical(p)!=plan:raise MicroBundleError('noncanonical plan')
    if p['schema']!=SCHEMA or p['month']!=JULY:raise MicroBundleError('wrong discovery plan')
    if p['protocol_sha256']!=sha256(protocol_bytes()).hexdigest() or p['frozen_code_token']!=freeze_sha256():
        raise MicroBundleError('code/protocol drift: new frozen plan required')
    if set(p['feature_cuts'])!=set(FEATURES):raise MicroBundleError('missing feature quantiles')
    return p


def _bins(features, cutoffs):
    bins=np.empty(features.shape,dtype=np.int8)
    for i,name in enumerate(FEATURES):
        lo,hi=map(float,cutoffs[name])
        if not all(math.isfinite(v) for v in (lo,hi)):raise MicroBundleError('invalid cut')
        if lo==hi:bins[:,i]=-1
        else:bins[:,i]=np.searchsorted([lo,hi],features[:,i],side='right')
    return bins


def _family():
    fam=[]
    for i,name in enumerate(FEATURES):
        for a in range(3):fam.append((f'{name}:b{a}',i,a,-1,-1))
    for i,aname in enumerate(FEATURES):
        for j in range(i+1,len(FEATURES)):
            for a in range(3):
                for b in range(3):fam.append((f'{aname}:b{a}&{FEATURES[j]}:b{b}',i,a,j,b))
    return fam


def _membership(bins,spec):
    _,i,a,j,b=spec
    mask=(bins[:,i]==a)
    if j>=0:mask &= bins[:,j]==b
    return mask


def _forward_returns(rows,times,positions,horizon_s):
    n=len(times)
    y=np.full(n,np.nan,dtype=np.float64)
    # Detect gaps by prefix count; label path spans exactly [signal+5s, entry+H].
    start=np.fromiter((int(x['start_ms']) for x in rows),dtype=np.int64,count=len(rows))
    bad=np.zeros(len(rows),dtype=np.int64)
    bad[1:]=(np.diff(start)!=FIVE_MS)
    prefix=np.cumsum(bad)
    h=horizon_s//5
    entry_idx=positions+ENTRY_DELAY_BINS
    last_idx=entry_idx+h-1
    valid=(last_idx<len(rows))
    pos=positions[valid];entry=entry_idx[valid];end=last_idx[valid]
    ok=(prefix[end]==prefix[pos])
    ok &= (start[pos]//DAY_MS==start[end]//DAY_MS)
    # Check later hypothetical price is after signal's actual availability.
    av=np.fromiter((int(x['available_at_ms']) for x in rows),dtype=np.int64,count=len(rows))
    ok &= (av[pos]<start[entry])
    dst=np.flatnonzero(valid)[ok]
    if len(dst):
        entry_ok=entry[ok];end_ok=end[ok]
        first=np.array([float(rows[int(k)]['first_trade_price']) for k in entry_ok])
        last=np.array([float(rows[int(k)]['last_trade_price']) for k in end_ok])
        good=np.isfinite(first)&np.isfinite(last)&(first>0)&(last>0)
        y[dst[good]]=(last[good]/first[good]-1)*10000
    return y


def _same_day_hour_baseline(times,returns):
    """Outcome-only descriptive control; NEVER an input feature or trading rule."""
    groups=times//3_600_000
    _,inv=np.unique(groups,return_inverse=True)
    valid=np.isfinite(returns)
    count=np.bincount(inv[valid],minlength=inv.max()+1)
    total=np.bincount(inv[valid],weights=returns[valid],minlength=len(count))
    mean=np.divide(total,count,out=np.zeros_like(total),where=count>0)
    residual=np.full_like(returns,np.nan)
    residual[valid]=returns[valid]-mean[inv[valid]]
    return residual


def _day_mean_matrix(times,bins,returns,which,days,hypotheses):
    idx=np.flatnonzero(which & np.isfinite(returns))
    n_days=len(days)
    day_at=np.searchsorted(days,times[idx]//DAY_MS)
    residual=_same_day_hour_baseline(times,returns)
    mat=np.zeros((n_days,len(hypotheses)),dtype=np.float64)
    support=np.zeros((n_days,len(hypotheses)),dtype=np.int32)
    gross=np.zeros(len(hypotheses))
    for col,spec in enumerate(hypotheses):
        masked=_membership(bins[idx],spec)
        if not np.any(masked):continue
        dd=day_at[masked]
        val=residual[idx[masked]]
        counts=np.bincount(dd,minlength=n_days)
        sums=np.bincount(dd,weights=val,minlength=n_days)
        support[:,col]=counts
        mat[:,col]=np.divide(sums,counts,out=np.zeros(n_days),where=counts>0)
        gross[col]=float(np.mean(returns[idx[masked]]))
    return mat,support,gross


def _tstats(matrix,active):
    x=np.where(active,matrix,0.0)
    n=np.sum(active,axis=0)
    s=np.sum(x,axis=0)
    sq=np.sum(x*x,axis=0)
    var=np.divide(sq-s*s/np.maximum(n,1),np.maximum(n-1,1),out=np.zeros_like(s),where=n>1)
    se=np.sqrt(np.maximum(var,0)/np.maximum(n,1))
    vals=np.divide(s/np.maximum(n,1),se,out=np.zeros_like(s),where=se>1e-9)
    return vals


def _max_t(matrix,support,qualified,seed=MAXT_SEED,reps=MAXT_REPETITIONS):
    active=support>0
    obs=np.abs(_tstats(matrix,active))
    obs[~qualified]=0.0
    if not np.any(qualified):return np.ones(matrix.shape[1]),obs
    rng=np.random.default_rng(seed)
    null_max=np.zeros(reps,dtype=np.float64)
    for i in range(reps):
        flips=rng.choice(np.array([-1.,1.]),size=(matrix.shape[0],1))
        null_stats=np.abs(_tstats(matrix*flips,active))
        null_max[i]=float(np.max(null_stats[qualified]))
    padj=(1+(null_max[:,None]>=obs[None,:]).sum(axis=0))/(reps+1)
    return padj,obs


def _study_month(zip_path,plan,*,month):
    p=_parse_plan(plan)
    meta,c1,rows=inspect_micro_bundle(zip_path,collect_5s=True)
    if meta['month']!=month:raise MicroBundleError('requested month and ZIP mismatch')
    if month==JULY and _sha(zip_path)!=p['july_bundle_sha256']:
        raise MicroBundleError('discovery input ZIP digest mismatch')
    times,positions,feats=_dataset(c1,rows)
    if len(times)<200:raise MicroBundleError('not enough source decisions')
    if month==JULY and _thresholds(times,feats)!=p['feature_cuts']:
        raise MicroBundleError('feature cutoffs differ from unsupervised July train fit')
    bins=_bins(feats,p['feature_cuts'])
    return meta,rows,times,positions,bins


def run_july_discovery(zip_path:Path,plan:bytes):
    """Fixed family. Only Jul1-21 supplies testing/signs; Jul22-31 diagnostic only."""
    meta,rows,times,positions,bins=_study_month(zip_path,plan,month=JULY)
    day_of_month=((times//DAY_MS)-(times[0]//DAY_MS))+1
    train=day_of_month<=TRAIN_DAYS
    internal=~train
    candidates=_family()
    labels=[]; matrices=[]; supports=[]; rows_out=[]
    days_train=np.unique(times[train]//DAY_MS)
    days_internal=np.unique(times[internal]//DAY_MS)
    for horizon in HORIZONS_S:
        outcomes=_forward_returns(rows,times,positions,horizon)
        training_matrix,training_support,gross=_day_mean_matrix(
            times,bins,outcomes,train,days_train,candidates)
        internal_matrix,internal_support,internal_gross=_day_mean_matrix(
            times,bins,outcomes,internal,days_internal,candidates)
        for i,spec in enumerate(candidates):
            ntrain=int(np.sum(training_support[:,i])); dtrain=int(np.sum(training_support[:,i]>0))
            ntest=int(np.sum(internal_support[:,i])); dtest=int(np.sum(internal_support[:,i]>0))
            famkind=('C1_ONLY' if all(FEATURES[j].startswith('c1_') for j in (spec[1],spec[3]) if j>=0)
                     else 'C2_ONLY' if all(FEATURES[j].startswith('c2_') for j in (spec[1],spec[3]) if j>=0)
                     else 'C1_PLUS_C2')
            rows_out.append({'id':f'{spec[0]}|H{horizon}', 'family':famkind, 'horizon_s':horizon,
                'n_train':ntrain,'days_train':dtrain,'n_late_july':ntest,'days_late_july':dtest,
                'raw_mean_train_bps':round(float(gross[i]),6) if ntrain else None,
                'raw_mean_late_july_bps':round(float(internal_gross[i]),6) if ntest else None,
                'excess_mean_train_day_bps':round(float(training_matrix[training_support[:,i]>0,i].mean()),6) if dtrain else None,
                'excess_mean_late_july_day_bps':round(float(internal_matrix[internal_support[:,i]>0,i].mean()),6) if dtest else None,
                })
        matrices.append(training_matrix);supports.append(training_support)
        labels.append({'horizon_s':horizon, 'eligible_outcomes':int(np.isfinite(outcomes).sum()),
                       'unknown_outcomes':int((~np.isfinite(outcomes)).sum())})
    full=np.concatenate(matrices,axis=1)
    full_support=np.concatenate(supports,axis=1)
    quali=(full_support.sum(axis=0)>=MIN_TRAIN_ROWS)&((full_support>0).sum(axis=0)>=MIN_TRAIN_DAYS)
    pvals,obs=_max_t(full,full_support,quali)
    good=[]
    for k,row in enumerate(rows_out):
        row['qualified_for_joint_test']=bool(quali[k])
        row['abs_day_t_train']=round(float(obs[k]),5) if quali[k] else None
        row['maxT_adjusted_p_train']=round(float(pvals[k]),6) if quali[k] else None
        ex=row['excess_mean_train_day_bps']
        direction=1 if ex is not None and ex>0 else -1
        row['train_selected_direction']='LONG' if direction==1 else 'SHORT'
        raw=row['raw_mean_train_bps'];late=row['raw_mean_late_july_bps']
        row['train_proxy_cost16_net_bps']=round(direction*raw-COST_BPS[0],6) if raw is not None else None
        row['late_july_proxy_cost16_net_bps']=round(direction*late-COST_BPS[0],6) if late is not None else None
        row['passes_exploratory_gate']=(bool(quali[k]) and pvals[k]<=FAMILY_ALPHA
            and row['n_late_july']>=MIN_INTERNAL_ROWS and row['days_late_july']>=MIN_INTERNAL_DAYS
            and row['train_proxy_cost16_net_bps'] is not None and row['train_proxy_cost16_net_bps']>0
            and row['late_july_proxy_cost16_net_bps'] is not None and row['late_july_proxy_cost16_net_bps']>0
            and row['excess_mean_late_july_day_bps'] is not None
            and direction*row['excess_mean_late_july_day_bps']>0)
        if row['passes_exploratory_gate']:good.append(row)
    good.sort(key=lambda x:(x['maxT_adjusted_p_train'],-abs(x['excess_mean_train_day_bps']),x['id']))
    shortlist=[{'id':r['id'],'direction':r['train_selected_direction']} for r in good[:MAX_CANDIDATES]]
    result={'schema':SCHEMA,'month':JULY,'role':'REPEATEDLY_EXPLORED_NOT_INDEPENDENT',
            'plan_sha256':sha256(plan).hexdigest(),'freeze_token_sha256':freeze_sha256(),
            'counts':{'risk_decisions':len(times),'test_family_total':len(rows_out),
                      'tested_with_support':int(np.sum(quali)), 'exploratory_candidates':len(shortlist)},
            'horizon_label_coverage':labels,'maxT_repetitions':MAXT_REPETITIONS,
            'description':'maxT is an approximate day-signflip exploration, NOT a validation p-value or execution evidence',
            'fixed_shortlist_for_next_development':shortlist, 'all_hypotheses':rows_out,
            'tradeability':'UNKNOWN: no quotes, spreads, funding, observable fills, realistic capacity',
            'no_auto_next_read':True}
    return result


def make_replay_manifest(july_report,plan):
    p=_parse_plan(plan)
    if july_report['schema']!=SCHEMA or july_report['month']!=JULY or july_report['plan_sha256']!=sha256(plan).hexdigest():
        raise MicroBundleError('july report mismatches frozen plan')
    shortlist=july_report['fixed_shortlist_for_next_development']
    if len(shortlist)>MAX_CANDIDATES:raise MicroBundleError('too many candidates')
    valid_ids={f'{spec[0]}|H{h}' for h in HORIZONS_S for spec in _family()}
    if len(set((x['id'],x['direction']) for x in shortlist))!=len(shortlist):raise MicroBundleError('repeated candidates')
    if any(x['id'] not in valid_ids or x['direction'] not in ('LONG','SHORT') for x in shortlist):
        raise MicroBundleError('invalid candidate name/direction')
    # This is still NOT cryptographic proof that the report came from a clean run.
    return _canonical({'schema':SCHEMA,'target_month':AUGUST,
        'role':'ONE_SHOT_UNOPENED_DEVELOPMENT_NOT_PROTECTED',
        'plan_sha256':sha256(plan).hexdigest(),
        'july_report_sha256':sha256(_canonical(july_report)).hexdigest(),
        'freeze_token_sha256':p['frozen_code_token'],
        'preselected_candidates':shortlist,
        'if_empty':'NO AUGUST TEST OF SEARCH FAMILY; NO CANDIDATE PASSED JULY GATE'})


def run_august_replay(zip_path:Path,plan:bytes,manifest:bytes,july_report_bytes:bytes):
    p=_parse_plan(plan)
    if type(manifest) is not bytes:raise MicroBundleError('manifest must be exact bytes')
    try:m=json.loads(manifest)
    except (ValueError,TypeError) as exc:raise MicroBundleError('invalid manifest') from exc
    if _canonical(m)!=manifest or m['schema']!=SCHEMA or m['target_month']!=AUGUST or m['plan_sha256']!=sha256(plan).hexdigest() or m['freeze_token_sha256']!=p['frozen_code_token']:
        raise MicroBundleError('frozen manifest/code mismatch')
    if type(july_report_bytes) is not bytes:raise MicroBundleError('saved July report bytes required')
    try: july_report=json.loads(july_report_bytes)
    except (ValueError,TypeError) as exc:raise MicroBundleError('invalid prior report') from exc
    if _canonical(july_report)!=july_report_bytes or make_replay_manifest(july_report,plan)!=manifest:
        raise MicroBundleError('manifest is not the deterministic July result')
    chosen=m['preselected_candidates']
    if not chosen:raise MicroBundleError('NO CANDIDATE: no August scan allowed')
    if len(chosen)>MAX_CANDIDATES or any(x['direction'] not in ('LONG','SHORT') for x in chosen):
        raise MicroBundleError('invalid frozen candidate list')
    meta,rows,times,positions,bins=_study_month(zip_path,plan,month=AUGUST)
    fam={spec[0]:spec for spec in _family()}
    report=[]
    for x in chosen:
        name,h=x['id'].rsplit('|H',1)
        if name not in fam or int(h) not in HORIZONS_S:raise MicroBundleError('unknown frozen hypothesis')
        outcome=_forward_returns(rows,times,positions,int(h))
        included=_membership(bins,fam[name])&np.isfinite(outcome)
        direction=1 if x['direction']=='LONG' else -1
        net=(outcome[included]*direction-COST_BPS[0])
        days=times[included]//DAY_MS
        groups=np.unique(days)
        daymean=np.array([float(np.mean(net[days==day])) for day in groups])
        report.append({'id':x['id'],'direction':x['direction'],'completed_observations':len(net),
            'active_utc_days':len(groups),'gross_mean_bps':float(np.mean(net)+COST_BPS[0]) if len(net) else None,
            'proxy_net_mean_16bps':float(np.mean(net)) if len(net) else None,
            'proxy_net_mean_20bps':float(np.mean(net)-(COST_BPS[1]-COST_BPS[0])) if len(net) else None,
            'proxy_net_mean_24bps':float(np.mean(net)-(COST_BPS[2]-COST_BPS[0])) if len(net) else None,
            'positive_day_fraction':float(np.mean(daymean>0)) if len(daymean) else None})
    return {'schema':SCHEMA,'role':'ONE_SHOT_AUGUST_DEVELOPMENT_ONLY', 'month':AUGUST,
        'plan_sha256':sha256(plan).hexdigest(),'manifest_sha256':sha256(manifest).hexdigest(),
        'freeze_token_sha256':freeze_sha256(),'candidate_results':report,
        'status':'DESCRIPTIVE PROXY ONLY; not executed order/fill; never reselect based on August'}
