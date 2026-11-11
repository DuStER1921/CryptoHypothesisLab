"""Bounded read-only verified *synthetic fixture* gateway.

Uses POSIX directory descriptors and O_NOFOLLOW to close common path-swap
races. No real-data adapter or authorization source exists in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat

from .contracts import Role, Window
from .gate import ReadRequest, ValidationGate


class DataIntegrityError(RuntimeError):
    pass


def _relative_path(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or '\\' in value or not value or '\x00' in value:
        raise DataIntegrityError('unsafe path')
    p = PurePosixPath(value)
    parts = value.split('/')
    if p.is_absolute() or any(x in ('', '.', '..') or
                              re.fullmatch(r'[A-Za-z0-9_.=-]+', x) is None for x in parts):
        raise DataIntegrityError('absolute, traversal or nonportable source path')
    return tuple(parts)


@dataclass(frozen=True)
class FrozenPartition:
    partition_id: str
    family: str
    start: datetime
    end: datetime
    data_file: str
    data_sha256: str
    manifest_file: str
    manifest_sha256: str
    scope: str = 'SYNTHETIC_ONLY'

    def __post_init__(self):
        Window(self.start, self.end)
        if self.family not in ('klines', 'aggTrades', 'bookDepth'):
            raise DataIntegrityError('unsupported family')
        if type(self.partition_id) is not str or re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}', self.partition_id) is None:
            raise DataIntegrityError('invalid partition ID')
        _relative_path(self.data_file)
        _relative_path(self.manifest_file)
        if self.data_file == self.manifest_file:
            raise DataIntegrityError('manifest and source must differ')
        if any(type(s) is not str or re.fullmatch(r'[0-9a-f]{64}', s) is None
               for s in (self.data_sha256, self.manifest_sha256)):
            raise DataIntegrityError('invalid pinned content digest')
        if self.scope not in ('SYNTHETIC_ONLY', 'REAL'):
            raise DataIntegrityError('unsupported scope')


class DataGateway:
    """Pinned synthetic allowlist; never creates/modifies a source file."""

    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, root: Path, partitions: tuple[FrozenPartition, ...], gate: ValidationGate):
        self.root = Path(root)
        source = self.root.resolve()
        output = gate.ledger.root.resolve()
        if source == output or source in output.parents or output in source.parents:
            raise DataIntegrityError('output and source must not overlap')
        # Reject aliases in the root's ancestry, not just the final component.
        if self.root.absolute() != source or any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise DataIntegrityError('source root alias/symlink denied')
        self.partitions = {p.partition_id: p for p in partitions}
        if len(self.partitions) != len(partitions):
            raise DataIntegrityError('duplicate partition ID')
        self.gate = gate

    def _small_read(self, rel: str, expected_digest: str) -> bytes:
        """File-descriptor-anchored read. Fail on symlinks, special files and mutation."""
        if os.name != 'posix':
            raise DataIntegrityError('POSIX verified fixture reader only')
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
        parts = _relative_path(rel)
        dirs: list[int] = []
        fd = None
        try:
            dirs.append(os.open(self.root, directory_flags))
            for component in parts[:-1]:
                dirs.append(os.open(component, directory_flags, dir_fd=dirs[-1]))
            fd = os.open(parts[-1], file_flags, dir_fd=dirs[-1])
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_size > self.MAX_BYTES or before.st_size < 0):
                raise DataIntegrityError('unsafe or oversized fixture source')
            chunks, remaining = [], before.st_size
            while remaining:
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise DataIntegrityError('fixture truncated during read')
                chunks.append(chunk)
                remaining -= len(chunk)
            # Detect an appended byte even if the original size was small.
            if os.read(fd, 1):
                raise DataIntegrityError('fixture changed while reading')
            after = os.fstat(fd)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns)):
                raise DataIntegrityError('source metadata changed during read')
            payload = b''.join(chunks)
            if hashlib.sha256(payload).hexdigest() != expected_digest:
                raise DataIntegrityError('pinned SHA-256 mismatch')
            return payload
        except OSError as exc:
            raise DataIntegrityError('source read denied or unavailable') from exc
        finally:
            if fd is not None:
                os.close(fd)
            for d in reversed(dirs):
                os.close(d)

    def read_verified(self, partition_id: str, role: Role = Role.DEVELOPMENT) -> bytes:
        """Policy before bytes. Real and protected reads cannot pass this gate."""
        p = self.partitions.get(partition_id)
        if p is None:
            raise DataIntegrityError('partition not allowlisted')
        self.gate.before_read(ReadRequest(partition_id, role, Window(p.start, p.end), p.scope))
        if p.scope != 'SYNTHETIC_ONLY':
            raise DataIntegrityError('real dataset adapter not implemented')
        manifest = self._small_read(p.manifest_file, p.manifest_sha256)
        try:
            m = json.loads(manifest)
        except (ValueError, UnicodeError) as exc:
            raise DataIntegrityError('invalid manifest') from exc
        if (type(m) is not dict or m.get('scope') != 'SYNTHETIC_ONLY'
                or m.get('datatype') != p.family or
                m.get('canonical_sha256') != p.data_sha256 or
                m.get('partition_id') != p.partition_id):
            raise DataIntegrityError('manifest identity mismatch')
        return self._small_read(p.data_file, p.data_sha256)
