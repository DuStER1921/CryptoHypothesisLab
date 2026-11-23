"""Synthetic-only C2 sub-5s tests; no Drive, source, or market-data access."""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hypolab.c2_microstructure import MicrostructureError, rollup_aggtrades
from hypolab.c2_sub5 import (
    FEATURE, _PeakTracker, iter_sub5_5s, iter_verified_staged_c2_sub5_5s,
)
from hypolab.frozen import PinnedPartition
from hypolab.staging import StagedPartition


class Sub5Tests(unittest.TestCase):
    def setUp(self):
        self.base = datetime(2024, 12, 1, tzinfo=timezone.utc)

    def row(self, idx, offset_ms, *, price=100., qty=1., buyer_maker=False):
        return {'agg_trade_id': idx, 'transact_time': self.base + timedelta(milliseconds=offset_ms),
                'price': price, 'quantity': qty, 'is_buyer_maker': buyer_maker}

    def calculate(self, rows):
        return list(iter_sub5_5s(iter(rows), period_start=self.base,
                                 period_end=self.base + timedelta(days=1)))

    def test_uniform_and_burst_same_5s_totals_different_feature(self):
        uniform = self.calculate([self.row(i + 1, t) for i, t in enumerate((0, 1000, 2000, 3000, 4000))])
        burst = self.calculate([self.row(i + 1, t) for i, t in enumerate((1000, 1001, 1002, 1003, 4000))])
        self.assertEqual(uniform[0]['aggregate_rows'], burst[0]['aggregate_rows'])
        self.assertEqual(uniform[0]['taker_imbalance'], burst[0]['taker_imbalance'])
        self.assertEqual(uniform[0][FEATURE], 0.2)
        self.assertEqual(burst[0][FEATURE], 0.8)

    def test_same_baseline_max_interarrival_different_peak(self):
        # Matching totals, boundary prices, side-run, and max gap do NOT
        # uniquely determine the new timing-concentration feature.
        a = self.calculate([self.row(i+1, t) for i, t in enumerate((0, 500, 1000, 1500, 2000, 4000))])[0]
        b = self.calculate([self.row(i+1, t) for i, t in enumerate((0, 300, 400, 500, 2000, 4000))])[0]
        for name in ('aggregate_rows', 'max_interarrival_ms', 'first_to_last_trade_bps',
                     'longest_same_taker_side_agg_rows', 'executed_quote_notional'):
            self.assertEqual(a[name], b[name])
        self.assertNotEqual(a[FEATURE], b[FEATURE])

    def test_per_bucket_row_cap_fails_closed(self):
        from hypolab.c2_sub5 import MAX_BIN_ROWS
        tracker = _PeakTracker()
        tracker.start = int(self.base.timestamp() * 1000)
        tracker.count = MAX_BIN_ROWS
        with self.assertRaisesRegex(MicrostructureError, 'cap'):
            tracker.observe({'transact_time': self.base, 'agg_trade_id': 1})

    def test_random_small_bins_against_independent_brute_force(self):
        import random
        rng = random.Random(20261123)
        for _ in range(250):
            stamps = sorted(rng.randrange(5000) for _ in range(rng.randrange(1, 55)))
            rows = [self.row(i + 1, stamp) for i, stamp in enumerate(stamps)]
            actual = self.calculate(rows)[0][FEATURE]
            if len(stamps) < 5:
                self.assertIsNone(actual)
                continue
            expected = max(sum(t - 999 <= x <= t for x in stamps)
                           for t in stamps) / len(stamps)
            self.assertAlmostEqual(actual, expected)

    def test_same_millisecond_and_half_open_boundary(self):
        self.assertEqual(self.calculate([self.row(i + 1, 500) for i in range(5)])[0][FEATURE], 1.)
        self.assertEqual(self.calculate([self.row(i + 1, t) for i, t in enumerate((0, 999, 1000, 1999, 2000))])[0][FEATURE], 0.4)

    def test_below_five_is_unknown_not_zero_and_sparse_gaps_preserved(self):
        rows = [self.row(10, 0), self.row(11, 100), self.row(12, 45000)]
        output = self.calculate(rows)
        self.assertEqual(len(output), 2)
        self.assertEqual(output[1]['start_ms'] - output[0]['start_ms'], 45000)
        self.assertIsNone(output[0][FEATURE])
        self.assertIsNone(output[1][FEATURE])

    def test_single_pass_and_original_snapshot_exact_except_feature(self):
        rows = [self.row(i + 1, t, price=100 + (i % 2), buyer_maker=(i % 3 == 0))
                for i, t in enumerate((0, 150, 200, 500, 900, 5000, 5200, 5400, 5600, 5800))]
        class Once:
            def __init__(self, data): self.data, self.calls = data, 0
            def __iter__(self):
                self.calls += 1
                if self.calls > 1: raise AssertionError('re-read')
                yield from self.data
        one = Once(rows)
        actual = list(iter_sub5_5s(one, period_start=self.base, period_end=self.base + timedelta(days=1)))
        self.assertEqual(one.calls, 1)
        baseline = [r for sec, r in rollup_aggtrades(rows, period_start=self.base,
                    period_end=self.base + timedelta(days=1), seconds=(5,))]
        self.assertEqual([{k: v for k, v in r.items() if k != FEATURE} for r in actual], baseline)

    def test_missing_id_fail_closed(self):
        with self.assertRaisesRegex(MicrostructureError, 'continuity'):
            self.calculate([self.row(i, t) for i, t in enumerate((0, 100, 200, 300, 400), start=10)] + [self.row(20, 5100)])

    def test_duplicate_id_and_decreasing_time_fail_closed(self):
        for rows in ([self.row(1, 0), self.row(1, 100)],
                     [self.row(1, 100), self.row(2, 0)]):
            with self.subTest(rows=rows), self.assertRaises(MicrostructureError):
                self.calculate(rows)

    def test_source_schema_freshness_protected_and_invalid_inputs(self):
        with self.assertRaises(MicrostructureError):
            list(iter_verified_staged_c2_sub5_5s(None))
        with self.assertRaises(MicrostructureError):
            list(iter_sub5_5s([], period_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                                period_end=datetime(2025, 2, 1, tzinfo=timezone.utc)))
        with self.assertRaises(MicrostructureError):
            self.calculate([self.row(1, 0), self.row(2, 100, price=0.)])

    def test_utc_day_rollover_resets_bucket_no_ghosts(self):
        start = 86400000 - 4999
        rows = [self.row(i + 1, start + i * 1000) for i in range(5)]
        rows += [self.row(i + 6, 86400000 + i * 1000) for i in range(5)]
        actual = list(iter_sub5_5s(rows, period_start=self.base,
                                   period_end=self.base + timedelta(days=2)))
        self.assertEqual(len(actual), 2)
        self.assertEqual([r[FEATURE] for r in actual], [0.2, 0.2])

    def test_tracker_same_month_scoped_staged_file_requires_exact_period(self):
        ident = PinnedPartition('aggTrades', '2024-11', 'binance-usdm-BTCUSDT-aggTrades',
                                '0'*64, '1'*64, '2'*64, '3'*64)
        stage = StagedPartition(ident, __import__('pathlib').Path('/tmp/unused'), True)
        with self.assertRaisesRegex(MicrostructureError, '2024-12'):
            list(iter_verified_staged_c2_sub5_5s(stage))

    def test_owner_stage_verification_fails_before_pyarrow(self):
        ident = PinnedPartition('aggTrades', '2024-12', 'binance-usdm-BTCUSDT-aggTrades',
                                '0'*64, '1'*64, '2'*64, '3'*64)
        stage = StagedPartition(ident, __import__('pathlib').Path('/tmp/unused'), True)
        with patch('hypolab.c2_sub5._verify_staging', side_effect=RuntimeError('checksum mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                list(iter_verified_staged_c2_sub5_5s(stage))

    def test_owner_stage_adapter_synthetic_parquet_stream(self):
        # Exercise the one-pass reader/adapter without any actual warehouse.
        ident = PinnedPartition('aggTrades', '2024-12', 'binance-usdm-BTCUSDT-aggTrades',
                                '0'*64, '1'*64, '2'*64, '3'*64)
        stage = StagedPartition(ident, __import__('pathlib').Path('/tmp/unused'), True)
        data = [self.row(i+1, i*100) for i in range(5)]
        reads = []
        class Batch:
            def to_pylist(self): return data
        class Schema:
            def equals(self, expected): return expected == 'expected_schema'
        class Reader:
            schema_arrow = Schema()
            def __init__(self, path): reads.append('open')
            def __enter__(self): return self
            def __exit__(self, *a): reads.append('close')
            def iter_batches(self, *, batch_size, columns):
                self._check_columns(columns)
                reads.append('read')
                yield Batch()
            @staticmethod
            def _check_columns(cols):
                assert set(cols) == {'agg_trade_id','transact_time','price','quantity','is_buyer_maker'}
        pa = types.ModuleType('pyarrow')
        pq = types.ModuleType('pyarrow.parquet')
        pq.ParquetFile = Reader
        pa.parquet = pq
        with patch.dict(sys.modules, {'pyarrow': pa, 'pyarrow.parquet': pq}), \
             patch('hypolab.c2_sub5._verify_staging', side_effect=lambda *_: reads.append('verify')), \
             patch('hypolab.parquet_window.schema_for', return_value='expected_schema'):
            actual = list(iter_verified_staged_c2_sub5_5s(stage))
        self.assertEqual(actual[0][FEATURE], 1.)
        self.assertEqual(reads, ['verify','open','read','close','verify'])


if __name__ == '__main__':
    unittest.main()
