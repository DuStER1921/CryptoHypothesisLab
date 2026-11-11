"""Fail-closed local POSIX synthetic-only experiment journal.

A persistent flock provides *cooperating local* single-writer exclusion. A
transaction sentinel prevents claiming success after an interrupted multi-event
append. Neither this file nor a hash chain is a trusted, externally anchored
Google Drive/FUSE grant service. No real research access can be granted here.
"""
from __future__ import annotations

from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # Windows import must fail closed at runtime, not package import
    fcntl = None
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable


class LedgerError(RuntimeError):
    """The local evidence chain is missing, inconsistent, or not durable."""


KINDS = frozenset({'READ_STARTED', 'READ_DENIED', 'BATCH_REGISTERED', 'TRIAL_RESULT'})
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
ENTRY = re.compile(r'(\d{8})-([a-f0-9]{64})\.json\Z')
LOCK_NAME = '.writer.lock'
TXN_NAME = '.incomplete-transaction'
MAX_EVENT_BYTES = 4 * 1024 * 1024  # bounded batch registration (trial IDs)
MAX_BATCH_EVENTS = 5000
MAX_TRIALS = 25000


def canonical_json(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sync_dir(folder: Path) -> None:
    if os.name != 'posix':
        raise LedgerError('local POSIX directory durability required')
    fd = os.open(folder, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                 getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ExperimentLedger:
    """Local POSIX-only journal. Real data and protected reads remain DENIED."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.genesis = {'schema': 2, 'dataset': 'SYNTHETIC_ONLY',
                        'authorization': 'NO_REAL_RESEARCH', 'roles': 'RULES_V1'}

    @staticmethod
    def _ensure_local_posix() -> None:
        if os.name != 'posix' or fcntl is None:
            raise LedgerError('POSIX-only journal; Windows and Drive/FUSE unverified')

    def _check_root(self) -> None:
        if self.root.is_symlink() or not self.root.is_dir():
            raise LedgerError('ledger root missing or symlink')
        if (self.root.stat().st_mode & 0o077) != 0:
            raise LedgerError('ledger directory must not allow group/other access')

    @contextmanager
    def _locked(self, *, write: bool):
        self._ensure_local_posix()
        self._check_root()
        flags = (os.O_RDWR if write else os.O_RDONLY) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(self.root / LOCK_NAME, flags)
        except OSError as exc:
            raise LedgerError('writer lock unavailable') from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise LedgerError('invalid lock file')
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerError('another journal operation is active') from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def initialize(self) -> None:
        self._ensure_local_posix()
        if self.root.exists() or self.root.is_symlink():
            raise LedgerError('ledger root already exists')
        self.root.mkdir(mode=0o700)
        lock_fd = os.open(self.root / LOCK_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                          getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(lock_fd)
        self._create(self.root / 'genesis.json', canonical_json(self.genesis))
        _sync_dir(self.root)

    def _create(self, path: Path, payload: bytes, *, sync_directory: bool = True) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if sync_directory:
            _sync_dir(self.root)
        if path.is_symlink() or path.read_bytes() != payload:
            raise LedgerError('journal record readback mismatch')

    @staticmethod
    def _validate_event(kind: str, payload: dict[str, Any]) -> None:
        schema = {
            'READ_STARTED': {'partition_id', 'role', 'scope'},
            'READ_DENIED': {'partition_id', 'role', 'reason'},
            'BATCH_REGISTERED': {'batch_id', 'trials', 'trial_ids', 'definition_sha'},
            'TRIAL_RESULT': {'batch_id', 'trial_id', 'status', 'result_sha'},
        }
        if kind not in schema or type(payload) is not dict or set(payload) != schema[kind]:
            raise LedgerError('invalid journal event fields')
        for key, value in payload.items():
            if key == 'trial_ids':
                if (type(value) is not list or not (1 <= len(value) <= MAX_TRIALS)
                        or any(type(v) is not str or ID.fullmatch(v) is None for v in value)
                        or len(set(value)) != len(value)):
                    raise LedgerError('invalid registered trial list')
            elif key == 'trials':
                if type(value) is not int or not 1 <= value <= MAX_TRIALS:
                    raise LedgerError('invalid number of trials')
            elif key.endswith('_sha'):
                if type(value) is not str or SHA.fullmatch(value) is None:
                    raise LedgerError('invalid SHA-256')
            elif type(value) is not str or ID.fullmatch(value) is None:
                raise LedgerError('invalid journal identifier')
        if kind == 'BATCH_REGISTERED' and payload['trials'] != len(payload['trial_ids']):
            raise LedgerError('batch size does not match pre-registered trial IDs')
        if kind == 'READ_STARTED' and (payload['role'] != 'DEVELOPMENT' or
                                      payload['scope'] != 'SYNTHETIC_ONLY'):
            raise LedgerError('protected or real read forbidden')
        if kind == 'READ_DENIED' and payload['reason'] not in {
            'REAL_NOT_AUTHORIZED', 'PROTECTED_SEALED', 'OUT_OF_DEVELOPMENT'}:
            raise LedgerError('unknown denial reason')
        if kind == 'TRIAL_RESULT' and payload['status'] not in {'DONE', 'INSUFFICIENT', 'FAILED'}:
            raise LedgerError('unknown trial status')

    @classmethod
    def _apply(cls, state: dict[str, tuple[set[str], set[str]]], kind: str,
               payload: dict[str, Any]) -> None:
        cls._validate_event(kind, payload)
        if kind == 'BATCH_REGISTERED':
            batch = payload['batch_id']
            if batch in state:
                raise LedgerError('batch already registered')
            state[batch] = (set(payload['trial_ids']), set())
        elif kind == 'TRIAL_RESULT':
            pair = state.get(payload['batch_id'])
            if pair is None:
                raise LedgerError('trial result for unregistered batch')
            planned, done = pair
            trial = payload['trial_id']
            if trial not in planned or trial in done:
                raise LedgerError('unexpected or duplicate trial result')
            done.add(trial)

    def _read_file(self, path: Path, *, limit: int = MAX_EVENT_BYTES) -> bytes:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise LedgerError('unsafe journal record')
        try:
            return path.read_bytes()
        except OSError as exc:
            raise LedgerError('journal record unavailable') from exc

    def _replay_locked(self, *, expected_head: str | None = None,
                       allow_transaction: bool = False) -> tuple[str, list[dict[str, Any]], dict]:
        self._check_root()
        if (self.root / TXN_NAME).exists() and not allow_transaction:
            raise LedgerError('interrupted transaction needs owner recovery')
        genesis = self._read_file(self.root / 'genesis.json', limit=1024)
        if genesis != canonical_json(self.genesis):
            raise LedgerError('unexpected journal genesis')
        prev = digest(genesis)
        events: list[dict[str, Any]] = []
        state: dict[str, tuple[set[str], set[str]]] = {}
        items = sorted(self.root.iterdir(), key=lambda x: x.name)
        for path in items:
            if path.name in ('genesis.json', LOCK_NAME):
                continue
            if path.name == TXN_NAME and allow_transaction:
                continue
            match = ENTRY.fullmatch(path.name)
            if match is None or int(match[1]) != len(events) + 1:
                raise LedgerError('unknown journal item or missing sequence')
            raw = self._read_file(path)
            if digest(raw) != match[2]:
                raise LedgerError('event content digest mismatch')
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise LedgerError('invalid journal JSON') from exc
            if (type(event) is not dict or set(event) != {'seq', 'prev', 'kind', 'payload'}
                    or canonical_json(event) != raw or type(event['seq']) is not int
                    or event['seq'] != len(events) + 1 or event['prev'] != prev):
                raise LedgerError('invalid event schema, sequence or chain')
            self._apply(state, event['kind'], event['payload'])
            events.append(event)
            prev = digest(raw)
        if expected_head is not None and prev != expected_head:
            raise LedgerError('independently pinned head mismatch')
        return prev, events, state

    def replay(self, expected_head: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        with self._locked(write=False):
            head, events, _ = self._replay_locked(expected_head=expected_head)
            return head, events

    def append(self, kind: str, payload: dict[str, Any],
               expected_head: str | None = None) -> str:
        return self.append_many(((kind, payload),), expected_head=expected_head)

    def append_many(self, items: Iterable[tuple[str, dict[str, Any]]],
                    expected_head: str | None = None) -> str:
        """One bounded durable local transaction, with one replay per batch.

        Any error after the transaction sentinel is written keeps it in place.
        Owner must inspect/recover explicitly; the library never guesses whether
        a partially written batch was safely acknowledged.
        """
        work = tuple(items)
        if not 1 <= len(work) <= MAX_BATCH_EVENTS:
            raise LedgerError('empty or oversized append transaction')
        with self._locked(write=True):
            head, events, state = self._replay_locked(expected_head=expected_head)
            prepared = []
            prev = head
            for i, item in enumerate(work, start=1):
                if type(item) is not tuple or len(item) != 2:
                    raise LedgerError('invalid transaction item')
                kind, payload = item
                self._apply(state, kind, payload)
                seq = len(events) + i
                if seq > 99_999_999:
                    raise LedgerError('journal capacity exhausted')
                data = canonical_json({'seq': seq, 'prev': prev, 'kind': kind,
                                       'payload': payload})
                if len(data) > MAX_EVENT_BYTES:
                    raise LedgerError('journal record too large')
                prev = digest(data)
                prepared.append((self.root / f'{seq:08d}-{prev}.json', data))
            # All validation happens before the first data record is written.
            sentinel = canonical_json({'previous_head': head, 'expected_head': prev,
                                       'count': len(prepared)})
            self._create(self.root / TXN_NAME, sentinel)
            for path, data in prepared:
                self._create(path, data, sync_directory=False)
            _sync_dir(self.root)
            after, seen, _ = self._replay_locked(expected_head=prev, allow_transaction=True)
            if after != prev or len(seen) != len(events) + len(prepared):
                raise LedgerError('unverified journal append')
            if self._read_file(self.root / TXN_NAME) != sentinel:
                raise LedgerError('transaction sentinel changed')
            (self.root / TXN_NAME).unlink()
            _sync_dir(self.root)
            # Append has an ACK only after full replay and directory fsync.
            return after
