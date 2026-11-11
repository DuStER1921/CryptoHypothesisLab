import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from hypolab.drive_backend import VerifiedDriveWriter, VerifiedDriveReader, DriveBoundaryError
from hypolab.owner_journal import OwnerExposureJournal, OwnerJournalError
from hypolab.cloud_receipts import (
    OwnerReceiptAuthority, DevelopmentPermit, ReceiptError, SignedReadAck, verify_read_ack)
from hypolab.isolation import RuntimeRoles, IsolationError


class FakeDrive:
    def __init__(self):
        self.objects={}
        self.seq=0
        self.mutate_readback=False
        self.fail_after_create=False
        self.bad_parent=False
        self.calls=[]
    def list_named(self,parent,name):
        self.calls.append('list')
        return [{'id':k,'name':v['name']} for k,v in self.objects.items()
                if v['name']==name and v['parent']==parent]
    def create(self,parent,name,data,mime):
        self.calls.append('create')
        self.seq+=1
        fid=f'file-{self.seq}'
        self.objects[fid]={'parent':parent,'name':name,'data':data,'mime':mime}
        if self.fail_after_create:raise ConnectionError('lost network ACK')
        return {'id':fid}
    def metadata(self,fid):
        self.calls.append('metadata')
        v=self.objects[fid]
        return {'id':fid,'name':v['name'],'mimeType':v['mime'],
                'parents':['bad'] if self.bad_parent else [v['parent']],
                'trashed':False,'size':str(len(v['data']))}
    def download(self,fid):
        self.calls.append('download')
        value=self.objects[fid]['data']
        return value+b'!' if self.mutate_readback else value


class FakeReadOnlyPort:
    def __init__(self, backing):self.backing=backing
    def list_named(self,parent,name):return self.backing.list_named(parent,name)
    def metadata(self,fid):return self.backing.metadata(fid)
    def download(self,fid):return self.backing.download(fid)


def reader(p):
    v=VerifiedDriveReader(FakeReadOnlyPort(p),private_folder_id='private-results',
                          forbidden_folder_ids=frozenset({'source-folder'}))
    assert not hasattr(v,'write_once') and not hasattr(v.port,'create')
    return v


def writer(p):
    return VerifiedDriveWriter(p,private_folder_id='private-results',
                               forbidden_folder_ids=frozenset({'source-folder'}))


def permit(now=None):
    now=now or datetime(2026,11,11,10,0,tzinfo=timezone.utc)
    return DevelopmentPermit('g1','a'*64,'b'*64,'c'*64,'e1',
                             'aggTrades','2024-07','d'*64,
                             datetime(2024,7,1,tzinfo=timezone.utc),
                             datetime(2024,8,1,tzinfo=timezone.utc),
                             now+timedelta(days=1))


class TestDrive(unittest.TestCase):
    def test_create_only_exact_readback(self):
        f=FakeDrive(); w=writer(f)
        a=w.write_once('receipt-1.json',b'{"a":1}')
        self.assertEqual(w.read_exact(a.file_id,name=a.name,expected_sha=a.sha256),b'{"a":1}')
        with self.assertRaises(DriveBoundaryError):w.write_once('receipt-1.json',b'new')
        self.assertEqual(sum(c=='create' for c in f.calls),1)
    def test_modified_bytes_denied(self):
        f=FakeDrive();w=writer(f)
        f.mutate_readback=True
        with self.assertRaises(DriveBoundaryError):w.write_once('x.json',b'orig')
        self.assertEqual(len(f.objects),1) # no auto-cleanup or retry
    def test_wrong_parent_denied(self):
        f=FakeDrive();w=writer(f);f.bad_parent=True
        with self.assertRaises(DriveBoundaryError):w.write_once('x.json',b'orig')
    def test_duplicate_filename_rejected(self):
        f=FakeDrive();f.create('private-results','same.json',b'x','application/json')
        f.create('private-results','same.json',b'y','application/json')
        with self.assertRaises(DriveBoundaryError):writer(f).write_once('same.json',b'z')
    def test_uncertain_upload_does_not_retry(self):
        f=FakeDrive();f.fail_after_create=True
        with self.assertRaises(ConnectionError):writer(f).write_once('r.json',b'a')
        self.assertEqual(f.seq,1)
        with self.assertRaises(DriveBoundaryError):writer(f).write_once('r.json',b'a')
    def test_source_folder_and_unsafe_filename_denied(self):
        with self.assertRaises(DriveBoundaryError):
            VerifiedDriveWriter(FakeDrive(),private_folder_id='source-folder',
                                forbidden_folder_ids=frozenset({'source-folder'}))
        with self.assertRaises(DriveBoundaryError):writer(FakeDrive()).write_once('../secret.json',b'y')
    def test_oversize_denied_before_create(self):
        f=FakeDrive();w=VerifiedDriveWriter(f,private_folder_id='private-results',
            forbidden_folder_ids=frozenset({'source-folder'}),max_bytes=3)
        with self.assertRaises(DriveBoundaryError):w.write_once('ok.json',b'abcd')
        self.assertEqual(f.seq,0)


