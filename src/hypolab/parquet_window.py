"""Strict bounded Parquet windows over *already authenticated* staged files.

No direct Drive paths, grant bypass, unbounded materialization or role crossing.
The caller must separately have completed owner authorization and a durable
READ_STARTED record before staging any real source bytes.
"""
from __future__ import annotations
from datetime import datetime
import hashlib
import math
from pathlib import Path

from .contracts import Role, SPLITS, require_utc
from .staging import StagedPartition, StageError


class ParquetWindowError(RuntimeError):
    pass


_FIELDS = {
    'klines': ('open_time','open','high','low','close','volume','close_time',
               'quote_volume','count','taker_buy_base','taker_buy_quote','ignore'),
    'aggTrades': ('agg_trade_id','price','quantity','first_trade_id',
                  'last_trade_id','transact_time','is_buyer_maker'),
    'bookDepth': ('snapshot_time','percentage','depth','notional'),
}
_TS = {'klines': ('open_time','close_time'),
       'aggTrades':('transact_time',),'bookDepth':('snapshot_time',)}
_STAMP = {name:stamps[0] for name,stamps in _TS.items()}


def schema_for(family,pa):
    if family not in _FIELDS:
        raise ParquetWindowError('unknown family')
    def datatype(name):
        if name in _TS[family]:return pa.timestamp('ms',tz='UTC')
        if name in ('count','agg_trade_id','first_trade_id','last_trade_id'):return pa.int64()
        if name=='is_buyer_maker':return pa.bool_()
        return pa.float64()
    return pa.schema([pa.field(n,datatype(n),nullable=False) for n in _FIELDS[family]])


def _file_digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as fh:
        for part in iter(lambda:fh.read(4*1024*1024),b''):
            h.update(part)
    return h.hexdigest()


class ParquetWindowReader:
    MAX_ROWS=100_000
    BATCH_ROWS=16_384

    def read(self,stage:StagedPartition, *, start:datetime,end:datetime,
             columns:tuple[str,...],max_rows:int=10_000,role:Role=Role.DEVELOPMENT):
        try:import pyarrow as pa
        except ImportError as exc:raise ParquetWindowError('install optional pyarrow runtime') from exc
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
        if not stage.source_verified:
            raise ParquetWindowError('unverified stage')
        require_utc(start);require_utc(end)
        identity=stage.identity
        if role is not Role.DEVELOPMENT or not start<end or not SPLITS[role].contains(start,end):
            raise ParquetWindowError('protected or invalid requested range')
        low,high=identity.bounds
        if not low<=start<end<=high:
            raise ParquetWindowError('request exceeds exact partition')
        if (type(max_rows) is not int or not 1<=max_rows<=self.MAX_ROWS or
            type(columns) is not tuple or not columns or len(set(columns))!=len(columns) or
            any(name not in _FIELDS[identity.family] for name in columns)):
            raise ParquetWindowError('invalid columns or row cap')
        p=stage.parquet_path
        if p.is_symlink() or not p.is_file() or _file_digest(p)!=identity.canonical_sha256:
            raise ParquetWindowError('stage changed before reading')
        expected=schema_for(identity.family,pa)
        stamp=_STAMP[identity.family]
        selection=tuple(dict.fromkeys((*columns,stamp)))
        batches=[];total=0
        try:
            with pq.ParquetFile(p) as parquet:
                if not parquet.schema_arrow.equals(expected):
                    raise ParquetWindowError('exact canonical schema drift')
                for batch in parquet.iter_batches(batch_size=self.BATCH_ROWS,columns=list(selection)):
                    ts=batch.column(batch.schema.get_field_index(stamp))
                    mask=pc.and_(pc.greater_equal(ts,pa.scalar(start,type=expected.field(stamp).type)),
                                 pc.less(ts,pa.scalar(end,type=expected.field(stamp).type)))
                    out=pa.Table.from_batches([batch]).filter(mask).select(list(columns))
                    if out.num_rows:
                        total+=out.num_rows
                        if total>max_rows:
                            raise ParquetWindowError('row cap exceeded before output')
                        batches.extend(out.to_batches())
        except ParquetWindowError:raise
        except (ValueError,TypeError,OSError,pa.ArrowException) as exc:
            raise ParquetWindowError('parquet read failed') from exc
        if _file_digest(p)!=identity.canonical_sha256:
            raise ParquetWindowError('stage changed during read')
        return pa.Table.from_batches(batches,schema=pa.schema([expected.field(n) for n in columns]))
