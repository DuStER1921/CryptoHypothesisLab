"""Create-only private C2 sub-5s packet from an *already* owner-approved stage.

No Drive/source mounting, grant issuance, replay, labels, or network I/O.
This code does not authenticate the operator's READ_STARTED reference.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
from datetime import datetime, timezone
import re
import stat
import tempfile
import zipfile

from .c2_sub5 import FEATURE, iter_verified_staged_c2_sub5_5s
from .staging import StagedPartition

PROTOCOL_SHA256 = 'aa3e6326f8d421cba317cb435316c69e233f4ccdbff54ed54ec5811514b5c839'
TRIAL_ID = 'BTC_C2_SUB5_V1_2024-12_M0_M1_P1'
SCHEMA = 'BTC_C2_SUB5_OFFLINE_PACKET_V1'
FIELDS = (
    'start_ms', 'first_transact_ms', 'last_transact_ms',
    'first_agg_trade_id', 'last_agg_trade_id', 'aggregate_rows',
    'taker_buy_base', 'taker_sell_base', 'executed_quote_notional',
    'first_trade_price', 'last_trade_price', 'min_trade_price', 'max_trade_price',
    'first_to_last_trade_bps', 'max_interarrival_ms',
    'longest_same_taker_side_agg_rows', 'max_agg_trade_notional_quote', FEATURE,
)
INTS = frozenset(('start_ms','first_transact_ms','last_transact_ms',
                  'first_agg_trade_id','last_agg_trade_id','aggregate_rows',
                  'max_interarrival_ms','longest_same_taker_side_agg_rows'))
MAX_ROWS = 31*24*720
MAX_CSV_BYTES = 256*1024*1024
MAX_PACKET_BYTES = 128*1024*1024
SHA = re.compile(r'[0-9a-f]{64}\Z')
DEC_START_MS = int(datetime(2024,12,1,tzinfo=timezone.utc).timestamp()*1000)
JAN_START_MS = int(datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)


class PacketError(ValueError):
    pass


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(4*1024*1024),b''):
            h.update(part)
    return h.hexdigest()


def _private_folder(folder):
    folder=Path(folder)
    if (os.name!='posix' or folder.is_symlink() or not folder.is_dir() or
            (folder.stat().st_mode & 0o077)):
        raise PacketError('private POSIX directory mode 0700 required')
    return folder


def _row_values(bucket):
    if type(bucket) is not dict or not set(FIELDS).issubset(bucket):
        raise PacketError('missing fixed C2 columns')
    out={}
    for col in FIELDS:
        val=bucket[col]
        if col == FEATURE and val is None:
            out[col]=''
        elif col in INTS:
            if type(val) is not int:
                raise PacketError('non-integer C2 value')
            out[col]=str(val)
        else:
            if type(val) not in (float,int) or not math.isfinite(val):
                raise PacketError('nonfinite C2 feature')
            out[col]=repr(val)
    return out


def _build_packet_from_buckets(buckets, *, private_dir, target_name,
                               source_sha256, owner_event_sha256,
                               code_sha256, synthetic=False):
    """Internal packet writer. Testable on synthetic buckets, no source opener.

    `owner_event_sha256` is a reference, not evidence of verified authorization.
    Caller must provide an externally approved StagedPartition when real data.
    """
    if (not target_name.endswith('.zip') or '/' in target_name or
            '\\' in target_name or target_name.startswith('.') or
            any(not isinstance(x,str) or not SHA.fullmatch(x)
                for x in (source_sha256,owner_event_sha256,code_sha256))):
        raise PacketError('invalid target or pinned source/evidence hash')
    private_dir=_private_folder(private_dir)
    target=private_dir/target_name
    if target.exists() or target.is_symlink():
        raise PacketError('immutable packet already exists')
    with tempfile.TemporaryDirectory(prefix='.sub5-',dir=private_dir) as tmp:
        tmp=Path(tmp)
        csv_path=tmp/'c2_5s.csv'
        count=0;previous=None;first=None
        with csv_path.open('w',encoding='ascii',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=FIELDS,lineterminator='\n')
            writer.writeheader()
            for bucket in buckets:
                row=_row_values(bucket)
                start=int(row['start_ms'])
                if (start%5000 or previous is not None and start<=previous or
                        start < DEC_START_MS or start >= JAN_START_MS):
                    raise PacketError('invalid 2024-12 UTC ordered C2 bucket')
                previous=start;first=start if first is None else first
                writer.writerow(row)
                count+=1
                if count>MAX_ROWS or handle.tell()>MAX_CSV_BYTES:
                    raise PacketError('bounded 5s packet size/count exceeded')
            handle.flush();os.fsync(handle.fileno())
        if count==0:
            raise PacketError('empty C2 stage packet')
        size=csv_path.stat().st_size
        if size>MAX_CSV_BYTES:
            raise PacketError('uncompressed C2 packet too large')
        manifest={
            'schema':SCHEMA,'month':'2024-12','trial_id':TRIAL_ID,
            'protocol_sha256':PROTOCOL_SHA256,'code_sha256':code_sha256,
            'source_c2_sha256':source_sha256,
            'owner_read_started_reference_sha256':owner_event_sha256,
            'owner_receipt_independently_authenticated':False,
            'synthetic':bool(synthetic),
            'csv_columns':list(FIELDS),'csv_bytes':size,
            'csv_sha256':_digest(csv_path),'rows':count,
            'first_bucket_ms':first,'last_bucket_ms':previous,
            'notes':'Private derived rows; not an executable fill or trading edge',
        }
        archive=tmp/'packet.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6,allowZip64=False) as z:
            z.writestr('manifest.json',_canonical(manifest))
            z.write(csv_path,arcname='c2_5s.csv')
        if archive.stat().st_size>MAX_PACKET_BYTES:
            raise PacketError('compressed packet cap exceeded')
        with archive.open('rb') as h:os.fsync(h.fileno())
        # Hard link is atomic create-only within the same private directory.
        # Failure never replaces an existing packet; temporary files are discarded.
        os.link(archive,target,follow_symlinks=False)
        dirfd=os.open(private_dir,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        return {'path':target,'sha256':_digest(target),'manifest':manifest}


def write_private_staged_packet(stage:StagedPartition,*,private_dir,target_name,
                                owner_event_sha256,code_sha256):
    """Call ONLY after an independent 2024-12 owner admission and staged SHA proof.

    Never calls the source warehouse. Stage checksum is independently verified
    in iter_verified_staged_c2_sub5_5s before/after its one-pass iterator.
    """
    if type(stage) is not StagedPartition or not stage.source_verified or stage.identity.period!='2024-12':
        raise PacketError('owner-provided, verified 2024-12 Development stage required')
    return _build_packet_from_buckets(iter_verified_staged_c2_sub5_5s(stage),
                private_dir=private_dir,target_name=target_name,
                source_sha256=stage.identity.canonical_sha256,
                owner_event_sha256=owner_event_sha256,code_sha256=code_sha256)


def _read_manifest(z):
    infos=z.infolist()
    if (len(infos)!=2 or {i.filename for i in infos}!={'manifest.json','c2_5s.csv'} or
            any(i.file_size>MAX_CSV_BYTES or i.compress_size>MAX_PACKET_BYTES
                for i in infos) or
            z.getinfo('manifest.json').file_size>8192):
        raise PacketError('unsafe packet members/size')
    raw=z.read('manifest.json')
    try:manifest=json.loads(raw)
    except (ValueError,UnicodeError) as exc:raise PacketError('invalid manifest') from exc
    if _canonical(manifest)!=raw:
        raise PacketError('noncanonical manifest')
    return manifest


def verify_private_packet(path, *, expected_packet_sha256,expected_source_sha256,
                          expected_code_sha256,allow_synthetic=False):
    """Verification-only preflight. No market evaluation before full byte checks."""
    path=Path(path)
    for value in (expected_packet_sha256,expected_source_sha256,expected_code_sha256):
        if not isinstance(value,str) or not SHA.fullmatch(value):
            raise PacketError('explicit independently recorded SHA required')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_PACKET_BYTES:
        raise PacketError('unsafe private packet')
    if _digest(path)!=expected_packet_sha256:
        raise PacketError('packet SHA mismatch')
    with zipfile.ZipFile(path) as z:
        manifest=_read_manifest(z)
        if (manifest.get('schema')!=SCHEMA or manifest.get('month')!='2024-12' or
                manifest.get('trial_id')!=TRIAL_ID or
                manifest.get('protocol_sha256')!=PROTOCOL_SHA256 or
                manifest.get('source_c2_sha256')!=expected_source_sha256 or
                manifest.get('code_sha256')!=expected_code_sha256 or
                manifest.get('csv_columns')!=list(FIELDS) or
                ((manifest.get('synthetic') is not False and not allow_synthetic) or
                (type(manifest.get('synthetic')) is not bool)) or
                not isinstance(manifest.get('rows'),int) or not 0<manifest['rows']<=MAX_ROWS or
                manifest.get('csv_bytes')!=z.getinfo('c2_5s.csv').file_size or
                not SHA.fullmatch(str(manifest.get('owner_read_started_reference_sha256',''))) or
                not SHA.fullmatch(str(manifest.get('csv_sha256','')))):
            raise PacketError('packet/manifest identity mismatch')
        h=hashlib.sha256()
        with z.open('c2_5s.csv') as data:
            while chunk:=data.read(1024*1024):h.update(chunk)
        if h.hexdigest()!=manifest['csv_sha256']:
            raise PacketError('CSV content SHA mismatch')
    return manifest


def iter_packet_rows(path, *, verified_manifest=None):
    """Use ONLY after verify_private_packet() with external expected digests."""
    with zipfile.ZipFile(path) as z, z.open('c2_5s.csv') as binary:
        reader=csv.DictReader(io.TextIOWrapper(binary,encoding='ascii',newline=''))
        if reader.fieldnames!=list(FIELDS):
            raise PacketError('CSV columns mismatch')
        previous=None;previous_last_id=None;count=0;first=None
        for row in reader:
            if type(row) is not dict or any(v is None for v in row.values()):
                raise PacketError('malformed CSV row')
            obj={}
            for key in FIELDS:
                value=row[key]
                if key==FEATURE and value=='':obj[key]=None
                elif key in INTS:
                    try:n=int(value)
                    except ValueError as e:raise PacketError('invalid integer field') from e
                    if str(n)!=value:raise PacketError('noncanonical integer field')
                    obj[key]=n
                else:
                    try:n=float(value)
                    except ValueError as e:raise PacketError('invalid numeric field') from e
                    if not math.isfinite(n):raise PacketError('nonfinite field')
                    obj[key]=n
            s=obj['start_ms'];n=obj['aggregate_rows']
            if (s%5000 or s<DEC_START_MS or s>=JAN_START_MS or
                previous is not None and s<=previous or n<=0 or
                obj['first_transact_ms']<s or obj['last_transact_ms']>=s+5000 or
                obj['first_transact_ms']>obj['last_transact_ms'] or
                obj['last_agg_trade_id']-obj['first_agg_trade_id']+1!=n or
                (previous_last_id is not None and obj['first_agg_trade_id']!=previous_last_id+1) or
                obj['taker_buy_base']<0 or obj['taker_sell_base']<0 or
                obj['taker_buy_base']+obj['taker_sell_base']<=0 or
                not -1<=((obj['taker_buy_base']-obj['taker_sell_base'])/(obj['taker_buy_base']+obj['taker_sell_base']))<=1 or
                any(obj[k]<=0 for k in ('executed_quote_notional','first_trade_price',
                                        'last_trade_price','min_trade_price','max_trade_price')) or
                obj['min_trade_price']>min(obj['first_trade_price'],obj['last_trade_price']) or
                obj['max_trade_price']<max(obj['first_trade_price'],obj['last_trade_price']) or
                (obj[FEATURE] is None)!=(n<5) or
                obj[FEATURE] is not None and not (1/n<=obj[FEATURE]<=1)):
                raise PacketError('out-of-bounds or inconsistent C2 row')
            first=s if first is None else first
            previous=s;previous_last_id=obj['last_agg_trade_id'];count+=1
            if count>MAX_ROWS:
                raise PacketError('CSV row cap exceeded')
            yield obj
        if verified_manifest is not None and (
                count != verified_manifest['rows'] or
                first != verified_manifest['first_bucket_ms'] or
                previous != verified_manifest['last_bucket_ms']):
            raise PacketError('verified packet coverage/count mismatch')
