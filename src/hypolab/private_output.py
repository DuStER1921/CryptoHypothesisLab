"""Bounded create-only output on a separate private local POSIX directory.

Does not access Google Drive or the public repository. Cloud publication needs a
separately reviewed least-privilege writer and readback; local POSIX fsync is
not a cloud durability proof.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path


class PrivateOutputError(RuntimeError):
    pass


class PrivateOutputStore:
    MAX_BYTES = 8 * 1024 * 1024
    NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.json\Z')

    def __init__(self, root: Path, *, source_roots: tuple[Path, ...]):
        self.root = Path(root)
        if not source_roots:
            raise PrivateOutputError('source roots required to prove path separation')
        if os.name!='posix' or self.root.is_symlink() or not self.root.is_dir():
            raise PrivateOutputError('private local POSIX output root required')
        if self.root.stat().st_mode & 0o077:
            raise PrivateOutputError('private output must be mode 0700')
        out=self.root.resolve(strict=True)
        for x in source_roots:
            src=Path(x).resolve(strict=True)
            if out==src or out in src.parents or src in out.parents:
                raise PrivateOutputError('source/output tree overlap')

    def write_once(self, name: str, payload: bytes) -> str:
        if (type(name) is not str or self.NAME.fullmatch(name) is None or
                type(payload) is not bytes or not 0<len(payload)<=self.MAX_BYTES):
            raise PrivateOutputError('invalid report size/name')
        if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise PrivateOutputError('output root changed')
        expected=hashlib.sha256(payload).hexdigest()
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
        try:
            fd=os.open(self.root/name,flags,0o600)
        except OSError as e:
            raise PrivateOutputError('create-only report already exists or denied') from e
        with os.fdopen(fd,'wb') as out:
            out.write(payload);out.flush();os.fsync(out.fileno())
        dirfd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|getattr(os,'O_NOFOLLOW',0))
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
        p=self.root/name
        if (p.is_symlink() or not stat.S_ISREG(p.stat().st_mode) or
                hashlib.sha256(p.read_bytes()).hexdigest()!=expected):
            raise PrivateOutputError('output readback failed')
        return expected
