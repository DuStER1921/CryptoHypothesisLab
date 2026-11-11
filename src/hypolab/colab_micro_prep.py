"""Owner-supervised Colab-only Development microstructure preparation.

No source writes or new OAuth client. This is a LOWER-ASSURANCE manual pilot:
Drive FUSE events are NOT independently durable. Real reading requires a fresh
explicit typed owner confirmation, and an event record before any source open.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil

from .colab_prep import (MountedFrozenSource,_check_events_root,
                         _log_start_once,PrepError)
from .frozen import FrozenIndex
from .micro_bundle import build_micro_bundle_from_staged
from .staging import ReadOnlyStager


@dataclass(frozen=True)
class MicroPrepared:
    path:Path
    sha256:str
    event_sha256:str
    counts:dict


def prepare_one_micro_month(*,metadata_root:Path,generations_root:Path,
                            events_root:Path,private_cache_parent:Path,
                            private_scratch:Path,private_packets:Path,
                            month:str,attempt_id:str,confirmation:str,
                            expected_universe_sha256:str,expected_calendar_sha256:str,
                            producer_code_commit:str,assumed_latency_ms:int=1500)->MicroPrepared:
    if confirmation!=f'AUTHORIZE_MICRO_DEVELOPMENT_{month}':
        raise PrepError('fresh exact owner approval for new raw-C2 pilot required')
    if type(assumed_latency_ms) is not int or not 1<=assumed_latency_ms<=5000:
        raise PrepError('hypothetical latency must be 1..5000 milliseconds')
    frozen=FrozenIndex.load(Path(metadata_root),expected_universe_sha256,expected_calendar_sha256)
    c1=frozen.pick('klines',month)
    c2=frozen.pick('aggTrades',month)
    source=MountedFrozenSource(generations_root)
    for partition in (c1,c2):
        if not all(x.is_file() and not x.is_symlink() for x in source.partition_paths(partition)):
            raise PrepError('frozen source partition missing or unsafe')
    events=_check_events_root(events_root,source.root)
    roots=(Path(private_cache_parent),Path(private_scratch),Path(private_packets))
    for root in roots:
        if (os.name!='posix' or root.is_symlink() or not root.is_dir() or
                root.stat().st_mode&0o077 or not str(root.resolve()).startswith('/content/')):
            raise PrepError('all private temporary folders must be under /content with mode 0700')
    if len(set(map(lambda x:x.resolve(),roots)))!=3:
        raise PrepError('do not reuse scratch/cache/output directories')
    projected=sum(p.stat().st_size for partition in (c1,c2)
                  for p in source.partition_paths(partition))
    if shutil.disk_usage(roots[0]).free < projected+3*1024**3:
        raise PrepError('not enough Colab disk for full staged C2 and bounded micro features')
    target=roots[2]/f'HYPOLAB_MICRO_DEV_{month}_{attempt_id}.zip'
    if target.exists():raise PrepError('private output already exists')
    # This is a FUSE owner trace, not a signed or globally durable read grant.
    event_sha=_log_start_once(events,month,attempt_id,frozen,c1,c2,
                              assumed_latency_ms,producer_code_commit,purpose='MICRO_5S_15S_60S_V2')
    stager=ReadOnlyStager(roots[0])
    with source.open_pinned(c1) as (manifest,data):
        staged_c1=stager.stage(c1,manifest_stream=manifest,parquet_stream=data)
    try:
        with source.open_pinned(c2) as (manifest,data):
            staged_c2=stager.stage(c2,manifest_stream=manifest,parquet_stream=data)
        try:
            product=build_micro_bundle_from_staged(staged_c1,staged_c2,
                scratch_dir=roots[1],target=target,assumed_latency_ms=assumed_latency_ms,
                producer_code_commit=producer_code_commit,
                universe_sha256=frozen.universe_sha256,
                calendar_sha256=frozen.calendar_sha256,
                start_event_sha256=event_sha)
            return MicroPrepared(product['path'],product['sha256'],event_sha,product['counts'])
        finally:
            shutil.rmtree(staged_c2.local_dir)
    finally:
        shutil.rmtree(staged_c1.local_dir)
