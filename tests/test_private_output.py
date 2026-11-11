import os
import tempfile
import unittest
from pathlib import Path

from hypolab.private_output import PrivateOutputStore, PrivateOutputError


@unittest.skipUnless(os.name=='posix','local POSIX only')
class PrivateOutputTests(unittest.TestCase):
    def test_create_only_not_source_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'src';dst=root/'private';src.mkdir();dst.mkdir(mode=0o700)
            (src/'leave-unchanged').write_bytes(b'original')
            p=PrivateOutputStore(dst,source_roots=(src,))
            digest=p.write_once('study-1.json',b'{}')
            self.assertEqual(len(digest),64)
            self.assertEqual((src/'leave-unchanged').read_bytes(),b'original')
            with self.assertRaises(PrivateOutputError):p.write_once('study-1.json',b'new')
            with self.assertRaises(PrivateOutputError):p.write_once('../escape.json',b'x')
            with self.assertRaises(PrivateOutputError):p.write_once('secret.parquet',b'x')

    def test_output_source_tree_overlap_denied(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'source';src.mkdir()
            inside=src/'generated';inside.mkdir(mode=0o700)
            with self.assertRaises(PrivateOutputError):
                PrivateOutputStore(inside,source_roots=(src,))
            with self.assertRaises(PrivateOutputError):
                PrivateOutputStore(src,source_roots=(src,))
