"""Synthetic 2024 fixtures only; no real BTC market data or credentials."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from hypolab.airgap_bundle import BundleError, create_bundle
from hypolab.c1c2_features import MinuteFeature
from hypolab.premove_warning import (make_premove_plan, run_premove_study,
                                    _features, _rule_directions, _first_hit,
                                    _cooldown_positions)

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipUnless(np is not None,'numpy optional dependency')
class PremovemoveTests(unittest.TestCase):
    @staticmethod
    def fixture_rows(count=31*1440-1):
        start=datetime(2024,7,1,tzinfo=timezone.utc)
        import math
        rows=[]
        for i in range(count):
            t=start+timedelta(minutes=i)
            px=30000+1.2*math.sin(i/19)+i*.002
            known=i%127!=0
            buy=7+((i*3)%19) if known else None
            sell=7+((i*13)%23) if known else None
            delta=buy-sell if known else None
            imbalance=delta/(buy+sell) if known else None
            rows.append(MinuteFeature(t,t+timedelta(seconds=61,milliseconds=500),px,
                       'C2_PRESENT' if known else 'C2_UNKNOWN',buy,sell,delta,imbalance,10 if known else 0))
        return tuple(rows)

    @classmethod
    def setUpClass(cls):
        cls.packet=create_bundle(month='2024-07',features=cls.fixture_rows(),
                                 universe_sha256='a'*64,calendar_sha256='b'*64,
                                 c1_sha256='c'*64,c2_sha256='d'*64,
                                 assumed_latency_ms=1500,producer_code_commit='e'*40)
        cls.plan=make_premove_plan(cls.packet)

    def test_complete_plan_and_every_alert_is_counted(self):
        report=run_premove_study(self.packet,self.plan)
        self.assertEqual(report['total_variant_evaluations'],18)
        self.assertEqual(len(report['evaluations']),18)
        self.assertEqual(report['inference'],'DESCRIPTIVE_DEVELOPMENT_ONLY_NOT_INDEPENDENT_EDGE')
        self.assertEqual(report['book_depth'].split(';')[0],'UNKNOWN_NOT_INCLUDED')
        gallery=report['ex_post_burst_gallery']
        for rule in ('flow_acceleration','asia_pressure','asia_absorption_proxy'):
            x=gallery['event_proxy_warning_coverage'][rule]
            self.assertEqual(x['direction_matched_proxy_episodes']+x['proxy_episodes_missed'],
                             gallery['total_proxy_episodes'])
        for item in report['evaluations']:
            self.assertEqual(item['correct_direction_hit']+item['wrong_direction_first_hit']+
                             item['no_threshold_hit'],item['evaluated_alerts'])
            self.assertEqual(item['false_alarms_including_wrong_direction'],
                             item['wrong_direction_first_hit']+item['no_threshold_hit'])
            self.assertLessEqual(item['evaluated_alerts'],item['alerts_after_cooldown'])
            self.assertLessEqual(item['alerts_after_cooldown'],item['raw_candidate_minutes'])
            self.assertIn('naive_imbalance_reference',item)
            self.assertIn(item['status'],('NO_EVALUABLE_ALERTS','INSUFFICIENT_SIGNAL_COUNTS','DESCRIPTIVE_NO_SIGNIFICANCE_INFERENCE'))

    def test_preregistered_plan_cannot_be_modified(self):
        p=json.loads(self.plan)
        p['thresholds_bps']=[10,30]
        with self.assertRaises(BundleError):
            run_premove_study(self.packet,json.dumps(p).encode())
        with self.assertRaises(BundleError):
            run_premove_study(self.packet,b'')

    def test_no_lookahead_in_indicator_or_asia_level(self):
        _, rows=self.fixture_meta()
        f1=_features(rows)
        from copy import deepcopy
        newer=deepcopy(rows)
        k=600  # after session close; no change in as-of level before k
        newer[k+1]['close']='9999999.0'
        newer[k+1]['taker_imbalance']='0.95'
        newer[k+1]['taker_buy_base']='1000.0'
        newer[k+1]['taker_sell_base']='1.0'
        # Feature routine is intentionally pure and no-op on future labels.
        f2=_features(newer)
        for name in _rule_directions(f1):
            self.assertTrue(np.array_equal(_rule_directions(f1)[name][:k+1],
                                           _rule_directions(f2)[name][:k+1]))
        self.assertTrue(np.all(np.isnan(f1['asia_hi'][:480])))
        self.assertEqual(f1['asia_hi'][480],max(f1['px'][:480]))
        self.assertEqual(f1['asia_lo'][480],min(f1['px'][:480]))

    @classmethod
    def fixture_meta(cls):
        from hypolab.airgap_bundle import inspect_bundle
        return inspect_bundle(cls.packet)

    def test_missing_c2_and_history_unknown_never_alert(self):
        _,rows=self.fixture_meta()
        f=_features(rows)
        dirs=_rule_directions(f)
        self.assertFalse(f['usable'][0])
        self.assertFalse(f['usable'][127])
        for name in dirs:
            self.assertEqual(dirs[name][127],0)

    def test_first_hit_wrong_way_and_no_hindsight_entry(self):
        f=dict(ts=np.arange(30,dtype=np.int64)*60_000,
               available=np.arange(30,dtype=np.int64)*60_000+61_500,
               px=np.full(30,100.,dtype=float),day=np.zeros(30,dtype=np.int64))
        f['px'][2]=99.7
        f['px'][3]=100.4
        self.assertEqual(_first_hit(f,0,1,20,4)[0],'wrong_way')
        self.assertEqual(_first_hit(f,0,-1,20,4)[0],'correct')
        f['available'][0]=120000
        self.assertIsNone(_first_hit(f,0,1,20,4))
        f['available'][0]=61500
        f['day'][4]=1
        self.assertIsNone(_first_hit(f,0,1,20,3))

    def test_one_alert_cooldown_is_future_blind(self):
        x=np.ones(50,dtype=np.int8)
        day=np.zeros(50,dtype=np.int64)
        self.assertEqual(list(_cooldown_positions(x,day)),[0,16,32,48])

    def test_source_bundle_change_invalidates_plan(self):
        changed=create_bundle(month='2024-07',features=self.fixture_rows()[:-1],
                              universe_sha256='a'*64,calendar_sha256='b'*64,
                              c1_sha256='c'*64,c2_sha256='d'*64,
                              assumed_latency_ms=1500,producer_code_commit='e'*40)
        with self.assertRaises(BundleError):
            run_premove_study(changed,self.plan)

if __name__=='__main__':unittest.main()
