"""Synthetic tests ONLY: no public/private market history or external connectivity."""
import json
import pathlib
import unittest
from unittest.mock import patch
from hypolab import strategy_v2 as v

DAY=1722470400000  # invented synthetic UTC day


def row(i,first=99.95,last=None,high=None,low=None,imb=0.0,quote=1000,ms=DAY,available_extra=1500):
    if last is None:last=first
    if high is None:high=max(first,last)
    if low is None:low=min(first,last)
    assert high>=max(first,last) and low<=min(first,last)
    t=ms+i*5000
    return {k:str(val) for k,val in {
       'start_ms':t,'available_at_ms':t+5000+available_extra,
       'first_trade_price':first,'last_trade_price':last,
       'max_trade_price':high,'min_trade_price':low,
       'taker_imbalance':imb,'first_to_last_trade_bps':(last/first-1)*10000,
       'executed_quote_notional':quote,'aggregate_rows':10,
       'taker_delta_base':0.0}.items()}


def c1(i,high=100.,low=90.,ms=DAY):
    t=ms+i*60000
    return {k:str(val) for k,val in {
       'open_time_ms':t,'available_at_ms':t+60000+1500,
       'high':high,'low':low,'open':95.0,'close':95.5,
       'volume':10.,'count':100}.items()}


