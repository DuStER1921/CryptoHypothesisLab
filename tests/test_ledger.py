import os
import tempfile
import unittest
from pathlib import Path
from hypolab import ExperimentLedger, LedgerError
from hypolab.ledger import canonical_json, digest


@unittest.skipUnless(os.name == 'posix', 'local fsync proof POSIX only')
class TestLedger(unittest.TestCase):
    def test_chain_readback_and_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            head = ledger.append('READ_DENIED', {'partition_id':'p1', 'role':'FINAL_OOS', 'reason':'PROTECTED_SEALED'})
            self.assertEqual(ledger.replay(expected_head=head)[0], head)
            entry = next(p for p in ledger.root.iterdir() if p.name.endswith('.json') and p.name != 'genesis.json')
            entry.write_text('broken')
            with self.assertRaises(LedgerError):
                ledger.replay()

    def test_missing_suffix_detected_by_independent_head(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            head = ledger.append('READ_DENIED', {'partition_id':'p1', 'role':'VALIDATION', 'reason':'PROTECTED_SEALED'})
            entry = next(p for p in ledger.root.iterdir() if p.name.endswith('.json') and p.name != 'genesis.json')
            entry.unlink()
            # Unanchored chain can't detect suffix deletion; independently pinned head does.
            self.assertEqual(len(ledger.replay()[1]), 0)
            with self.assertRaises(LedgerError):
                ledger.replay(expected_head=head)

    def test_does_not_claim_protected_read(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            with self.assertRaises(LedgerError):
                ledger.append('READ_STARTED', {'partition_id':'p1', 'role':'FINAL_OOS','scope':'SYNTHETIC_ONLY'})
            self.assertEqual(len(ledger.replay()[1]), 0)

    def test_unexpected_item_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            (ledger.root/'README.txt').write_text('bad')
            with self.assertRaises(LedgerError):
                ledger.replay()

    def test_batch_uniqueness_and_trial_registration(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            one = {'batch_id': 'b1', 'trials': 1, 'trial_ids': ['trial'], 'definition_sha': 'a'*64}
            ledger.append('BATCH_REGISTERED', one)
            with self.assertRaises(LedgerError):
                ledger.append('BATCH_REGISTERED', one)
            with self.assertRaises(LedgerError):
                ledger.append('TRIAL_RESULT', {'batch_id':'missing', 'trial_id':'trial',
                                               'status':'DONE', 'result_sha':'b'*64})
            result = {'batch_id':'b1', 'trial_id':'trial', 'status':'DONE', 'result_sha':'b'*64}
            ledger.append('TRIAL_RESULT', result)
            with self.assertRaises(LedgerError):
                ledger.append('TRIAL_RESULT', result)
            self.assertEqual(len(ledger.replay()[1]), 2)

    def test_rejects_noncanonical_payload(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'led')
            ledger.initialize()
            with self.assertRaises(LedgerError):
                ledger.append('TRIAL_RESULT', {'batch_id': 'x', 'trial_id': 'y', 'status': 'DONE',
                                               'result_sha': 'not_sha'})


if __name__ == '__main__':
    unittest.main()
