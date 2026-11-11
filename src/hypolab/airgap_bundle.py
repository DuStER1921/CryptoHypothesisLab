"""Portable DEVELOPMENT-only C1+C2 minute-feature bundles for clean Colab VMs.

No Drive import, token, source mount, cloud upload, grant issuer or stored labels.
The package is an UNTRUSTED input: verify ZIP structure, SHA, row schema, full
UTC ordering, availability and Development split before any discovery use.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import hashlib
import io
import json
import math
import re
import zipfile

from .c1c2_features import MinuteFeature
from .contracts import Role, SPLITS


class BundleError(ValueError):
    pass

MAX_ROWS = 44_640
MAX_CSV = 12 * 1024 * 1024
MAX_ARCHIVE = 5 * 1024 * 1024
FIELD_NAMES = ('open_time_ms','available_at_ms','close','c2_status','taker_buy_base',
               'taker_sell_base','taker_delta_base','taker_imbalance','c2_aggregate_rows')
SHA_PATTERN = re.compile(r'[a-f0-9]{64}\Z')
PERIOD_PATTERN = re.compile(r'20(2[0-4])-(0[1-9]|1[0-2])\Z')


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canonical(v) -> bytes:
    return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _millis(t: datetime) -> int:
    if (type(t) is not datetime or t.tzinfo is None or t.utcoffset()!=timedelta(0)
            or t.microsecond % 1000 != 0):
        raise BundleError('datetime must be UTC at millisecond precision')
    return int((t-datetime(1970,1,1,tzinfo=timezone.utc)).total_seconds()*1000)


def _month_bounds(period: str) -> tuple[int,int]:
    if type(period) is not str or PERIOD_PATTERN.fullmatch(period) is None:
        raise BundleError('only full Development months 2020-2024 allowed')
    year, month = map(int, period.split('-'))
    lo = datetime(year,month,1,tzinfo=timezone.utc)
    hi = datetime(year+(month==12),month%12+1,1,tzinfo=timezone.utc)
    return _millis(lo), _millis(hi)


def _decimal(value, *, positive=False, nonnegative=False):
    try:
        number=float(value)
    except (TypeError,ValueError,OverflowError) as e:
        raise BundleError('non-numeric feature') from e
    if not math.isfinite(number) or positive and number<=0 or nonnegative and number<0:
        raise BundleError('non-finite/invalid feature')
    return number


def _validate_row(row:dict, month:str, previous:int|None):
    lo,hi=_month_bounds(month)
    if type(row) is not dict or set(row)!=set(FIELD_NAMES):
        raise BundleError('unknown/missing feature column')
    try:
        op=int(row['open_time_ms']); avail=int(row['available_at_ms']); count=int(row['c2_aggregate_rows'])
    except (TypeError,ValueError) as e:
        raise BundleError('invalid timestamp/count') from e
    if (str(op)!=str(row['open_time_ms']) or str(avail)!=str(row['available_at_ms'])
            or str(count)!=str(row['c2_aggregate_rows'])):
        raise BundleError('noncanonical timestamps/count')
    if (op<lo or op+60_000>hi or op%60_000!=0 or
            previous is not None and op!=previous+60_000 or
            avail<op+60_001 or avail>hi or
            avail>=_millis(SPLITS[Role.DEVELOPMENT].end) or count<0):
        raise BundleError('time boundary, gap or lookahead violation')
    close=_decimal(row['close'],positive=True)
    state=row['c2_status']
    if state=='C2_UNKNOWN':
        if count or any(row[n]!='' for n in FIELD_NAMES[4:8]):
            raise BundleError('unknown minute must not contain fabricated trades')
    elif state=='C2_PRESENT':
        if count<=0 or any(row[n]=='' for n in FIELD_NAMES[4:8]):
            raise BundleError('present minute requires nonempty aggregates')
        buy=_decimal(row['taker_buy_base'],nonnegative=True)
        sell=_decimal(row['taker_sell_base'],nonnegative=True)
        delta=_decimal(row['taker_delta_base'])
        imbalance=_decimal(row['taker_imbalance'])
        if buy+sell<=0 or not math.isclose(delta,buy-sell,rel_tol=1e-11,abs_tol=1e-9) or (
            not -1 <=imbalance<=1 or not math.isclose(imbalance,delta/(buy+sell),rel_tol=1e-11,abs_tol=1e-11)):
            raise BundleError('inconsistent trade imbalance')
    else:
        raise BundleError('unknown C2 status')
    return op


def _feature_row(r:MinuteFeature):
    if type(r) is not MinuteFeature:
        raise BundleError('expected verified MinuteFeature row')
    return {
        'open_time_ms': str(_millis(r.open_time)),
        'available_at_ms':str(_millis(r.assumed_available_at)),
        'close':repr(float(r.close)),
        'c2_status':r.c2_status,
        'taker_buy_base':'' if r.taker_buy_base is None else repr(float(r.taker_buy_base)),
        'taker_sell_base':'' if r.taker_sell_base is None else repr(float(r.taker_sell_base)),
        'taker_delta_base':'' if r.taker_delta_base is None else repr(float(r.taker_delta_base)),
        'taker_imbalance':'' if r.taker_imbalance is None else repr(float(r.taker_imbalance)),
        'c2_aggregate_rows':str(r.c2_aggregate_rows),
    }


def _pack_member(archive:zipfile.ZipFile, name:str, raw:bytes):
    info=zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
    info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=(0o600&0xffff)<<16
    archive.writestr(info,raw)


def create_bundle(*, month:str, features:tuple[MinuteFeature,...],
                  universe_sha256:str,calendar_sha256:str,
                  c1_sha256:str,c2_sha256:str,assumed_latency_ms:int,
                  producer_code_commit:str) -> bytes:
    """Build a bounded portable package IN MEMORY. No file/Drive writes."""
    lo,hi=_month_bounds(month)
    if (type(features) is not tuple or not 1<=len(features)<=MAX_ROWS or
            type(assumed_latency_ms) is not int or not 1<=assumed_latency_ms<=600000 or
            any(type(v) is not str or SHA_PATTERN.fullmatch(v) is None
                for v in (universe_sha256,calendar_sha256,c1_sha256,c2_sha256)) or
            type(producer_code_commit) is not str or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',producer_code_commit)):
        raise BundleError('invalid bundle specification')
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=FIELD_NAMES,lineterminator='\n')
    writer.writeheader()
    prev=None
    for r in features:
        row=_feature_row(r)
        op=_validate_row(row,month,prev)
        if int(row['available_at_ms']) != op+60000+assumed_latency_ms:
            raise BundleError('latency overlay mismatch')
        writer.writerow(row)
        prev=op
    csv_raw=output.getvalue().encode('utf-8')
    if len(csv_raw)>MAX_CSV:
        raise BundleError('output exceeds bounded size')
    manifest={'schema':'HYPOLAB_DEVELOPMENT_MINUTES_V1','role':'DEVELOPMENT',
              'month':month,'universe_sha256':universe_sha256,
              'calendar_sha256':calendar_sha256,'c1_sha256':c1_sha256,
              'c2_sha256':c2_sha256,'producer_code_commit':producer_code_commit,
              'assumed_latency_ms':assumed_latency_ms,
              'count':len(features),'csv_sha256':_digest(csv_raw),
              'first_open_time_ms':int(_feature_row(features[0])['open_time_ms']),
              'last_open_time_ms':int(_feature_row(features[-1])['open_time_ms']),
              'notes':'Hypothetical availability, not observed exchange-to-trader latency. No labels or protected data.'}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        _pack_member(z,'manifest.json',_canonical(manifest))
        _pack_member(z,'features.csv',csv_raw)
    result=buffer.getvalue()
    if len(result)>MAX_ARCHIVE:
        raise BundleError('compressed output exceeds cap')
    return result


def inspect_bundle(payload:bytes) -> tuple[dict,tuple[dict,...]]:
    """Validate the entire packet; never return partial rows after an error."""
    if type(payload) is not bytes or not 0<len(payload)<=MAX_ARCHIVE:
        raise BundleError('missing or oversized archive')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            infos=z.infolist()
            if (len(infos)!=2 or [i.filename for i in infos]!=['manifest.json','features.csv']
                    or any(i.is_dir() or i.file_size>MAX_CSV or i.compress_type!=zipfile.ZIP_DEFLATED for i in infos)):
                raise BundleError('unexpected archive members')
            manifest_raw=z.read('manifest.json');csv_raw=z.read('features.csv')
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError) as e:
        raise BundleError('invalid or corrupt bundle') from e
    if len(manifest_raw)>8192 or len(csv_raw)>MAX_CSV:
        raise BundleError('oversized manifest or feature table')
    try:
        meta=json.loads(manifest_raw)
    except (UnicodeDecodeError,ValueError) as e:
        raise BundleError('invalid manifest JSON') from e
    keys={'schema','role','month','universe_sha256','calendar_sha256',
          'c1_sha256','c2_sha256','producer_code_commit','assumed_latency_ms','count','csv_sha256',
          'first_open_time_ms','last_open_time_ms','notes'}
    if (type(meta) is not dict or set(meta)!=keys or manifest_raw!=_canonical(meta)
            or meta['schema']!='HYPOLAB_DEVELOPMENT_MINUTES_V1' or meta['role']!='DEVELOPMENT'
            or type(meta['count']) is not int or not 1<=meta['count']<=MAX_ROWS
            or type(meta['assumed_latency_ms']) is not int or not 1<=meta['assumed_latency_ms']<=600000
            or any(type(meta[x]) is not str or SHA_PATTERN.fullmatch(meta[x]) is None
                   for x in ('universe_sha256','calendar_sha256','c1_sha256','c2_sha256','csv_sha256'))
            or type(meta['producer_code_commit']) is not str
            or re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',meta['producer_code_commit']) is None
            or meta['notes']!='Hypothetical availability, not observed exchange-to-trader latency. No labels or protected data.'
            or _digest(csv_raw)!=meta['csv_sha256']):
        raise BundleError('invalid or modified bundle manifest')
    _month_bounds(meta['month'])
    try:
        reader=csv.DictReader(io.StringIO(csv_raw.decode('utf-8')))
        if tuple(reader.fieldnames or ())!=FIELD_NAMES:
            raise BundleError('wrong feature columns')
        prev=None; rows=[]
        for row in reader:
            if None in row or len(rows)>=meta['count']:
                raise BundleError('bad row width/count')
            op=_validate_row(row,meta['month'],prev)
            if int(row['available_at_ms']) != op+60000+meta['assumed_latency_ms']:
                raise BundleError('forged arrival assumption')
            rows.append(row);prev=op
        if (len(rows)!=meta['count'] or int(rows[0]['open_time_ms'])!=meta['first_open_time_ms'] or
                int(rows[-1]['open_time_ms'])!=meta['last_open_time_ms']):
            raise BundleError('manifest row/count/time mismatch')
    except (UnicodeDecodeError,csv.Error,ValueError,TypeError) as e:
        if isinstance(e,BundleError):raise
        raise BundleError('invalid feature CSV') from e
    return meta,tuple(rows)


def write_bundle_local_once(path:Path, payload:bytes) -> str:
    """Create only in Colab's local /content scratch, NEVER on mounted Drive."""
    import os
    path=Path(path)
    if (not str(path.resolve(strict=False)).startswith('/content/hypolab-packets/')
            or path.is_symlink() or path.parent.is_symlink()
            or not path.parent.is_dir()):
        raise BundleError('output must be inside /content/hypolab-packets')
    inspect_bundle(payload)
    try:
        flags=os.O_WRONLY|os.O_EXCL|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)
        fd=os.open(path,flags,0o600)
        with os.fdopen(fd,'wb') as out:
            out.write(payload);out.flush();os.fsync(out.fileno())
    except OSError as e:
        raise BundleError('cannot create output or file already exists') from e
    if _digest(path.read_bytes())!=_digest(payload):
        raise BundleError('local output readback mismatch')
    return _digest(payload)
