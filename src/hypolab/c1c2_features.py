"""Bounded, deterministic streaming C1+C2 1-minute feature join.

No historical arrival timestamp exists. `assumed_available_at` is explicitly
hypothetical and cannot establish realizable trading execution or live latency.
Missing C2 events are UNKNOWN, never silently filled with zero. This converter
operates only on fully verified *private local staging* and is not a real-data
authorization API. No output is persisted or published by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import math

from .contracts import Role, SPLITS, require_utc
from .parquet_window import schema_for
from .staging import StagedPartition


class FeatureError(RuntimeError):
    pass


@dataclass(frozen=True)
class MinuteFeature:
    open_time: datetime
    assumed_available_at: datetime
    close: float
    c2_status: str  # C2_PRESENT or C2_UNKNOWN
    taker_buy_base: float | None
    taker_sell_base: float | None
    taker_delta_base: float | None
    taker_imbalance: float | None
    c2_aggregate_rows: int


def _hash(path):
    h=hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda:fh.read(4*1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _verify_staging(stage:StagedPartition,family:str):
    if not stage.source_verified or stage.identity.family!=family or stage.parquet_path.is_symlink():
        raise FeatureError('wrong or unverified stage')
    if _hash(stage.parquet_path)!=stage.identity.canonical_sha256:
        raise FeatureError('staged file checksum mismatch')


def _iter_rows(parquet,columns):
    for batch in parquet.iter_batches(batch_size=16384,columns=columns):
        for r in batch.to_pylist():
            yield r


class C1C2MinuteBuilder:
    MAX_BARS=44_640

    def build(self,c1:StagedPartition,c2:StagedPartition,*,assumed_latency_ms:int,
              require_complete_month:bool=True)->tuple[MinuteFeature,...]:
        if (c1.identity.period!=c2.identity.period or
                c1.identity.period[:4] not in ('2020','2021','2022','2023','2024')):
            raise FeatureError('unapproved or mismatched Development partition')
        if type(assumed_latency_ms) is not int or not 1<=assumed_latency_ms<=600_000:
            raise FeatureError('explicit positive hypothetical latency (1..600000 ms) required')
        _verify_staging(c1,'klines');_verify_staging(c2,'aggTrades')
        try:import pyarrow.parquet as pq
        except ImportError as e:raise FeatureError('pyarrow optional dependency required') from e
        import pyarrow as pa
        first,last=c1.identity.bounds
        if not SPLITS[Role.DEVELOPMENT].contains(first,last):
            raise FeatureError('split crossing denied')
        rows=[]
        try:
            with pq.ParquetFile(c1.parquet_path) as bars, pq.ParquetFile(c2.parquet_path) as trades:
                if (not bars.schema_arrow.equals(schema_for('klines',pa)) or
                        not trades.schema_arrow.equals(schema_for('aggTrades',pa))):
                    raise FeatureError('canonical family schema mismatch')
                trade_stream=iter(_iter_rows(trades,['agg_trade_id','transact_time','quantity','price','is_buyer_maker']))
                nxt=next(trade_stream,None)
                prev_id=-1;prev_time=None;prev_bar=None;count=0
                for bar in _iter_rows(bars,['open_time','close_time','close']):
                    stamp=bar['open_time'];end=stamp+timedelta(minutes=1)
                    if (stamp<first or end>last or bar['close_time']!=end-timedelta(milliseconds=1)
                            or prev_bar is not None and stamp!=prev_bar+timedelta(minutes=1)):
                        raise FeatureError('invalid or gapped 1m grid')
                    px=bar['close']
                    if not isinstance(px,(int,float)) or not math.isfinite(px) or px<=0:
                        raise FeatureError('non-finite/invalid C1 close')
                    prev_bar=stamp;count+=1
                    if count>self.MAX_BARS:
                        raise FeatureError('minute limit exceeded')
                    b=s=0.0;events=0
                    while nxt is not None and nxt['transact_time']<end:
                        trade_time=nxt['transact_time'];agg_id=nxt['agg_trade_id']
                        if (trade_time<stamp or trade_time<first or trade_time>=last or
                                type(agg_id) is not int or agg_id<=prev_id or
                                prev_time is not None and trade_time<prev_time or
                                not isinstance(nxt['quantity'],(float,int)) or
                                not math.isfinite(nxt['quantity']) or nxt['quantity']<=0 or
                                not isinstance(nxt['price'],(float,int)) or
                                not math.isfinite(nxt['price']) or nxt['price']<=0 or
                                type(nxt['is_buyer_maker']) is not bool):
                            raise FeatureError('invalid/order-gapped C2 aggTrade')
                        if nxt['is_buyer_maker']:
                            s=math.fsum((s,float(nxt['quantity'])))
                        else:
                            b=math.fsum((b,float(nxt['quantity'])))
                        prev_id=agg_id;prev_time=trade_time;events+=1
                        nxt=next(trade_stream,None)
                    seen=end+timedelta(milliseconds=assumed_latency_ms)
                    if seen>last:
                        # The last observed minute of Development becomes
                        # unusable if hypothetical availability is in Validation.
                        continue
                    if events:
                        delta=b-s;balance=delta/(b+s)
                        status='C2_PRESENT'
                    else:
                        b=s=delta=balance=None
                        status='C2_UNKNOWN'
                    rows.append(MinuteFeature(stamp,seen,float(px),status,b,s,delta,balance,events))
                if require_complete_month:
                    expected=int((last-first).total_seconds()//60)
                    if count!=expected or prev_bar!=last-timedelta(minutes=1):
                        raise FeatureError('C1 month not complete; no gap filling')
                if nxt is not None:
                    # Any trailing C2 event in the same canonical month is
                    # incompatible with a C1 grid truncated at the source tail.
                    raise FeatureError('C2 event outside C1 month coverage')
        except FeatureError:raise
        except (OSError,ValueError,TypeError,pa.ArrowException) as e:
            raise FeatureError('feature conversion failed') from e
        _verify_staging(c1,'klines');_verify_staging(c2,'aggTrades')
        return tuple(rows)
