"""Synthetic-only V3.1 checks. No market data or Drive services involved."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from hypolab import premove_v31 as v

DAY = 1719792000000  # Synthetic timestamps; values are invented fixtures.


def row(i, price=100., low=None, high=None, ms=DAY):
    t=ms+i*5000
    return {'start_ms':t,'available_at_ms':t+6500,
            'last_trade_price':price,
            'first_trade_price':price,
            'min_trade_price':low if low is not None else price,
            'max_trade_price':high if high is not None else price,
            'taker_delta_base':3., 'taker_imbalance':.65,
            'aggregate_rows':20,'executed_quote_notional':4000.,
            'first_to_last_trade_bps':0.}


def c1_rows(minutes=90):
    return [{'open_time_ms':DAY+i*60000,'available_at_ms':DAY+(i+1)*60000+1500,
             'open':100.,'close':100.,'high':100.05,'low':99.95,
             'volume':20.,'count':10} for i in range(minutes)]


class V31PureTests(unittest.TestCase):
    def test_trailing_range_only_complete_twelve_and_intra_high_low(self):
        rows=[row(i) for i in range(24)]
        rows[5]['max_trade_price']=100.2
        x=v._range_60s(rows)
        self.assertTrue(all(r is None for r in x[:11]))
        self.assertGreater(x[11],19.9)
        self.assertLess(x[20],.001)  # past outlier expired after exactly 12 buckets

    def test_trailing_range_resets_on_gap(self):
        rows=[row(i) for i in range(17)]
        rows[8]['start_ms']+=5000
        x=v._range_60s(rows)
        self.assertIsNone(x[11])
        self.assertIsNone(x[-1])

    def test_trailing_range_resets_at_day_boundary(self):
        rows=[row(i,ms=DAY+86400000-30000) for i in range(12)]
        self.assertTrue(all(x is None for x in v._range_60s(rows)))

    def test_flow_feature_no_secret_15bps_endpoint_gate(self):
        rows=[row(i) for i in range(14)]
        rows[12]['last_trade_price']=104.
        self.assertIsNotNone(v._flow_features(rows,12))
        self.assertAlmostEqual(v._flow_features(rows,12)['rel_quote'],1.)

    def test_flow_feature_accepts_actual_csv_string_fields(self):
        # inspect_micro_bundle returns strings from csv.reader, unlike the
        # integer-valued synthetic fixtures used by earlier V3.1 tests.
        rows=[{key:str(value) for key,value in row(i).items()} for i in range(14)]
        self.assertAlmostEqual(v._flow_features(rows,12)['rel_quote'],1.)
        self.assertEqual(len([x for x in v._range_60s(rows) if x is not None]),3)

    def test_flow_feature_missing_history_is_unknown(self):
        rows=[row(i) for i in range(14)]
        rows[5]['start_ms']+=1000
        self.assertIsNone(v._flow_features(rows,12))
        self.assertIsNone(v._flow_features(rows,11))

    def test_stage_thresholds_are_inclusive_and_gap_unknown(self):
        self.assertEqual(v._stage(None),'UNKNOWN_GAP_OR_BOUNDARY')
        self.assertEqual(v._stage(8),'QUIET_8BPS')
        self.assertEqual(v._stage(8.001),'EMERGING_8_TO_20BPS')
        self.assertEqual(v._stage(20.001),'ACTIVE_OVER_20BPS')

    def test_reference_requires_same_day_continuity_and_cooldown(self):
        rows=[row(i,price=100.) for i in range(240)]
        for j in range(30,240): rows[j]['last_trade_price']=100.4
        refs=v._references(rows)
        self.assertTrue(refs)
        self.assertEqual(refs[0]['reference_start_ms'],DAY+30*5000)
        self.assertEqual(refs[0]['direction'],1)
        self.assertEqual(len(refs),1)  # later prices stationary
        rows[4]['start_ms']+=1000
        # The first reference uses only indices 18..30, so earlier data do not change it.
        self.assertEqual(refs[0]['reference_start_ms'],v._references(rows)[0]['reference_start_ms'])

    def test_matching_is_previous_only_unique_and_no_suppressed_controls(self):
        base=(DAY,1,1,False,False)
        def d(i,side=False,raw=False):
            s={'c2_flow_surge':1} if side else {}
            return {'index':i,'t':DAY+i*5000,'stratum':base,
                    'signals':s,'raw_signals':{'c2_flow_surge':1} if raw else s}
        decisions=[d(1),d(2,raw=True),d(3),d(80,side=True),d(100,side=True)]
        pair,miss=v._match_previous('c2_flow_surge',decisions)
        self.assertEqual(miss,0)
        self.assertEqual(len(pair),2)
        self.assertEqual({b['index'] for _,b in pair},{1,3})
        self.assertTrue(all(c['t']>=ctrl['t']+330000 for c,ctrl in pair))
        self.assertNotIn(2,[b['index'] for _,b in pair])

    def test_near_asia_is_not_matched_away_for_c1_rule(self):
        def d(i,near,case):
            return {'index':i,'t':DAY+i*5000,
                    'stratum':(DAY,1,1,near,False),
                    'signals':{'c1_near_asia':1} if case else {},
                    'raw_signals':{'c1_near_asia':1} if case else {}}
        data=[d(1,False,False),d(100,True,True)]
        self.assertEqual(len(v._match_previous('c1_near_asia',data)[0]),1)
        self.assertEqual(len(v._match_previous('fusion_asia_flow',data)[0]),0)

    def test_protocol_is_stable_and_months_are_development(self):
        p=json.loads(v.protocol_bytes())
        self.assertEqual(p['new_months_in_frozen_order'],['2024-08','2024-09','2024-10'])
        self.assertEqual(p['primary_scenario'],'all5_12bps')
        self.assertEqual(len(p['scenario_grid']),6)
        self.assertEqual(p['lead_seconds'],[5,15,30,60,120])
        self.assertEqual(p['label_thresholds_bps'],[15,30])

    def test_no_source_write_or_drive_use_in_module(self):
        code=Path(v.__file__).read_text(encoding='utf8')
        for term in ['drive.mount(', 'google.colab', 'CryptoResearchLab', 'open_pinned(',
                     'generations_root=', 'AUTHORIZE_MICRO_DEVELOPMENT_']:
            self.assertNotIn(term,code)

    def test_valid_plan_rejects_unlisted_month(self):
        meta={'month':'2025-01','c1_sha256':'a'*64,'c2_sha256':'b'*64,
              'producer_code_commit':'c'*40,'assumed_latency_ms':1500}
        with patch.object(v,'inspect_micro_bundle',return_value=(meta,None,None)):
            with self.assertRaises(v.MicroBundleError):v.make_v31_plan(Path('/fake.zip'))


class V31IntegrationTests(unittest.TestCase):
    def test_synthetic_end_to_end_all_trials_and_no_v3_source_io(self):
        rows=[row(i) for i in range(720)]
        # One ex-post move, plus some brief aggressive flow bursts.
        for i in range(430,len(rows)):
            rows[i]['last_trade_price']=100.4
            rows[i]['max_trade_price']=100.4
            rows[i]['min_trade_price']=100.4
        for i in range(1,len(rows)):
            if i%17==0:
                rows[i]['executed_quote_notional']=22000.
                rows[i]['aggregate_rows']=90
                rows[i]['taker_imbalance']=.9
        # Reproduce the literal string-valued schema produced by the MICRO
        # bundle CSV parser, rather than relying on numeric fixture values.
        rows=[{key:str(value) for key,value in item.items()} for item in rows]
        source_c1=[{key:str(value) for key,value in item.items()} for item in c1_rows(60)]
        meta={'month':'2024-07','assumed_latency_ms':1500,
              'counts':{'c1_1m':60,'c2_5s':720,'c2_15s':240,'c2_60s':60}}
        with patch.object(v,'make_v31_plan',return_value=b'plan'),\
             patch.object(v,'inspect_micro_bundle',return_value=(meta,source_c1,rows)),\
             patch.object(v,'_sha',return_value='z'*64):
            report=v.run_v31(Path('/fake.zip'),b'plan')
        self.assertEqual(report['schema'],v.SCHEMA)
        # A non-empty, well-formed 5s feed must not silently produce 0 risk
        # decisions because of parser type mismatches.
        self.assertGreater(report['risk_sensitivity']['all5_no_quiet']['eligible_risk_decisions'],0)
        self.assertGreater(report['risk_sensitivity']['all5_12bps']['eligible_risk_decisions'],0)
        self.assertGreater(report['risk_sensitivity']['all5_no_quiet']['raw_signal_counts']['c2_flow_surge'],0)
        self.assertEqual(len(report['trials']),28)
        self.assertEqual(report['reference']['count'],1)
        self.assertEqual(len(report['reference']['all_references']),1)
        self.assertEqual(len(report['reference']['all_references'][0]['pre_reference_samples']),5)
        ref=report['reference']['all_references'][0]
        self.assertFalse(ref['pre_reference_samples'][0]['observable_before_reference'])
        self.assertEqual(set(ref['legacy_miss_reasons']),{name for name,_ in v.RULES})
        for name,_ in v.RULES:
            self.assertEqual(sum(report['reference']['legacy_miss_reason_counts'][name].values()),1)
        for lead in v.LEADS_SECONDS:
            stages=report['reference']['lead_stage_counts'][str(lead)]
            self.assertEqual(sum(y for x,y in stages.items()
                              if x in ('QUIET_8BPS','EMERGING_8_TO_20BPS','ACTIVE_OVER_20BPS',
                                       'UNKNOWN_GAP_OR_BOUNDARY')),1)
        ss=report['risk_sensitivity']
        self.assertLessEqual(ss['sparse_8bps']['eligible_risk_decisions'],
                             ss['all5_8bps']['eligible_risk_decisions'])
        self.assertLessEqual(ss['all5_8bps']['eligible_risk_decisions'],
                             ss['all5_12bps']['eligible_risk_decisions'])
        self.assertLessEqual(ss['all5_12bps']['eligible_risk_decisions'],
                             ss['all5_no_quiet']['eligible_risk_decisions'])

    def test_strict_plan_fails_closed(self):
        with patch.object(v,'make_v31_plan',return_value=b'exact'):
            for invalid in ('exact',b'inexact',b''):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(v.MicroBundleError):
                        v.run_v31(Path('/fake.zip'),invalid)


if __name__=='__main__':unittest.main()
