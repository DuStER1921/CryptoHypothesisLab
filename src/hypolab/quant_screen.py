"""Predeclared, Development-only C1+C2 short-horizon *screen*, never HFT execution.

The entire 1,440-trial universe is frozen before computing any outcomes. Features
are as-of the completed source minute. Entry is the NEXT fully completed minute
close after hypothetical signal availability, not the unavailable signal close.
All outputs are descriptive proxy outcomes, not fills or validated trading edges.
"""
from __future__ import annotations

import hashlib
import json
import math
from itertools import product

from .airgap_bundle import inspect_bundle, BundleError, _month_bounds

ENGINE = 'QUANT_DEVELOPMENT_SCREEN_V1'
THRESHOLDS = (20, 30, 40, 50, 60, 70)  # absolute imbalance, percent
HORIZONS = (1, 3, 5, 10, 15, 30)
SIDES = ('follow', 'fade')
REGIMES = ('all', 'trend_with', 'trend_against', 'high_vol', 'low_vol')
SESSIONS = ('all', 'utc_00_07', 'utc_08_15', 'utc_16_23')
COST_BPS = (2.0, 5.0, 10.0)  # illustrative TOTAL round-trip; not observed costs
FIT_DAYS = 21
SEED = 20261111
PERMUTATIONS = 1024
MIN_TRAIN_EVENTS = 50
MIN_TRAIN_DAYS = 12
MIN_FWD_EVENTS = 15
MIN_FWD_DAYS = 5
MAX_PLAN_BYTES = 512 * 1024


def _canonical(obj):
    return (json.dumps(obj, ensure_ascii=True, sort_keys=True,
                       allow_nan=False, separators=(',', ':'))+'\n').encode('utf-8')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _specs():
    """No data-dependent search space: deterministic full Cartesian product."""
    return [{'id':f'{side}_t{threshold:02d}_h{h:02d}_{regime}_{session}',
             'side':side,'threshold_pct':threshold,'horizon_minutes':h,
             'regime':regime,'utc_session':session}
            for side,threshold,h,regime,session
            in product(SIDES, THRESHOLDS, HORIZONS, REGIMES, SESSIONS)]


def make_default_plan(packet: bytes) -> bytes:
    """Create the public/fixed trial universe *before* any outcome evaluation."""
    meta,_ = inspect_bundle(packet)
    if len(_specs())!=1440:
        raise RuntimeError('trial-universe drift')
    plan={'schema':ENGINE,'role':'DEVELOPMENT','bundle_sha256':_digest(packet),
          'month':meta['month'],'universe_sha256':meta['universe_sha256'],
          'c1_sha256':meta['c1_sha256'],'c2_sha256':meta['c2_sha256'],
          'producer_code_commit':meta['producer_code_commit'],
          'train_calendar_days':FIT_DAYS,'permutations':PERMUTATIONS,
          'seed':SEED,'total_round_trip_cost_bps':list(COST_BPS),
          'trials':_specs(),
          'scope':'DISCOVERY_ONLY_NO_INDEPENDENT_OOS',
          'limitation':'next-minute-close proxy; not executable HFT fills or arbitrage'}
    raw=_canonical(plan)
    if len(raw)>MAX_PLAN_BYTES:
        raise RuntimeError('plan exceeds public trial bound')
    return raw


def _daily_summary(indices, signed_gross_bps, day_number, max_days):
    """One return per nonoverlapping event; per-day aggregates avoid IID-trade p-values."""
    import numpy as np
    if not len(indices):
        return {'events':0,'active_days':0,'positive_fraction':None,
                'gross_mean_bps':None,'net_mean_bps':{str(int(k)):None for k in COST_BPS}}, np.zeros(max_days)
    data=signed_gross_bps[indices]
    days=day_number[indices]
    counts=np.bincount(days,minlength=max_days)
    daily_sum=np.bincount(days,weights=data,minlength=max_days)
    active=counts>0
    daily_net=np.zeros(max_days,dtype=float)
    daily_net[active]=daily_sum[active]/counts[active]-COST_BPS[0]
    gross=float(data.mean())
    return {'events':int(len(indices)),'active_days':int(active.sum()),
            'positive_fraction':float(np.mean(data>0)),
            'gross_mean_bps':gross,
            'net_mean_bps':{str(int(cost)):gross-cost for cost in COST_BPS}},daily_net


