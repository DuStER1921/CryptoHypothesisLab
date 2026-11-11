"""Owner-side one-use Development admission; never used inside analyst process.

This trusted orchestration executes in an owner-controlled OS process with an
independently pinned FrozenIndex, signed grant, local persistent SQLite and a
read-only source opener. No automatic OAuth, Drive mounts or path discovery.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import BinaryIO, Protocol

from .cloud_receipts import (SignedReadAck, ReceiptError,
                             permit_from_signed_development_grant,
                             verify_read_ack, utc_text)
from .drive_backend import VerifiedDriveReader
from .frozen import FrozenIndex, PinnedPartition
from .owner_journal import OwnerExposureJournal
from .staging import ReadOnlyStager, StagedPartition


class ReadOnlyPinnedSource(Protocol):
    """Source files are opened only on enter and only for exact frozen identity.

    Implementations must be credentialed with drive.readonly ONLY. The broker
    cannot prove this protocol is implemented honestly; owner reviews adapter.
    """
    def open_pinned(self, partition: PinnedPartition) -> AbstractContextManager[
        tuple[BinaryIO, BinaryIO]]: ...


class OwnerOneUseBroker:
    """Verify grant + owner ACK + cloud bytes, durably consume, then open source.

    No public convenience flags or unverified permit instance can bypass the
    signed grant. Crash after consumption burns the attempt. A new owner grant
    and a different attempt are required for a different source read.
    """
    def __init__(self, *, frozen: FrozenIndex, journal: OwnerExposureJournal,
                 receipt_reader: VerifiedDriveReader, stager: ReadOnlyStager,
                 source: ReadOnlyPinnedSource,
                 pinned_grant_key: bytes, grant_key_fingerprint: str,
                 pinned_owner_key: bytes, owner_key_fingerprint: str):
        self.frozen = frozen
        self.journal = journal
        self.receipt_reader = receipt_reader
        self.stager = stager
        self.source = source
        self.grant_key = pinned_grant_key
        self.grant_key_sha = grant_key_fingerprint
        self.owner_key = pinned_owner_key
        self.owner_key_sha = owner_key_fingerprint

    def stage_once(self, *, grant_bytes: bytes, grant_signature: bytes,
                   signed_ack: SignedReadAck, family: str, period: str,
                   now: datetime) -> StagedPartition:
        permit = permit_from_signed_development_grant(
            frozen=self.frozen, grant_bytes=grant_bytes,
            grant_signature=grant_signature, grant_public_key=self.grant_key,
            pinned_grant_key_sha256=self.grant_key_sha,
            family=family, period=period, now=now)
        partition = self.frozen.pick(family, period)
        if (partition.canonical_sha256 != permit.canonical_sha256 or
                partition.manifest_sha256 is None):
            raise ReceiptError('frozen source/permit mismatch')
        evidence = verify_read_ack(
            ack=signed_ack, permit=permit, pinned_owner_public_key=self.owner_key,
            pinned_public_sha256=self.owner_key_sha, reader=self.receipt_reader,
            now=now)
        # MUST complete the SQLite COMMIT before calling source.open_pinned.
        # A crash or failed open/stage consumes the permit; NO auto retry.
        self.journal.consume_acked_once(
            attempt_id=evidence['attempt_id'], drive_id=evidence['file_id'],
            event_sha256=evidence['event_sha256'], consumed_at_utc=utc_text(now))
        with self.source.open_pinned(partition) as (manifest, parquet):
            return self.stager.stage(partition, manifest_stream=manifest,
                                     parquet_stream=parquet)
