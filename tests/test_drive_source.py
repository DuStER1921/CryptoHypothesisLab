"""Synthetic Google API streaming stub; no Google account or network."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hypolab.drive_source import GoogleDrivePinnedSource, SourceReadError
from hypolab.frozen import PinnedPartition
from hypolab.single_account import READ_SCOPE, WRITE_SCOPE


class FakeCredentials:
    def __init__(self, scopes):self.granted_scopes=scopes


class FakeRequest:
    def __init__(self, raw):self.raw=raw


class FakeDownloader:
    def __init__(self, file, request, chunksize):
        self.file=file;self.raw=request.raw;self.index=0
    def next_chunk(self,num_retries=0):
        end=min(self.index+7,len(self.raw))
        self.file.write(self.raw[self.index:end])
        self.index=end
        return None,self.index>=len(self.raw)


class FakeFiles:
    def __init__(self):self.content={'manifest-id':b'{"synthetic":true}',
                                      'data-id':b'PAR1 completely synthetic bytes'}
    def get(self,*,fileId,fields,supportsAllDrives):
        parent=self
        class Req:
            def execute(self):
                return {'id':fileId,'size':str(len(parent.content[fileId])),
                        'mimeType':'application/json' if fileId=='manifest-id' else 'application/octet-stream',
                        'trashed':False}
        return Req()
    def get_media(self,*,fileId,supportsAllDrives):
        return FakeRequest(self.content[fileId])


class FakeService:
    def __init__(self):self.files_service=FakeFiles()
    def files(self):return self.files_service


@unittest.skipUnless(os.name=='posix' and importlib.util.find_spec('googleapiclient') is not None,
                     'Drive SDK + POSIX integration test')
class TestDriveSource(unittest.TestCase):
    def partition(self):
        return PinnedPartition('klines','2024-07','binance-usdm-BTCUSDT-klines-1m',
                               'a'*64,'b'*64,'c'*64,'d'*64)
    def test_readonly_exact_file_id_stream_and_scratch_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            cache=Path(d)/'source-scratch';cache.mkdir(mode=0o700)
            p=self.partition();fs=FakeService()
            source=GoogleDrivePinnedSource(service=fs,
                credentials=FakeCredentials([READ_SCOPE]),
                mapping={(p.family,p.period,p.canonical_sha256):('manifest-id','data-id')},
                scratch_parent=cache)
            with patch('googleapiclient.http.MediaIoBaseDownload',FakeDownloader):
                with source.open_pinned(p) as (m,f):
                    self.assertEqual(m.read(),fs.files_service.content['manifest-id'])
                    self.assertEqual(f.read(),fs.files_service.content['data-id'])
            self.assertEqual(list(cache.iterdir()),[])

    def test_overscope_unknown_scopes_and_unpinned_partition_refused(self):
        with tempfile.TemporaryDirectory() as d:
            cache=Path(d)/'private';cache.mkdir(mode=0o700)
            p=self.partition();mapping={(p.family,p.period,p.canonical_sha256):('manifest-id','data-id')}
            for granted in ([READ_SCOPE,WRITE_SCOPE],[WRITE_SCOPE],None):
                with self.subTest(scopes=granted),self.assertRaises(SourceReadError):
                    GoogleDrivePinnedSource(service=FakeService(),
                        credentials=FakeCredentials(granted),mapping=mapping,
                        scratch_parent=cache)
            source=GoogleDrivePinnedSource(service=FakeService(),
                credentials=FakeCredentials([READ_SCOPE]),mapping=mapping,
                scratch_parent=cache)
            other=PinnedPartition(p.family,'2025-01',p.logical_dataset_id,
                                   p.generation,p.canonical_sha256,p.manifest_sha256,p.source_sha256)
            with self.assertRaises(SourceReadError):
                with source.open_pinned(other):pass
            self.assertEqual(list(cache.iterdir()),[])

    def test_wrong_byte_count_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cache=Path(d)/'private';cache.mkdir(mode=0o700)
            p=self.partition();fs=FakeService()
            source=GoogleDrivePinnedSource(service=fs,
                credentials=FakeCredentials([READ_SCOPE]),
                mapping={(p.family,p.period,p.canonical_sha256):('manifest-id','data-id')},
                scratch_parent=cache)
            class ShortDownloader(FakeDownloader):
                def __init__(self,file,request,chunksize):
                    super().__init__(file,request,chunksize)
                    self.raw=self.raw[:-1]
            with patch('googleapiclient.http.MediaIoBaseDownload',ShortDownloader):
                with self.assertRaises(SourceReadError):
                    with source.open_pinned(p):pass
            self.assertEqual(list(cache.iterdir()),[])
