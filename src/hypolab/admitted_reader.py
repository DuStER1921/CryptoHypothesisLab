"""Gate for real data orchestration, intentionally incomplete until durable receipt.

This module separates verified authorization from physical source access. The
caller cannot pass a bare ``authorized=True`` flag or choose its own default
key. The real read plane remains default-deny until an independently accepted
trusted receipt backend and read-only credentials exist.
"""
from __future__ import annotations

from typing import BinaryIO

from .admission import VerifiedDevelopmentGrant, GrantError
from .frozen import PinnedPartition
from .staging import ReadOnlyStager, StagedPartition


class ResearchAccessNotReady(PermissionError):
    pass


class GovernedRealReader:
    def __init__(self, stager: ReadOnlyStager):
        self.stager=stager

    def stage_real(self, partition: PinnedPartition, grant: VerifiedDevelopmentGrant,
                   *, manifest_stream: BinaryIO, parquet_stream: BinaryIO,
                   durable_receipt_ack: object | None = None) -> StagedPartition:
        """No accepted production receipt issuer yet; always deny before bytes."""
        if type(grant) is not VerifiedDevelopmentGrant:
            raise ResearchAccessNotReady('verified owner grant required')
        grant.permits(partition.family,partition.period,partition.canonical_sha256)
        # Intentionally no trusted receipt implementation. A user-created object
        # is not permission; DO NOT weaken this guard by checking a Boolean.
        raise ResearchAccessNotReady('real read blocked: trusted durable READ_STARTED not accepted')
