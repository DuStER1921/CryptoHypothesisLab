"""Entirely synthetic micro-bundle and no-lookahead regression suite."""
from __future__ import annotations

import csv
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from hypolab.c2_microstructure import rollup_aggtrades
from hypolab.micro_bundle import (SCHEMA,RESOLUTIONS,C1_FIELDS,FLOW_FIELDS,FILES,
                                 inspect_micro_bundle,MicroBundleError,
                                 _check_c1_row,_check_flow_row)
from hypolab.micro_warning import (make_micro_warning_plan,run_micro_warning_study,
                                   _asia_levels,_feature_at,_signals,_label)


BASE=datetime(2024,7,1,tzinfo=timezone.utc)
LO=int(BASE.timestamp()*1000)
HI=int(datetime(2024,8,1,tzinfo=timezone.utc).timestamp()*1000)
LATENCY=1500


def fields_line(keys):return ','.join(keys)+'\n'


def write_csv(path,columns,rows):
    with open(path,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns,lineterminator='\n')
        w.writeheader();w.writerows(rows)


def synth_5s_rows():
    """Full 5s grids for 12 selected days, all fake event prices/quantities."""
    lastid=0
    for day in range(12):
        # Starting at 08:00, no future C3 or market data are ever accessed.
        for i in range(120):
            t=BASE+timedelta(days=day,hours=8,seconds=i*5)
            for k in range(3):
                lastid+=1
                side=(i//5+k)%2==0
                # Local synthetic mild directional/momentum states.
                price=60000+day*17+i*.45+(k*.03)
                yield {'agg_trade_id':lastid,'transact_time':t+timedelta(milliseconds=100+k*500),
                       'price':float(price),'quantity':.05+(.001*i),
                       'is_buyer_maker':side}


def make_synthetic_zip(folder:Path):
    folder.mkdir(exist_ok=True)
    c1=[]
    for i in range(31*24*60):
        t=LO+i*60000
        px=60000+(i//1440)*17+((i%1440)/1440)*5
        # At least one quote/one trade always consistent with OHLC.
        c1.append({'open_time_ms':t,'available_at_ms':t+60000+LATENCY,
                   'open':px,'high':px+10,'low':px-10,'close':px,
                   'volume':100,'quote_volume':px*100,'count':50,
                   'taker_buy_base':55,'taker_buy_quote':px*55})
    write_csv(folder/'c1_1m.csv',C1_FIELDS,c1)
    data={s:[] for s in RESOLUTIONS}
    for s,b in rollup_aggtrades(synth_5s_rows(),period_start=BASE,
                                period_end=BASE+timedelta(days=31)):
        data[s].append({'start_ms':b['start_ms'],
                       'available_at_ms':b['end_ms_exclusive']+LATENCY,
                       **{key:b[key] for key in FLOW_FIELDS if key not in ('start_ms','available_at_ms')}})
    counts={'c1_1m':len(c1)}
    for s in RESOLUTIONS:
        write_csv(folder/f'c2_{s}s.csv',FLOW_FIELDS,data[s])
        counts[f'c2_{s}s']=len(data[s])
    hashes={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in FILES[1:]}
    sizes={name:(folder/name).stat().st_size for name in FILES[1:]}
    manifest={'schema':SCHEMA,'role':'DEVELOPMENT','month':'2024-07',
              'producer_code_commit':'f'*40,'c1_sha256':'a'*64,'c2_sha256':'b'*64,
              'frozen_universe_sha256':'c'*64,'frozen_calendar_sha256':'d'*64,
              'start_event_sha256':'e'*64,
              'assumed_latency_ms':LATENCY,'counts':counts,'files_sha256':hashes,
              'files_bytes':sizes,'c3_status':'NOT_INCLUDED_UNKNOWN',
              'price_basis':'synthetic non-executable','latency_basis':'hypothetical'}
    (folder/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')))
    output=folder/'fake-private-micro.zip'
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in FILES:z.write(folder/f,arcname=f)
    return output


class MicroV2Contracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.folder=Path(cls.tmp.name)
        cls.packet=make_synthetic_zip(cls.folder)
        cls.plan=make_micro_warning_plan(cls.packet)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_private_bundle_valid_and_complete(self):
        metadata,c1,flow=inspect_micro_bundle(self.packet)
        self.assertEqual(metadata['schema'],SCHEMA)
        self.assertEqual(metadata['c3_status'],'NOT_INCLUDED_UNKNOWN')
        self.assertEqual(len(c1),44640)
        self.assertEqual(len(flow),12*120)
        self.assertTrue(all(float(r['max_agg_trade_notional_quote'])>0 for r in flow))
        self.assertEqual(flow[0]['available_at_ms'],str(int(flow[0]['start_ms'])+6500))

    def test_report_all_rules_and_all_negative_outcomes(self):
        report=run_micro_warning_study(self.packet,self.plan)
        self.assertEqual(report['schema'],'PREMOVE_MICRO_WARNING_V2')
        self.assertEqual(len(report['trials']),5*2*2)
        self.assertEqual(report['role'],'DEVELOPMENT')
        self.assertEqual(report['c3_book_depth'],'UNKNOWN_NOT_INCLUDED')
        self.assertEqual(report['plan_sha256'],hashlib.sha256(self.plan).hexdigest())
        for t in report['trials']:
            self.assertEqual(t['total_alerts'],sum(t['outcomes'].values()))
            self.assertEqual(t['false_alarms'],t['outcomes']['wrong_way']+t['outcomes']['no_move'])
            self.assertIn(t['status'],('INSUFFICIENT_EVIDENCE','DESCRIPTIVE_SUFFICIENT_COUNTS_NOT_VALIDATED'))
        self.assertTrue(all(0<=v<=report['reference']['episodes'] for v in report['reference']['missed_by_rule'].values()))
        self.assertNotIn('VALIDATED_EDGE',report['inference'])

    def test_plan_tamper_denied(self):
        v=json.loads(self.plan)
        v['horizons_seconds']=[5]
        with self.assertRaises(MicroBundleError):run_micro_warning_study(self.packet,json.dumps(v).encode())
        with self.assertRaises(MicroBundleError):run_micro_warning_study(self.packet,self.plan+b' ')

    def test_tampered_archive_rejected_before_signal(self):
        with zipfile.ZipFile(self.packet) as z:
            payload={x:z.read(x) for x in FILES}
        payload['c1_1m.csv']=payload['c1_1m.csv'].replace(b'60010',b'99999',1)
        altered=self.folder/'tampered.zip'
        with zipfile.ZipFile(altered,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for x in FILES:z.writestr(x,payload[x])
        with self.assertRaises(MicroBundleError):inspect_micro_bundle(altered)

    def test_reject_bad_c1_and_c2_rows(self):
        valid={'open_time_ms':str(LO),'available_at_ms':str(LO+61500),
               'open':'100','high':'102','low':'99','close':'101','volume':'10',
               'quote_volume':'1000','count':'3','taker_buy_base':'8','taker_buy_quote':'800'}
        self.assertEqual(_check_c1_row(valid,None,LO,HI,LATENCY),LO)
        for bad in ({**valid,'high':'100.5'},{**valid,'taker_buy_base':'11'},
                    {**valid,'available_at_ms':str(LO+60000)},
                    {**valid,'open_time_ms':str(HI)}):
            with self.assertRaises(MicroBundleError):_check_c1_row(bad,None,LO,HI,LATENCY)
        b={k:'0' for k in FLOW_FIELDS}
        b.update(start_ms=str(LO),available_at_ms=str(LO+6500),aggregate_rows='2',
                 taker_buy_base='2',taker_sell_base='1',taker_delta_base='1',
                 taker_imbalance=str(1/3),executed_quote_notional='300',
                 max_agg_trade_notional_quote='200',
                 max_buy_agg_trade_notional_quote='200',
                 max_sell_agg_trade_notional_quote='100',
                 first_trade_price='100',last_trade_price='100',
                 min_trade_price='100',max_trade_price='100',
                 first_to_last_trade_bps='0',longest_same_taker_side_agg_rows='1',
                 max_interarrival_ms='100',first_taker_side='1',last_taker_side='-1')
        self.assertEqual(_check_flow_row(b,5,None,LO,HI,LATENCY),LO)
        for key,badvalue in [('taker_delta_base','0'),('taker_imbalance','0.9'),
                             ('max_agg_trade_notional_quote','400'),
                             ('available_at_ms',str(LO+1)),('first_taker_side','0')]:
            with self.subTest(key=key),self.assertRaises(MicroBundleError):
                _check_flow_row({**b,key:badvalue},5,None,LO,HI,LATENCY)

    def test_lookahead_false_alarm_and_missing_5s_protection(self):
        base=LO+8*3600*1000
        rows=[{'start_ms':str(base+5000*i),'available_at_ms':str(base+5000*i+6500),
               'last_trade_price':str(100+i*.01)} for i in range(100)]
        # Next CLOSE after alert at 0 -> entry price 100.01; later rise is
        # label, never visible to signal formation.
        self.assertEqual(_label(rows,0,1,15,60)[0],'no_move')
        rows[6]['last_trade_price']='105'
        self.assertEqual(_label(rows,0,1,15,60),('correct',25))
        rows[2]['last_trade_price']='90'
        self.assertEqual(_label(rows,0,1,15,60),('wrong_way',5))
        rows[3]['start_ms']=str(base+10**6)
        self.assertIsNone(_label(rows,0,1,15,60))
        rows[3]['start_ms']=str(base+15000)
        rows[0]['available_at_ms']=str(base+11000)
        self.assertIsNone(_label(rows,0,1,15,60))

    def test_ohlc_asia_wicks_not_close_proxy_and_no_past_levels(self):
        c1=[{'open_time_ms':str(LO+i*60000),'high':str(1000 if i==123 else 101),
             'low':str(1 if i==234 else 99),'close':'100'} for i in range(480)]
        asia=_asia_levels(c1)
        self.assertEqual(asia[LO//86400000],(1000,1))
        self.assertEqual(_asia_levels(c1[:-1]),{})
        example={'imbalance':.55,'rel_quote':3.,'rel_count':2.,'impact_bps':.2,
                 'max_agg_fraction':.5,'near_hi':True,'near_lo':False,
                 'imbalance_acceleration':.2}
        self.assertEqual(_signals(example)['asia_wick_pressure_follow'],1)


    def test_only_previous_closed_c1_bar_is_visible(self):
        start=LO+8*3600000
        rows=[{'start_ms':str(start+i*5000),'last_trade_price':'100',
               'executed_quote_notional':'100','aggregate_rows':'3',
               'taker_imbalance':'0.8','taker_delta_base':'2',
               'max_agg_trade_notional_quote':'50',
               'first_to_last_trade_bps':'1'} for i in range(16)]
        prev=start
        bars={prev:{'open':'100','high':'103','low':'99','close':'101'},
              start+60000:{'open':'100','high':'10000','low':'1','close':'110'}}
        f=_feature_at(rows,12,{},bars)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f['prior_c1_upper_wick_bps'],(103-101)/101*10000)
        self.assertLess(f['prior_c1_range_bps'],500)
        self.assertIn('delta_acceleration_btc',f)
        self.assertIn('signed_price_impact_bps_per_1m_quote',f)

    def test_warning_only_from_past_contiguous_buckets(self):
        start=LO+8*3600000
        rows=[{'start_ms':str(start+i*5000),'last_trade_price':'100',
               'executed_quote_notional':'100', 'aggregate_rows':'3',
               'taker_imbalance':'0.8','taker_delta_base':'1',
               'max_agg_trade_notional_quote':'50',
               'first_to_last_trade_bps':'0'} for i in range(16)]
        features=_feature_at(rows,12,{start//86400000:(101,99)})
        self.assertIsNotNone(features)
        self.assertGreater(features['rel_quote'],0)
        rows[5]['start_ms']=str(start+6*5000)
        self.assertIsNone(_feature_at(rows,12,{start//86400000:(101,99)}))

if __name__=='__main__':unittest.main()
