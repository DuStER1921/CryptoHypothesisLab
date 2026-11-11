"""All metadata, Parquet and grant material is randomly synthetic."""
import hashlib
import io
import zipfile
import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta

from hypolab.admission import (verify_development_grant, GrantError, canonical_bytes,
                               VerifiedDevelopmentGrant)
from hypolab.admitted_reader import GovernedRealReader, ResearchAccessNotReady
from hypolab.contracts import Role, utc
from hypolab.frozen import FrozenIndex, FrozenError, PinnedPartition
from hypolab.staging import ReadOnlyStager, StageError
from hypolab.parquet_window import ParquetWindowReader, ParquetWindowError

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa=pq=None
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError:
    Ed25519PrivateKey=None


def canonical(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def synthetic_frozen(root):
    entries={}
    c3dates={'2024-12-31','2025-01-01'}
    for family in ('klines','aggTrades','bookDepth'):
        periods=sorted(c3dates) if family=='bookDepth' else ['2024-12','2025-01']
        rows=[]
        for period in periods:
            rows.append({'period':period,
                         'logical_dataset_id':('binance-usdm-BTCUSDT-klines-1m' if family=='klines' else f'binance-usdm-BTCUSDT-{family}'),
                         'generation': 'a'*64,'source_sha256':'b'*64,
                         'canonical_sha256':'c'*64,'manifest_sha256':'d'*64})
        entries[family]=rows
    days=[{'date':'2024-12-31','book_depth_pass_candidate':True,'book_depth_quality':'PASS'},
          {'date':'2025-01-01','book_depth_pass_candidate':False,'book_depth_quality':'WARN'}]
    quality={'entries':[{**r,'status':'PASS' if r['period']=='2024-12-31' else 'WARN'}
                         for r in entries['bookDepth']]}
    quality_raw=canonical(quality)
    owner={'owner_approved':True,'rule':'PASS_ONLY','research_authorized':False,
           'eligible_periods':['2024-12-31'],'quality_ledger_sha256':sha(quality_raw)}
    owner_raw=canonical(owner)
    bundle=io.BytesIO()
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('quality.json',quality_raw)
        archive.writestr('owner.json',owner_raw)
    (root/'reviewed-evidence-bundle.zip').write_bytes(bundle.getvalue())
    cal=canonical({'days':days,'role_allocation':None,'research_authorized':False})
    (root/'calendar.json').write_bytes(cal)
    u={'universe_version':1,'universe_id':'synthetic-1','symbol':'BTCUSDT',
       'availability_calendar_sha256':sha(cal),
       'families':{f:{'partition_evidence_sha256':None,
        'coverage':{'start':rows[0]['period'],'end':rows[-1]['period'],
                    'expected_partitions':len(rows)},
        'generations':[{'generation':'a'*64}],
        'quality':{'eligibility_rule':'PASS_ONLY','eligibility_ledger_sha256':sha(quality_raw),
                   'eligibility_decision_sha256':sha(owner_raw)} if f=='bookDepth' else None}
        for f,rows in entries.items()}}
    for f,rows in entries.items():
        raw=canonical({'ledger_version':1,'family':f,'symbol':'BTCUSDT','entries':rows})
        (root/f'partition-ledger-{f}.json').write_bytes(raw)
        u['families'][f]['partition_evidence_sha256']=sha(raw)
    u_raw=canonical(u)
    (root/'universe.json').write_bytes(u_raw)
    support={f:sha((root/f).read_bytes()) for f in
             ('partition-ledger-klines.json','partition-ledger-aggTrades.json',
              'partition-ledger-bookDepth.json','reviewed-evidence-bundle.zip')}
    receipt={'freeze_receipt_version':2,'universe_id':'synthetic-1',
             'universe_sha256':sha(u_raw),'calendar_sha256':sha(cal),
             'write_contract':'create_only;synthetic',
             'research_authorized':False,'validation_authorized':False,
             'oos_authorized':False,'accepted_owner_decision_sha256':sha(owner_raw),
             'reviewed_source_bundle_sha256':sha(bundle.getvalue()),
             'frozen_support_file_sha256':support}
    receipt_raw=canonical(receipt)
    (root/'freeze-receipt.json').write_bytes(receipt_raw)
    marker={'marker_version':2,'status':'FROZEN_BYTES_WRITTEN_AND_FUSE_RECHECKED',
            'universe_sha256':sha(u_raw),'calendar_sha256':sha(cal),
            'receipt_sha256':sha(receipt_raw),'research_authorized':False,
            'frozen_files_sha256':support}
    (root/'FREEZE-COMMITTED.json').write_bytes(canonical(marker))
    return FrozenIndex.load(root,sha(u_raw),sha(cal))


def synthetic_pair(identity, raw=b'fake parquet for read-stream checks'):
    m={'manifest_version':1 if identity.family=='klines' else 3,
       'provider':'binance','datatype':identity.family,'symbol':'BTCUSDT','market':'usdm',
       'logical_dataset_id':identity.logical_dataset_id,'canonical_name':'data.parquet',
       'canonical_sha256':sha(raw),'source_sha256':identity.source_sha256,
       'year':2024,'month':12,'row_count':1,
       'validation':{'checksum':'pass','schema':'pass','temporal':'pass',
         'structural':'pass','read_back':'pass'}}
    if identity.family=='klines':
        m['interval']='1m'
        m['schema_version']='binance-usdm-klines-1m-v1'
    else:
        m['generation']=identity.generation
    if identity.family=='bookDepth':m['day']=31
    m_raw=canonical(m)
    p=PinnedPartition(identity.family,identity.period,identity.logical_dataset_id,
                      identity.generation,sha(raw),sha(m_raw),identity.source_sha256)
    return p,m_raw,raw


class FrozenTest(unittest.TestCase):
    def test_frozen_ledger_hash_and_no_implicit_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);idx=synthetic_frozen(root)
            self.assertEqual(idx.pick('klines','2024-12').period,'2024-12')
            self.assertEqual(idx.pick('bookDepth','2024-12-31').period,'2024-12-31')
            with self.assertRaises(FrozenError):idx.pick('aggTrades','2025-01')
            with self.assertRaises(FrozenError):idx.pick('bookDepth','2025-01-01')
            with self.assertRaises(FrozenError):idx.pick('klines','2024-12',role=Role.VALIDATION)
            (root/'partition-ledger-klines.json').write_bytes(b'{}\n')
            with self.assertRaises(FrozenError):FrozenIndex.load(root,idx.universe_sha256,idx.calendar_sha256)

    def test_tampered_calendar_and_implicit_universe_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);idx=synthetic_frozen(root)
            with self.assertRaises(FrozenError):FrozenIndex.load(root,'a'*64,idx.calendar_sha256)
            (root/'calendar.json').write_bytes(b'{"days":[]}\n')
            with self.assertRaises(FrozenError):FrozenIndex.load(root,idx.universe_sha256,idx.calendar_sha256)


