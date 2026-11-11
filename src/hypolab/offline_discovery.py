"""Small descriptive (NOT inferential) event study inside clean Colab runtime.

The discovery sample is NOT a holdout; report no p-values, edge or trade strategy.
Everything is computed from previously validated Development-only features.
"""
from __future__ import annotations
from statistics import mean
from .airgap_bundle import inspect_bundle, BundleError


def describe_forward_return(packet:bytes,*,imbalance_threshold:float=0.6,horizon_minutes:int=5):
    if not isinstance(imbalance_threshold,(int,float)) or not 0<=imbalance_threshold<=1:
        raise BundleError('invalid threshold')
    if type(horizon_minutes) is not int or not 1<=horizon_minutes<=60:
        raise BundleError('horizon must be 1..60 minutes')
    manifest,rows=inspect_bundle(packet)
    n=0;positives=0;gross_returns=[];eligible=0
    # Features are minute-aligned; labels are evaluated strictly after the
    # assumed feature availability. All labels stay in the SAME Development month.
    for i,row in enumerate(rows):
        end=i+horizon_minutes
        if end>=len(rows):break
        if row['c2_status']!='C2_PRESENT':continue
        now=int(row['available_at_ms'])
        future=rows[end]
        label_end=int(future['open_time_ms'])+60_000
        if label_end<=now:continue
        # Only labels with a complete, chronologically contiguous interval.
        if label_end-int(row['open_time_ms']) != (horizon_minutes+1)*60000:
            continue
        eligible+=1
        signal=float(row['taker_imbalance'])
        if abs(signal)<imbalance_threshold:continue
        # This is an exploratory conditional return; NOT executable entry price.
        x=float(row['close']);y=float(future['close'])
        gross_returns.append(y/x-1)
        n+=1;positives+=int(y>x)
    return {'month':manifest['month'],'role':'DEVELOPMENT','horizon_minutes':horizon_minutes,
            'threshold':float(imbalance_threshold),'eligible_feature_rows':eligible,
            'events':n,'positive_fraction':positives/n if n else None,
            'mean_forward_close_return':mean(gross_returns) if n else None,
            'inference':'DESCRIPTIVE_ONLY_NOT_VALIDATED',
            'warning':'Overlapping events are dependent; no execution cost, confidence interval or OOS confirmation.'}
