"""Adversarial synthetic tests for journal concurrency and input immutability."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from hypolab import (AccessDenied, DataGateway, DataIntegrityError, ExperimentLedger,
                     FrozenPartition, LedgerError, Role, ValidationGate)
from hypolab.contracts import utc
from hypolab.ledger import digest, canonical_json
from hypolab.temporal import Observation, TemporalError, TimeGuard
from hypolab.discovery import DiscoveryEngine, EventSpec
from test_discovery import synthetic_bars
from test_gate_gateway import make_fixture


@unittest.skipUnless(os.name == 'posix', 'local POSIX filesystem only')
class JournalHardening(unittest.TestCase):
    def _new(self, d):
        journal = ExperimentLedger(Path(d) / 'journal')
        journal.initialize()
        return journal

    def test_exact_registered_trial_id_required(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            ledger.append('BATCH_REGISTERED', {
                'batch_id': 'b', 'trials': 2, 'trial_ids': ['a', 'c'],
                'definition_sha': 'c' * 64})
            with self.assertRaises(LedgerError):
                ledger.append('TRIAL_RESULT', {
                    'batch_id': 'b', 'trial_id': 'b', 'status': 'DONE',
                    'result_sha': 'a' * 64})
            self.assertEqual(len(ledger.replay()[1]), 1)

    def test_register_duplicate_plan_is_rejected_without_partial_write(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            with self.assertRaises(LedgerError):
                ledger.append('BATCH_REGISTERED', {
                    'batch_id': 'bad', 'trials': 2, 'trial_ids': ['same', 'same'],
                    'definition_sha': 'a' * 64})
            self.assertEqual(len(ledger.replay()[1]), 0)
            self.assertFalse((ledger.root / '.incomplete-transaction').exists())

    def test_multiprocess_style_exclusive_lock_refuses_other_reader_and_writer(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            with ledger._locked(write=True):
                with self.assertRaisesRegex(LedgerError, 'another journal operation'):
                    ledger.replay()
                with self.assertRaisesRegex(LedgerError, 'another journal operation'):
                    ledger.append('READ_DENIED', {
                        'partition_id': 'p', 'role': 'VALIDATION',
                        'reason': 'PROTECTED_SEALED'})
            self.assertEqual(len(ledger.replay()[1]), 0)

    def test_separate_process_cannot_append_during_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            worker = """import sys
from pathlib import Path
from hypolab import ExperimentLedger, LedgerError
j = ExperimentLedger(Path(sys.argv[1]))
try:
    j.append('READ_DENIED', {'partition_id': 'external', 'role': 'VALIDATION', 'reason': 'PROTECTED_SEALED'})
except LedgerError:
    sys.exit(0)
