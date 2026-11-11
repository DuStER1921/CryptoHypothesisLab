"""Pinned synthetic fixture reader: validate identifiers, no mutations in source tree.

Production data adapter is deliberately absent until independent trusted grants,
immutable universe/calendar binding and durable protected READ_STARTED exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .contracts import Role, Window
from .gate import ReadRequest, ValidationGate


class DataIntegrityError(RuntimeError):
    pass


def _relative_path(value: str) -> Path:
    if not isinstance(value, str) or '\\' in value or not value or '\x00' in value:
        raise DataIntegrityError('unsafe path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise DataIntegrityError('absolute/traversal path denied')
    return Path(*p.parts)


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
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}', self.partition_id):
            raise DataIntegrityError('invalid ID')
        _relative_path(self.data_file)
        _relative_path(self.manifest_file)
        if self.data_file == self.manifest_file:
            raise DataIntegrityError('manifest and source must differ')
        if any(not re.fullmatch('[0-9a-f]{64}', value) for value in (self.data_sha256, self.manifest_sha256)):
            raise DataIntegrityError('invalid hash')


class DataGateway:
    """Explicit allowlist + verified read; only tiny, identified synthetic fixtures V1."""

    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, root: Path, partitions: tuple[FrozenPartition, ...], gate: ValidationGate):
        self.root = Path(root)
        # A mistaken output path must never point at/under the source root.
        source_resolved = self.root.resolve()
        ledger_resolved = gate.ledger.root.resolve()
        if ledger_resolved == source_resolved or source_resolved in ledger_resolved.parents:
            raise DataIntegrityError('ledger/output location must be outside source tree')
        self.partitions = {p.partition_id: p for p in partitions}
        if len(self.partitions) != len(partitions):
            raise DataIntegrityError('duplicate partition ID')
        self.gate = gate

    def _checked_path(self, rel: str) -> Path:
        if self.root.is_symlink() or not self.root.is_dir():
            raise DataIntegrityError('invalid source root')
        target = self.root
        for part in _relative_path(rel).parts:
            target = target / part
            if target.is_symlink():
                raise DataIntegrityError('symlink source denied')
        if not target.is_file():
            raise DataIntegrityError('missing or non-file source')
        return target

    def _small_read(self, path: Path) -> bytes:
        if path.stat().st_size > self.MAX_BYTES:
            raise DataIntegrityError('fixture exceeds bounded reader budget')
        with path.open('rb') as fh:
            payload = fh.read(self.MAX_BYTES + 1)
        if len(payload) > self.MAX_BYTES:
            raise DataIntegrityError('fixture read too large')
        return payload

    def read_verified(self, partition_id: str, role: Role = Role.DEVELOPMENT) -> bytes:
        """Gate first, then exact pinned file+manifest. Never produce a mutable source handle."""
        # Avoid revealing a source path to callers before checking allowlist.
        if partition_id not in self.partitions:
            raise DataIntegrityError('partition not allowlisted')
        p = self.partitions[partition_id]
        self.gate.before_read(ReadRequest(partition_id, role, Window(p.start, p.end), p.scope))
        if p.scope != 'SYNTHETIC_ONLY':
            raise DataIntegrityError('real dataset adapter not implemented')
        manifest = self._small_read(self._checked_path(p.manifest_file))
        if hashlib.sha256(manifest).hexdigest() != p.manifest_sha256:
            raise DataIntegrityError('manifest hash mismatch')
        try:
            m = json.loads(manifest)
        except (ValueError, UnicodeError) as exc:
            raise DataIntegrityError('invalid manifest') from exc
        if (not isinstance(m, dict) or m.get('scope') != 'SYNTHETIC_ONLY'
                or m.get('datatype') != p.family or m.get('canonical_sha256') != p.data_sha256
                or m.get('partition_id') != p.partition_id):
            raise DataIntegrityError('manifest identity mismatch')
        content = self._small_read(self._checked_path(p.data_file))
        if hashlib.sha256(content).hexdigest() != p.data_sha256:
            raise DataIntegrityError('partition hash mismatch')
        return content
