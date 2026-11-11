"""Trusted owner-only Drive readonly source adapter for exact frozen partitions.

No implicit authentication, no folder listing, no mutations, no data in repo.
Memory usage is bounded by Google Drive MediaIoBaseDownload chunk size. The
private scratch download is separate from the verified staging destination.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .frozen import PinnedPartition
from .single_account import READ_SCOPE


class SourceReadError(RuntimeError):
    pass


class GoogleDrivePinnedSource:
    """Only use inside owner broker; source token must never reach analyst.

    mapping keys: (family, period, canonical_sha256), values:
    (manifest_file_id, parquet_file_id). This mapping MUST be private.
    """
    MAX_PARQUET_BYTES = 16 * 1024**3
    MAX_MANIFEST_BYTES = 1024 * 1024
    CHUNK = 4 * 1024 * 1024

    def __init__(self, *, service, credentials, mapping: dict,
                 scratch_parent: Path):
        if not mapping or not isinstance(mapping, dict):
            raise SourceReadError('explicit pinned file mapping required')
        # google-auth granted_scopes may be None; in that case we fail closed.
        # Requested scope / OAuth consent alone is NOT a scope proof.
        granted=getattr(credentials,'granted_scopes',None)
        if not isinstance(granted,(list,tuple,set)) or set(granted)!={READ_SCOPE}:
            raise SourceReadError('verified drive.readonly-only token required')
        self._mapping={}
        for key, pair in mapping.items():
            if (type(key) is not tuple or len(key)!=3 or type(pair) is not tuple
                    or len(pair)!=2 or any(type(x) is not str or not x for x in pair)
                    or any(type(x) is not str for x in key)
                    or not re.fullmatch('[a-f0-9]{64}',key[2])
                    or pair[0] == pair[1]):
                raise SourceReadError('invalid pinned Drive mapping')
            self._mapping[key]=pair
        self._files=service.files()
        parent=Path(scratch_parent)
        if (os.name!='posix' or parent.is_symlink() or not parent.is_dir()
                or parent.stat().st_mode & 0o077):
            raise SourceReadError('private local POSIX scratch parent required')
        self._parent=parent

    def _download_one(self, file_id: str, path: Path, cap: int) -> None:
        # Metadata read uses only files.get (not list/search). No source write APIs.
        info=self._files.get(fileId=file_id,fields='id,size,trashed,mimeType',
                             supportsAllDrives=True).execute()
        if (type(info) is not dict or info.get('id')!=file_id
                or info.get('trashed') is not False or
                info.get('mimeType') not in ('application/octet-stream',
                    'application/x-parquet','application/json','text/plain',
                    'application/vnd.apache.parquet')):
            raise SourceReadError('unexpected canonical source metadata')
        try:
            expected_size=int(info['size'])
        except (KeyError,ValueError,TypeError) as exc:
            raise SourceReadError('unknown source size') from exc
        if not 0 < expected_size <= cap:
            raise SourceReadError('source exceeds bounded scratch size')
        # Avoid a large partial C2 staging if disk is clearly too small.
        if shutil.disk_usage(self._parent).free < expected_size + 64 * 1024**2:
            raise SourceReadError('insufficient scratch space for source file')
        try:
            from googleapiclient.http import MediaIoBaseDownload
        except ImportError as exc:
            raise SourceReadError('google-api-python-client not installed') from exc
        request=self._files.get_media(fileId=file_id,supportsAllDrives=True)
        written=0
        # Exclusive new local file; no source file is opened with write access.
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|
                   getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            with os.fdopen(fd,'wb') as output:
                downloader=MediaIoBaseDownload(output,request,chunksize=self.CHUNK)
                done=False
                while not done:
                    _,done=downloader.next_chunk(num_retries=0)
                    written=output.tell()
                    if written > expected_size or written > cap:
                        raise SourceReadError('download exceeded pinned size')
                output.flush();os.fsync(output.fileno())
        except BaseException:
            raise
        if written != expected_size or not stat.S_ISREG(path.stat().st_mode):
            raise SourceReadError('truncated or unusual downloaded file')
        # No reliance on provider checksum; ReadOnlyStager compares SHA-256.

    @contextmanager
    def open_pinned(self, partition: PinnedPartition):
        if type(partition) is not PinnedPartition:
            raise SourceReadError('exact frozen partition object required')
        key=(partition.family,partition.period,partition.canonical_sha256)
        pair=self._mapping.get(key)
        if pair is None:
            raise SourceReadError('partition is not pinned in private Drive file map')
        with tempfile.TemporaryDirectory(prefix='hypolab-read-',dir=self._parent) as d:
            folder=Path(d)
            manifest=folder/'manifest.json';parquet=folder/'data.parquet'
            self._download_one(pair[0],manifest,self.MAX_MANIFEST_BYTES)
            self._download_one(pair[1],parquet,self.MAX_PARQUET_BYTES)
            with manifest.open('rb') as m,parquet.open('rb') as p:
                yield m,p