@unittest.skipUnless(os.name=='posix','local POSIX only')
class StageTest(unittest.TestCase):
    def fixture(self,d):
        root=Path(d);source=root/'source';scratch=root/'scratch';source.mkdir();scratch.mkdir(mode=0o700)
        idx=synthetic_frozen(source)
        identity,manifest,parquet=synthetic_pair(idx.pick('klines','2024-12'))
        (source/'manifest.json').write_bytes(manifest)
        (source/'data.bin').write_bytes(parquet)
        return ReadOnlyStager(scratch),source,identity,manifest,parquet

    def test_stream_staging_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,parquet=self.fixture(d)
            before={x.name:sha(x.read_bytes()) for x in source.glob('*') if x.is_file()}
            stage=stager.stage(p,manifest_stream=io.BytesIO(manifest),parquet_stream=io.BytesIO(parquet))
            self.assertEqual(stage.parquet_path.read_bytes(),parquet)
            self.assertEqual(before,{x.name:sha(x.read_bytes()) for x in source.glob('*') if x.is_file()})
            self.assertEqual(stage.local_dir.stat().st_mode&0o077,0)

    def test_real_c1_v1_contract_has_no_generation_but_keeps_full_sha_binding(self):
        with tempfile.TemporaryDirectory() as d:
            stager,_,identity,manifest,parquet=self.fixture(d)
            parsed=json.loads(manifest)
            self.assertEqual(parsed['manifest_version'],1)
            self.assertNotIn('generation',parsed)
            stage=stager.stage(identity,manifest_stream=io.BytesIO(manifest),
                               parquet_stream=io.BytesIO(parquet))
            self.assertTrue(stage.source_verified)

    def test_c1_v1_generation_not_silently_fabricated_or_missing_version_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            stager,_,identity,manifest,parquet=self.fixture(d)
            for change in ({'generation':identity.generation},
                           {'manifest_version':None},
                           {'logical_dataset_id':'different'},
                           {'schema_version':'unknown'},
                           {'provider':'different'}):
                with self.subTest(change=change):
                    fake={**json.loads(manifest),**change}
                    raw=canonical(fake)
                    # Even if a malicious caller re-pins this synthetic hash,
                    # the structure contract must fail closed.
                    other=PinnedPartition(identity.family,identity.period,
                                          identity.logical_dataset_id,identity.generation,
                                          identity.canonical_sha256,sha(raw),identity.source_sha256)
                    with self.assertRaises(StageError):
                        stager.stage(other,manifest_stream=io.BytesIO(raw),
                                     parquet_stream=io.BytesIO(parquet))
            self.assertEqual(list(stager.cache_parent.iterdir()),[])

    def test_c2_v3_generation_still_required(self):
        with tempfile.TemporaryDirectory() as d:
            stager,_,ident,_,parquet=self.fixture(d)
            c2=PinnedPartition('aggTrades',ident.period,
                               'binance-usdm-BTCUSDT-aggTrades',ident.generation,
                               ident.canonical_sha256,ident.manifest_sha256,ident.source_sha256)
            c2,m,parquet=synthetic_pair(c2,parquet)
            good=stager.stage(c2,manifest_stream=io.BytesIO(m),
                              parquet_stream=io.BytesIO(parquet))
            self.assertTrue(good.source_verified)
            for change in ({'generation':None},{'generation':'f'*64}):
                bad={**json.loads(m),**change}
                raw=canonical(bad)
                modified=PinnedPartition(c2.family,c2.period,c2.logical_dataset_id,
                                         c2.generation,c2.canonical_sha256,sha(raw),c2.source_sha256)
                with self.subTest(change=change),self.assertRaises(StageError):
                    stager.stage(modified,manifest_stream=io.BytesIO(raw),
                                 parquet_stream=io.BytesIO(parquet))

    def test_tampered_source_rejected_and_temp_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,parquet=self.fixture(d)
            with self.assertRaises(StageError):
                stager.stage(p,manifest_stream=io.BytesIO(manifest),parquet_stream=io.BytesIO(b'tampered'))
            self.assertEqual(list(stager.cache_parent.iterdir()),[])

    def test_bad_manifest_denied_before_parquet_bytes(self):
        class Unreadable:
            def read(self,*args):raise AssertionError('parquet must never be read')
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,parquet=self.fixture(d)
            with self.assertRaises(StageError):
                stager.stage(p,manifest_stream=io.BytesIO(b'bad'),parquet_stream=Unreadable())

    def test_short_read_and_size_cap(self):
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,parquet=self.fixture(d)
            original=ReadOnlyStager.MAX_DATA
            try:
                ReadOnlyStager.MAX_DATA=8
                with self.assertRaises(StageError):
                    stager.stage(p,manifest_stream=io.BytesIO(manifest),parquet_stream=io.BytesIO(parquet))
            finally:ReadOnlyStager.MAX_DATA=original

    def test_real_reader_denies_even_with_fake_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,parquet=self.fixture(d)
            class BadSource:
                def read(self,*args):raise AssertionError('forbidden source opened')
            with self.assertRaises(ResearchAccessNotReady):
                GovernedRealReader(stager).stage_real(p,None,manifest_stream=BadSource(),parquet_stream=BadSource())