def _t_statistics(matrix):
    """One-sided daily-block t score, with degenerate samples rejected as zero."""
    import numpy as np
    d=matrix.shape[-2]
    avg=matrix.mean(axis=-2)
    v=matrix.var(axis=-2,ddof=1) if d>1 else np.zeros_like(avg)
    se=np.sqrt(v/d)
    return np.divide(avg,se,out=np.zeros_like(avg),where=se>1e-12)



def _future_proxy(starts, available, close, days, horizon):
    """NEXT completed 1m close as price proxy; no same-close fantasy fills.

    An event's exit and its label must stay in the same UTC day as the
    feature, preventing cross-day label contamination of daily blocks.
    """
    import numpy as np
    n=len(starts)
    gross=np.zeros(n,dtype=float)
    allowed=np.zeros(n,dtype=bool)
    size=n-1-horizon
    if size>0:
        gross[:size]=(close[1+horizon:]/close[1:n-horizon]-1.)*10000.
        allowed[:size]=(days[:size]==days[1+horizon:]) & (
            available[:size] < starts[1:size+1]+60_000)
    return gross,allowed


def _nonoverlap(mask, horizon):
    """Deterministic earliest-event policy, no overlapping close-to-close labels."""
    import numpy as np
    found=np.flatnonzero(mask)
    accepted=[]
    previous_exit=-1
    for i in found:
        i=int(i)
        if i<=previous_exit:
            continue
        accepted.append(i)
        previous_exit=i+1+horizon
    return np.asarray(accepted,dtype=np.int64)


