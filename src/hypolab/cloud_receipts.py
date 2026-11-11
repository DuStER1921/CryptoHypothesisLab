"""Owner-signed READ_STARTED acknowledgments for Development ONLY.

A signed receipt is released ONLY after an independent owner SQLite reservation,
Drive create+readback and owner SQLite ACK. Failure consumes the attempt and
requires owner review: no automatic retry. Protected banks never supported.
Research process must not hold this signer or owner SQLite DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, date, timedelta
import hashlib
import json
import re
from typing import Protocol

from .owner_journal import OwnerExposureJournal, OwnerJournalError, encode, hash_bytes
from .drive_backend import VerifiedDriveWriter, VerifiedDriveReader, DriveBoundaryError


class ReceiptError(PermissionError):
    pass


def utc_text(dt: datetime) -> str:
    if not isinstance(dt,datetime) or dt.tzinfo is None or dt.utcoffset().total_seconds()!=0:
        raise ReceiptError('UTC datetime required')
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')


def parse_utc(s: str) -> datetime:
    if type(s) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',s):
        raise ReceiptError('canonical UTC datetime required')
    try: return datetime.fromisoformat(s.replace('Z','+00:00'))
    except ValueError as e:raise ReceiptError('invalid datetime') from e


@dataclass(frozen=True)
class DevelopmentPermit:
    """Must be derived from externally verified signed grant, never user input.

    Owner authority is trusted to validate the grant outside the analyst runtime.
    """
    grant_id: str
    grant_sha256: str
    universe_sha256: str
    calendar_sha256: str
    experiment_id: str
    family: str
    period: str
    canonical_sha256: str
    window_start: datetime
    window_end: datetime
    expires: datetime

    def check(self, now: datetime):
        utc_text(now)
        for sha in (self.grant_sha256,self.universe_sha256,self.calendar_sha256,self.canonical_sha256):
            if type(sha) is not str or not re.fullmatch('[0-9a-f]{64}',sha):
                raise ReceiptError('invalid pinned identity')
        for ident in (self.grant_id,self.experiment_id):
            if type(ident) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}',ident):
                raise ReceiptError('invalid owner identity')
        if self.family not in ('klines','aggTrades','bookDepth'):
            raise ReceiptError('invalid family')
        if not re.fullmatch((r'\d{4}-\d\d-\d\d' if self.family=='bookDepth'
                             else r'\d{4}-\d\d'),self.period):
            raise ReceiptError('invalid family/period grain')
        try:
            p=date.fromisoformat(self.period if self.family=='bookDepth' else self.period+'-01')
        except ValueError as exc:
            raise ReceiptError('invalid period') from exc
        if self.family=='bookDepth':
            expected_end=p+timedelta(days=1)
        else:
            expected_end=date(p.year+(p.month==12),p.month%12+1,1)
        if (self.window_start.date()!=p or self.window_end.date()!=expected_end
                or self.window_start.hour or self.window_start.minute or self.window_start.second
                or self.window_end.hour or self.window_end.minute or self.window_end.second):
            raise ReceiptError('permit must match exact full frozen partition')
        low=datetime(2020,1,1,tzinfo=timezone.utc)
        high=datetime(2025,1,1,tzinfo=timezone.utc)
        if not(low<=self.window_start < self.window_end<=high and now<self.expires):
            raise ReceiptError('expired or non-Development permit')
        utc_text(self.window_start);utc_text(self.window_end);utc_text(self.expires)


@dataclass(frozen=True)
class SignedReadAck:
    body: bytes
    signature: bytes


class OwnerReceiptAuthority:
    def __init__(self, journal: OwnerExposureJournal, writer: VerifiedDriveWriter, private_signer):
        self.journal=journal
        self.writer=writer
        self.signer=private_signer

    def issue(self, *, frozen, grant_bytes: bytes, grant_signature: bytes,
              grant_public_key: bytes, pinned_grant_key_sha256: str,
              family: str, period: str, attempt_id: str, now: datetime) -> SignedReadAck:
        """Public owner entry point: ALWAYS verify the real signed Development grant.

        Calling this method cannot authorize arbitrary user-created permit objects.
        The signing key and this authority must never run inside the analyst process.
        """
        permit = permit_from_signed_development_grant(
            frozen=frozen,grant_bytes=grant_bytes,grant_signature=grant_signature,
            grant_public_key=grant_public_key,pinned_grant_key_sha256=pinned_grant_key_sha256,
            family=family,period=period,now=now)
        return self._issue_verified_permit(permit,attempt_id=attempt_id,now=now)

    def _issue_verified_permit(self, permit: DevelopmentPermit, *, attempt_id: str,
                               now: datetime) -> SignedReadAck:
        """Owner-internal helper; used directly only with fabricated test permits."""
        permit.check(now)
        if type(attempt_id) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,49}',attempt_id):
            raise ReceiptError('invalid attempt ID')
        timestamp=utc_text(now)
        def event(seq,previous):
            return {'v':1,'kind':'READ_STARTED','role':'DEVELOPMENT','scope':'REAL_DEVELOPMENT',
                    'seq':seq,'prev_head':previous,'attempt_id':attempt_id,
                    'grant_id':permit.grant_id,'grant_sha256':permit.grant_sha256,
                    'experiment_id':permit.experiment_id,
                    'universe_sha256':permit.universe_sha256,
                    'calendar_sha256':permit.calendar_sha256,
                    'family':permit.family,'period':permit.period,
                    'canonical_sha256':permit.canonical_sha256,
                    'started_at_utc':timestamp}
        reservation=self.journal.reserve(attempt_id=attempt_id,grant_id=permit.grant_id,
                                         partition_sha=permit.canonical_sha256,make_event=event)
        name=f'READ_STARTED-{reservation.sequence:08d}-{reservation.event_sha256[:24]}.json'
        # A failed upload is *not retried*, because Drive's outcome may be ambiguous.
        stored=self.writer.write_once(name,reservation.event_bytes)
        if stored.sha256!=reservation.event_sha256:
            raise ReceiptError('uploaded receipt SHA mismatch')
        self.journal.acknowledge(reservation,drive_id=stored.file_id)
        # Signature is never published to Drive; broker receives it out-of-band.
        payload={'v':1,'kind':'OWNER_READ_ACK','scope':'REAL_DEVELOPMENT',
                 'role':'DEVELOPMENT','attempt_id':attempt_id,
                 'seq':reservation.sequence,'prev_head':reservation.prev_head,
                 'file_id':stored.file_id,'file_name':stored.name,
                 'event_sha256':stored.sha256,'grant_sha256':permit.grant_sha256,
                 'canonical_sha256':permit.canonical_sha256,
                 'acknowledged_at_utc':timestamp}
        ack=encode(payload)
        signature=self.signer.sign(ack)
        if type(signature) is not bytes or len(signature)!=64:
            raise ReceiptError('invalid owner signature')
        return SignedReadAck(ack,signature)


def verify_read_ack(*, ack: SignedReadAck, permit: DevelopmentPermit,
                    pinned_owner_public_key: bytes, pinned_public_sha256: str,
                    reader: VerifiedDriveReader, now: datetime) -> dict:
    """Consumer-side: independent owner pubkey + Drive record must match.

    The same owner key/signing authority is NEVER exposed to this consumer.
    Caller must additionally establish that source provider has no write power.
    """
    permit.check(now)
    if (type(ack) is not SignedReadAck or not isinstance(ack.body,bytes)
            or not isinstance(ack.signature,bytes) or len(ack.signature)!=64
            or not isinstance(pinned_owner_public_key,bytes)
            or hashlib.sha256(pinned_owner_public_key).hexdigest()!=pinned_public_sha256):
        raise ReceiptError('bad signed ACK or untrusted key fingerprint')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(pinned_owner_public_key).verify(ack.signature,ack.body)
        v=json.loads(ack.body)
    except (ValueError,TypeError) as e:
        raise ReceiptError('owner signature or ACK invalid') from e
    except Exception as e:
        raise ReceiptError('owner signature invalid') from e
    if type(v) is not dict or encode(v)!=ack.body or set(v)!={
        'v','kind','scope','role','attempt_id','seq','prev_head','file_id','file_name',
        'event_sha256','grant_sha256','canonical_sha256','acknowledged_at_utc'}:
        raise ReceiptError('unknown ACK schema')
    if (v['v']!=1 or v['kind']!='OWNER_READ_ACK' or v['scope']!='REAL_DEVELOPMENT'
            or v['role']!='DEVELOPMENT' or v['grant_sha256']!=permit.grant_sha256
            or v['canonical_sha256']!=permit.canonical_sha256
            or v['seq']<1 or parse_utc(v['acknowledged_at_utc'])>now):
        raise ReceiptError('protected or mismatched ACK')
    raw=reader.read_exact(v['file_id'],name=v['file_name'],expected_sha=v['event_sha256'])
    try:event=json.loads(raw)
    except (TypeError,ValueError) as e:raise ReceiptError('invalid stored event') from e
    if (type(event) is not dict or encode(event)!=raw
            or event.get('v')!=1 or event.get('kind')!='READ_STARTED'
            or event.get('scope')!='REAL_DEVELOPMENT'
            or event.get('role')!='DEVELOPMENT'
            or event.get('seq')!=v['seq'] or event.get('prev_head')!=v['prev_head']
            or event.get('attempt_id')!=v['attempt_id']
            or event.get('grant_sha256')!=permit.grant_sha256
            or event.get('grant_id')!=permit.grant_id
            or event.get('experiment_id')!=permit.experiment_id
            or event.get('universe_sha256')!=permit.universe_sha256
            or event.get('calendar_sha256')!=permit.calendar_sha256
            or event.get('family')!=permit.family or event.get('period')!=permit.period
            or event.get('canonical_sha256')!=permit.canonical_sha256
            or event.get('started_at_utc')!=v['acknowledged_at_utc']):
        raise ReceiptError('Drive event inconsistent with signed ACK/permit')
    return {'status':'ACK_VERIFIED_DEV_ONLY','attempt_id':v['attempt_id'],
            'event_sha256':v['event_sha256'],'file_id':v['file_id']}


def permit_from_signed_development_grant(*, frozen, grant_bytes: bytes,
    grant_signature: bytes, grant_public_key: bytes, pinned_grant_key_sha256: str,
    family: str, period: str, now: datetime) -> DevelopmentPermit:
    """Owner-only bridge to existing Phase 2 signed-grant + frozen validators.

    Never accept a user-built DevelopmentPermit as owner approval. This bridge
    uses the original, pinned FrozenIndex and its exact partition allowlist.
    It does NOT read any market rows or sign the final read ACK.
    """
    from .admission import verify_development_grant
    grant=verify_development_grant(grant_bytes=grant_bytes,signature=grant_signature,
             trusted_public_key_bytes=grant_public_key,
             expected_public_key_sha256=pinned_grant_key_sha256,frozen=frozen,now=now)
    partition=frozen.pick(family,period)  # rejects quality failures/role boundaries
    grant.permits(family,period,partition.canonical_sha256)
    low,high=partition.bounds
    if not grant.start <= low < high <= grant.end:
        raise ReceiptError('partition crosses granted role')
    result=DevelopmentPermit(grant.grant_id,hash_bytes(grant_bytes),
                             grant.universe_sha256,grant.calendar_sha256,
                             grant.experiment_id,family,period,partition.canonical_sha256,
                             low,high,grant.expires)
    result.check(now)
    return result