@unittest.skipUnless(Ed25519PrivateKey is not None,'cryptography optional')
class GrantTest(unittest.TestCase):
    def test_signed_grant_and_rejections(self):
        with tempfile.TemporaryDirectory() as d:
            idx=synthetic_frozen(Path(d));p=idx.pick('klines','2024-12')
            key=Ed25519PrivateKey.generate()
            public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            g={'v':1,'grant_id':'demo-1','experiment_id':'synthetic-1',
               'scope':'REAL_DEVELOPMENT','role':'DEVELOPMENT',
               'universe_sha256':idx.universe_sha256,'calendar_sha256':idx.calendar_sha256,
               'start_utc':'2024-12-01T00:00:00Z','end_utc':'2025-01-01T00:00:00Z',
               'expires_utc':'2099-01-01T00:00:00Z',
               'partitions':[{'family':'klines','period':'2024-12','canonical_sha256':p.canonical_sha256}]}
            raw=canonical_bytes(g);sig=key.sign(raw)
            args=dict(grant_bytes=raw,signature=sig,trusted_public_key_bytes=public,
                      expected_public_key_sha256=sha(public),frozen=idx,now=utc('2026-11-11T08:00:00Z'))
            result=verify_development_grant(**args)
            result.permits('klines','2024-12',p.canonical_sha256)
            with self.assertRaises(GrantError):result.permits('aggTrades','2024-12',p.canonical_sha256)
            with self.assertRaises(GrantError):verify_development_grant(**dict(args,signature=b'0'*64))
            with self.assertRaises(GrantError):verify_development_grant(**dict(args,expected_public_key_sha256='0'*64))
            g['role']='FINAL_OOS';new=canonical_bytes(g)
            with self.assertRaises(GrantError):verify_development_grant(**dict(args,grant_bytes=new,signature=key.sign(new)))
            g['role']='DEVELOPMENT';g['partitions'][0]['period']='2025-01';new=canonical_bytes(g)
            with self.assertRaises(GrantError):verify_development_grant(**dict(args,grant_bytes=new,signature=key.sign(new)))
            with self.assertRaises(GrantError):verify_development_grant(**dict(args,now=utc('2100-01-01T00:00:00Z')))


