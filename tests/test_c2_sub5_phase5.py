"""Only synthetic packet + offline study tests; NEVER a real market data read."""
import datetime as dt
import hashlib
import math
import os
from pathlib import Path
import random
import tempfile
import unittest
import zipfile

from hypolab.c2_sub5 import iter_sub5_5s,FEATURE
from hypolab.c2_microstructure import MicrostructureError
from hypolab.c2_sub5_packet import (
    PacketError, _build_packet_from_buckets, verify_private_packet,
    iter_packet_rows, PROTOCOL_SHA256,
)
from hypolab.c2_sub5_study import (
    _uniform_placebo,_candidate_samples,evaluate_samples,
    DEC_START_MS,DAY_MS,ELIGIBLE_ANCHORS_PER_DAY,
)


class PrivatePacketTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        self.base=dt.datetime(2024,12,1,tzinfo=dt.timezone.utc)

    def tearDown(self):self.temp.cleanup()

    def rows(self,offsets):
        return [{'agg_trade_id':i+1,'transact_time':self.base+dt.timedelta(milliseconds=ms),
                 'price':100.+i/10.,'quantity':1.+i/10.,'is_buyer_maker':i%2==0}
                for i,ms in enumerate(offsets)]

    def packet(self,offsets,*,name='sample.zip'):
        buckets=iter_sub5_5s(self.rows(offsets),period_start=self.base,
                            period_end=self.base+dt.timedelta(days=31))
        return _build_packet_from_buckets(buckets,private_dir=self.folder,
                  target_name=name,source_sha256='1'*64,owner_event_sha256='2'*64,
                  code_sha256='3'*64,synthetic=True)

    def test_roundtrip_valid_burst_and_manifest_integrity(self):
        result=self.packet([0,200,400,600,800,5000,6000,7000,8000,9000])
        self.assertTrue(result['path'].is_file())
        self.assertEqual(result['manifest']['protocol_sha256'],PROTOCOL_SHA256)
        self.assertFalse(result['manifest']['owner_receipt_independently_authenticated'])
        man=verify_private_packet(result['path'],expected_packet_sha256=result['sha256'],
                expected_source_sha256='1'*64,expected_code_sha256='3'*64,
                allow_synthetic=True)
        self.assertEqual(man['rows'],2)
        rows=list(iter_packet_rows(result['path']))
        self.assertEqual(len(rows),2)
        self.assertAlmostEqual(rows[0][FEATURE],1.)
        self.assertEqual(rows[1][FEATURE],.2)

    def test_synthetic_packet_cannot_impersonate_real(self):
        result=self.packet([0,200,400,600,800])
        with self.assertRaisesRegex(PacketError,'identity'):
            verify_private_packet(result['path'],expected_packet_sha256=result['sha256'],
                  expected_source_sha256='1'*64,expected_code_sha256='3'*64)

    def test_wrong_packet_and_source_sha_fail_closed(self):
        result=self.packet([0,200,400,600,800])
        for kw in [{'expected_packet_sha256':'e'*64},{'expected_source_sha256':'f'*64},
                   {'expected_code_sha256':'d'*64}]:
            args=dict(expected_packet_sha256=result['sha256'],
                    expected_source_sha256='1'*64,expected_code_sha256='3'*64,
                    allow_synthetic=True)
            args.update(kw)
            with self.subTest(kw=kw),self.assertRaises(PacketError):
                verify_private_packet(result['path'],**args)

    def test_packet_create_only_and_out_of_period_abort(self):
        result=self.packet([0,200,400,600,800])
        before=result['path'].read_bytes()
        with self.assertRaisesRegex(PacketError,'already exists'):
            self.packet([0,200,400,600,800])
        self.assertEqual(before,result['path'].read_bytes())
        with self.assertRaises((PacketError,MicrostructureError)):
            self.packet([31*86400000,31*86400000+1],name='out.zip')
        self.assertFalse((self.folder/'out.zip').exists())
        self.assertEqual(sorted(x.name for x in self.folder.iterdir()),['sample.zip'])

    def test_packet_unknown_under_five(self):
        result=self.packet([0,100,200,5000,5100,5200,5300,5400])
        rows=list(iter_packet_rows(result['path']))
        self.assertIsNone(rows[0][FEATURE])
        self.assertIsNotNone(rows[1][FEATURE])

    def test_fail_private_dir_permissions(self):
        self.folder.chmod(0o755)
        with self.assertRaisesRegex(PacketError,'private POSIX'):
            self.packet([0,100,200,300,400])
        self.folder.chmod(0o700)

    def test_truncated_or_tampered_packet_denied(self):
        p=self.packet([0,100,200,300,400])
        with p['path'].open('ab') as f:f.write(b'tamper')
        with self.assertRaisesRegex(PacketError,'SHA mismatch'):
            verify_private_packet(p['path'],expected_packet_sha256=p['sha256'],
                expected_source_sha256='1'*64,expected_code_sha256='3'*64,
                allow_synthetic=True)


