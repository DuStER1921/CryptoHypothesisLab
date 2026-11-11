"""Durable local *synthetic-only* hash-chained ledger; no cloud/fuse security claims."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


class LedgerError(RuntimeError):
    pass


KINDS = frozenset({'READ_STARTED', 'READ_DENIED', 'BATCH_REGISTERED', 'TRIAL_RESULT'})
ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$')
ENTRY_PATTERN = re.compile(r'^(\d{8})-([a-f0-9]{64})\.json$')


def canonical_json(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sync_dir(folder: Path) -> None:
    if os.name != 'posix':
        raise LedgerError('local POSIX directory durability required; no Windows/Drive ACK claim')
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ExperimentLedger:
    """Create-only events. One writer, POSIX filesystem, separate trusted anchor still required."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.genesis = {'schema': 1, 'dataset': 'SYNTHETIC_ONLY',
                        'authorization': 'NO_REAL_RESEARCH', 'roles': 'RULES_V1'}

    def initialize(self) -> None:
        if os.name != 'posix':
            raise LedgerError('POSIX only')
        if self.root.exists() or self.root.is_symlink():
            raise LedgerError('ledger already exists')
        self.root.mkdir(mode=0o700)  # never within source root
        self._create(self.root / 'genesis.json', canonical_json(self.genesis))

    def _create(self, path: Path, payload: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_dir(self.root)
        if path.read_bytes() != payload:
            raise LedgerError('readback mismatch')

    def replay(self, expected_head: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        if self.root.is_symlink() or not self.root.is_dir():
            raise LedgerError('ledger missing or symlink')
        genesis_path = self.root / 'genesis.json'
        if genesis_path.is_symlink() or genesis_path.read_bytes() != canonical_json(self.genesis):
            raise LedgerError('genesis mismatch')
        prev = digest(canonical_json(self.genesis))
        out: list[dict[str, Any]] = []
        batches: dict[str, tuple[int, set[str]]] = {}
        items = list(self.root.iterdir())
        for path in sorted(items):
            if path.name == 'genesis.json':
                continue
            match = ENTRY_PATTERN.fullmatch(path.name)
            if not match or path.is_symlink() or not path.is_file():
                raise LedgerError('unexpected ledger item')
            if int(match.group(1)) != len(out) + 1:
                raise LedgerError('missing/duplicate event number')
            content = path.read_bytes()
            if len(content) > 65536 or digest(content) != match.group(2):
                raise LedgerError('ledger event checksum invalid')
            try:
                event = json.loads(content)
            except (ValueError, UnicodeError) as exc:
                raise LedgerError('invalid JSON event') from exc
            if (canonical_json(event) != content or not isinstance(event, dict)
                    or set(event) != {'seq', 'prev', 'kind', 'payload'}
                    or event['seq'] != len(out) + 1 or event['prev'] != prev
                    or event['kind'] not in KINDS or not isinstance(event['payload'], dict)):
                raise LedgerError('ledger sequence/chain/schema invalid')
            self._validate_event(event['kind'], event['payload'])
            kind, payload = event['kind'], event['payload']
            if kind == 'BATCH_REGISTERED':
                if payload['batch_id'] in batches:
                    raise LedgerError('batch already registered')
                batches[payload['batch_id']] = (payload['trials'], set())
            elif kind == 'TRIAL_RESULT':
                match = batches.get(payload['batch_id'])
                if match is None:
                    raise LedgerError('result without registered batch')
                expected, seen = match
                if payload['trial_id'] in seen or len(seen) >= expected:
                    raise LedgerError('duplicate or excess trial result')
                seen.add(payload['trial_id'])
            out.append(event)
            prev = digest(content)
        if expected_head is not None and expected_head != prev:
            raise LedgerError('independent head mismatch / possible suffix deletion')
        return prev, out

    @staticmethod
    def _validate_event(kind: str, payload: dict[str, Any]) -> None:
        schema = {
            'READ_STARTED': {'partition_id', 'role', 'scope'},
            'READ_DENIED': {'partition_id', 'role', 'reason'},
            'BATCH_REGISTERED': {'batch_id', 'trials', 'definition_sha'},
            'TRIAL_RESULT': {'batch_id', 'trial_id', 'status', 'result_sha'},
        }
        if kind not in schema or set(payload) != schema[kind]:
            raise LedgerError('unsupported event or fields')
        for key, value in payload.items():
            if key == 'trials':
                if type(value) is not int or not (1 <= value <= 100000):
                    raise LedgerError('invalid trial count')
            elif key.endswith('_sha'):
                if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
                    raise LedgerError('invalid digest')
            elif not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
                raise LedgerError('invalid event ID/status')
        if kind == 'READ_STARTED' and (payload['scope'] != 'SYNTHETIC_ONLY' or payload['role'] != 'DEVELOPMENT'):
            raise LedgerError('protected access is not implementable in prototype')

    def append(self, kind: str, payload: dict[str, Any], expected_head: str | None = None) -> str:
        if os.name != 'posix':
            raise LedgerError('POSIX only')
        self._validate_event(kind, payload)
        # Single trusted writer per local ledger. Do not use on Drive/FUSE.
        head, events = self.replay(expected_head)
        if kind == 'BATCH_REGISTERED':
            if any(e['kind'] == 'BATCH_REGISTERED' and e['payload']['batch_id'] == payload['batch_id']
                   for e in events):
                raise LedgerError('batch already registered')
        if kind == 'TRIAL_RESULT':
            registrations = [e for e in events if e['kind'] == 'BATCH_REGISTERED'
                             and e['payload']['batch_id'] == payload['batch_id']]
            seen = [e for e in events if e['kind'] == 'TRIAL_RESULT'
                    and e['payload']['batch_id'] == payload['batch_id']]
            if (len(registrations) != 1 or len(seen) >= registrations[0]['payload']['trials']
                    or any(e['payload']['trial_id'] == payload['trial_id'] for e in seen)):
                raise LedgerError('unregistered, duplicate or excess trial result')
        event = {'seq': len(events) + 1, 'prev': head, 'kind': kind, 'payload': payload}
        content = canonical_json(event)
        result = digest(content)
        self._create(self.root / f'{len(events) + 1:08d}-{result}.json', content)
        confirmed, _ = self.replay(expected_head=result)
        if confirmed != result:
            raise LedgerError('durable replay failed')
        return result
