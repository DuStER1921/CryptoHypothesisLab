"""One-owner-approved month: full C1 OHLCV + sparse 5/15/60s C2 features.

No Drive access, no implicit source authorization, no execution claims. Source
inputs must be *already* owner-authorized, staged, SHA-verified and frozen-pinned.
The file is private; its features are actual historical-derived market data.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .airgap_bundle import _month_bounds
from .c1c2_features import _verify_staging
from .c2_microstructure import iter_verified_staged_c2_micro_buckets
from .frozen import PinnedPartition
from .parquet_window import schema_for
from .staging import StagedPartition


class MicroBundleError(ValueError):
    pass


SCHEMA = 'HYPOLAB_MICROSTRUCTURE_DEVELOPMENT_V2'
RESOLUTIONS = (5, 15, 60)
C1_FIELDS = ('open_time_ms','available_at_ms','open','high','low','close','volume',
             'quote_volume','count','taker_buy_base','taker_buy_quote')
FLOW_FIELDS = ('start_ms','available_at_ms','aggregate_rows','taker_buy_base',
               'taker_sell_base','taker_delta_base','taker_imbalance',
               'executed_quote_notional','max_agg_trade_notional_quote',
               'max_buy_agg_trade_notional_quote','max_sell_agg_trade_notional_quote',
               'first_trade_price','last_trade_price','min_trade_price',
               'max_trade_price','first_to_last_trade_bps',
               'longest_same_taker_side_agg_rows','max_interarrival_ms',
               'first_taker_side','last_taker_side')
FILES = ('manifest.json','c1_1m.csv','c2_5s.csv','c2_15s.csv','c2_60s.csv')
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_C1_BYTES = 20 * 1024 * 1024
MAX_C2_BYTES = {5: 180*1024*1024, 15: 90*1024*1024, 60: 35*1024*1024}
MAX_ROWS = {5: 31*24*720,15: 31*24*240,60: 31*24*60}
MAX_MANIFEST_BYTES = 4096


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):
            h.update(b)
    return h.hexdigest()


def _ms(t):
    if type(t) is not datetime or t.tzinfo is None or t.utcoffset()!=timedelta(0):
        raise MicroBundleError('C1 timestamp not UTC')
    if t.microsecond%1000:
        raise MicroBundleError('C1 timestamp not millisecond-precise')
    return int((t-datetime(1970,1,1,tzinfo=timezone.utc)).total_seconds()*1000)


def _float(x, *, positive=False, nonnegative=False):
    if type(x) not in (int,float,str):
        raise MicroBundleError('invalid numeric type')
    try:y=float(x)
    except (TypeError,ValueError,OverflowError) as e:raise MicroBundleError('invalid numeric value') from e
    if not math.isfinite(y) or positive and y<=0 or nonnegative and y<0:
        raise MicroBundleError('nonfinite/invalid numeric')
    return y


def _int(x):
    try:v=int(x)
    except (TypeError,ValueError,OverflowError) as e:raise MicroBundleError('bad integer') from e
    if str(v)!=str(x):raise MicroBundleError('noncanonical integer')
    return v


def _close(a,b,*,atol=1e-7):
    return math.isclose(a,b,rel_tol=1e-10,abs_tol=atol)


def _check_c1_row(v,prev,lo,hi,latency):
    if set(v)!=set(C1_FIELDS):raise MicroBundleError('C1 columns mismatch')
    t=_int(v['open_time_ms']);av=_int(v['available_at_ms'])
    if (t<lo or t+60000>hi or t%60000 or av!=t+60000+latency
            or prev is not None and t!=prev+60000):
        raise MicroBundleError('C1 grid/availability boundary mismatch')
    o,h,l,c=(_float(v[x],positive=True) for x in ('open','high','low','close'))
    if h<max(o,c,l) or l>min(o,c,h):raise MicroBundleError('C1 OHLC invalid')
    vol=_float(v['volume'],nonnegative=True)
    quote=_float(v['quote_volume'],nonnegative=True)
    buy=_float(v['taker_buy_base'],nonnegative=True)
    buyquote=_float(v['taker_buy_quote'],nonnegative=True)
    count=_int(v['count'])
    if (count<0 or buy>vol+max(1e-7,vol*1e-8) or buyquote>quote+max(1e-7,quote*1e-8)):
        raise MicroBundleError('C1 volume/count invariant')
    if vol==0 and (quote>1e-9 or count>0 or buy>0 or buyquote>0):
        raise MicroBundleError('C1 unexpected zero-volume bar')
    return t


def _check_flow_row(v,s,prev,lo,hi,latency):
    if set(v)!=set(FLOW_FIELDS):raise MicroBundleError('C2 columns mismatch')
    t=_int(v['start_ms']);av=_int(v['available_at_ms']);n=_int(v['aggregate_rows'])
    if (t<lo or t+s*1000>hi or t%(s*1000) or
            prev is not None and t<=prev or av!=t+s*1000+latency or n<1):
        raise MicroBundleError('invalid sparse C2 bin boundary/count')
    for x in ('longest_same_taker_side_agg_rows','max_interarrival_ms'):
        if _int(v[x])<0:raise MicroBundleError('invalid streak/gap')
    if _int(v['longest_same_taker_side_agg_rows'])>n:
        raise MicroBundleError('streak longer than events')
    for x in ('first_taker_side','last_taker_side'):
        if _int(v[x]) not in (-1,1):raise MicroBundleError('invalid taker side')
    buy=_float(v['taker_buy_base'],nonnegative=True)
    sell=_float(v['taker_sell_base'],nonnegative=True)
    delta=_float(v['taker_delta_base'])
    imb=_float(v['taker_imbalance'])
    total=buy+sell
    if total<=0 or not _close(delta,buy-sell) or not _close(imb,delta/total,atol=1e-9):
        raise MicroBundleError('C2 imbalance/delta mismatch')
    price=[_float(v[x],positive=True) for x in ('first_trade_price','last_trade_price','min_trade_price','max_trade_price')]
    fst,last,mn,mx=price
    if not mn<=fst<=mx or not mn<=last<=mx:raise MicroBundleError('C2 price bounds mismatch')
    if not _close(_float(v['first_to_last_trade_bps']), (last/fst-1)*10000, atol=1e-7):
        raise MicroBundleError('C2 price impact mismatch')
    quote=_float(v['executed_quote_notional'],positive=True)
    maximum=_float(v['max_agg_trade_notional_quote'],positive=True)
    buymax=_float(v['max_buy_agg_trade_notional_quote'],nonnegative=True)
    sellmax=_float(v['max_sell_agg_trade_notional_quote'],nonnegative=True)
    if (maximum>quote+max(.0001,quote*1e-8) or
            not _close(max(buymax,sellmax),maximum,atol=.0001)):
        raise MicroBundleError('C2 max aggregate notional invalid')
    return t


def _write_csv(path,columns,iterator,validator):
    count=0;previous=None
    with path.open('x',newline='',encoding='utf-8') as f:
        os.chmod(path,0o600)
        writer=csv.DictWriter(f,fieldnames=columns,lineterminator='\n')
        writer.writeheader()
        for row in iterator:
            normalized={key:str(row[key]) for key in columns}
            previous=validator(normalized,previous)
            writer.writerow(normalized)
            count+=1
        f.flush();os.fsync(f.fileno())
    return count


def _iter_verified_c1(stage,month,latency):
    import pyarrow as pa
    import pyarrow.parquet as pq
    with pq.ParquetFile(stage.parquet_path) as source:
        if not source.schema_arrow.equals(schema_for('klines',pa)):
            raise MicroBundleError('C1 schema mismatch')
        columns=('open_time','close_time','open','high','low','close','volume','quote_volume',
                 'count','taker_buy_base','taker_buy_quote')
        for batch in source.iter_batches(batch_size=16384,columns=list(columns)):
            for row in batch.to_pylist():
                t=_ms(row['open_time'])
                if _ms(row['close_time'])!=t+59999:
                    raise MicroBundleError('C1 close_time mismatch')
                yield {'open_time_ms':t,'available_at_ms':t+60000+latency,
                       **{k:row[k] for k in C1_FIELDS if k not in ('open_time_ms','available_at_ms')}}


def build_micro_bundle_from_staged(c1:StagedPartition,c2:StagedPartition,*,
                                    scratch_dir:Path,target:Path,
                                    assumed_latency_ms:int,producer_code_commit:str,
                                    universe_sha256:str,calendar_sha256:str,
                                    start_event_sha256:str)->dict:
    """Build private owner-only ZIP, C2 canonical Parquet iterated **once**.

    Must run *after* distinct owner approval and a recorded start event. No
    function here opens Drive, grants access or modifies the frozen source.
    """
    import re
    if (type(c1) is not StagedPartition or type(c2) is not StagedPartition or
            c1.identity.family!='klines' or c2.identity.family!='aggTrades' or
            c1.identity.period!=c2.identity.period):
        raise MicroBundleError('exact matched C1/C2 staged source required')
    for pin in (universe_sha256,calendar_sha256,start_event_sha256):
        if type(pin) is not str or len(pin)!=64 or any(ch not in '0123456789abcdef' for ch in pin):
            raise MicroBundleError('missing SHA-256 frozen/owner event provenance')
    if (type(assumed_latency_ms) is not int or not 1<=assumed_latency_ms<=5000 or
            type(producer_code_commit) is not str or
            not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',producer_code_commit)):
        raise MicroBundleError('invalid latency/code pin')
    month=c1.identity.period
    lo,hi=_month_bounds(month)
    if (lo,hi)!=tuple(_ms(t) for t in c1.identity.bounds) or c2.identity.bounds!=c1.identity.bounds:
        raise MicroBundleError('frozen month bounds mismatch')
    scratch=Path(scratch_dir);target=Path(target)
    if (os.name!='posix' or not scratch.is_dir() or scratch.is_symlink() or
            scratch.stat().st_mode&0o077 or target.parent.is_symlink() or
            target.parent.stat().st_mode&0o077 or target.exists()):
        raise MicroBundleError('create-only private local POSIX scratch and target required')
    _verify_staging(c1,'klines');_verify_staging(c2,'aggTrades')
    temp=Path(tempfile.mkdtemp(prefix='hypolab-micro-',dir=scratch))
    counts={}
    try:
        c1_path=temp/'c1_1m.csv'
        counts['c1_1m']=_write_csv(c1_path,C1_FIELDS,
            _iter_verified_c1(c1,month,assumed_latency_ms),
            lambda row,prev:_check_c1_row(row,prev,lo,hi,assumed_latency_ms))
        if counts['c1_1m']!=(hi-lo)//60000:
            raise MicroBundleError('incomplete C1 month; fail closed')
        import contextlib
        with contextlib.ExitStack() as stack:
            writers={};file_handles={}
            for sec in RESOLUTIONS:
                path=temp/f'c2_{sec}s.csv'
                fh=stack.enter_context(path.open('x',encoding='utf-8',newline=''))
                os.chmod(path,0o600)
                f=csv.DictWriter(fh,fieldnames=FLOW_FIELDS,lineterminator='\n')
                f.writeheader()
                writers[sec]=f;file_handles[sec]=fh
                counts[f'c2_{sec}s']=0
            prev={s:None for s in RESOLUTIONS}
            for sec,b in iter_verified_staged_c2_micro_buckets(c2):
                row={'start_ms':b['start_ms'],
                     'available_at_ms':b['end_ms_exclusive']+assumed_latency_ms,
                     **{k:b[k] for k in FLOW_FIELDS if k not in ('start_ms','available_at_ms')}}
                normalized={k:str(row[k]) for k in FLOW_FIELDS}
                prev[sec]=_check_flow_row(normalized,sec,prev[sec],lo,hi,assumed_latency_ms)
                writers[sec].writerow(normalized)
                counts[f'c2_{sec}s']+=1
                if counts[f'c2_{sec}s']>MAX_ROWS[sec]:
                    raise MicroBundleError('unexpected C2 bucket excess')
            for fh in file_handles.values():
                fh.flush();os.fsync(fh.fileno())
        if not all(counts[f'c2_{s}s']>0 for s in RESOLUTIONS):
            raise MicroBundleError('C2 data absent; refuse empty observed-bucket archive')
        _verify_staging(c1,'klines');_verify_staging(c2,'aggTrades')
        hashes={name:_sha(temp/name) for name in FILES[1:]}
        sizes={name:(temp/name).stat().st_size for name in FILES[1:]}
        if sizes['c1_1m.csv']>MAX_C1_BYTES or any(sizes[f'c2_{s}s.csv']>MAX_C2_BYTES[s] for s in RESOLUTIONS):
            raise MicroBundleError('generated feature file exceeds cap')
        manifest={'schema':SCHEMA,'role':'DEVELOPMENT','month':month,
                  'producer_code_commit':producer_code_commit,
                  'frozen_universe_sha256':universe_sha256,
                  'frozen_calendar_sha256':calendar_sha256,
                  'start_event_sha256':start_event_sha256,
                  'c1_sha256':c1.identity.canonical_sha256,
                  'c2_sha256':c2.identity.canonical_sha256,
                  'assumed_latency_ms':assumed_latency_ms,'counts':counts,
                  'files_sha256':hashes,'files_bytes':sizes,
                  'c3_status':'NOT_INCLUDED_UNKNOWN',
                  'price_basis':'C1 candle OHLCV / C2 executed aggregate trade prices; no bid ask L2',
                  'latency_basis':'hypothetical post-bucket completion, not observed data feed delay'}
        raw=json.dumps(manifest,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        if len(raw)>MAX_MANIFEST_BYTES:raise MicroBundleError('manifest exceeds cap')
        (temp/'manifest.json').write_bytes(raw)
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix='hypolab-micro-',suffix='.zip',delete=False) as out:
            partial=Path(out.name)
        os.chmod(partial,0o600)
        try:
            with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5,allowZip64=False) as z:
                for name in FILES:z.write(temp/name,arcname=name)
            if partial.stat().st_size>MAX_ARCHIVE_BYTES:raise MicroBundleError('compressed archive exceeds cap')
            # Target exists? Atomic hardlink create-only; no overwrite or rename.
            os.link(partial,target)
            fd=os.open(target,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            partial.unlink(missing_ok=True)
        return {'path':target,'sha256':_sha(target),'counts':counts,'month':month}
    finally:
        shutil.rmtree(temp)


def inspect_micro_bundle(path:Path,*,collect_5s=True):
    """Fully validate private archive before returning C1 and optional 5s rows.

    Sparse 5/15/60 buckets are UNKNOWN in gaps; never fill synthetic zeros.
    Does not trust ZIP filenames, manifest or uncompressed sizes by themselves.
    """
    p=Path(path)
    if not p.is_file() or p.is_symlink() or not 0<p.stat().st_size<=MAX_ARCHIVE_BYTES:
        raise MicroBundleError('missing/unsafe or oversized private bundle')
    c1=[];flows5=[]
    cross={15:{},60:{}}
    with zipfile.ZipFile(p,'r') as z:
        info=z.infolist()
        if ([x.filename for x in info]!=list(FILES) or
                any(x.is_dir() or x.compress_type!=zipfile.ZIP_DEFLATED or
                    x.flag_bits&1 for x in info)):
            raise MicroBundleError('unexpected archive layout or encryption')
        if info[0].file_size>MAX_MANIFEST_BYTES:raise MicroBundleError('oversized manifest')
        try:manifest=json.loads(z.read('manifest.json'))
        except (ValueError,UnicodeDecodeError,RuntimeError) as e:raise MicroBundleError('bad manifest') from e
        if (type(manifest) is not dict or manifest.get('schema')!=SCHEMA or
                manifest.get('role')!='DEVELOPMENT' or manifest.get('c3_status')!='NOT_INCLUDED_UNKNOWN' or
                type(manifest.get('assumed_latency_ms')) is not int or
                not 1<=manifest['assumed_latency_ms']<=5000 or
                any(not isinstance(manifest.get(x),str) or len(manifest[x])!=64 or
                    any(ch not in '0123456789abcdef' for ch in manifest[x])
                    for x in ('frozen_universe_sha256','frozen_calendar_sha256',
                              'start_event_sha256','c1_sha256','c2_sha256')) or
                type(manifest.get('producer_code_commit')) is not str or
                len(manifest['producer_code_commit']) not in (40,64) or
                any(ch not in '0123456789abcdef' for ch in manifest['producer_code_commit'])):
            raise MicroBundleError('manifest schema/provenance invalid')
        lo,hi=_month_bounds(manifest.get('month'))
        latency=manifest['assumed_latency_ms']
        counts=manifest.get('counts');hashes=manifest.get('files_sha256');sizes=manifest.get('files_bytes')
        if not all(type(x) is dict for x in (counts,hashes,sizes)) or set(counts)!={'c1_1m','c2_5s','c2_15s','c2_60s'} or set(hashes)!=set(FILES[1:]) or set(sizes)!=set(FILES[1:]):
            raise MicroBundleError('manifest file index mismatch')
        for i,name in enumerate(FILES[1:],start=1):
            sec=None if i==1 else RESOLUTIONS[i-2]
            if info[i].file_size> (MAX_C1_BYTES if sec is None else MAX_C2_BYTES[sec]):
                raise MicroBundleError('uncompressed feature stream exceeds cap')
            if type(sizes[name]) is not int or sizes[name]!=info[i].file_size:
                raise MicroBundleError('declared length mismatch')
            if type(hashes[name]) is not str or len(hashes[name])!=64 or any(ch not in '0123456789abcdef' for ch in hashes[name]):
                raise MicroBundleError('invalid declared source digest')
            h=hashlib.sha256();n=0;prev=None
            with z.open(name) as opened:
                reader=io.TextIOWrapper(opened,encoding='utf-8',newline='')
                # csv.reader avoids any internal reordering of dictionary columns.
                rawiter=csv.reader(reader)
                if tuple(next(rawiter,())) != (C1_FIELDS if sec is None else FLOW_FIELDS):
                    raise MicroBundleError('column header mismatch')
                # Do not read whole file into memory. Recompute canonical row bytes
                # (writer always emits newline, no CRLF and no quoting for numeric).
                header=','.join(C1_FIELDS if sec is None else FLOW_FIELDS)+'\n'
                h.update(header.encode())
                for values in rawiter:
                    if len(values)!=(len(C1_FIELDS) if sec is None else len(FLOW_FIELDS)):
                        raise MicroBundleError('malformed CSV data')
                    v=dict(zip(C1_FIELDS if sec is None else FLOW_FIELDS,values))
                    prev=(_check_c1_row(v,prev,lo,hi,latency) if sec is None
                          else _check_flow_row(v,sec,prev,lo,hi,latency))
                    if sec is None:
                        c1.append(v)
                    elif sec==5:
                        if collect_5s:flows5.append(v)
                        for res in (15,60):
                            bucket=prev-prev%(res*1000)
                            entry=cross[res].setdefault(bucket,{'count':0,'buy':0.,
                                'sell':0.,'quote':0.,'first':None,'last':None,
                                'min':float('inf'),'max':0.,'maxagg':0.})
                            entry['count']+=_int(v['aggregate_rows'])
                            entry['buy']+=float(v['taker_buy_base'])
                            entry['sell']+=float(v['taker_sell_base'])
                            entry['quote']+=float(v['executed_quote_notional'])
                            if entry['first'] is None:
                                entry['first']=float(v['first_trade_price'])
                            entry['last']=float(v['last_trade_price'])
                            entry['min']=min(entry['min'],float(v['min_trade_price']))
                            entry['max']=max(entry['max'],float(v['max_trade_price']))
                            entry['maxagg']=max(entry['maxagg'],float(v['max_agg_trade_notional_quote']))
                    else:
                        entry=cross[sec].pop(prev,None)
                        if entry is None or entry['count']!=_int(v['aggregate_rows']) or any(
                            not _close(entry[key],float(v[field]),atol=1e-5) for key,field in (
                                ('buy','taker_buy_base'),('sell','taker_sell_base'),
                                ('quote','executed_quote_notional'),
                                ('first','first_trade_price'),('last','last_trade_price'),
                                ('min','min_trade_price'),('max','max_trade_price'),
                                ('maxagg','max_agg_trade_notional_quote'))):
                            raise MicroBundleError('C2 5/15/60s aggregation inconsistency')
                    h.update((','.join(values)+'\n').encode('utf-8'))
                    n+=1
                    if n > ((hi-lo)//60000 if sec is None else MAX_ROWS[sec]):
                        raise MicroBundleError('feature row count overflow')
            if h.hexdigest()!=hashes[name]:
                raise MicroBundleError('CSV SHA-256 mismatch')
            if type(counts['c1_1m' if sec is None else f'c2_{sec}s']) is not int or counts['c1_1m' if sec is None else f'c2_{sec}s']!=n:
                raise MicroBundleError('row count mismatch')
            if sec is None and (n!=(hi-lo)//60000 or prev!=hi-60000):
                raise MicroBundleError('incomplete C1 month')
        if cross[15] or cross[60]:
            raise MicroBundleError('incomplete cross-resolution C2 coverage')
        if z.testzip() is not None:raise MicroBundleError('ZIP CRC failure')
    return manifest,tuple(c1),tuple(flows5)
