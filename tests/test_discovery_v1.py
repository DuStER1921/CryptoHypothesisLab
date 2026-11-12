"""Synthetic-only guards; no real or protected market rows read."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from hypolab import discovery_v1 as d
from hypolab.micro_bundle import MicroBundleError


class DiscoveryV1Tests(unittest.TestCase):
    def test_predeclared_family_exactly_2520_in_four_horizons(self):
        self.assertEqual(len(d.FEATURES), 12)
        self.assertEqual(len(d._family()), 630)
        self.assertEqual(len(d._family()) * len(d.HORIZONS_S), 2520)
        self.assertEqual(d.HORIZONS_S,(300,900,1800,3600))
        self.assertEqual(d.COST_BPS,(16.,20.,24.))
        self.assertEqual(d.JULY,'2024-07')
        self.assertEqual(d.AUGUST,'2024-08')

    @staticmethod
    def c1(t,close=100.,available=None):
        return { 'open_time_ms':str(t), 'available_at_ms':str(t+61500 if available is None else available),
            'open':'100','close':str(close),'high':str(max(close,101.)),
            'low':'99.','volume':'12','quote_volume':'1200','count':'20',
            'taker_buy_base':'6','taker_buy_quote':'600' }

    @staticmethod
    def c2(t,*,last=100.,available=None):
        return {'start_ms':str(t),'available_at_ms':str(t+6500 if available is None else available),
            'aggregate_rows':'10','taker_buy_base':'2','taker_sell_base':'3',
            'taker_imbalance':'-0.2','executed_quote_notional':'1000',
            'max_agg_trade_notional_quote':'300','first_trade_price':str(last),
            'last_trade_price':str(last),'min_trade_price':'99.9',
            'max_trade_price':'100.1','first_to_last_trade_bps':'0'}

    def test_causal_dataset_text_csv_and_old_c1_only(self):
        c1=[self.c1(i*60_000) for i in range(125)]
        rows=[self.c2(i*5_000) for i in range(125*12)]
        times,idx,features=d._dataset(c1,rows)
        self.assertEqual(len(times),65) # minute 60 through 124
        self.assertEqual(features.shape,(65,len(d.FEATURES)))
        self.assertEqual(times[0],60*60_000)
        self.assertTrue(np.all(np.isfinite(features)))
        # Future C1 bar cannot alter prior signal.
        copy=[dict(x) for x in c1]
        copy[100]['high']='1000000'
        copy[100]['close']='999999'
        t2,_,f2=d._dataset(copy,rows)
        self.assertEqual(times.tolist(),t2.tolist())
        self.assertTrue(np.array_equal(features[times<100*60_000],f2[t2<100*60_000]))
        # If the most recent previous C1 bar has not arrived, fail closed.
        c1[70]['available_at_ms']=str(70*60_000+120_000)
        t3,_,_=d._dataset(c1,rows)
        self.assertNotIn(71*60_000,t3)
        # Missing C2 5s history excludes the affected minute.
        gapped=rows[:60*12-1]+rows[60*12:]
        tg,_,_=d._dataset([self.c1(i*60_000) for i in range(125)],gapped)
        self.assertNotIn(60*60_000,tg)

    def test_forward_return_entry_latency_and_gap_and_day_fence(self):
        # Signal at 0, entry at 15s, 300-second end = 310s last bin.
        rows=[self.c2(i*5000,last=100.0 if i<3 else 102.) for i in range(90)]
        rows[3]['first_trade_price']='100'
        times=np.array([0],dtype=np.int64)
        idx=np.array([0],dtype=np.int64)
        y=d._forward_returns(rows,times,idx,300)
        self.assertAlmostEqual(y[0],200.)
        late=[dict(x) for x in rows];late[0]['available_at_ms']='99999'
        self.assertTrue(np.isnan(d._forward_returns(late,times,idx,300)[0]))
        hole=[dict(x) for x in rows];hole[35]['start_ms']=str(35*5000+5000)
        self.assertTrue(np.isnan(d._forward_returns(hole,times,idx,300)[0]))
        # No horizon may cross the UTC day boundary even if rows are contiguous.
        near_end=86_400_000-100_000
        crossing=[self.c2(near_end+i*5000) for i in range(90)]
        self.assertTrue(np.isnan(d._forward_returns(crossing,np.array([near_end]),np.array([0]),300)[0]))

    def test_train_only_terciles_and_immutable_membership(self):
        times=d.JULY_START_MS+np.repeat(np.arange(31,dtype=np.int64)*d.DAY_MS,10)+np.tile(np.arange(10,dtype=np.int64)*d.MINUTE_MS,31)
        vals=np.zeros((310,12))
        vals[:,0]=np.repeat(np.arange(31),10)
        cut=d._thresholds(times,vals)
        self.assertEqual(cut['c2_imbalance_5s'],np.quantile(np.repeat(np.arange(21),10),[1/3,2/3]).tolist())
        vals[210:,0]=1e8
        self.assertEqual(d._thresholds(times,vals),cut)
        self.assertTrue(np.all(d._bins(vals,cut)[:,1]==-1))
        self.assertEqual(d._bins(vals,cut)[0,0],0)
        self.assertEqual(d._bins(vals,cut)[200,0],2)

    def test_missing_first_july_day_never_shifts_train_boundary(self):
        # July 1 is missing. July 22 must still remain OUTSIDE the July 1-21 fit.
        days=np.repeat(np.arange(1,31),10)
        times=d.JULY_START_MS+days*d.DAY_MS+np.tile(np.arange(10),30)*d.MINUTE_MS
        vals=np.zeros((len(times),12));vals[:,0]=days
        cuts=d._thresholds(times,vals)
        expected=np.quantile(np.repeat(np.arange(1,21),10),[1/3,2/3]).tolist()
        self.assertEqual(cuts[d.FEATURES[0]],expected)
        vals[days>=21,0]=999999
        self.assertEqual(d._thresholds(times,vals),cuts)

    def test_joint_max_t_correction_and_ineligible(self):
        rng=np.random.default_rng(21)
        x=rng.normal(0,1,size=(20,30))
        support=np.ones_like(x,dtype=np.int32)
        q=np.ones(30,dtype=bool);q[0]=False
        p,t=d._max_t(x,support,q,seed=55,reps=75)
        self.assertEqual(p.shape,(30,))
        self.assertTrue(np.all(p>=1/76))
        self.assertTrue(np.all(p<=1))
        self.assertEqual(t[0],0.)
        self.assertEqual(p[0],1.)

    def test_empty_july_shortlist_refuses_august(self):
        with patch.object(d,'freeze_sha256',return_value='a'*64):
            p={'schema':d.SCHEMA,'month':d.JULY,'protocol_sha256':d.sha256(d.protocol_bytes()).hexdigest(),
               'frozen_code_token':'a'*64,'feature_cuts':{n:[0.,1.] for n in d.FEATURES},
               'july_bundle_sha256':'b'*64}
            plan=d._canonical(p)
            report={'schema':d.SCHEMA,'month':d.JULY,'plan_sha256':d.sha256(plan).hexdigest(),
                    'fixed_shortlist_for_next_development':[]}
            manifest=d.make_replay_manifest(report,plan)
            self.assertIn(b'NO AUGUST TEST',manifest)
            with self.assertRaisesRegex(MicroBundleError,'NO CANDIDATE'):
                d.run_august_replay(Path('/no-actual-source.zip'),plan,manifest,d._canonical(report))

    def test_tampering_plan_or_manifest_denied_before_market_open(self):
        with patch.object(d,'freeze_sha256',return_value='a'*64):
            p={'schema':d.SCHEMA,'month':d.JULY,'protocol_sha256':d.sha256(d.protocol_bytes()).hexdigest(),
               'frozen_code_token':'a'*64,'feature_cuts':{n:[0.,1.] for n in d.FEATURES},
               'july_bundle_sha256':'b'*64}
            plan=d._canonical(p)
            self.assertEqual(d._parse_plan(plan),p)
            with self.assertRaises(MicroBundleError):d._parse_plan(plan+b'\n')
            tampered=json.loads(plan);tampered['frozen_code_token']='z'*64
            with self.assertRaises(MicroBundleError):d._parse_plan(d._canonical(tampered))

    def test_no_private_sources_orders_or_protected_roles(self):
        source=Path(d.__file__).read_text()
        for forbidden in ('drive.mount(', 'GoogleDrivePinnedSource(', 'authenticate_user(',
                          'place_order(', 'ccxt.', '2025-01', '2026-01'):
            self.assertNotIn(forbidden,source)

if __name__=='__main__':unittest.main()
