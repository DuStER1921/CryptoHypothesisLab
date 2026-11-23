"""Frozen C2 sub-5s M0/M1/placebo offline study; no Drive, market, or orders.

Reads only an independently SHA-verified private packet in an unmounted VM.
All evaluated prices are executed-trade proxies, NEVER executable fills.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import math

from .c2_sub5 import FEATURE
from .c2_sub5_packet import (PROTOCOL_SHA256, TRIAL_ID, PacketError,
                             iter_packet_rows, verify_private_packet)

DEC_START_MS = 1733011200000
DAY_MS = 86_400_000
BUCKET_MS = 5_000
ROWS_PER_DAY = DAY_MS//BUCKET_MS
WINDOW_BUCKETS = 28  # B-12*5s .. B+15*5s inclusive
ELIGIBLE_ANCHORS_PER_DAY = ROWS_PER_DAY-WINDOW_BUCKETS+1


def _uniform_placebo(n:int)->float:
    """Exactly the max 1s fraction for floor(5000*(j+.5)/n), j=0..n-1.

    Ceiling(n/5) follows from evenly allocating n rounded timestamps over
    five disjoint 1000ms spans; verified brute force in synthetic tests.
    """
    return ((n+4)//5)/n


def _features(window):
    prior=list(window)[:12]
    b=window[12]
    n=b['aggregate_rows'];quote=b['executed_quote_notional']
    first=prior[0]['first_trade_price'];last=prior[-1]['last_trade_price']
    highest=max(r['max_trade_price'] for r in prior)
    lowest=min(r['min_trade_price'] for r in prior)
    return (
        (b['taker_buy_base']-b['taker_sell_base'])/
            (b['taker_buy_base']+b['taker_sell_base']),
        math.log1p(n),math.log1p(quote),b['first_to_last_trade_bps'],
        b['max_interarrival_ms'],
        b['longest_same_taker_side_agg_rows']/n,
        b['max_agg_trade_notional_quote']/quote,
        (b['last_transact_ms']-b['first_transact_ms'])/5000,
        10000*math.log(last/first),
        10000*math.log(highest/lowest),
    )


def _candidate_samples(rows):
    """Stream rolling same-day, strictly contiguous source-observed windows.

    Requires all observed bins B-60s .. B+75s. Day/bank changes reset state.
    No missing bucket is filled. Returns small numeric tuples for fit/metrics.
    """
    q=deque(maxlen=WINDOW_BUCKETS)
    previous=None
    for b in rows:
        t=b['start_ms']
        if previous is not None and (t!=previous+BUCKET_MS or t//DAY_MS!=previous//DAY_MS):
            q.clear()
        previous=t
        q.append(b)
        if len(q)!=WINDOW_BUCKETS:
            continue
        center=q[12]
        if center[FEATURE] is None:
            continue
        side_delta=center['taker_buy_base']-center['taker_sell_base']
        if not side_delta:
            continue
        direction=1 if side_delta>0 else -1
        try:
            features=_features(q)
            y=direction*10000*math.log(q[27]['first_trade_price']/q[15]['first_trade_price'])
            if not all(map(math.isfinite,features)) or not math.isfinite(y):
                continue
        except (ZeroDivisionError,ValueError,OverflowError):
            continue
        day=(t//DAY_MS)  # study train/eval split defined on center B, not window tail
        yield day,features,center[FEATURE],_uniform_placebo(center['aggregate_rows']),y


def _fit_predict(np,x_train,y_train,x_test,columns):
    """Train-only z-score; fixed alpha=1 ridge, unpenalized intercept."""
    tr=x_train[:,columns];te=x_test[:,columns]
    mu=tr.mean(axis=0)
    sigma=tr.std(axis=0,ddof=0)
    if not np.all(np.isfinite(mu)) or not np.all(np.isfinite(sigma)) or np.any(sigma<=0):
        raise PacketError('frozen feature has zero/nonfinite TRAIN variance; stop')
    z=(tr-mu)/sigma
    zte=(te-mu)/sigma
    y_mu=y_train.mean()
    beta=np.linalg.solve(z.T@z+np.eye(z.shape[1]),z.T@(y_train-y_mu))
    return zte@beta+y_mu


def evaluate_samples(samples, *, synthetic=True):
    """Pure deterministic evaluator. No file I/O; all attempted cases included."""
    try:import numpy as np
    except ImportError as exc:raise PacketError('optional numpy required') from exc
    sample=list(samples)
    if not sample:
        return {'decision':'REJECT_OR_INSUFFICIENT','reason':'NO_ELIGIBLE_SAMPLES',
                'synthetic':synthetic,'trial_id':TRIAL_ID,'protocol_sha256':PROTOCOL_SHA256}
    X=np.array([[*s[1],s[2],s[3]] for s in sample],dtype=np.float64)
    y=np.array([s[4] for s in sample],dtype=np.float64)
    days=np.array([s[0] for s in sample],dtype=np.int64)
    train_until=DEC_START_MS//DAY_MS+20
    train=days<train_until
    late=~train
    counts={str(i):int(np.count_nonzero(days==DEC_START_MS//DAY_MS+i-1))
            for i in range(1,32)}
    diag_days=int(np.count_nonzero([counts[str(i)] for i in range(21,32)]))
    if not train.any() or not late.any():
        return {'decision':'REJECT_OR_INSUFFICIENT','reason':'MISSING_TRAIN_OR_DIAGNOSTIC',
                'train_rows':int(train.sum()),'diagnostic_rows':int(late.sum()),
                'synthetic':synthetic,'trial_id':TRIAL_ID,'protocol_sha256':PROTOCOL_SHA256,
                'eligible_per_day':counts}
    # No hyperparameter optimization; identical training and late samples.
    pred0=_fit_predict(np,X[train],y[train],X[late],list(range(10)))
    pred1=_fit_predict(np,X[train],y[train],X[late],list(range(11)))
    predp=_fit_predict(np,X[train],y[train],X[late],list(range(10))+[11])
    loss0=(y[late]-pred0)**2
    loss1=(y[late]-pred1)**2
    lossp=(y[late]-predp)**2
    m0=float(loss0.mean())
    if m0<=0 or not math.isfinite(m0):
        raise PacketError('degenerate/nonfinite baseline loss')
    true_lift=float((loss0.mean()-loss1.mean())/m0)
    placebo_lift=float((loss0.mean()-lossp.mean())/m0)
    diag_day_ids=days[late]
    pair=[]
    for idx in range(21,32):
        d=DEC_START_MS//DAY_MS+idx-1
        m=diag_day_ids==d
        if np.any(m):
            pair.append({'utc_day':f'2024-12-{idx:02d}',
                         'decisions':int(m.sum()),
                         'm0_sse':float(loss0[m].sum()),
                         'm1_sse':float(loss1[m].sum()),
                         'placebo_sse':float(lossp[m].sum()),
                         'mse_lift':float((loss0[m].mean()-loss1[m].mean())/loss0[m].mean())
                                       if loss0[m].mean()>0 else None})
    positives=sum((p['m0_sse']>p['m1_sse']) for p in pair)
    def segment(lo,hi):
        m=(diag_day_ids>=DEC_START_MS//DAY_MS+lo-1)&(diag_day_ids<=DEC_START_MS//DAY_MS+hi-1)
        return float((loss0[m].sum()-loss1[m].sum())/loss0[m].sum()) if m.any() and loss0[m].sum()>0 else None
    s1=segment(21,25);s2=segment(26,31)
    # Cluster-bootstrap over WHOLE UTC days, not individual overlapping observations.
    # Exploratory interval only; no null p-value and no OOS validation claim.
    daily=np.array([[p['m0_sse'],p['m1_sse']] for p in pair],dtype=np.float64)
    interval=None
    if len(pair)>1:
        rng=np.random.default_rng(20261123)
        sample_idx=rng.integers(len(pair),size=(2000,len(pair)))
        boot=daily[sample_idx].sum(axis=1)
        with np.errstate(divide='ignore',invalid='ignore'):
            lift=(boot[:,0]-boot[:,1])/boot[:,0]
        lift=lift[np.isfinite(lift)]
        if lift.size:
            interval=[float(v) for v in np.quantile(lift,[.025,.975])]
    insufficient=diag_days<9 or int(late.sum())<10000
    passes=(not insufficient and true_lift>=.005 and
            diag_days>0 and positives/diag_days>=.7 and
            s1 is not None and s1>0 and s2 is not None and s2>0 and
            true_lift-placebo_lift>=.0025)
    return {'schema':'BTC_C2_SUB5_DESCRIPTIVE_STUDY_V1',
            'trial_id':TRIAL_ID,'protocol_sha256':PROTOCOL_SHA256,
            'synthetic':synthetic,'train_rows':int(train.sum()),
            'diagnostic_rows':int(late.sum()),'diagnostic_days':diag_days,
            'mse_M0':m0,'mse_M1':float(loss1.mean()),
            'mse_placebo':float(lossp.mean()),
            'mse_lift':true_lift,'placebo_lift':placebo_lift,
            'true_minus_placebo_lift':true_lift-placebo_lift,
            'positive_days':positives,'fraction_positive_days':positives/diag_days if diag_days else None,
            'lift_21_25':s1,'lift_26_31':s2,
            'day_bootstrap_ci95_descriptive_only':interval,
            'daily_paired':pair,'eligible_per_day':counts,
            'non_evaluable_per_day':{k:ELIGIBLE_ANCHORS_PER_DAY-v for k,v in counts.items()},
            'decision':('SYNTHETIC_ONLY_NO_MARKET_CLAIM' if synthetic else
                        'EXPLORATORY_INCREMENTAL_FORECAST_CANDIDATE' if passes
                        else 'REJECT_OR_INSUFFICIENT'),
            'gate_results':{
                'enough_days':diag_days>=9,'enough_decisions':int(late.sum())>=10000,
                'lift_005':true_lift>=.005,'positive_days_070':positives/diag_days>=.7 if diag_days else False,
                'both_late_segments_positive':s1 is not None and s1>0 and s2 is not None and s2>0,
                'true_minus_placebo_0025':true_lift-placebo_lift>=.0025},
            'interpretation':'Synthetic only' if synthetic else
                'Development-only executed-price forecast proxy; NOT a tradable edge, fill or protected OOS.'}


def evaluate_verified_private_packet(path,*,expected_packet_sha256,
                                     expected_source_sha256,expected_code_sha256):
    """Verify complete private packet first; compute offline; never mount source."""
    manifest=verify_private_packet(path,expected_packet_sha256=expected_packet_sha256,
                 expected_source_sha256=expected_source_sha256,
                 expected_code_sha256=expected_code_sha256)
    report=evaluate_samples(_candidate_samples(iter_packet_rows(path,verified_manifest=manifest)),synthetic=False)
    report['packet_sha256']=expected_packet_sha256
    report['source_c2_sha256']=manifest['source_c2_sha256']
    report['manifest_csv_sha256']=manifest['csv_sha256']
    report['owner_receipt_independently_authenticated']=False
    return report
