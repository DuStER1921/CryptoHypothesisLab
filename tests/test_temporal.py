import unittest
from datetime import timedelta
from hypolab import Observation, Role, TemporalError, TimeGuard
from hypolab.contracts import SPLITS, utc


class TestTemporal(unittest.TestCase):
    def fixture(self, decision='2024-12-30T12:01:00Z', end='2024-12-30T12:16:00Z'):
        d = utc(decision)
        return Observation(d-timedelta(minutes=1), d, d, d,
                           d+timedelta(microseconds=1), utc(end))

    def test_exact_split_boundaries(self):
        self.assertEqual(SPLITS[Role.DEVELOPMENT].end, utc('2025-01-01T00:00:00Z'))
        self.assertEqual(SPLITS[Role.VALIDATION].end, utc('2026-01-01T00:00:00Z'))
        self.assertEqual(SPLITS[Role.FINAL_OOS].end, utc('2026-09-01T00:00:00Z'))

    def test_safe_observation(self):
        TimeGuard.check(self.fixture())

    def test_future_feature_fails(self):
        o = self.fixture()
        with self.assertRaises(TemporalError):
            TimeGuard.check(Observation(o.feature_start, o.feature_end, o.available_at + timedelta(seconds=1),
                                        o.decision_at, o.label_start, o.label_end))

    def test_label_leaks_next_bank(self):
        o = self.fixture(decision='2024-12-31T23:59:00Z', end='2025-01-01T00:15:00Z')
        with self.assertRaises(TemporalError):
            TimeGuard.check(o)

    def test_protected_role_refuses_old_development_sample(self):
        with self.assertRaises(TemporalError):
            TimeGuard.check(self.fixture(), Role.VALIDATION)

    def test_naive_time_rejected(self):
        o = self.fixture()
        with self.assertRaises(ValueError):
            TimeGuard.check(Observation(o.feature_start.replace(tzinfo=None), o.feature_end,
                                        o.available_at, o.decision_at, o.label_start, o.label_end))

    def test_purge_touching_label_boundary(self):
        a = self.fixture(decision='2024-07-01T00:01:00Z', end='2024-07-01T00:05:00Z')
        b = self.fixture(decision='2024-07-01T00:02:00Z', end='2024-07-01T00:12:00Z')
        c = self.fixture(decision='2024-07-01T00:10:00Z', end='2024-07-01T00:15:00Z')
        d = self.fixture(decision='2024-07-01T00:20:00Z', end='2024-07-01T00:21:00Z')
        x = TimeGuard.purge_forward([a,b,c,d], utc('2024-07-01T00:10:00Z'), utc('2024-07-01T00:19:00Z'))
        self.assertEqual(x['training'], (0,))
        self.assertEqual(x['purged'], (1,))
        self.assertEqual(x['evaluation'], (2,))
        self.assertEqual(x['future_excluded'], (3,))

    def test_explicit_embargo(self):
        a = self.fixture(decision='2024-07-01T00:01:00Z', end='2024-07-01T00:05:00Z')
        x = TimeGuard.purge_forward([a], utc('2024-07-01T00:10:00Z'),
                                    utc('2024-07-01T00:19:00Z'), timedelta(minutes=6))
        self.assertEqual(x['purged'], (0,))


if __name__ == '__main__':
    unittest.main()
