"""Only made-up keys/bytes/Drive responses. No user data or Google API calls."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hypolab.admission import canonical_bytes
from hypolab.cloud_receipts import OwnerReceiptAuthority, ReceiptError
from hypolab.drive_backend import VerifiedDriveWriter, VerifiedDriveReader
from hypolab.frozen import PinnedPartition, FrozenError
from hypolab.owner_broker import OwnerOneUseBroker
from hypolab.owner_journal import OwnerExposureJournal, OwnerJournalError
from hypolab.single_account import (TokenScopeDeclaration, OneAccountError,
                                    READ_SCOPE, WRITE_SCOPE, preflight_analyst_environment)
from hypolab.staging import ReadOnlyStager, StageError

NOW=datetime(2026,11,11,10,0,tzinfo=timezone.utc)


def sha(raw):return hashlib.sha256(raw).hexdigest()


class FakeFrozen:
    def __init__(self, partition):
        self.partition=partition
        self.universe_sha256='e'*64
        self.calendar_sha256='f'*64
    def pick(self,family,period):
        if (family,period)!=(self.partition.family,self.partition.period):
            raise FrozenError('not permitted frozen partition')
        return self.partition


class FakeDrive:
    def __init__(self):self.items={};self.seq=0
    def list_named(self,parent,name):
        return [{'id':i,'name':x['name']} for i,x in self.items.items()
                if x['parent']==parent and x['name']==name]
    def create(self,parent,name,data,mime):
        self.seq+=1;i=f'f{self.seq}';self.items[i]={
            'parent':parent,'name':name,'data':data,'mime':mime};return {'id':i}
    def metadata(self,file_id):
        x=self.items[file_id]
        return {'id':file_id,'name':x['name'],'parents':[x['parent']],
                'mimeType':x['mime'],'trashed':False,'size':str(len(x['data']))}
    def download(self,file_id):return self.items[file_id]['data']


class ReadPort:
    def __init__(self,drive):self._drive=drive
    def list_named(self,parent,name):return self._drive.list_named(parent,name)
    def metadata(self,file_id):return self._drive.metadata(file_id)
    def download(self,file_id):return self._drive.download(file_id)


class FakeSource:
    def __init__(self,manifest,parquet):
        self.manifest=manifest;self.parquet=parquet;self.opens=0
        self.assert_before_open=None;self.fail=False
    @contextmanager
    def open_pinned(self,partition):
        self.opens+=1
        if self.assert_before_open:self.assert_before_open()
        if self.fail:raise OSError('simulated source disk failure')
        with io.BytesIO(self.manifest) as m,io.BytesIO(self.parquet) as p:
            yield m,p


@unittest.skipUnless(os.name=='posix','owner local POSIX test')
class TestOwnerOneUseBroker(unittest.TestCase):
    def setup(self,d):
        folder=Path(d);owner=folder/'owner';owner.mkdir(mode=0o700)
        cache=folder/'cache';cache.mkdir(mode=0o700)
        db_path=owner/'journal.sqlite3'
        journal=OwnerExposureJournal.create_new(db_path)
        parquet=b'synthetic-only-not-real-parquet'
        p= PinnedPartition('klines','2024-12','binance-usdm-BTCUSDT-klines-1m',
                           'a'*64,sha(parquet),'0'*64,'b'*64)
        manifest_object={'manifest_version':1,'provider':'binance','datatype':'klines',
            'symbol':'BTCUSDT','market':'usdm','logical_dataset_id':p.logical_dataset_id,
            'canonical_name':'data.parquet','interval':'1m',
            'schema_version':'binance-usdm-klines-1m-v1',
            'canonical_sha256':p.canonical_sha256,'source_sha256':p.source_sha256,
            'year':2024,'month':12,'row_count':1,
            'validation':{x:'pass' for x in ('checksum','schema','temporal','structural','read_back')}}
        manifest=json.dumps(manifest_object,sort_keys=True).encode('utf-8')
        p=PinnedPartition(p.family,p.period,p.logical_dataset_id,p.generation,
                          p.canonical_sha256,sha(manifest),p.source_sha256)
        frozen=FakeFrozen(p)
        gkey=Ed25519PrivateKey.generate();okey=Ed25519PrivateKey.generate()
        def pub(key):return key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        gpk=pub(gkey);opk=pub(okey)
        grant={'v':1,'grant_id':'dev-1','experiment_id':'synthetic-1',
               'scope':'REAL_DEVELOPMENT','role':'DEVELOPMENT',
               'universe_sha256':frozen.universe_sha256,
               'calendar_sha256':frozen.calendar_sha256,
               'start_utc':'2024-12-01T00:00:00Z',
               'end_utc':'2025-01-01T00:00:00Z',
               'expires_utc':'2099-01-01T00:00:00Z',
               'partitions':[{'family':'klines','period':'2024-12',
                              'canonical_sha256':p.canonical_sha256}]}
        grant_bytes=canonical_bytes(grant);grant_sig=gkey.sign(grant_bytes)
        drive=FakeDrive();args={'private_folder_id':'private-events',
                               'forbidden_folder_ids':frozenset({'source'})}
        writer=VerifiedDriveWriter(drive,**args)
        reader=VerifiedDriveReader(ReadPort(drive),**args)
        source=FakeSource(manifest,parquet)
        common=dict(frozen=frozen,grant_bytes=grant_bytes,grant_signature=grant_sig,
                    grant_public_key=gpk,pinned_grant_key_sha256=sha(gpk),
                    family='klines',period='2024-12',now=NOW)
        ack=OwnerReceiptAuthority(journal,writer,okey).issue(
            **common,attempt_id='attempt-1')
        broker=OwnerOneUseBroker(frozen=frozen,journal=journal,receipt_reader=reader,
               source=source,stager=ReadOnlyStager(cache),
               pinned_grant_key=gpk,grant_key_fingerprint=sha(gpk),
               pinned_owner_key=opk,owner_key_fingerprint=sha(opk))
        call=dict(grant_bytes=grant_bytes,grant_signature=grant_sig,
                  signed_ack=ack,family='klines',period='2024-12',now=NOW)
        return journal,db_path,source,broker,call,drive

    def test_consumed_before_open_and_replay_denied(self):
        with tempfile.TemporaryDirectory() as d:
            journal,db_path,source,broker,call,drive=self.setup(d)
            source.assert_before_open=lambda:self.assertTrue(journal.consumed('attempt-1'))
            stage=broker.stage_once(**call)
            self.assertEqual(stage.parquet_path.read_bytes(),source.parquet)
            self.assertEqual(source.opens,1)
            with self.assertRaises(OwnerJournalError):broker.stage_once(**call)
            self.assertEqual(source.opens,1)
            journal.close()
            reopened=OwnerExposureJournal(db_path)
            self.assertTrue(reopened.consumed('attempt-1'))
            reopened.close()

    def test_forged_grant_does_not_consume_or_open(self):
        with tempfile.TemporaryDirectory() as d:
            journal,_,source,broker,call,_=self.setup(d)
            with self.assertRaises(PermissionError):broker.stage_once(
                **{**call,'grant_signature':b'\0'*64})
            self.assertFalse(journal.consumed('attempt-1'))
            self.assertEqual(source.opens,0)
            journal.close()

    def test_wrong_bank_fails_before_consumption(self):
        with tempfile.TemporaryDirectory() as d:
            journal,_,source,broker,call,_=self.setup(d)
            with self.assertRaises((PermissionError, FrozenError)):broker.stage_once(
                **{**call,'period':'2025-01'})
            self.assertEqual(source.opens,0)
            self.assertFalse(journal.consumed('attempt-1'))
            journal.close()

    def test_tampered_cloud_event_blocks_open(self):
        with tempfile.TemporaryDirectory() as d:
            journal,_,source,broker,call,drive=self.setup(d)
            drive.items['f1']['data']+=b'\n'
            with self.assertRaises(Exception):broker.stage_once(**call)
            self.assertEqual(source.opens,0)
            self.assertFalse(journal.consumed('attempt-1'))
            journal.close()

    def test_failed_source_open_burns_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            journal,_,source,broker,call,_=self.setup(d)
            source.fail=True
            with self.assertRaises(OSError):broker.stage_once(**call)
            self.assertTrue(journal.consumed('attempt-1'))
            source.fail=False
            with self.assertRaises(OwnerJournalError):broker.stage_once(**call)
            self.assertEqual(source.opens,1)
            journal.close()

    def test_ledger_failure_blocks_open(self):
        with tempfile.TemporaryDirectory() as d:
            journal,_,source,broker,call,_=self.setup(d)
            original=journal.consume_acked_once
            journal.consume_acked_once=lambda **kwargs: (_ for _ in ()).throw(OwnerJournalError('disk failure'))
            with self.assertRaises(OwnerJournalError):broker.stage_once(**call)
            self.assertEqual(source.opens,0)
            journal.consume_acked_once=original
            journal.close()

    def test_two_owner_connections_cannot_consume_same_ack(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as d:
            journal,db_path,source,broker,call,_=self.setup(d)
            def attempt(_):
                own=OwnerExposureJournal(db_path)
                try:
                    another=OwnerOneUseBroker(
                        frozen=broker.frozen,journal=own,
                        receipt_reader=broker.receipt_reader,stager=broker.stager,
                        source=source,pinned_grant_key=broker.grant_key,
                        grant_key_fingerprint=broker.grant_key_sha,
                        pinned_owner_key=broker.owner_key,
                        owner_key_fingerprint=broker.owner_key_sha)
                    another.stage_once(**call)
                    return True
                except Exception:
                    return False
                finally:
                    own.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                result=list(pool.map(attempt, [0,1]))
            self.assertEqual(result.count(True),1)
            self.assertEqual(source.opens,1)
            self.assertTrue(journal.consumed('attempt-1'))
            journal.close()

    def test_old_schema_rejected_not_automigrated(self):
        with tempfile.TemporaryDirectory() as d:
            journal,db_path,_,_,_,_=self.setup(d)
            journal.close()
            import sqlite3
            con=sqlite3.connect(db_path)
            con.execute('UPDATE meta SET value=? WHERE key=?',('1','schema'))
            con.commit();con.close()
            with self.assertRaises(OwnerJournalError):OwnerExposureJournal(db_path)


class TestSingleGmailConfiguration(unittest.TestCase):
    def test_same_gmail_distinct_scopes_are_allowed_as_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=TokenScopeDeclaration((READ_SCOPE,),(WRITE_SCOPE,),
                Path(d)/'reader'/'token.json',Path(d)/'writer'/'token.json')
            self.assertIsNone(cfg.check_local())

    def test_broad_scopes_and_reused_tokens_denied(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'reader'/'token.json';out=Path(d)/'writer'/'token.json'
            for cfg in (
                TokenScopeDeclaration((WRITE_SCOPE,),(WRITE_SCOPE,),src,out),
                TokenScopeDeclaration((READ_SCOPE,),(READ_SCOPE,),src,out),
                TokenScopeDeclaration((READ_SCOPE,WRITE_SCOPE),(WRITE_SCOPE,),src,out),
                TokenScopeDeclaration((READ_SCOPE,),(WRITE_SCOPE,),src,src),
                TokenScopeDeclaration((READ_SCOPE,),(WRITE_SCOPE,),Path(d)/'same'/'a.json',Path(d)/'same'/'b.json'),
            ):
                with self.subTest(cfg=cfg),self.assertRaises(OneAccountError):
                    cfg.check_local()

    def test_analyst_path_and_environment_check(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'source';out=root/'analyst';src.mkdir();out.mkdir()
            preflight_analyst_environment(allowed_root=out,forbidden_roots=(src,),environ={})
            with self.assertRaises(OneAccountError):
                preflight_analyst_environment(allowed_root=src,forbidden_roots=(src,),environ={})
            with self.assertRaises(OneAccountError):
                preflight_analyst_environment(allowed_root=out,forbidden_roots=(src,),
                    environ={'GOOGLE_APPLICATION_CREDENTIALS':'/private/token'})
            with self.assertRaises(OneAccountError):
                preflight_analyst_environment(allowed_root=out,forbidden_roots=(),environ={})
            with self.assertRaises(OneAccountError):
                preflight_analyst_environment(allowed_root=out,forbidden_roots=(src,),
                    environ={'HOME':str(root)})

if __name__=='__main__':unittest.main()
