"""Pure read-stream-to-private-local-cache import of pinned Parquet/manifest.

This class never opens, writes, deletes or renames any SOURCE path. Its callers
must provide two already-open, read-only byte streams. A separate trusted
admission gate MUST run before streams are opened; importing bytes does not
itself authorize research. No Colab/Drive/FUSE durability claim is made.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
import hashlib
import json
import os
import shutil
import stat
import tempfile

from .frozen import PinnedPartition, FrozenError


class StageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedPartition:
    identity: PinnedPartition
    local_dir: Path
    source_verified: bool

    @property
    def parquet_path(self) -> Path:
        return self.local_dir / "data.parquet"


class ReadOnlyStager:
    """Stages only to caller-owned private POSIX cache, never to source."""

    MAX_MANIFEST = 1024 * 1024
    MAX_DATA = 16 * 1024**3
    CHUNK = 4 * 1024 * 1024

    def __init__(self, cache_parent: Path):
        self.cache_parent = Path(cache_parent)
        if (os.name != 'posix' or self.cache_parent.is_symlink() or
                not self.cache_parent.is_dir() or
                (self.cache_parent.stat().st_mode & 0o077)):
            raise StageError("private local POSIX cache required; no Drive/FUSE source writes")
        # Users must not point cache at mounted source; enforce with OS permissions.
        # This class has no source path, and cannot verify the mount identity.

    @staticmethod
    def _copy(stream: BinaryIO, target: Path, expected: str, limit: int) -> int:
        h = hashlib.sha256()
        total = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0)
        fd = os.open(target,flags,0o600)
        try:
            with os.fdopen(fd,'wb') as out:
                while True:
                    chunk=stream.read(ReadOnlyStager.CHUNK)
                    if not isinstance(chunk,bytes):
                        raise StageError("source stream must return bytes")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise StageError("source exceeds explicit byte cap")
                    h.update(chunk)
                    out.write(chunk)
                out.flush(); os.fsync(out.fileno())
        except BaseException:
            raise
        if not total or h.hexdigest()!=expected:
            raise StageError("source hash/size mismatch")
        return total

    @staticmethod
    def _check_manifest(raw: bytes, identity: PinnedPartition) -> None:
        try:
            v=json.loads(raw)
        except (ValueError,UnicodeDecodeError) as exc:
            raise StageError("invalid source manifest") from exc
        if (type(v) is not dict or v.get('datatype') != identity.family or
                v.get('provider') != 'binance' or v.get('symbol') != 'BTCUSDT' or
                v.get('market') != 'usdm' or
                v.get('logical_dataset_id') != identity.logical_dataset_id or
                v.get('canonical_name') != 'data.parquet' or
                v.get('canonical_sha256') != identity.canonical_sha256 or
                v.get('source_sha256') != identity.source_sha256):
            raise StageError("manifest disagrees with frozen ledger")
        # The original C1 1m V1 manifest has NO `generation` member.
        # Generation is pinned by the frozen partition ledger, the exact
        # manifest SHA-256 (verified before this call), and the generation
        # directory chosen by MountedFrozenSource. Do not invent a field.
        if identity.family == 'klines' and v.get('manifest_version') == 1:
            if ('generation' in v or v.get('interval') != '1m' or
                    v.get('schema_version') != 'binance-usdm-klines-1m-v1'):
                raise StageError("unexpected C1 V1 source manifest contract")
        elif v.get('generation') != identity.generation:
            raise StageError("manifest generation disagrees with frozen ledger")
        if identity.family == 'bookDepth':
            period=f"{v.get('year'):04d}-{v.get('month'):02d}-{v.get('day'):02d}" if all(type(v.get(k)) is int for k in ('year','month','day')) else ''
        else:
            period=f"{v.get('year'):04d}-{v.get('month'):02d}" if all(type(v.get(k)) is int for k in ('year','month')) else ''
        if period != identity.period or type(v.get('row_count')) is not int or v['row_count'] < 1:
            raise StageError("manifest period/row-count mismatch")
        checks=v.get('validation')
        if (type(checks) is not dict or
            any(val != 'pass' for val in checks.values()) or
            not {'checksum','schema','temporal','structural','read_back'}.issubset(checks)):
            raise StageError("manifest verification contract insufficient")

    def stage(self, identity: PinnedPartition, *, manifest_stream: BinaryIO,
              parquet_stream: BinaryIO) -> StagedPartition:
        """Gated caller supplies streams; no real read is authorized here.

        Caller must first enforce a trusted grant+record durable READ_STARTED.
        Files created exclusively in private temporary cache. On failure only
        this freshly created scratch directory is removed, never an input path.
        """
        directory=Path(tempfile.mkdtemp(prefix='hypolab-verified-',dir=self.cache_parent))
        try:
            self._copy(manifest_stream,directory/'manifest.json',
                       identity.manifest_sha256,self.MAX_MANIFEST)
            self._check_manifest((directory/'manifest.json').read_bytes(),identity)
            self._copy(parquet_stream,directory/'data.parquet',
                       identity.canonical_sha256,self.MAX_DATA)
            for filename,expected in (('manifest.json',identity.manifest_sha256),
                                      ('data.parquet',identity.canonical_sha256)):
                p=directory/filename
                if p.is_symlink() or not stat.S_ISREG(p.stat().st_mode):
                    raise StageError("non-regular staging artifact")
                digest=hashlib.sha256()
                with p.open('rb') as fh:
                    for chunk in iter(lambda:fh.read(self.CHUNK),b''):
                        digest.update(chunk)
                if digest.hexdigest()!=expected:
                    raise StageError("staged readback does not match frozen SHA")
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
            return StagedPartition(identity,directory,True)
        except BaseException:
            shutil.rmtree(directory)
            raise