@unittest.skipUnless(pa is not None and os.name=='posix','pyarrow/posix optional')
class ParquetTest(unittest.TestCase):
    def test_staged_exact_schema_bounded_window_and_sealed_role(self):
        from hypolab.parquet_window import schema_for
        with tempfile.TemporaryDirectory() as d:
            stager,source,p,manifest,bogus=StageTest().fixture(d)
            schema=schema_for('klines',pa)
            from datetime import datetime
            times=[utc('2024-12-31T23:56:00Z')+timedelta(minutes=i) for i in range(4)]
            rows=[]
            for i,t in enumerate(times):
                row={n:(t if n=='open_time' else t+timedelta(milliseconds=59999) if n=='close_time' else 1 if n=='count' else 100.0) for n in schema.names}
                rows.append(row)
            table=pa.Table.from_pylist(rows,schema=schema)
            bio=io.BytesIO();pq.write_table(table,bio,compression='NONE');blob=bio.getvalue()
            p,manifest,blob=synthetic_pair(p,blob)
            stage=stager.stage(p,manifest_stream=io.BytesIO(manifest),parquet_stream=io.BytesIO(blob))
            reader=ParquetWindowReader()
            result=reader.read(stage,start=utc('2024-12-31T23:56:00Z'),end=utc('2025-01-01T00:00:00Z'),columns=('open_time','close'),max_rows=4)
            self.assertEqual(result.num_rows,4)
            with self.assertRaises(ParquetWindowError):reader.read(stage,start=utc('2024-12-31T23:56:00Z'),end=utc('2025-01-01T00:01:00Z'),columns=('close',))
            with self.assertRaises(ParquetWindowError):reader.read(stage,start=utc('2024-12-31T23:56:00Z'),end=utc('2025-01-01T00:00:00Z'),columns=('close',),max_rows=3)
            with self.assertRaises(ParquetWindowError):reader.read(stage,start=utc('2024-12-31T23:56:00Z'),end=utc('2025-01-01T00:00:00Z'),columns=('close',),role=Role.VALIDATION)
            stage.parquet_path.write_bytes(b'tampered')
            with self.assertRaises(ParquetWindowError):reader.read(stage,start=utc('2024-12-31T23:56:00Z'),end=utc('2025-01-01T00:00:00Z'),columns=('close',))


if __name__=='__main__':unittest.main()

