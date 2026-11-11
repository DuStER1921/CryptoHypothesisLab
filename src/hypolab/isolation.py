"""Explicit runtime-identity separation policy, independent from Drive ACL setup.

Actual provider permissions must be verified out-of-band using a distinct
Google identity; this module never pretends to revoke account-level rights.
"""
from __future__ import annotations

from dataclasses import dataclass


class IsolationError(PermissionError):
    pass


@dataclass(frozen=True)
class RuntimeRoles:
    # These are opaque owner-reviewed identifiers, not email/password/Drive IDs.
    source_reader_identity: str
    private_writer_identity: str
    analyst_identity: str
    source_effective_readonly_verified: bool
    writer_has_no_source_access_verified: bool
    analyst_has_no_source_access_verified: bool
    source_mount_absent_from_analyst: bool

    def require_separated(self):
        names=(self.source_reader_identity,self.private_writer_identity,self.analyst_identity)
        if any(not isinstance(x,str) or not x or len(x)>120 for x in names):
            raise IsolationError('missing separately established identity')
        if len(set(names))!=3:
            raise IsolationError('reader/writer/analyst identities must be different')
        if not all((self.source_effective_readonly_verified,
                    self.writer_has_no_source_access_verified,
                    self.analyst_has_no_source_access_verified,
                    self.source_mount_absent_from_analyst)):
            raise IsolationError('effective account permissions and mounts unverified')
        return True
