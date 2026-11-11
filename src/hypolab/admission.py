"""Signature verification for *candidate* owner Development-only grants.

A valid signature is necessary but NOT sufficient to read market rows: the
independent, durable READ_STARTED receipt and genuinely read-only source access
must first be accepted. This module never returns file handles or datasets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re

from .contracts import Role, SPLITS, require_utc, utc
from .frozen import FrozenIndex, FrozenError, HEX


class GrantError(PermissionError):
    pass


def canonical_bytes(obj: dict) -> bytes:
    return (json.dumps(obj,sort_keys=True,ensure_ascii=True,separators=(',',':'),
                       allow_nan=False)+'\n').encode('utf-8')


@dataclass(frozen=True)
class VerifiedDevelopmentGrant:
    grant_id: str
    experiment_id: str
    universe_sha256: str
    calendar_sha256: str
    allowed_partitions: frozenset[tuple[str,str,str]]
    start: datetime
    end: datetime
    expires: datetime
    signer_fingerprint: str

    def permits(self, family: str, period: str, canonical_sha: str) -> None:
        if (family,period,canonical_sha) not in self.allowed_partitions:
            raise GrantError('partition not explicitly granted')


def verify_development_grant(*, grant_bytes: bytes, signature: bytes,
                             trusted_public_key_bytes: bytes, expected_public_key_sha256: str,
                             frozen: FrozenIndex, now: datetime) -> VerifiedDevelopmentGrant:
    """No key discovery and no caller-supplied unsigned role override.

    The trusted key fingerprint must be independently pinned in a private
    owner-controlled configuration; fetching it from the same grant is unsafe.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise GrantError('cryptography dependency unavailable') from exc
    require_utc(now)
    if (type(expected_public_key_sha256) is not str or not HEX.fullmatch(expected_public_key_sha256)
            or hashlib.sha256(trusted_public_key_bytes).hexdigest()!=expected_public_key_sha256):
        raise GrantError('untrusted owner key')
    if not isinstance(grant_bytes,bytes) or len(grant_bytes)>64*1024 or not isinstance(signature,bytes) or len(signature)!=64:
        raise GrantError('invalid signed grant input')
    try:
        Ed25519PublicKey.from_public_bytes(trusted_public_key_bytes).verify(signature,grant_bytes)
    except (InvalidSignature,ValueError,TypeError) as exc:
        raise GrantError('owner signature invalid') from exc
    try:
        g=json.loads(grant_bytes)
    except (ValueError,TypeError,UnicodeError) as exc:
        raise GrantError('invalid signed JSON') from exc
    fields={'v','grant_id','experiment_id','scope','role','universe_sha256',
            'calendar_sha256','start_utc','end_utc','expires_utc','partitions'}
    if type(g) is not dict or set(g)!=fields or canonical_bytes(g)!=grant_bytes:
        raise GrantError('grant not canonical/exact')
    if g['v']!=1 or g['scope']!='REAL_DEVELOPMENT' or g['role']!='DEVELOPMENT':
        raise GrantError('protected or unknown grant scope')
    if (g['universe_sha256']!=frozen.universe_sha256 or
            g['calendar_sha256']!=frozen.calendar_sha256):
        raise GrantError('wrong frozen identity')
    for k in ('grant_id','experiment_id'):
        if type(g[k]) is not str or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}',g[k]):
            raise GrantError('invalid grant ID')
    try:
        start,end,expiry=(utc(g[k]) for k in ('start_utc','end_utc','expires_utc'))
    except (TypeError,ValueError) as exc:
        raise GrantError('invalid grant dates') from exc
    if not SPLITS[Role.DEVELOPMENT].contains(start,end) or now>=expiry or now<start:
        raise GrantError('not active or outside Development')
    if (type(g['partitions']) is not list or not 1<=len(g['partitions'])<=100 or
            len({(x.get('family'),x.get('period')) for x in g['partitions'] if type(x) is dict}) != len(g['partitions'])):
        raise GrantError('missing/duplicate partition scope')
    allowed=[]
    for e in g['partitions']:
        if type(e) is not dict or set(e)!={'family','period','canonical_sha256'}:
            raise GrantError('invalid partition grant entry')
        try:
            p=frozen.pick(e['family'],e['period'])
        except (FrozenError,KeyError,TypeError) as exc:
            raise GrantError('unavailable partition in grant') from exc
        if p.canonical_sha256!=e['canonical_sha256']:
            raise GrantError('partition SHA not frozen')
        low,high=p.bounds
        if not start<=low<high<=end:
            raise GrantError('grant window not complete partition')
        allowed.append((p.family,p.period,p.canonical_sha256))
    return VerifiedDevelopmentGrant(g['grant_id'],g['experiment_id'],
                                    frozen.universe_sha256,frozen.calendar_sha256,
                                    frozenset(allowed),start,end,expiry,expected_public_key_sha256)
