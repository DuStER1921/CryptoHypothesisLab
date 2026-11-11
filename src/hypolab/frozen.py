"""Offline inspection of a pinned frozen-universe snapshot; never an access grant.

The universe/ledger files are supplied by an operator in a private input directory.
No files are modified and no source warehouse directories are searched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import hashlib
import io
import zipfile
import json
import re

from .contracts import Role, SPLITS, utc

HEX = re.compile(r"[0-9a-f]{64}\Z")
FAMILIES = ("klines", "aggTrades", "bookDepth")
NAMES = {f: f"partition-ledger-{f}.json" for f in FAMILIES}


class FrozenError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_read(root: Path, name: str, *, cap: int = 16_777_216) -> bytes:
    """Metadata only; no arbitrary filename, symlink or directory walking."""
    if name not in {*NAMES.values(), "universe.json", "calendar.json",
                    "freeze-receipt.json", "FREEZE-COMMITTED.json", "reviewed-evidence-bundle.zip"}:
        raise FrozenError("unexpected frozen metadata file")
    if root.is_symlink() or not root.is_dir() or (root / name).is_symlink():
        raise FrozenError("unsafe metadata root or symlink")
    p = root / name
    if not p.is_file() or not 0 < p.stat().st_size <= cap:
        raise FrozenError("missing or oversized metadata")
    with p.open("rb") as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise FrozenError("oversized metadata")
    return raw


def _json(raw: bytes) -> dict:
    def no_duplicate(pairs):
        obj = {}
        for k,v in pairs:
            if k in obj:
                raise FrozenError("duplicate JSON key")
            obj[k] = v
        return obj
    try:
        obj = json.loads(raw, object_pairs_hook=no_duplicate,
                         parse_constant=lambda _: (_ for _ in ()).throw(FrozenError("invalid JSON constant")))
    except (ValueError, UnicodeDecodeError, TypeError) as e:
        raise FrozenError("invalid metadata JSON") from e
    if type(obj) is not dict or json.dumps(obj,sort_keys=True,ensure_ascii=False,
       separators=(",",":"),allow_nan=False).encode("utf-8") != raw:
        raise FrozenError("metadata not canonical JSON")
    return obj


def _bounds(family: str, period: str):
    if family == "bookDepth":
        if not re.fullmatch(r"\d{4}-\d\d-\d\d",period):
            raise FrozenError("invalid daily period")
        d = date.fromisoformat(period)
        return utc(d.isoformat()+"T00:00:00Z"), utc((d+timedelta(days=1)).isoformat()+"T00:00:00Z")
    if not re.fullmatch(r"\d{4}-\d\d",period):
        raise FrozenError("invalid monthly period")
    d = date.fromisoformat(period+"-01")
    month2=date(d.year+(d.month==12), d.month%12+1,1)
    return utc(d.isoformat()+"T00:00:00Z"),utc(month2.isoformat()+"T00:00:00Z")


@dataclass(frozen=True)
class PinnedPartition:
    family: str
    period: str
    logical_dataset_id: str
    generation: str
    canonical_sha256: str
    manifest_sha256: str
    source_sha256: str

    def __post_init__(self):
        _bounds(self.family,self.period)
        for v in (self.generation,self.canonical_sha256,self.manifest_sha256,self.source_sha256):
            if type(v) is not str or not HEX.fullmatch(v):
                raise FrozenError("invalid partition SHA")
        if self.logical_dataset_id != ("binance-usdm-BTCUSDT-klines-1m" if self.family == "klines"
                                       else f"binance-usdm-BTCUSDT-{self.family}"):
            raise FrozenError("unsupported dataset identity")

    @property
    def bounds(self):
        return _bounds(self.family,self.period)


@dataclass(frozen=True)
class FrozenIndex:
    universe_sha256: str
    calendar_sha256: str
    partitions: dict[tuple[str,str],PinnedPartition]
    c3_pass_days: frozenset[str]

    @classmethod
    def load(cls, root: Path, expected_universe_sha256: str, expected_calendar_sha256: str):
        """Bound only to pre-pinned external digests, never a self-declared latest root."""
        if not all(type(h) is str and HEX.fullmatch(h) for h in
                   (expected_universe_sha256,expected_calendar_sha256)):
            raise FrozenError("explicit expected frozen SHA required")
        # The original freeze is EIGHT files, not just an unsigned ledger set.
        required = {*NAMES.values(), "universe.json", "calendar.json",
                    "freeze-receipt.json", "FREEZE-COMMITTED.json", "reviewed-evidence-bundle.zip"}
        if {x.name for x in root.iterdir()} != required:
            raise FrozenError("incomplete or unexpected frozen support artifact")
        u_raw=_safe_read(root,"universe.json")
        c_raw=_safe_read(root,"calendar.json")
        if _sha(u_raw)!=expected_universe_sha256 or _sha(c_raw)!=expected_calendar_sha256:
            raise FrozenError("frozen universe/calendar SHA mismatch")
        u,c=_json(u_raw),_json(c_raw)
        receipt_raw=_safe_read(root,"freeze-receipt.json")
        marker_raw=_safe_read(root,"FREEZE-COMMITTED.json")
        receipt,marker=_json(receipt_raw),_json(marker_raw)
        if (receipt.get("freeze_receipt_version")!=2 or marker.get("marker_version")!=2 or
            marker.get("status")!="FROZEN_BYTES_WRITTEN_AND_FUSE_RECHECKED" or
            receipt.get("write_contract","").split(";")[0]!="create_only" or
            receipt.get("universe_sha256")!=expected_universe_sha256 or
            marker.get("universe_sha256")!=expected_universe_sha256 or
            receipt.get("calendar_sha256")!=expected_calendar_sha256 or
            marker.get("calendar_sha256")!=expected_calendar_sha256 or
            marker.get("receipt_sha256")!=_sha(receipt_raw) or
            any(receipt.get(k) is not False for k in
                ("research_authorized","validation_authorized","oos_authorized")) or
            marker.get("research_authorized") is not False or
            u.get("universe_id")!=receipt.get("universe_id")):
            raise FrozenError("frozen receipt/committed marker mismatch")
        supports={*NAMES.values(),"reviewed-evidence-bundle.zip"}
        declared=marker.get("frozen_files_sha256")
        if (type(declared) is not dict or set(declared)!=supports or
                declared!=receipt.get("frozen_support_file_sha256")):
            raise FrozenError("missing frozen support SHA chain")
        for name in supports:
            raw=_safe_read(root,name,cap=20*1024*1024)
            if _sha(raw)!=declared[name]:
                raise FrozenError("frozen support artifact changed")
        if _sha(_safe_read(root,"reviewed-evidence-bundle.zip",cap=20*1024*1024))!=receipt.get("reviewed_source_bundle_sha256"):
            raise FrozenError("frozen evidence bundle mismatch")
        if (u.get("universe_version")!=1 or u.get("symbol")!="BTCUSDT" or
            u.get("availability_calendar_sha256")!=expected_calendar_sha256 or
            type(u.get("families")) is not dict or set(u["families"])!=set(FAMILIES) or
            c.get("research_authorized",False) is True or c.get("role_allocation") is not None):
            raise FrozenError("unsupported universe or untrusted role allocation")
        entries={}
        for family in FAMILIES:
            raw=_safe_read(root,NAMES[family])
            cfg=u["families"][family]
            if _sha(raw)!=cfg.get("partition_evidence_sha256"):
                raise FrozenError("partition ledger digest mismatch")
            ledger=_json(raw)
            if (ledger.get("family")!=family or ledger.get("symbol")!="BTCUSDT" or
                ledger.get("ledger_version")!=1 or type(ledger.get("entries")) is not list):
                raise FrozenError("unexpected ledger metadata")
            periods=[]
            for item in ledger["entries"]:
                if type(item) is not dict or set(item)!={"period","logical_dataset_id","generation","source_sha256","canonical_sha256","manifest_sha256"}:
                    raise FrozenError("unexpected partition fields")
                p=PinnedPartition(family=family,**item)
                key=(family,p.period)
                if key in entries:
                    raise FrozenError("duplicate partition")
                entries[key]=p; periods.append(p.period)
            if (periods!=sorted(set(periods)) or
                len(periods)!=cfg["coverage"]["expected_partitions"] or
                periods[0]!=cfg["coverage"]["start"] or
                periods[-1]!=cfg["coverage"]["end"]):
                raise FrozenError("unexpected ledger coverage")
            for earlier,later in zip(periods,periods[1:]):
                if _bounds(family,earlier)[1]!=_bounds(family,later)[0]:
                    raise FrozenError("missing partition in calendar sequence")
            generations={r["generation"] for r in cfg["generations"]}
            if {entries[family,p].generation for p in periods}!=generations:
                raise FrozenError("unexpected generation")
        days=c.get("days")
        if type(days) is not list or not days:
            raise FrozenError("missing availability calendar")
        pass_days=[]
        prev=None
        for d in days:
            if type(d) is not dict:
                raise FrozenError("invalid calendar entry")
            day=d.get("date")
            if type(day) is not str:
                raise FrozenError("missing calendar date")
            current=date.fromisoformat(day)
            if prev is not None and current != prev + timedelta(days=1):
                raise FrozenError("calendar gap/duplicate")
            prev=current
            if d.get("book_depth_pass_candidate") is True:
                if d.get("book_depth_quality")!="PASS" or ("bookDepth",day) not in entries:
                    raise FrozenError("false C3 quality candidate")
                pass_days.append(day)
        # C3 quality must match the independently accepted evidence inside
        # the pinned reviewed bundle, not calendar flags alone.
        qc=u['families']['bookDepth'].get('quality')
        if (type(qc) is not dict or qc.get('eligibility_rule')!='PASS_ONLY' or
            qc.get('eligibility_decision_sha256')!=receipt.get('accepted_owner_decision_sha256')):
            raise FrozenError('C3 quality decision not pinned')
        quality_sha=qc.get('eligibility_ledger_sha256')
        owner_sha=qc.get('eligibility_decision_sha256')
        selected={quality_sha:[],owner_sha:[]}
        if (len(selected)!=2 or any(type(h) is not str or not HEX.fullmatch(h) for h in selected)):
            raise FrozenError('C3 quality evidence hash invalid')
        with zipfile.ZipFile(io.BytesIO(_safe_read(root,'reviewed-evidence-bundle.zip',cap=20*1024*1024))) as archive:
            members=archive.infolist()
            if len(members)>64 or len({i.filename for i in members})!=len(members):
                raise FrozenError('unsafe evidence bundle')
            for info in members:
                if info.is_dir() or not info.filename.endswith('.json'):
                    continue
                if info.file_size>16*1024*1024:
                    raise FrozenError('oversized evidence member')
                raw=archive.read(info)
                d=_sha(raw)
                if d in selected:selected[d].append(raw)
        if any(len(x)!=1 for x in selected.values()):
            raise FrozenError('missing/duplicate quality and owner proof')
        quality=json.loads(selected[quality_sha][0]);owner=json.loads(selected[owner_sha][0])
        if (owner.get('owner_approved') is not True or owner.get('rule')!='PASS_ONLY' or
            owner.get('research_authorized') is not False or
            owner.get('eligible_periods')!=pass_days or
            owner.get('quality_ledger_sha256')!=quality_sha):
            raise FrozenError('owner PASS_ONLY decision conflict')
        rows=quality.get('entries')
        if type(rows) is not list or len(rows)!=sum(k[0]=='bookDepth' for k in entries):
            raise FrozenError('C3 quality ledger incomplete')
        quality_by_date={}
        for row in rows:
            if type(row) is not dict or row.get('status') not in {'PASS','WARN','QUARANTINE'}:
                raise FrozenError('unknown C3 quality')
            day=row.get('period');partition=entries.get(('bookDepth',day))
            if (day in quality_by_date or partition is None or
                row.get('generation')!=partition.generation or
                row.get('canonical_sha256')!=partition.canonical_sha256 or
                row.get('manifest_sha256')!=partition.manifest_sha256):
                raise FrozenError('C3 quality/partition binding mismatch')
            quality_by_date[day]=row['status']
        for d in days:
            if (d.get('book_depth_quality')!=quality_by_date.get(d['date']) or
                (d.get('book_depth_pass_candidate') is True)!=(quality_by_date.get(d['date'])=='PASS')):
                raise FrozenError('calendar vs C3 quality drift')
        return cls(expected_universe_sha256,expected_calendar_sha256,entries,frozenset(pass_days))

    def pick(self,family: str,period: str,role: Role=Role.DEVELOPMENT)->PinnedPartition:
        if role is not Role.DEVELOPMENT:
            raise FrozenError("protected period sealed")
        p=self.partitions.get((family,period))
        if p is None:
            raise FrozenError("partition absent")
        if not SPLITS[Role.DEVELOPMENT].contains(*p.bounds):
            raise FrozenError("partition outside Development")
        if family=="bookDepth" and period not in self.c3_pass_days:
            raise FrozenError("C3 not PASS candidate")
        return p
