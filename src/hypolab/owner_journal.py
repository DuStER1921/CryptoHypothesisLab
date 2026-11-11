"""Independent owner-side SQLite exposure journal, never stored on Google Drive.

SQLite is stdlib and provides atomic local transactions and uniqueness. All
PENDING events remain consumed after a crash: no automatic retry/refund.
Only the owner controller may hold write access; analyst has NO copy of DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import sqlite3
from typing import Callable


class OwnerJournalError(RuntimeError):
    pass


ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


def encode(obj: dict) -> bytes:
    return (json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                       allow_nan=False)+'\n').encode()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ReservedEvent:
    attempt_id: str
    sequence: int
    prev_head: str
    event_bytes: bytes
    event_sha256: str


class OwnerExposureJournal:
    """Must live on an independent owner disk, not on Drive/FUSE/Colab scratch."""
    GENESIS = '0'*64

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        if (self.path.name in ('.','..') or not self.path.is_file() or self.path.is_symlink()
                or not self.path.parent.exists() or self.path.parent.is_symlink()):
            raise OwnerJournalError('invalid local owner journal path')
        if os.name == 'posix' and (self.path.parent.stat().st_mode & 0o077 or
                                   self.path.stat().st_mode & 0o077):
            raise OwnerJournalError('owner journal must be private mode 0700/0600')
        self._db = sqlite3.connect(self.path,timeout=5.0,isolation_level=None)
        try:
            self._db.execute('PRAGMA synchronous=FULL')
            # Journal mode is set at bootstrap; inspect without racing writers.
            mode=self._db.execute('PRAGMA journal_mode').fetchone()
            if not mode or mode[0].lower() != 'delete':
                raise OwnerJournalError('unsupported SQLite journal mode')
            self._db.execute('PRAGMA foreign_keys=ON')
            self._db.execute('PRAGMA busy_timeout=5000')
            try:
                version=self._db.execute(
                    'SELECT value FROM meta WHERE key=?',('schema',)).fetchone()
            except sqlite3.OperationalError as exc:
                raise OwnerJournalError('owner journal not initialized') from exc
            if version != ('2',):
                raise OwnerJournalError('owner journal schema not initialized')
        except BaseException:
            self._db.close()
            raise

    @classmethod
    def create_new(cls, db_path: Path):
        path = Path(db_path)
        if path.exists() or path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
            raise OwnerJournalError('create-only journal needs fresh private local path')
        if os.name == 'posix' and path.parent.stat().st_mode & 0o077:
            raise OwnerJournalError('owner directory must be mode 0700')
        # Open with restrictive permissions from the first instant; sqlite3's
        # default file creation could otherwise briefly expose an owner DB.
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
                   getattr(os,'O_NOFOLLOW',0),0o600)
        os.close(fd)
        con = sqlite3.connect(path,isolation_level=None)
        try:
            con.executescript('''
              PRAGMA journal_mode=DELETE;
              PRAGMA synchronous=FULL;
              CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              INSERT INTO meta VALUES ('schema','2');
              CREATE TABLE events (
                seq INTEGER PRIMARY KEY CHECK(seq > 0),
                attempt_id TEXT UNIQUE NOT NULL,
                grant_id TEXT NOT NULL,
                partition_sha TEXT NOT NULL,
                prev_head TEXT NOT NULL,
                event_bytes BLOB NOT NULL,
                event_sha TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PENDING','ACKED')),
                drive_id TEXT UNIQUE,
                UNIQUE(grant_id,partition_sha)
              );
              CREATE TABLE read_consumptions (
                attempt_id TEXT PRIMARY KEY REFERENCES events(attempt_id),
                event_sha TEXT UNIQUE NOT NULL,
                consumed_at_utc TEXT NOT NULL
              );
            ''')
        finally:
            con.close()
        if os.name == 'posix':
            os.chmod(path,0o600)
            fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(fd)
            finally:os.close(fd)
        return cls(path)

    def close(self):
        self._db.close()

    def reserve(self, *, attempt_id: str, grant_id: str, partition_sha: str,
                make_event: Callable[[int,str],dict]) -> ReservedEvent:
        if (any(type(x) is not str or not ID.fullmatch(x) for x in (attempt_id,grant_id))
                or type(partition_sha) is not str or not SHA.fullmatch(partition_sha)):
            raise OwnerJournalError('invalid request identity')
        try:
            self._db.execute('BEGIN IMMEDIATE')
            rows=self._db.execute('SELECT seq,event_sha,state FROM events ORDER BY seq DESC LIMIT 1').fetchall()
            if rows and rows[0][2] != 'ACKED':
                raise OwnerJournalError('uncertain PENDING exposure: manual recovery required')
            seq=(rows[0][0]+1) if rows else 1
            prev=rows[0][1] if rows else self.GENESIS
            event=make_event(seq,prev)
            if type(event) is not dict:
                raise OwnerJournalError('event factory must return canonical dict')
            raw=encode(event)
            sha=hash_bytes(raw)
            self._db.execute('''INSERT INTO events
                  (seq,attempt_id,grant_id,partition_sha,prev_head,event_bytes,event_sha,state)
                  VALUES (?,?,?,?,?,?,?,?)''',
                  (seq,attempt_id,grant_id,partition_sha,prev,raw,sha,'PENDING'))
            self._db.execute('COMMIT')
            return ReservedEvent(attempt_id,seq,prev,raw,sha)
        except BaseException as e:
            if self._db.in_transaction:self._db.execute('ROLLBACK')
            if isinstance(e,sqlite3.IntegrityError):
                raise OwnerJournalError('duplicate attempt or previously exposed grant/partition') from e
            raise

    def acknowledge(self, reserved: ReservedEvent, *, drive_id: str):
        if not drive_id or not isinstance(drive_id,str):
            raise OwnerJournalError('missing Drive ID')
        try:
            self._db.execute('BEGIN IMMEDIATE')
            row=self._db.execute('SELECT state,event_sha FROM events WHERE attempt_id=?',
                                 (reserved.attempt_id,)).fetchone()
            if row != ('PENDING',reserved.event_sha256):
                raise OwnerJournalError('missing/changed PENDING reservation')
            self._db.execute('UPDATE events SET state=?,drive_id=? WHERE attempt_id=?',
                             ('ACKED',drive_id,reserved.attempt_id))
            self._db.execute('COMMIT')
        except BaseException:
            if self._db.in_transaction:self._db.execute('ROLLBACK')
            raise

    def status(self,attempt_id: str):
        return self._db.execute('SELECT state,drive_id,event_sha FROM events WHERE attempt_id=?',
                                (attempt_id,)).fetchone()

    def consume_acked_once(self, *, attempt_id: str, drive_id: str,
                           event_sha256: str, consumed_at_utc: str) -> None:
        """Durably consume one ACK before opening source streams. No retry.

        Operator must protect the DB against rollback to an older backup.
        """
        if (type(attempt_id) is not str or ID.fullmatch(attempt_id) is None
                or type(drive_id) is not str or not drive_id
                or type(event_sha256) is not str or SHA.fullmatch(event_sha256) is None):
            raise OwnerJournalError('invalid source consumption identity')
        from datetime import datetime, timezone
        if (type(consumed_at_utc) is not str or
                re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',
                             consumed_at_utc) is None):
            raise OwnerJournalError('invalid UTC consumption timestamp')
        try:
            stamp=datetime.fromisoformat(consumed_at_utc.replace('Z','+00:00'))
            if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
                raise ValueError('not UTC')
        except ValueError as exc:
            raise OwnerJournalError('invalid consumed timestamp') from exc
        try:
            self._db.execute('BEGIN IMMEDIATE')
            row=self._db.execute('''SELECT state,drive_id,event_sha
                       FROM events WHERE attempt_id=?''',(attempt_id,)).fetchone()
            if row != ('ACKED',drive_id,event_sha256):
                raise OwnerJournalError('source read lacks matching durable ACK')
            self._db.execute('''INSERT INTO read_consumptions
                (attempt_id,event_sha,consumed_at_utc) VALUES (?,?,?)''',
                (attempt_id,event_sha256,consumed_at_utc))
            self._db.execute('COMMIT')
        except BaseException as exc:
            if self._db.in_transaction:
                self._db.execute('ROLLBACK')
            if isinstance(exc,sqlite3.IntegrityError):
                raise OwnerJournalError('READ_STARTED already consumed') from exc
            raise

    def consumed(self, attempt_id: str) -> bool:
        row=self._db.execute('SELECT 1 FROM read_consumptions WHERE attempt_id=?',
                             (attempt_id,)).fetchone()
        return row == (1,)
