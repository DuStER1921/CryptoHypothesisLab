"""Purely synthetic 2024 Development minute fixtures. Never read private ZIPs in CI."""
import json
import math
import unittest
from datetime import datetime, timedelta, timezone

from hypolab.airgap_bundle import create_bundle, BundleError
from hypolab.c1c2_features import MinuteFeature
from hypolab.quant_screen import (make_default_plan,run_registered_screen,
                                  _nonoverlap,_t_statistics,_daily_summary,_future_proxy,
                                  COST_BPS,ENGINE)

try:
    import numpy as np
except ImportError:
    np=None


@unittest.skipUnless(np is not None, 'optional numpy unavailable')
class QuantScreenContracts(unittest.TestCase):
    @staticmethod
    def rows(count):
        base=datetime(2024,7,1,tzinfo=timezone.utc)
        arr=[]
        for i in range(count):
            minute=base+timedelta(minutes=i)
            # Synthetic-only trend+noise with some C2_UNKNOWN minutes.
            price=60000+0.025*i+0.7*math.sin(i/7)
            state='C2_UNKNOWN' if i%73==0 else 'C2_PRESENT'
            buy=4.+(i%17)/7 if state=='C2_PRESENT' else None
            sell=4.+((i*11)%19)/8 if state=='C2_PRESENT' else None
            delta=buy-sell if buy is not None else None
            imb=delta/(buy+sell) if buy is not None else None
            arr.append(MinuteFeature(minute, minute+timedelta(seconds=61,milliseconds=500),
                                     price,state,buy,sell,delta,imb,0 if buy is None else 17))
        return tuple(arr)

    @classmethod
    def setUpClass(cls):
        cls.payload=create_bundle(month='2024-07',features=cls.rows(31*24*60-1),
                                  universe_sha256='a'*64,calendar_sha256='b'*64,
                                  c1_sha256='c'*64,c2_sha256='d'*64,
                                  assumed_latency_ms=1500,producer_code_commit='e'*40)
        cls.plan=make_default_plan(cls.payload)

    def test_plan_is_deterministic_full_universe(self):
        self.assertEqual(self.plan,make_default_plan(self.payload))
        obj=json.loads(self.plan)
        self.assertEqual(obj['schema'],ENGINE)
        self.assertEqual(len(obj['trials']),1440)
        self.assertEqual(len(set(x['id'] for x in obj['trials'])),1440)
        self.assertEqual(obj['train_calendar_days'],21)
        self.assertEqual(obj['role'],'DEVELOPMENT')
        self.assertNotIn('2025',str(obj['trials']))

    def test_any_cherrypicking_or_retargeting_plan_denied(self):
        plan=json.loads(self.plan)
        plan['trials'].pop()
        with self.assertRaises(BundleError):
            run_registered_screen(self.payload,json.dumps(plan).encode())
        with self.assertRaises(BundleError):
            run_registered_screen(self.payload,self.plan+b'\n')
        with self.assertRaises(BundleError):
            run_registered_screen(self.payload,b'')

    def test_full_study_reports_every_trial_and_no_validation(self):
        report=run_registered_screen(self.payload,self.plan)
        self.assertEqual(report['total_trials'],1440)
        self.assertEqual(len(report['all_trials']),1440)
        self.assertEqual(report['month'],'2024-07')
        self.assertEqual(report['train_calendar_days'],21)
        self.assertEqual(report['forward_development_days'],10)
        self.assertEqual(set(report['benchmarks_by_horizon']),{'1','3','5','10','15','30'})
        self.assertIn('EXPLORATORY',report['inference'])
        for entry in report['all_trials']:
            self.assertIn(entry['screen_status'],('INSUFFICIENT_TRAIN_EVIDENCE','SCORED'))
            self.assertGreaterEqual(entry['training']['events'],0)
            if entry['p_maxT_train'] is not None:
                self.assertGreaterEqual(entry['p_maxT_train'],1/1025)
                self.assertLessEqual(entry['p_maxT_train'],1.)
        self.assertNotIn('source_locator',str(report))
        self.assertNotIn('PRIVATE',str(report))

    def test_future_proxy_strictly_after_feature_availability(self):
        starts=np.arange(12,dtype=np.int64)*60000
        available=starts+61500
        close=np.array([100.,100.,200.,800.]+[800.]*8)
        days=np.zeros(12,dtype=np.int64)
        gross,valid=_future_proxy(starts,available,close,days,1)
        # Event at minute 0: entry minute 1 close=100, exit minute 2 close=200.
        self.assertAlmostEqual(gross[0],10000.)
        self.assertTrue(valid[0])
        available[0]=starts[1]+60000  # next close is NOT strictly after data arrival.
        gross,valid=_future_proxy(starts,available,close,days,1)
        self.assertFalse(valid[0])
        available[0]=61500
        days[2]=1  # simulated UTC day boundary at exit
        gross,valid=_future_proxy(starts,available,close,days,1)
        self.assertFalse(valid[0])

    def test_nonoverlap_and_cost_math(self):
        selected=_nonoverlap(np.ones(20,dtype=bool),5)
        self.assertTrue(np.array_equal(selected,np.array([0,7,14])))
        gross=np.array([1.,0.,7.,0.])
        summary,days=_daily_summary(np.array([0,2]),gross,np.array([0,0,1,1]),2)
        self.assertEqual(summary['gross_mean_bps'],4.)
        self.assertEqual(summary['net_mean_bps']['2'],2.)
        self.assertEqual(summary['net_mean_bps']['5'],-1.)
        self.assertTrue(np.array_equal(days,np.array([-1.,5.])))

    def test_day_permutation_stat_zero_if_constant(self):
        vals=np.zeros((21,2))
        vals[:,1]=1.
        stat=_t_statistics(vals)
        self.assertEqual(list(stat),[0.,0.])

    def test_incomplete_month_rejected_even_if_zip_internally_consistent(self):
        partial=create_bundle(month='2024-07',features=self.rows(120),
                              universe_sha256='a'*64,calendar_sha256='b'*64,
                              c1_sha256='c'*64,c2_sha256='d'*64,
                              assumed_latency_ms=1500,producer_code_commit='e'*40)
        with self.assertRaises(BundleError):
            run_registered_screen(partial,make_default_plan(partial))

    def test_2025_month_cannot_create_bundle(self):
        with self.assertRaises(BundleError):
            create_bundle(month='2025-01',features=self.rows(5),universe_sha256='a'*64,
                          calendar_sha256='b'*64,c1_sha256='c'*64,c2_sha256='d'*64,
                          assumed_latency_ms=1500,producer_code_commit='e'*40)

if __name__=='__main__': unittest.main()