class TestOwner(unittest.TestCase):
    def mk(self,root):
        private=Path(root)/'owner';private.mkdir(mode=0o700)
        os.chmod(private,0o700)
        j=OwnerExposureJournal.create_new(private/'journal.sqlite3')
        key=Ed25519PrivateKey.generate()
        pk=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        f=FakeDrive();w=writer(f)
        auth=OwnerReceiptAuthority(j,w,key)
        return j,key,pk,f,w,auth

    def test_end_to_end_owner_signed_drive_ack(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc)
            p=permit(now)
            ack=auth.issue(p,attempt_id='attempt-1',now=now)
            self.assertEqual(j.status('attempt-1')[0],'ACKED')
            proof=verify_read_ack(ack=ack,permit=p,pinned_owner_public_key=pk,
                  pinned_public_sha256=hashlib.sha256(pk).hexdigest(),reader=reader(f),now=now)
            self.assertEqual(proof['status'],'ACK_VERIFIED_DEV_ONLY')
            with self.assertRaises(OwnerJournalError):auth.issue(p,attempt_id='attempt-2',now=now)
            j.close()

    def test_uncertain_drive_upload_consumes_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc);p=permit(now)
            f.fail_after_create=True
            with self.assertRaises(ConnectionError):auth.issue(p,attempt_id='attempt-1',now=now)
            self.assertEqual(j.status('attempt-1')[0],'PENDING')
            f.fail_after_create=False
            with self.assertRaises(OwnerJournalError):auth.issue(p,attempt_id='attempt-2',now=now)
            self.assertEqual(f.seq,1)
            j.close()

    def test_signature_tamper_or_missing_file_denied(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc);p=permit(now)
            ack=auth.issue(p,attempt_id='attempt-1',now=now)
            fake=SignedReadAck(ack.body,b'0'*64)
            with self.assertRaises(ReceiptError):verify_read_ack(ack=fake,permit=p,
                pinned_owner_public_key=pk,pinned_public_sha256=hashlib.sha256(pk).hexdigest(),reader=reader(f),now=now)
            f.objects.clear()
            with self.assertRaises(DriveBoundaryError):verify_read_ack(ack=ack,permit=p,
                pinned_owner_public_key=pk,pinned_public_sha256=hashlib.sha256(pk).hexdigest(),reader=reader(f),now=now)
            j.close()

    def test_future_and_protected_role_cannot_issue(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc)
            p=permit(now)
            wrong=DevelopmentPermit(**{**vars(p),'window_end':datetime(2025,2,1,tzinfo=timezone.utc)})
            with self.assertRaises(ReceiptError):auth.issue(wrong,attempt_id='bad',now=now)
            self.assertEqual(f.seq,0)
            j.close()

    def test_sqlite_reopened_status_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc)
            auth.issue(permit(now),attempt_id='stable',now=now)
            path=j.path;j.close()
            again=OwnerExposureJournal(path)
            self.assertEqual(again.status('stable')[0],'ACKED')
            again.close()

    def test_second_ack_chain_and_source_key_binding(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc)
            first=auth.issue(permit(now),attempt_id='first',now=now)
            new=DevelopmentPermit(**{**vars(permit(now)),'grant_id':'g2',
                                     'canonical_sha256':'e'*64})
            second=auth.issue(new,attempt_id='second',now=now)
            raw=json.loads(second.body)
            self.assertEqual(raw['seq'],2)
            self.assertEqual(raw['prev_head'],j.status('first')[2])
            with self.assertRaises(ReceiptError):
                verify_read_ack(ack=second,permit=permit(now),
                  pinned_owner_public_key=pk,pinned_public_sha256=hashlib.sha256(pk).hexdigest(),
                  reader=reader(f),now=now)
            self.assertEqual(verify_read_ack(ack=second,permit=new,
                  pinned_owner_public_key=pk,pinned_public_sha256=hashlib.sha256(pk).hexdigest(),
                  reader=reader(f),now=now)['status'],'ACK_VERIFIED_DEV_ONLY')
            j.close()

    def test_wrong_fingerprint_and_expiry_denied(self):
        with tempfile.TemporaryDirectory() as d:
            j,key,pk,f,w,auth=self.mk(d)
            now=datetime(2026,11,11,10,0,tzinfo=timezone.utc)
            p=permit(now)
            ack=auth.issue(p,attempt_id='one',now=now)
            with self.assertRaises(ReceiptError):verify_read_ack(ack=ack,permit=p,
                pinned_owner_public_key=pk,pinned_public_sha256='f'*64,
                reader=reader(f),now=now)
            with self.assertRaises(ReceiptError):verify_read_ack(ack=ack,permit=p,
                pinned_owner_public_key=pk,pinned_public_sha256=hashlib.sha256(pk).hexdigest(),
                reader=reader(f),now=now+timedelta(days=2))
            j.close()

    def test_invalid_owner_journal_path(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'journal.sqlite3'
            p.write_bytes(b'garbage')
            with self.assertRaises(OwnerJournalError):OwnerExposureJournal.create_new(p)


class TestIsolation(unittest.TestCase):
    def test_verified_separate_roles(self):
        roles=RuntimeRoles('reader','writer','analyst',True,True,True,True)
        self.assertTrue(roles.require_separated())
    def test_same_owner_not_isolated(self):
        roles=RuntimeRoles('owner','owner','owner',True,False,False,False)
        with self.assertRaises(IsolationError):roles.require_separated()
    def test_assertions_are_not_os_proof(self):
        roles=RuntimeRoles('reader','writer','analyst',False,True,True,True)
        with self.assertRaises(IsolationError):roles.require_separated()


if __name__=='__main__':unittest.main()
