"""V3 synthetic-only pre-onset and matched-control regression contracts."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone

from hypolab.micro_bundle import MicroBundleError
from hypolab.premove_v3 import (
    ENGINE, RULES, THRESHOLDS, HORIZONS, make_v3_plan, run_v3,
    _future_event, _quiet_range, _near_asia, _bar_context,
    _raw_signals, _match, _split_summary, COOLDOWN_SECONDS)
from test_micro_v2 import make_synthetic_zip

BASE=int(datetime(2024,7,1,8,tzinfo=timezone.utc).timestamp()*1000)


def timeline(count=90, base=BASE):
    return [{'start_ms':str(base+i*5000),
             'available_at_ms':str(base+i*5000+6500),
             'last_trade_price':'100',
             'min_trade_price':'100','max_trade_price':'100'}
            for i in range(count)]


class V3Causality(unittest.TestCase):
    def test_quiet_gate_rejects_hidden_intrabin_spike(self):
        rows=timeline()
        self.assertTrue(_quiet_range(rows,12))
        rows[4]['max_trade_price']='100.15' # 15bps spike, ending unchanged
        self.assertFalse(_quiet_range(rows,12))
        rows[4]['max_trade_price']='100'
        self.assertTrue(_quiet_range(rows,12))
        rows[1]['start_ms']=str(BASE+12345)
        self.assertFalse(_quiet_range(rows,12))

    def test_future_only_from_next_completed_close(self):
        rows=timeline()
        # Signal close=100, next close=100, future 15bps up.
        rows[5]['last_trade_price']='100.2'
        result=_future_event(rows,0,15,60,1500)
        self.assertEqual(result['status'],'EVENT')
        self.assertEqual(result['direction'],1)
        self.assertEqual(result['onset_proxy_s'],20)
        self.assertEqual(result['threshold_hit_s'],20)
        # Entry uses next completed close, not 5s signal close.
        rows[1]['last_trade_price']='99.9'
        result=_future_event(rows,0,15,60,1500)
        self.assertEqual(result['direction'],1)
        # Entire future horizon required; silent gap must be UNKNOWN.
        rows[10]['start_ms']=str(BASE+1_000_000)
        self.assertEqual(_future_event(rows,0,15,60,1500)['status'],'UNKNOWN')
        rows[10]['start_ms']=str(BASE+50000)
        rows[0]['available_at_ms']=str(BASE+10_000)
        self.assertEqual(_future_event(rows,0,15,60,1500)['status'],'UNKNOWN')

    def test_first_hit_and_directions_and_no_move(self):
        rows=timeline()
        self.assertEqual(_future_event(rows,0,15,60,1500)['status'],'NO_EVENT')
        rows[3]['last_trade_price']='99.8'
        rows[6]['last_trade_price']='100.3'
        out=_future_event(rows,0,15,60,1500)
        self.assertEqual(out['direction'],-1)
        self.assertEqual(out['threshold_hit_s'],10)

    def test_day_boundary_unknown(self):
        base=int(datetime(2024,7,1,23,59,30,tzinfo=timezone.utc).timestamp()*1000)
        rows=timeline(90,base)
        self.assertEqual(_future_event(rows,0,15,60,1500)['status'],'UNKNOWN')

    def test_asia_only_after_closed_and_true_c1_context(self):
        bar={'close':100.,'available_at_ms':BASE,'compression':False}
        day=BASE//86400000
        levels={day:(100.,90.)}
        self.assertEqual(_near_asia(bar,levels,day,7*3600000),0)
        self.assertEqual(_near_asia(bar,levels,day,BASE+6500),1)
        self.assertEqual(_near_asia(bar,{},day,BASE+6500),0)
        c1=[]
        for i in range(23):
            c1.append({'open_time_ms':str(BASE+i*60000),
                       'available_at_ms':str(BASE+(i+1)*60000+1500),
                       'open':'100','high':'100.2','low':'99.8','close':'100',
                       'volume':'10'})
        lookup=_bar_context(c1)
        self.assertIn(BASE+21*60000,lookup)
        # Change a FUTURE C1 candle: current known features unchanged.
        before=lookup[BASE+21*60000].copy()
        c1[22]['high']='10000';c1[22]['volume']='9999'
        after=_bar_context(c1)
        self.assertEqual(after[BASE+21*60000],before)

    def test_fixed_rule_and_placebo_only_past(self):
        f={'imbalance':.8,'rel_quote':3,'rel_count':2,
           'imbalance_acceleration':.3,'impact_bps':.1}
        bar={'median_range_bps':10,'range_bps':6,
             'wick_up_bps':0,'wick_down_bps':0,'compression':True}
        raw=_raw_signals(f,bar,1,None)
        self.assertEqual(raw['c2_flow_surge'],1)
        self.assertEqual(raw['fusion_asia_flow'],1)
        self.assertEqual(raw['fusion_compression_flow'],1)
        self.assertEqual(raw['c2_impact_divergence_fade'],-1)
        self.assertNotIn('stale_c2_surge_60m',raw)
        self.assertEqual(_raw_signals(f,bar,1,-1)['stale_c2_surge_60m'],-1)

    def test_case_control_match_uses_only_predecision_values(self):
        # Same stratum but a raw signal suppressed by cooldown is not a control.
        stratum=(1,8,0,False,False)
        def d(index,t,signals,raw):
            return {'index':index,'t':t,'day':1,'stratum':stratum,
                    'signals':signals,'raw_signals':raw}
        decisions=[d(1,BASE+10000,{}, {'c2_flow_surge':1}),
                   d(2,BASE+30000,{}, {}),
                   d(0,BASE+360000,{'c2_flow_surge':1},{'c2_flow_surge':1}),
                   d(3,BASE+720000,{}, {})]
        pairs,unmatched=_match('c2_flow_surge',decisions)
        self.assertEqual(len(pairs),1)
        self.assertEqual(unmatched,0)
        self.assertEqual(pairs[0][1]['index'],2)
        # A FUTURE control in the same stratum must never be used.
        later_only=[d(0,BASE+360000,{'c2_flow_surge':1},{'c2_flow_surge':1}),
                    d(4,BASE+720000,{}, {})]
        later_pairs,unmatched=_match('c2_flow_surge',later_only)
        self.assertEqual(later_pairs,[])
        self.assertEqual(unmatched,1)
        # Counterfactual future labels are not even passed to _match.

    def test_paired_daily_summary_not_overinterpreted(self):
        a={'index':0,'day':1,'day_in_month':1,'signals':{'r':1},'active_rule':'r'}
        b={'index':1}
        outcomes={(0,15,60):{'status':'EVENT','direction':1,'onset_proxy_s':20},
                  (1,15,60):{'status':'NO_EVENT','direction':0}}
        r=_split_summary([(a,b)],outcomes,15,60,'train')
        self.assertEqual(r['event_risk_difference'],1)
        self.assertEqual(r['correct_direction_rate'],1)
        self.assertEqual(r['status'],'INSUFFICIENT_MATCHED_EVIDENCE')
        self.assertIsNone(r['daily_bootstrap_95pct_risk_difference'])


class V3FullSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.bundle=make_synthetic_zip(Path(cls.temp.name))
        cls.plan=make_v3_plan(cls.bundle)
        cls.report=run_v3(cls.bundle,cls.plan)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_plan_exact_fixed_and_provenance(self):
        p=json.loads(self.plan)
        self.assertEqual(p['schema'],ENGINE)
        self.assertEqual(p['role'],'DEVELOPMENT')
        self.assertEqual(p['thresholds_bps'],list(THRESHOLDS))
        self.assertEqual(p['horizons_seconds'],list(HORIZONS))
        self.assertEqual(len(p['rules']),len(RULES))
        self.assertEqual(p['bundle_sha256'],hashlib.sha256(self.bundle.read_bytes()).hexdigest())
        changed=json.loads(self.plan);changed['quiet_max_intra_bucket_price_range_bps']=100
        with self.assertRaises(MicroBundleError):
            run_v3(self.bundle,json.dumps(changed).encode())
        with self.assertRaises(MicroBundleError):run_v3(self.bundle,self.plan+b' ')

    def test_all_negative_cases_reported_even_no_signals(self):
        r=self.report
        self.assertEqual(len(r['trials']),len(RULES)*len(THRESHOLDS)*len(HORIZONS))
        self.assertEqual(r['role'],'DEVELOPMENT')
        self.assertEqual(r['c3_book_depth'],'UNKNOWN_NOT_INCLUDED')
        self.assertGreater(r['eligible_risk_decisions'],0)
        self.assertEqual(set(r['pair_accounting']),set(name for name,_ in RULES))
        for trial in r['trials']:
            self.assertIn(trial['train']['status'],('EXPLORATORY_DESCRIPTIVE_ONLY','INSUFFICIENT_MATCHED_EVIDENCE'))
            self.assertIn(trial['later_development_diagnostic']['status'],('EXPLORATORY_DESCRIPTIVE_ONLY','INSUFFICIENT_MATCHED_EVIDENCE'))
        self.assertIn('REUSED_DEVELOPMENT_NOT_VALIDATED',r['inference'])
        self.assertEqual(r['retrospective_references']['reference_count'],0)
        self.assertEqual(r['plan_sha256'],hashlib.sha256(self.plan).hexdigest())

if __name__=='__main__':unittest.main()
