import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from hypolab import (AccessDenied, DataGateway, DataIntegrityError, ExperimentLedger,
                     FrozenPartition, Role, ValidationGate)
from hypolab.contracts import utc


def make_fixture(root, *, scope='SYNTHETIC_ONLY', role=Role.DEVELOPMENT):
    source = root / 'source'
    out = root / 'independent_ledger'
    source.mkdir()
    payload = b'synthetic-only-fixture-contents\n'
    (source / 'data.bin').write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    m = {'scope': scope, 'datatype': 'aggTrades', 'canonical_sha256': sha,
         'partition_id': 'synthetic-001'}
    manifest = json.dumps(m, sort_keys=True).encode('utf-8')
    (source / 'manifest.json').write_bytes(manifest)
    p = FrozenPartition('synthetic-001', 'aggTrades', utc('2024-07-01T00:00:00Z'),
                        utc('2024-08-01T00:00:00Z'), 'data.bin', sha,
                        'manifest.json', hashlib.sha256(manifest).hexdigest(), scope)
    ledger = ExperimentLedger(out)
    ledger.initialize()
    return DataGateway(source, (p,), ValidationGate(ledger)), source, ledger, p, payload


class TestGateway(unittest.TestCase):
    def test_verified_fixture_read_only_source_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            before = {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in source.iterdir()}
            self.assertEqual(gateway.read_verified('synthetic-001'), payload)
            self.assertEqual(before, {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in source.iterdir()})
            head, events = ledger.replay()
            self.assertEqual(events[0]['kind'], 'READ_STARTED')
            self.assertEqual(len(events), 1)

    def test_protected_role_denied_before_file_open(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            (source / 'data.bin').unlink()
            with self.assertRaises(AccessDenied):
                gateway.read_verified('synthetic-001', Role.VALIDATION)
            self.assertEqual(ledger.replay()[1][-1]['kind'], 'READ_DENIED')

    def test_real_is_default_denied_before_source_open(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d), scope='REAL')
            (source / 'data.bin').unlink()
            with self.assertRaisesRegex(AccessDenied, 'REAL_NOT_AUTHORIZED'):
                gateway.read_verified('synthetic-001')

    def test_bad_manifest_and_tampered_source_fail_closed(self):
        for filename in ('manifest.json', 'data.bin'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as d:
                gateway, source, ledger, p, payload = make_fixture(Path(d))
                (source / filename).write_bytes(b'tampered!')
                with self.assertRaises(DataIntegrityError):
                    gateway.read_verified('synthetic-001')

    def test_symlink_and_traversal_denied(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            with self.assertRaises(DataIntegrityError):
                FrozenPartition('one', 'klines', p.start, p.end, '../secret', p.data_sha256,
                                p.manifest_file, p.manifest_sha256)
            (source / 'data.bin').unlink()
            (source / 'data.bin').symlink_to(Path(d) / 'out-of-root')
            with self.assertRaises(DataIntegrityError):
                gateway.read_verified('synthetic-001')

    def test_rejects_ledger_output_inside_source(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            accidental = ExperimentLedger(source / 'output')
            with self.assertRaises(DataIntegrityError):
                DataGateway(source, (p,), ValidationGate(accidental))

    def test_missing_partition_not_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            with self.assertRaises(DataIntegrityError):
                gateway.read_verified('unknown')
            self.assertEqual(len(ledger.replay()[1]), 0)

    def test_oversized_fixture_fails(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            with (source / 'data.bin').open('ab') as fh:
                fh.truncate(8 * 1024 * 1024 + 1)
            with self.assertRaises(DataIntegrityError):
                gateway.read_verified('synthetic-001')

    def test_c3_family_only_from_exact_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            gateway, source, ledger, p, payload = make_fixture(Path(d))
            with self.assertRaises(DataIntegrityError):
                FrozenPartition('x', 'fullL2', p.start, p.end, p.data_file, p.data_sha256,
                                p.manifest_file, p.manifest_sha256)


if __name__ == '__main__':
    unittest.main()