class FrozenStudyTests(unittest.TestCase):
    def test_exact_uniform_placebo_vs_bruteforce(self):
        for n in list(range(1,300))+[499,500,501,997,1000,1001,5000,5001,10000]:
            ms=[(5000*(2*j+1))//(2*n) for j in range(n)]
            left=0;top=0
            for right,t in enumerate(ms):
                while t-ms[left]>=1000:left+=1
                top=max(top,right-left+1)
            self.assertAlmostEqual(_uniform_placebo(n),top/n)

    @staticmethod
    def bucket(day,idx):
        t=DEC_START_MS+(day-1)*DAY_MS+idx*5000
        n=7+idx%7
        signed=1 if idx%3 else -1
        px=100.+(idx*.023)+(idx%11-5)*.03+day*.15
        count_side=int(n*.66) if signed>0 else int(n*.29)
        return {'start_ms':t,'first_transact_ms':t+100,'last_transact_ms':t+3500+(idx%11)*75,
                'first_agg_trade_id':1+idx,'last_agg_trade_id':n+idx,
                'aggregate_rows':n,'taker_buy_base':float(count_side),
                'taker_sell_base':float(n-count_side),
                'executed_quote_notional':px*(n+1),
                'first_trade_price':px,'last_trade_price':px+((idx%5)-2)*.004,
                'min_trade_price':px-.01,'max_trade_price':px+.01,
                'first_to_last_trade_bps':((idx%5)-2)*.4,
                'max_interarrival_ms':100+idx%19,
                'longest_same_taker_side_agg_rows':1+idx%4,
                'max_agg_trade_notional_quote':px*(1+idx%3),
                FEATURE:(1+(idx%3))/n}

    def test_no_cross_day_and_missing_bin_reset(self):
        rows=[self.bucket(1,i) for i in range(31)]+[self.bucket(2,i) for i in range(29)]
        sample=list(_candidate_samples(rows))
        self.assertEqual(len(sample),6)  # 31-27=4; 29-27=2
        rows=[self.bucket(1,i) for i in range(30) if i!=14]
        self.assertEqual(len(list(_candidate_samples(rows))),0)

    def test_causal_entry_exit_alignment(self):
        rows=[self.bucket(21,i) for i in range(28)]
        sample=list(_candidate_samples(rows))
        self.assertEqual(len(sample),1)
        s=sample[0]
        center=rows[12]
        sign=1 if center['taker_buy_base']>center['taker_sell_base'] else -1
        self.assertAlmostEqual(s[4],sign*10000*math.log(rows[27]['first_trade_price']/rows[15]['first_trade_price']))
        self.assertEqual(s[2],center[FEATURE])

    def test_no_candidate_if_missing_feature_or_neutral_flow(self):
        rows=[self.bucket(3,i) for i in range(28)]
        rows[12][FEATURE]=None
        self.assertEqual(len(list(_candidate_samples(rows))),0)
        rows[12][FEATURE]=.5
        rows[12]['taker_buy_base']=rows[12]['taker_sell_base']=5.
        self.assertEqual(len(list(_candidate_samples(rows))),0)

    def test_insufficient_diagnostic_always_reject(self):
        samples=[]
        for i in range(300):
            x=[math.sin(i/17 + j/5)+math.cos(i/13 + j/3) for j in range(10)]
            samples.append((DEC_START_MS//DAY_MS+i//150,*[tuple(x),abs(math.sin(i/11))+.1,
                            abs(math.sin(i/13))+.1,float(math.sin(i/9))]))
        report=evaluate_samples(samples)
        self.assertEqual(report['decision'],'REJECT_OR_INSUFFICIENT')
        self.assertEqual(report['trial_id'],'BTC_C2_SUB5_V1_2024-12_M0_M1_P1')

    def test_empty_study_no_fake_success(self):
        self.assertEqual(evaluate_samples([])['reason'],'NO_ELIGIBLE_SAMPLES')

    def test_zero_variance_train_rejects_no_tuning(self):
        # Nonzero y and varied fixed-baseline features except one constant.
        samples=[]
        for i in range(200):
            d=DEC_START_MS//DAY_MS+(0 if i<100 else 21)
            features=(0.,)+tuple(float(i%19+j) for j in range(1,10))
            samples.append((d,features,float(i%5)/5,float(i%7)/7,float(i%9)))
        with self.assertRaisesRegex(PacketError,'zero/nonfinite TRAIN variance'):
            evaluate_samples(samples)

    def test_adding_true_feature_changes_prediction_in_synthetic_data(self):
        # Distinguish model M1 from placebo P1; never claim market evidence.
        import numpy as np
        rng=np.random.default_rng(23)
        samples=[]
        for i in range(3500):
            d=DEC_START_MS//DAY_MS+(i//175 if i<1750 else 20+(i-1750)//175)
            baseline=tuple(float(v) for v in rng.normal(size=10))
            true_feature=float(rng.normal())
            placebo=float(rng.normal())
            y=15*true_feature+float(rng.normal(scale=1.5))
            samples.append((d,baseline,true_feature,placebo,y))
        report=evaluate_samples(samples)
        self.assertGreater(report['mse_lift'],.25)
        self.assertGreater(report['mse_lift'],report['placebo_lift'])
        self.assertEqual(report['decision'],'SYNTHETIC_ONLY_NO_MARKET_CLAIM')

    def test_full_declared_day_coverage_and_reject_status(self):
        # 4 train days and 10 evaluation days, 1100 consecutive 5s buckets/day.
        # Synthetic only. Train-only fit, full day controls, no source reads.
        days=[1,2,3,4]+list(range(21,31))
        samples=list(_candidate_samples(self.bucket(d,i) for d in days for i in range(1100)))
        report=evaluate_samples(samples)
        self.assertGreaterEqual(report['diagnostic_rows'],10000)
        self.assertEqual(report['diagnostic_days'],10)
        self.assertTrue(report['gate_results']['enough_days'])
        self.assertTrue(report['gate_results']['enough_decisions'])
        self.assertEqual(report['eligible_per_day']['5'],0)
        self.assertEqual(report['non_evaluable_per_day']['5'],ELIGIBLE_ANCHORS_PER_DAY)
        self.assertEqual(report['synthetic'],True)
        self.assertEqual(report['decision'],'SYNTHETIC_ONLY_NO_MARKET_CLAIM')
        self.assertEqual(len(report['daily_paired']),10)
        self.assertIsNotNone(report['day_bootstrap_ci95_descriptive_only'])

if __name__=='__main__': unittest.main()
