"""Operator-supervised Colab DEVELOPMENT preview, using Colab's built-in mount.

This is an *alternative lower-assurance pilot* to the Phase-4 signed broker.
It does not assert cloud-atomic logging, read-only account permissions or
independent research authorization. A human must confirm exactly one pilot.
No code path writes to the frozen/source tree; bytes are read using O_RDONLY.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile

from .airgap_bundle import create_bundle, inspect_bundle, write_bundle_local_once
from .c1c2_features import C1C2MinuteBuilder
from .contracts import Role, SPLITS
from .frozen import FrozenIndex, PinnedPartition
from .staging import ReadOnlyStager


class PrepError(RuntimeError):
    pass


def _check_mount_source(base:Path):
    base=Path(base)
    if (base.name!='generations' or base.parent.name!='canonical'
            or base.parent.parent.name!='data'
            or base.parent.parent.parent.name!='CryptoResearchLab'
            or not base.is_dir() or base.is_symlink()):
        raise PrepError('expected exact CryptoResearchLab/data/canonical/generations')
    return base.resolve(strict=True)


def _check_events_root(events:Path, source:Path):
    events=Path(events)
    if (events.name!='research-events' or events.parent.name!='CryptoHypothesisLab'
            or not events.is_dir() or events.is_symlink()):
        raise PrepError('expected separate CryptoHypothesisLab/research-events')
    out=events.resolve(strict=True)
    if source in out.parents or out in source.parents or source==out:
        raise PrepError('source and event trees overlap')
    return out


def _check_regular_readonly_file(root:Path,path:Path,max_bytes:int):
    """No arbitrary file chooser: exact frozen layout and read-only open."""
    if root not in path.parents:
        raise PrepError('source path escaped canonical generations root')
    cursor=path.parent
    while cursor!=root:
        if cursor.is_symlink():
            raise PrepError('symlinked source path denied')
        cursor=cursor.parent
    if path.is_symlink():
        raise PrepError('source symlink denied')
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    try:
        fd=os.open(path,flags)
    except OSError as e:
        raise PrepError('frozen source file missing or unreadable') from e
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= max_bytes:
        os.close(fd)
        raise PrepError('invalid source type/size')
    return os.fdopen(fd,'rb',buffering=4*1024*1024)


class MountedFrozenSource:
    """Only pinned *Development* partition rows; no provider discovery."""
    def __init__(self,generations_root:Path):
        self.root=_check_mount_source(generations_root)

    def partition_paths(self,identity:PinnedPartition):
        if type(identity) is not PinnedPartition or identity.family not in ('klines','aggTrades'):
            raise PrepError('C1+C2 only')
        lo,hi=identity.bounds
        if not SPLITS[Role.DEVELOPMENT].contains(lo,hi):
            raise PrepError('Validation/OOS source denied')
        y,m=identity.period.split('-')
        root=self.root/identity.generation/'binance'/'usdm'/'BTCUSDT'/identity.family
        if identity.family=='klines':root=root/'1m'
        root=root/f'year={y}'/f'month={m}'
        return root/'manifest.json',root/'data.parquet'

    @contextmanager
    def open_pinned(self,identity:PinnedPartition):
        manifest,parquet=self.partition_paths(identity)
        with _check_regular_readonly_file(self.root,manifest,ReadOnlyStager.MAX_MANIFEST) as a:
            with _check_regular_readonly_file(self.root,parquet,ReadOnlyStager.MAX_DATA) as b:
                yield a,b


def _log_start_once(events:Path,month:str,attempt_id:str,frozen:FrozenIndex,
                    c1:PinnedPartition,c2:PinnedPartition,assumed_latency_ms:int,
                    producer_code_commit:str,purpose:str|None=None):
    if purpose is not None and purpose!='MICRO_5S_15S_60S_V2':
        raise PrepError('unsupported owner event purpose')
    if type(attempt_id) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{5,39}',attempt_id) is None:
        raise PrepError('attempt ID must be 6..40 ASCII characters')
    if type(assumed_latency_ms) is not int or not 1<=assumed_latency_ms<=600000:
        raise PrepError('invalid hypothetical latency')
    if type(producer_code_commit) is not str or re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',producer_code_commit) is None:
        raise PrepError('invalid producer code commit')
    event={'v':1,'kind':'COLAB_DEVELOPMENT_PREP_STARTED',
           'scope':'OWNER_CONFIRMED_DEVELOPMENT_ONLY',
           'month':month,'attempt_id':attempt_id,
           'universe_sha256':frozen.universe_sha256,
           'calendar_sha256':frozen.calendar_sha256,
           'c1_sha256':c1.canonical_sha256,'c2_sha256':c2.canonical_sha256,
           'assumed_latency_ms':assumed_latency_ms,
           'producer_code_commit':producer_code_commit,
           'started_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    if purpose is not None:
        event['purpose']=purpose
    raw=json.dumps(event,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    name=f'COLAB_PREP_STARTED_{month}_{attempt_id}.json'
    path=events/name
    # O_EXCL is supported on POSIX and may not be atomic on Drive FUSE. This
    # operator-only pilot MUST NOT be represented as independent cloud authority.
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush()
            try:os.fsync(f.fileno())
            except OSError as exc:raise PrepError('Drive mount did not acknowledge fsync') from exc
    except FileExistsError as e:
        raise PrepError('attempt ID already used; no retry') from e
    except OSError as e:
        raise PrepError('cannot durably write private start event') from e
    if path.read_bytes()!=raw:
        raise PrepError('READ_STARTED readback mismatch')
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class PreparedPackage:
    path: Path
    sha256: str
    feature_rows: int
    start_event_sha256: str


def prepare_one_development_month(*,metadata_root:Path,generations_root:Path,
                                  events_root:Path,private_cache_parent:Path,
                                  month:str,attempt_id:str,confirmation:str,
                                  expected_universe_sha256:str,
                                  expected_calendar_sha256:str,
                                  producer_code_commit:str,
                                  assumed_latency_ms:int=1500) -> PreparedPackage:
    """No real row access before owner confirmation and readback of start event.

    This is a human-supervised pilot, NOT a replacement for the independently
    signed Phase4 broker and NOT a production multi-writer permission system.
    """
    if confirmation!=f'AUTHORIZE_DEVELOPMENT_{month}':
        raise PrepError('explicit owner Development-only authorization required')
    frozen=FrozenIndex.load(Path(metadata_root),expected_universe_sha256,expected_calendar_sha256)
    c1=frozen.pick('klines',month);c2=frozen.pick('aggTrades',month)
    if c1.period!=c2.period:
        raise PrepError('source family alignment mismatch')
    source=MountedFrozenSource(generations_root)
    for p in (c1,c2):
        paths=source.partition_paths(p)
        if not all(x.is_file() and not x.is_symlink() for x in paths):
            raise PrepError('selected source partition missing or unsafe')
    events=_check_events_root(events_root,source.root)
    parent=Path(private_cache_parent)
    if (str(parent.resolve(strict=False)).startswith('/content/') is False
            or parent.is_symlink() or not parent.is_dir() or parent.stat().st_mode&0o077):
        raise PrepError('private temporary Colab cache under /content required')
    if not Path('/content/hypolab-packets').is_dir():
        raise PrepError('create /content/hypolab-packets first')
    # Validate code provenance and enough scratch space *before* any event or source open.
    if type(producer_code_commit) is not str or re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}',producer_code_commit) is None:
        raise PrepError('invalid code provenance')
    projected=sum(p.stat().st_size for identity in (c1,c2)
                  for p in source.partition_paths(identity))
    if shutil.disk_usage(parent).free < projected+512*1024**2:
        raise PrepError('insufficient scratch space for full verified C1+C2 snapshots')
    record=_log_start_once(events,month,attempt_id,frozen,c1,c2,
                           assumed_latency_ms,producer_code_commit)
    stager=ReadOnlyStager(parent)
    with source.open_pinned(c1) as (m,b):
        stage1=stager.stage(c1,manifest_stream=m,parquet_stream=b)
    try:
        with source.open_pinned(c2) as (m,b):
            stage2=stager.stage(c2,manifest_stream=m,parquet_stream=b)
        try:
            rows=C1C2MinuteBuilder().build(stage1,stage2,assumed_latency_ms=assumed_latency_ms)
            packet=create_bundle(month=month,features=rows,
                                 universe_sha256=frozen.universe_sha256,
                                 calendar_sha256=frozen.calendar_sha256,
                                 c1_sha256=c1.canonical_sha256,c2_sha256=c2.canonical_sha256,
                                 assumed_latency_ms=assumed_latency_ms,
                                 producer_code_commit=producer_code_commit)
            name=f'HYPOLAB_DEV_{month}_{attempt_id}.zip'
            target=Path('/content/hypolab-packets')/name
            sha=write_bundle_local_once(target,packet)
            return PreparedPackage(target,sha,len(rows),record)
        finally:
            shutil.rmtree(stage2.local_dir)
    finally:
        shutil.rmtree(stage1.local_dir)
