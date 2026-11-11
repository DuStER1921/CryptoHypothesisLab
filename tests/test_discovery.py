import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from hypolab import DiscoveryEngine, EventSpec, ExperimentLedger, MinuteBar, TemporalError
from hypolab.contracts import utc


def synthetic_bars(n=80, start='2024-07-01T00:00:00Z'):
    origin = utc(start)
    return [MinuteBar(origin+timedelta(minutes=i), origin+timedelta(minutes=i+1),
                      100.0 + (i % 17) * 0.1, 12.0 if i % 3 == 0 else 3.0,
                      2.0 if i % 3 == 0 else 5.0) for i in range(n)]


class TestDiscovery(unittest.TestCase):
    def test_trials_and_losers_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'ledger')
            ledger.initialize()
            results = DiscoveryEngine(ledger).run(bars=synthetic_bars(),
                specs=(EventSpec('buy', .5), EventSpec('unreachable', 1.0)),
                horizons=(1, 5), batch_id='demo')
            self.assertEqual(len(results), 4)
            self.assertEqual(results[0].events, 27)
            self.assertEqual(results[1].baseline_samples, 75)
            self.assertEqual(results[2].events, 0)
            self.assertIsNone(results[2].event_mean_return)
            events = ledger.replay()[1]
            self.assertEqual(events[0]['kind'], 'BATCH_REGISTERED')
            self.assertEqual([e['kind'] for e in events].count('TRIAL_RESULT'), 4)
            self.assertEqual(events[0]['payload']['trials'], 4)

    def test_gap_rejected_not_interpolated(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'ledger')
            ledger.initialize()
            b=synthetic_bars(10)
            b.pop(5)
            with self.assertRaises(TemporalError):
                DiscoveryEngine(ledger).run(bars=b,specs=(EventSpec('x', .2),),
                                            horizons=(1,), batch_id='gap')
            self.assertEqual(len(ledger.replay()[1]), 0)

    def test_no_cross_bank_label(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'ledger')
            ledger.initialize()
            bars = synthetic_bars(15, start='2024-12-31T23:50:00Z')
            result = DiscoveryEngine(ledger).run(bars=bars, specs=(EventSpec('x', .0),),
                                                  horizons=(5,), batch_id='boundary')
            # No Development outcome whose label reaches 2025.
            self.assertEqual(result[0].baseline_samples, 5)

    def test_future_available_delays_are_not_causal_fills(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = ExperimentLedger(Path(d)/'ledger')
            ledger.initialize()
            bars = synthetic_bars(10)
            shifted = MinuteBar(bars[0].start, bars[0].available_at + timedelta(minutes=15),
                                bars[0].close, bars[0].buy_qty, bars[0].sell_qty)
            bars[0] = shifted
            with self.assertRaises(TemporalError):
                DiscoveryEngine(ledger).run(bars=bars, specs=(EventSpec('x', .0),),
                                            horizons=(1,), batch_id='shift')


if __name__ == '__main__':
    unittest.main()
