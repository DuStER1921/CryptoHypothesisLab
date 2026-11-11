"""Synthetic Google client API shape proof; absolutely no network/authentication."""
import unittest
from hypolab.drive_backend import GoogleDriveV3Port, VerifiedDriveWriter
try:
    from googleapiclient.http import MediaIoBaseUpload
except ImportError:
    MediaIoBaseUpload = None


class Request:
    def __init__(self, func):self.func=func
    def execute(self):return self.func()


class FakeApi:
    def __init__(self):self.objects={};self.n=0
    def files(self):return self
    def list(self, *, q, fields, page_size, spaces, supportsAllDrives, includeItemsFromAllDrives):
        assert page_size==100 and spaces=='drive' and supportsAllDrives and includeItemsFromAllDrives
        assert "'private-results' in parents" in q
        return Request(lambda: {'files':[{'id':id,'name':v['name'],'parents':[v['parent']],
            'trashed':False} for id,v in self.objects.items() if v['parent']=='private-results'
            and f"name = '{v['name']}'" in q]})
    def create(self, *, body, media_body, fields, supportsAllDrives):
        assert body['parents']==['private-results'] and supportsAllDrives
        assert isinstance(media_body,MediaIoBaseUpload)
        def cb():
            self.n+=1
            fid=f'fake-{self.n}'
            self.objects[fid]={'name':body['name'],'parent':body['parents'][0],
                               'data':media_body.getbytes(0,media_body.size())}
            return {'id':fid}
        return Request(cb)
    def get(self, *, fileId, fields, supportsAllDrives):
        assert supportsAllDrives
        def cb():
            v=self.objects[fileId]
            return {'id':fileId,'name':v['name'],'parents':[v['parent']],
                    'mimeType':'application/json','trashed':False,'size':str(len(v['data']))}
        return Request(cb)
    def get_media(self, *, fileId, supportsAllDrives):
        assert supportsAllDrives
        return Request(lambda:self.objects[fileId]['data'])


@unittest.skipUnless(MediaIoBaseUpload is not None,'optional Google SDK not installed')
class TestGoogleV3Adapter(unittest.TestCase):
    def test_v3_create_metadata_media_and_dedup(self):
        fake=FakeApi()
        writer=VerifiedDriveWriter(GoogleDriveV3Port(fake),private_folder_id='private-results',
                                    forbidden_folder_ids=frozenset({'source-folder'}))
        receipt=writer.write_once('test.json',b'{"synthetic":true}')
        self.assertEqual(receipt.file_id,'fake-1')
        self.assertEqual(len(fake.objects),1)
        self.assertEqual(writer.read_exact(receipt.file_id,name=receipt.name,
                                           expected_sha=receipt.sha256),b'{"synthetic":true}')


if __name__=='__main__':unittest.main()