class PureStrategyTests(unittest.TestCase):
    def test_levels_use_previous_sixty_closed_candles_with_latency(self):
        bars=[c1(i) for i in range(64)]
        levels=v._rolling_levels(bars)
        self.assertNotIn(DAY+59*60000,levels)
        self.assertEqual(levels[DAY+60*60000],(100.,90.,DAY+60*60000+1500))
        self.assertIn(DAY+63*60000,levels)
        self.assertNotIn(DAY+64*60000+60000,levels)

    def test_levels_reset_when_c1_history_has_gap(self):
        bars=[c1(i) for i in range(66)]
        bars[40]['open_time_ms']=str(DAY+40*60000+1000)
        levels=v._rolling_levels(bars)
        self.assertNotIn(DAY+60*60000,levels)

    def test_failure_at_upper_known_level_generates_short(self):
        rows=[row(i) for i in range(35)]
        rows[19]=row(19,first=99.99,last=100.01,high=100.05,low=99.99,imb=.80,quote=3500)
        rows[20]=row(20,first=100.00,last=99.95,high=100.00,low=99.95,imb=-.55,quote=1300)
        levels={DAY+1*60000:(100.,90.,DAY+1*60000+1500)}
        signal=v._signal(rows,20,levels)
        self.assertIsNotNone(signal)
        self.assertEqual(signal[0],-1)
        self.assertEqual(signal[1]['level_type'],'UPPER')
        # No current minute C1 can leak into this signal.
        bad={DAY+1*60000:(100.,90.,DAY+1*60000+700_000)}
        self.assertIsNone(v._signal(rows,20,bad))
        # If the buy-side sweep has no volume surprise, do not signal.
        rows[19]=row(19,first=99.99,last=100.01,high=100.05,low=99.99,imb=.80,quote=900)
        self.assertIsNone(v._signal(rows,20,levels))

    def test_failed_lower_auction_is_symmetric_long(self):
        rows=[row(i,first=90.05) for i in range(40)]
        rows[19]=row(19,first=90.01,last=89.98,high=90.01,low=89.94,imb=-.83,quote=5000)
        rows[20]=row(20,first=90.00,last=90.06,high=90.06,low=90.00,imb=.51,quote=1500)
        levels={DAY+60000:(100.,90.,DAY+61500)}
        self.assertEqual(v._signal(rows,20,levels)[0],1)

    def test_future_data_cannot_change_past_signal(self):
        rows=[row(i) for i in range(45)]
        rows[19]=row(19,first=99.99,last=100.01,high=100.05,low=99.99,imb=.82,quote=3500)
        rows[20]=row(20,first=100.00,last=99.95,imb=-.60)
        levels={DAY+60000:(100.,90.,DAY+61500)}
        before=v._signal(rows,20,levels)
        self.assertIsNotNone(before)
        rows[24]=row(24,first=200.,last=201.,high=300.,low=190.,imb=.99,quote=999999)
        self.assertEqual(before,v._signal(rows,20,levels))

    def test_synthetic_end_to_end_reports_all_losses_and_controls(self):
        rows=[row(i) for i in range(520)]
        rows[19]=row(19,first=99.99,last=100.01,high=100.05,low=99.99,imb=.80,quote=3500)
        rows[20]=row(20,first=100.,last=99.95,imb=-.55)
        rows[26]=row(26,first=99.95,last=99.95,high=99.95,low=99.30)
        bars=[c1(i,ms=DAY-3600000) for i in range(110)]
        meta={'month':'2024-07','counts':{'c1_1m':110,'c2_5s':520,
                                          'c2_15s':174,'c2_60s':44}}
        with patch.object(v,'make_strategy_v2_plan',return_value=b'plan'), \
             patch.object(v,'inspect_micro_bundle',return_value=(meta,bars,rows)), \
             patch.object(v,'freeze_token_sha256',return_value='x'*64):
            result=v.run_strategy_v2(pathlib.Path('/fake.zip'),b'plan')
        self.assertEqual(result['primary']['trades'],1)
        self.assertEqual(result['opposite_same_case_control']['trades'],1)
        self.assertEqual(result['primary']['mean_net_bps'],44.)
        self.assertEqual(result['opposite_same_case_control']['mean_net_bps'],-46.)
        self.assertEqual(result['model_status'],'FAILS_PREDECLARED_MODEL_GATES')
        self.assertEqual(len(result['trades']),1)
        self.assertFalse(result['gates']['enough_trades'])

    def test_no_private_source_or_google_drive_api_in_engine(self):
        code=pathlib.Path(v.__file__).read_text(encoding='utf8')
        for forbidden in ('drive.mount(', 'GoogleDrivePinnedSource(', 'AUTHORIZE_MICRO_DEVELOPMENT_',
                          'generations_root=', 'CryptoResearchLab.git'):
            self.assertNotIn(forbidden,code)

    def test_c2_strings_no_type_mismatch(self):
        rows=[row(i) for i in range(50)]
        self.assertIsNone(v._signal(rows,20,{}))
        self.assertIsInstance(v._signal(rows,20,{DAY+60000:(100.,90.,DAY+61500)}),type(None))

    def test_execution_first_future_trade_and_no_future_gap_rejection(self):
        rows=[row(i,first=100.) for i in range(510)]
        # Third subsequent bucket opens at 100, signals known 8.5s earlier.
        rows[26]=row(26,first=100.,last=100.,high=100.,low=99.35)
        rows[310]['start_ms']=str(DAY+310*5000+1000)  # AFTER target hit
        t,ix=v._simulate(rows,20,-1,v.BASE_SLIP_BPS_PER_SIDE)
        self.assertEqual(t['status'],'COMPLETED')
        self.assertEqual(t['entry_ms'],DAY+23*5000)
        self.assertEqual(t['reason'],'TARGET')
        self.assertEqual(t['net_bps'],44.)  # 60 gross less 16 round trip
        self.assertEqual(ix,26)

    def test_gap_before_actual_exit_is_unknown(self):
        rows=[row(i,first=100.) for i in range(510)]
        rows[26]['start_ms']=str(DAY+26*5000+1000)
        t,ix=v._simulate(rows,20,-1,v.BASE_SLIP_BPS_PER_SIDE)
        self.assertEqual(t['status'],'UNKNOWN_PATH_GAP')
        self.assertIsNone(ix)

    def test_stop_first_dual_touch_and_adverse_open(self):
        rows=[row(i,first=100.) for i in range(510)]
        rows[24]=row(24,first=100.45,last=100.,high=100.7,low=99.2)
        trade,_=v._simulate(rows,20,-1,3.)
        self.assertEqual(trade['reason'],'DUAL_TOUCH_STOP_FIRST')
        # Entry is 100; gap first-trade was 100.45 and we use worse price
        self.assertAlmostEqual(trade['gross_bps'],-45.,places=3)
        self.assertAlmostEqual(trade['net_bps'],-61.,places=3)

    def test_signal_known_before_entry_fails_closed(self):
        rows=[row(i,first=100.) for i in range(510)]
        rows[20]['available_at_ms']=str(DAY+20*5000+16_000)
        self.assertEqual(v._simulate(rows,20,1,3.)[0]['status'],'UNKNOWN_LATE_SIGNAL')

    def test_calendar_and_fixed_stop_targets(self):
        p=json.loads(v.protocol_bytes())
        self.assertEqual(p['exit']['stop_bps'],30.)
        self.assertEqual(p['exit']['target_bps'],60.)
        self.assertEqual(p['entry']['delay_5s_bins'],3)
        self.assertEqual(p['cost']['fee_bps_each_side'],5.)
        self.assertEqual(p['development_august'],'one-shot owner-authorized future source read only')

    def test_plan_is_strictly_exact_bytes_and_months(self):
        with patch.object(v,'make_strategy_v2_plan',return_value=b'exact'):
            for p in (b'',b'tampered','exact'):
                with self.assertRaises(v.MicroBundleError):
                    v.run_strategy_v2(pathlib.Path('/fake.zip'),p)
        meta={'month':'2025-01'}
        with patch.object(v,'inspect_micro_bundle',return_value=(meta,None,None)):
            with self.assertRaises(v.MicroBundleError):
                v.make_strategy_v2_plan(pathlib.Path('/fake.zip'))


if __name__=='__main__':unittest.main()