def run_registered_screen(packet:bytes, plan_bytes:bytes) -> dict:
    """Full selection-accounted screening. No network, Drive, keys or protected data.

    Requires a byte-for-byte fixed plan; caller must persist it *before* calling.
    The July 2024 second-period diagnostics are STILL Development, not a holdout.
    """
    import numpy as np
    if type(plan_bytes) is not bytes or len(plan_bytes)>MAX_PLAN_BYTES or not plan_bytes:
        raise BundleError('missing or oversize preregistration plan')
    if plan_bytes != make_default_plan(packet):
        raise BundleError('plan mismatch, partial trial universe or modified search grid')
    meta,rows=inspect_bundle(packet)
    n=len(rows)
    expected_start,expected_end=_month_bounds(meta["month"])
    if (n!=(expected_end-expected_start)//60_000-1
            or int(rows[0]["open_time_ms"])!=expected_start
            or int(rows[-1]["open_time_ms"])!=expected_end-120_000):
        raise BundleError("screen requires complete Development calendar month except unavailable last bar")
    starts=np.fromiter((int(x['open_time_ms']) for x in rows),dtype=np.int64,count=n)
    available=np.fromiter((int(x['available_at_ms']) for x in rows),dtype=np.int64,count=n)
    close=np.fromiter((float(x['close']) for x in rows),dtype=np.float64,count=n)
    valid_c2=np.fromiter((x['c2_status']=='C2_PRESENT' for x in rows),dtype=bool,count=n)
    imb=np.fromiter((float(x['taker_imbalance']) if x['c2_status']=='C2_PRESENT' else 0.0 for x in rows),dtype=float,count=n)
    minute_ms=60_000
    days=(starts-expected_start)//(24*60*minute_ms)
    total_days=int(days[-1])+1
    if total_days<=FIT_DAYS:
        raise BundleError('full development month required for forward diagnostics')
    hours=(starts//(60*minute_ms))%24
    log_close=np.log(close)
    log_ret=np.zeros(n,dtype=np.float64)
    log_ret[1:]=np.diff(log_close)
    # Rolling volatility context uses ONLY minute-close returns at or before t.
    prefix=np.concatenate(([0.],np.cumsum(log_ret)))
    prefix_sq=np.concatenate(([0.],np.cumsum(log_ret*log_ret)))
    idx=np.arange(n)
    mean15=np.zeros(n);mean60=np.zeros(n)
    var15=np.zeros(n);var60=np.zeros(n)
    for window,mean_out,var_out in ((15,mean15,var15),(60,mean60,var60)):
        valid=idx>=window
        sums=prefix[idx[valid]+1]-prefix[idx[valid]+1-window]
        squares=prefix_sq[idx[valid]+1]-prefix_sq[idx[valid]+1-window]
        mean_out[valid]=sums/window
        var_out[valid]=np.maximum(0.,squares/window-mean_out[valid]**2)
    vol15=np.sqrt(var15);vol60=np.sqrt(var60)
    trend=np.zeros(n,dtype=np.float64)
    trend[15:]=close[15:]/close[:-15]-1.
    valid_history=idx>=60
    regimes={
        'all': valid_history,
        'trend_with':valid_history & (np.sign(trend)==np.sign(imb)) & (trend!=0),
        'trend_against':valid_history & (np.sign(trend)==-np.sign(imb)) & (trend!=0),
        'high_vol':valid_history & (vol15>=vol60) & (vol60>0),
        'low_vol':valid_history & (vol15<vol60) & (vol60>0),
    }
    sessions={'all':np.ones(n,dtype=bool),
              'utc_00_07':hours<8,'utc_08_15':(hours>=8)&(hours<16),
              'utc_16_23':hours>=16}
    # Precompute every horizon ONCE; repeated research trials reuse cached
    # labels instead of rescanning data for each threshold/regime/UTC session.
    horizon_cache={h:_future_proxy(starts,available,close,days,h) for h in HORIZONS}
    train_daily=np.zeros((FIT_DAYS,len(_specs())),dtype=float)
    results=[]
    benchmarks={}
    for h in HORIZONS:
        base,eligible=horizon_cache[h]
        anchors=_nonoverlap(eligible&valid_history,h)
        benchmarks[str(h)]={}
        for name,mult in (("always_long",1),("always_short",-1)):
            train_a=anchors[days[anchors]<FIT_DAYS]
            fwd_a=anchors[days[anchors]>=FIT_DAYS]
            fit,_=_daily_summary(train_a,base*mult,days,FIT_DAYS)
            fwd,_=_daily_summary(fwd_a,base*mult,days-FIT_DAYS,total_days-FIT_DAYS)
            benchmarks[str(h)][name]={'training':fit,'forward_development':fwd}
    for trial_index,s in enumerate(_specs()):
        h=s['horizon_minutes']
        price_return,valid=horizon_cache[h]
        side=np.sign(imb)
        if s['side']=='fade':side=-side
        gross=price_return*side
        mask=(valid&valid_c2&regimes[s['regime']]&sessions[s['utc_session']]
              &(np.abs(imb)>=s['threshold_pct']/100.0))
        chosen=_nonoverlap(mask,h)
        train=chosen[days[chosen]<FIT_DAYS]
        forward=chosen[days[chosen]>=FIT_DAYS]
        train_summary,daily_train=_daily_summary(train,gross,days,FIT_DAYS)
        fwd_summary,_=_daily_summary(forward,gross,days-FIT_DAYS,total_days-FIT_DAYS)
        train_daily[:,trial_index]=daily_train
        valid_train=(train_summary['events']>=MIN_TRAIN_EVENTS
                     and train_summary['active_days']>=MIN_TRAIN_DAYS)
        result={'trial_id':s['id'],'definition':s,'training':train_summary,
                'forward_development':fwd_summary,
                'screen_status':'SCORED' if valid_train else 'INSUFFICIENT_TRAIN_EVIDENCE',
                'p_maxT_train':None,'train_t_daily_net2':None,
                'forward_diagnostic_status':('SCORED' if fwd_summary['events']>=MIN_FWD_EVENTS
                                             and fwd_summary['active_days']>=MIN_FWD_DAYS
                                             else 'INSUFFICIENT_FORWARD_EVIDENCE')}
        results.append(result)
    valid_trials=np.asarray([x['screen_status']=='SCORED' for x in results],dtype=bool)
    tested=int(valid_trials.sum())
    if tested:
        selected_matrix=train_daily[:,valid_trials]
        observed=_t_statistics(selected_matrix)
        rng=np.random.default_rng(SEED)
        sign_flips=rng.choice(np.array([-1.,1.]),size=(PERMUTATIONS,FIT_DAYS))
        # Same daily sign flips across all trials preserve cross-trial dependence.
        # 21 day blocks: explicit broadcast accumulation is faster and avoids
        # oversubscribing Colab/OpenBLAS worker threads on tiny day dimensions.
        signed_sums=np.zeros((PERMUTATIONS,tested),dtype=float)
        for day in range(FIT_DAYS):
            signed_sums += sign_flips[:,day,None]*selected_matrix[day,None,:]
        d=FIT_DAYS
        avg=signed_sums/d
        sumsq=(selected_matrix*selected_matrix).sum(axis=0)
        variance=np.maximum(0.,(sumsq-signed_sums*signed_sums/d)/(d-1))
        denom=np.sqrt(variance/d)
        null_t=np.divide(avg,denom,out=np.zeros_like(avg),where=denom>1e-12)
        null_max=np.maximum(0.,null_t.max(axis=1))
        used=np.flatnonzero(valid_trials)
        for k,t in enumerate(used):
            score=float(observed[k])
            p=(1+int(np.count_nonzero(null_max>=score)))/(PERMUTATIONS+1) if score>0 else 1.
            results[int(t)]['train_t_daily_net2']=score
            results[int(t)]['p_maxT_train']=p
    # Full trial universe reported; one candidate chosen by TRAIN only.
    eligible=[x for x in results if x['p_maxT_train'] is not None
              and x['training']['net_mean_bps']['2']>0]
    ranked=sorted(eligible,key=lambda x:(x['p_maxT_train'],
                                        -x['training']['net_mean_bps']['2'],x['trial_id']))
    leaders=[x['trial_id'] for x in ranked[:10]]
    return {'schema':ENGINE,'role':'DEVELOPMENT',
            'inference':'EXPLORATORY_DAILY_BLOCK_MAXT_NOT_INDEPENDENT_VALIDATION',
            'bundle_sha256':_digest(packet),'plan_sha256':_digest(plan_bytes),
            'month':meta['month'],'total_rows':n,'train_calendar_days':FIT_DAYS,
            'forward_development_days':total_days-FIT_DAYS,
            'total_trials':len(results),'sufficient_training_trials':tested,
            'permutations':PERMUTATIONS,'permutation_seed':SEED,
            'selected_by_training_only':leaders,'benchmarks_by_horizon':benchmarks,
            'all_trials':results,
            'assumptions':[
              'Prices are NEXT completed 1m close-to-close PROXIES, not executable fills.',
              'Signals use completed minute C2 and C1 with explicitly assumed 1.5s availability.',
              'UTC sessions and 15/60m trend/volatility contexts are causal descriptive features.',
              'Nonoverlapping events per trial and same-calendar-day exits; blocks not independent in general.',
              'Sign-flip maxT assumes exchangeable symmetric TRAIN daily returns under the global null.',
              'Total 2, 5, 10 bps round-trip costs are illustrative sensitivity only; no fees/spread/impact observed.',
              'Forward days are still Development, inspected as stability diagnostics, NOT Validation/OOS.',
              'The candidate set was fixed for this screen; prior exploratory dataset exposure means it is not an independent ex-ante research registration.',
              'Local plan file is not an independent tamperproof anchor.',
              'No market-neutral arbitrage, HFT latency edge, broker fills or profitability is established.',
            ]}
