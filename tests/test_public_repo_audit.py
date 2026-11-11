import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_public_tree import inspect_file


class TestPublicTreeAudit(unittest.TestCase):
    def test_allow_public_source(self):
        self.assertEqual(inspect_file('src/hypolab/fake.py', b'print(123)\n'), ())

    def test_reject_market_artifact(self):
        self.assertIn('disallowed file type/name', inspect_file('x.parquet', b'fake'))
        self.assertIn('disallowed directory', inspect_file('data/market.py', b'fake'))

    def test_reject_credentials_and_private_drive_link(self):
        self.assertTrue(inspect_file('.env', b'password=1'))
        url = 'https://' + 'drive.google.com/drive/' + 'folders/' + 'test'
        self.assertIn('potential secret or private Drive URL',
                      inspect_file('notes.md', url.encode()))

    def test_reject_non_utf8_binary_and_large_payload(self):
        self.assertIn('binary content', inspect_file('blob.md', b'\x00'))
        self.assertIn('oversized source file', inspect_file('huge.md', b'X' * (513 * 1024)))


if __name__ == '__main__':
    unittest.main()