sys.exit(9)
"""
            env = dict(os.environ)
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
            with ledger._locked(write=True):
                result = subprocess.run([sys.executable, '-c', worker, str(ledger.root)],
                                        env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(ledger.replay()[1], [])

    def test_interrupted_multi_append_blocks_all_future_reads(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            original = ledger._create
            counter = 0
            def fail_on_second_data_record(path, content, **kwargs):
                nonlocal counter
                if path.name.endswith('.json') and path.name != 'genesis.json':
                    counter += 1
                    if counter == 2:
                        raise OSError('synthetic crash after first record')
                return original(path, content, **kwargs)
            pairs = [('READ_DENIED', {'partition_id': 'p', 'role': 'VALIDATION',
                                       'reason': 'PROTECTED_SEALED'})] * 3
            with patch.object(ledger, '_create', side_effect=fail_on_second_data_record):
                with self.assertRaises(OSError):
                    ledger.append_many(pairs)
            self.assertTrue((ledger.root / '.incomplete-transaction').exists())
            with self.assertRaisesRegex(LedgerError, 'interrupted transaction'):
                ledger.replay()
            with self.assertRaisesRegex(LedgerError, 'interrupted transaction'):
                ledger.append(*pairs[0])

    def test_bounded_bulk_append_and_expected_head(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            initial = ledger.replay()[0]
            records = [('READ_DENIED', {'partition_id': f'p{i}', 'role': 'FINAL_OOS',
                                         'reason': 'PROTECTED_SEALED'}) for i in range(125)]
            latest = ledger.append_many(records, expected_head=initial)
            self.assertEqual(len(ledger.replay(expected_head=latest)[1]), 125)
            with self.assertRaisesRegex(LedgerError, 'pinned head'):
                ledger.append(*records[0], expected_head=initial)
            self.assertFalse((ledger.root / '.incomplete-transaction').exists())

    def test_batch_invalid_mid_transaction_has_no_writes(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            valid = ('READ_DENIED', {'partition_id': 'p', 'role': 'VALIDATION',
                                     'reason': 'PROTECTED_SEALED'})
            invalid = ('TRIAL_RESULT', {'batch_id': 'no', 'trial_id': 'x',
                                        'status': 'DONE', 'result_sha': 'a'*64})
            with self.assertRaises(LedgerError):
                ledger.append_many([valid, invalid])
            self.assertEqual(len(ledger.replay()[1]), 0)
            self.assertFalse((ledger.root / '.incomplete-transaction').exists())

    def test_protected_read_event_rejected_even_in_bulk(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = self._new(d)
            with self.assertRaises(LedgerError):
                ledger.append_many([('READ_STARTED', {
                    'partition_id': 'p', 'role': 'FINAL_OOS', 'scope': 'SYNTHETIC_ONLY'})])
            self.assertEqual(ledger.replay()[1], [])


@unittest.skipUnless(os.name == 'posix', 'local POSIX filesystem only')
class GatewayHardening(unittest.TestCase):
    def test_directory_symlink_denied(self):
        with tempfile.TemporaryDirectory() as d:
            g, source, ledger, p, payload = make_fixture(Path(d))
            (source / 'safe').mkdir()
            (source / 'safe' / 'data.bin').write_bytes(payload)
            (source / 'alias').symlink_to(source / 'safe', target_is_directory=True)
            q = FrozenPartition('nested', 'aggTrades', p.start, p.end, 'alias/data.bin',
                                p.data_sha256, p.manifest_file, p.manifest_sha256)
            g2 = DataGateway(source, (q,), ValidationGate(ledger))
            with self.assertRaises(DataIntegrityError):
                g2.read_verified('nested')

    def test_special_file_rejected_without_hanging(self):
        with tempfile.TemporaryDirectory() as d:
            g, source, ledger, p, payload = make_fixture(Path(d))
            (source / 'data.bin').unlink()
            os.mkfifo(source / 'data.bin')
            with self.assertRaises(DataIntegrityError):
                g.read_verified('synthetic-001')

    def test_same_size_tampering_detected(self):
        with tempfile.TemporaryDirectory() as d:
            g, source, ledger, p, payload = make_fixture(Path(d))
            (source / 'data.bin').write_bytes(b'X' * len(payload))
            with self.assertRaisesRegex(DataIntegrityError, 'SHA-256'):
                g.read_verified('synthetic-001')

    def test_output_ancestor_of_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            g, source, ledger, p, payload = make_fixture(Path(d))
            unsafe = ExperimentLedger(Path(d))
            with self.assertRaises(DataIntegrityError):
                DataGateway(source, (p,), ValidationGate(unsafe))

    def test_unexpected_role_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            g, source, ledger, p, payload = make_fixture(Path(d))
            with self.assertRaises(AccessDenied):
                g.read_verified('synthetic-001', 'ADMIN')
            self.assertEqual(len(ledger.replay()[1]), 0)


@unittest.skipUnless(os.name == 'posix', 'local POSIX filesystem only')
class DiscoveryHardening(unittest.TestCase):
    def test_sorted_threshold_equivalent_to_reference(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'journal')
            ledger.initialize()
            bars = synthetic_bars(200)
            thresholds = [i/100 for i in range(101)]
            specs = tuple(EventSpec(f't{i}', t) for i,t in enumerate(thresholds))
            findings = DiscoveryEngine(ledger).run(bars=bars, specs=specs,
                                                    horizons=(1,3,10), batch_id='reference')
            self.assertEqual(len(findings), 303)
            for f in findings:
                spec_index = int(f.trial_id.split('-')[0][1:])
                h = int(f.trial_id.split('-h')[1])
                expected = []
                for i in range(len(bars)-h):
                    b = bars[i]
                    imb = (b.buy_qty-b.sell_qty)/(b.buy_qty+b.sell_qty)
                    if imb >= thresholds[spec_index]:
                        expected.append(bars[i+h].close/b.close-1)
                self.assertEqual(f.events, len(expected))
                if expected:
                    self.assertAlmostEqual(f.event_mean_return, sum(expected)/len(expected), places=12)
            events = ledger.replay()[1]
            self.assertEqual(sum(e['kind']=='TRIAL_RESULT' for e in events), 303)
            self.assertEqual(events[0]['payload']['trials'], 303)

    def test_inactive_minute_is_not_threshold_zero_event(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'journal')
            ledger.initialize()
            bars = synthetic_bars(80)
            zero = bars[0]
            bars[0] = type(zero)(zero.start, zero.available_at, zero.close, 0, 0)
            f = DiscoveryEngine(ledger).run(bars=bars, specs=(EventSpec('x', 0.0),),
                                             horizons=(1,), batch_id='inactive')[0]
            self.assertEqual(f.events, 26)


if __name__ == '__main__':
    unittest.main()

class ExtraTemporalHardening(unittest.TestCase):
    def test_purging_cannot_be_requested_on_protected_bank(self):
        with self.assertRaises(TemporalError):
            TimeGuard.purge_forward([], utc('2025-01-01T00:00:00Z'),
                                    utc('2025-02-01T00:00:00Z'))

    def test_rejected_label_available_at_decision_boundary(self):
        d = utc('2024-07-01T00:01:00Z')
        with self.assertRaises(TemporalError):
            TimeGuard.check(Observation(d-timedelta(minutes=1), d, d, d, d, d+timedelta(minutes=1)))