@unittest.skipUnless(pa is not None and os.name=='posix','pyarrow/posix optional')
class MinuteFeaturesTest(unittest.TestCase):
    def test_streaming_join_missing_c2_and_boundary(self):
        from hypolab.parquet_window import schema_for
        from hypolab.c1c2_features import C1C2MinuteBuilder,FeatureError
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);source=base/'source';source.mkdir();cache=base/'cache';cache.mkdir(mode=0o700)
            idx=synthetic_frozen(source)
            one=idx.pick('klines','2024-12');two=idx.pick('aggTrades','2024-12')
            date0=utc('2024-12-31T23:56:00Z')
            bars=[]
            for i in range(4):
                opened=date0+timedelta(minutes=i)
                bars.append({name:(opened if name=='open_time' else opened+timedelta(milliseconds=59_999)
                                   if name=='close_time' else 1 if name=='count' else 100.0)
                             for name in schema_for('klines',pa).names})
            trades=[{'agg_trade_id':10,'price':100.0,'quantity':2.0,
                     'first_trade_id':10,'last_trade_id':10,'transact_time':date0+timedelta(seconds=20),
                     'is_buyer_maker':False},
                    {'agg_trade_id':11,'price':100.0,'quantity':1.0,
                     'first_trade_id':11,'last_trade_id':11,'transact_time':date0+timedelta(seconds=30),
                     'is_buyer_maker':True},
                    {'agg_trade_id':12,'price':100.0,'quantity':1.0,
                     'first_trade_id':12,'last_trade_id':12,'transact_time':date0+timedelta(minutes=2,seconds=5),
                     'is_buyer_maker':True}]
            def stage(identity,rows):
                buf=io.BytesIO()
                pq.write_table(pa.Table.from_pylist(rows,schema=schema_for(identity.family,pa)),buf,compression='NONE')
                p,m,b=synthetic_pair(identity,buf.getvalue())
                return ReadOnlyStager(cache).stage(p,manifest_stream=io.BytesIO(m),parquet_stream=io.BytesIO(b))
            c1=stage(one,bars);c2=stage(two,trades)
            result=C1C2MinuteBuilder().build(c1,c2,assumed_latency_ms=1000,require_complete_month=False)
            self.assertEqual(len(result),3)
            self.assertEqual([v.c2_status for v in result],['C2_PRESENT','C2_UNKNOWN','C2_PRESENT'])
            self.assertAlmostEqual(result[0].taker_imbalance,1/3)
            self.assertEqual(result[0].taker_delta_base,1.0)
            self.assertIsNone(result[1].taker_imbalance)
            self.assertEqual(result[2].taker_delta_base,-1.0)
            with self.assertRaises(FeatureError):C1C2MinuteBuilder().build(c1,c2,assumed_latency_ms=0,require_complete_month=False)
            with self.assertRaises(FeatureError):C1C2MinuteBuilder().build(c1,c2,assumed_latency_ms=1000)

    def test_duplicate_agg_trade_id_denied(self):
        from hypolab.parquet_window import schema_for
        from hypolab.c1c2_features import C1C2MinuteBuilder,FeatureError
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);idx=synthetic_frozen(root);cache=root/'cache';cache.mkdir(mode=0o700)
            t=utc('2024-12-31T23:57:00Z')
            def stage(p,rows):
                buf=io.BytesIO();pq.write_table(pa.Table.from_pylist(rows,schema=schema_for(p.family,pa)),buf,compression='NONE')
                x,m,b=synthetic_pair(p,buf.getvalue())
                return ReadOnlyStager(cache).stage(x,manifest_stream=io.BytesIO(m),parquet_stream=io.BytesIO(b))
            c1=[]
            for i in range(2):
                x=t+timedelta(minutes=i)
                c1.append({name:(x if name=='open_time' else x+timedelta(milliseconds=59_999)
                                  if name=='close_time' else 1 if name=='count' else 101.0)
                           for name in schema_for('klines',pa).names})
            c2=[]
            for id in (11,11):
                c2.append({'agg_trade_id':id,'price':100.0,'quantity':1.0,
                           'first_trade_id':id,'last_trade_id':id,'transact_time':t,
                           'is_buyer_maker':False})
            a=stage(idx.pick('klines','2024-12'),c1);b=stage(idx.pick('aggTrades','2024-12'),c2)
            with self.assertRaises(FeatureError):C1C2MinuteBuilder().build(a,b,assumed_latency_ms=1000,require_complete_month=False)
