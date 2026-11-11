"""Synthetic-only tests for a single frozen trading hypothesis, NOT market evidence."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from hypolab import strategy_v1 as s

DAY=1722470400000  # Synthetic first-August UTC time; prices are invented.


def bar(i, price=100., lo=None, hi=None, op=None):
    t=DAY+i*5000
    return {
        'start_ms':str(t),'available_at_ms':str(t+6500),
        'first_trade_price':str(price if op is None else op),
        'last_trade_price':str(price),
        'min_trade_price':str(price if lo is None else lo),
        'max_trade_price':str(price if hi is None else hi),
        'first_to_last_trade_bps':'0',
        'taker_imbalance':'0.0','taker_delta_base':'0.0',
        'executed_quote_notional':'4000','aggregate_rows':'20',
    }


def completed_c1(minutes=110):
    return [{'open_time_ms':str(DAY+i*60000),
             'available_at_ms':str(DAY+(i+1)*60000+1500),
             'open':'100.0','high':'100.05','low':'99.95',
             'close':'100.02','volume':'200', 'count':'100'} for i in range(minutes)]


class StrategyV1Tests(unittest.TestCase):
    def test_fixed_nonoptimized_parameters_and_development_scope(self):
        p=json.loads(s.protocol_bytes())
        self.assertEqual(p['months']['one_shot_future_development'],'2024-08')
        self.assertEqual(p['signal']['inclusive_trailing_trade_range_bps'],[8.,20.])
        self.assertEqual(p['execution_proxy']['target_bps'],36.)
        self.assertEqual(p['execution_proxy']['stop_bps'],18.)
        self.assertEqual(p['execution_proxy']['entry_lag_complete_5s_bars'],2)
        self.assertEqual(p['cost_assumptions']['base_slippage_bps_per_side'],2.)
        self.assertEqual(len(json.loads(s.protocol_bytes())['signal']),8)

    def test_actual_string_csv_fields_generate_causal_emergence(self):
        rows=[bar(i) for i in range(300)]
        i=260
        rows[i].update({'first_trade_price':'100.08','last_trade_price':'100.11',
                        'max_trade_price':'100.13','min_trade_price':'100.0',
                        'first_to_last_trade_bps':'2.997',
                        'taker_imbalance':'0.9','executed_quote_notional':'16000',
                        'aggregate_rows':'100'})
        ctx=s._bar_context(completed_c1(110))
        rg=s._range_60s(rows)
        found=s._signal(rows,i,rg[i],ctx)
        self.assertIsNotNone(found)
        self.assertEqual(found[0],1)
        self.assertGreaterEqual(found[1]['range_bps'],8)
        # A future surge beyond the signal cannot affect the earlier decision.
        snap=found
        rows[i+1]['last_trade_price']='350'
        self.assertEqual(s._signal(rows,i,rg[i],ctx),snap)
        # Previous C1 bar marked as not yet available: deny entry.
        bar_key=(DAY+i*5000)//60000*60000
        ctx[bar_key]['available_at_ms']=int(rows[i]['available_at_ms'])+1000
        self.assertIsNone(s._signal(rows,i,rg[i],ctx))

    def test_stop_first_on_ambiguous_same_bin_and_gaps_conservative(self):
        rows=[bar(i) for i in range(280)]
        i=20
        rows[i+2]['last_trade_price']='100'
        both=rows[i+3]
        both.update({'first_trade_price':'100','min_trade_price':'99.70','max_trade_price':'100.50'})
        t,ix=s._simulate_trade(rows,i,1,2.)
        self.assertEqual(t['reason'],'DUAL_TOUCH_STOP_FIRST')
        self.assertEqual(ix,i+3)
        self.assertAlmostEqual(t['gross_bps'],-18.,places=3)
        self.assertAlmostEqual(t['net_bps'],-30.,places=3)
        both['first_trade_price']='99.50'
        t,_=s._simulate_trade(rows,i,1,2.)
        self.assertAlmostEqual(t['gross_bps'],-50.,places=3)
        rows[i+5]['start_ms']=str(DAY+(i+5)*5000+1000)
        t,_=s._simulate_trade(rows,i,1,2.)
        self.assertEqual(t['status'],'UNKNOWN_GAP')

    def test_target_time_exit_no_fill_fantasies(self):
        rows=[bar(i) for i in range(300)]
        i=20
        rows[i+3].update({'max_trade_price':'100.50','min_trade_price':'100.0'})
        t,_=s._simulate_trade(rows,i,1,2.)
        self.assertEqual(t['reason'],'TARGET')
        self.assertAlmostEqual(t['gross_bps'],36.,places=3)
        self.assertAlmostEqual(t['net_bps'],24.,places=3)
        rows[i+3].update({'max_trade_price':'100.0','min_trade_price':'100.0'})
        t,_=s._simulate_trade(rows,i,1,2.)
        self.assertEqual(t['reason'],'TIME_EXIT')
        self.assertAlmostEqual(t['net_bps'],-12.,places=3)

    def test_out_of_scope_source_denied_before_analysis(self):
        m={'month':'2025-01'}
        with patch.object(s,'inspect_micro_bundle',return_value=(m,[],[])):
            with self.assertRaises(s.MicroBundleError):s.make_strategy_plan(Path('/fake'))

    def test_canonical_plan_fail_closed(self):
        with patch.object(s,'make_strategy_plan',return_value=b'plan'):
            for value in (b'bad','plan',b''):
                with self.subTest(value=value),self.assertRaises(s.MicroBundleError):
                    s.run_strategy_v1(Path('/fake'),value)

    def test_full_parser_realistic_synthetic_end_to_end_and_all_gates(self):
        rows=[bar(i) for i in range(960)]
        for i in (260,450,670):
            rows[i].update({'first_trade_price':'100.08','last_trade_price':'100.11',
                           'max_trade_price':'100.13','min_trade_price':'100',
                           'first_to_last_trade_bps':'2.997','taker_imbalance':'0.9',
                           'executed_quote_notional':'16000','aggregate_rows':'100'})
            rows[i+2]['last_trade_price']='100.11'
            rows[i+3]['max_trade_price']='100.6'
        meta={'month':'2024-07','counts':{'c1_1m':80,'c2_5s':960,'c2_15s':320,'c2_60s':80}}
        with patch.object(s,'make_strategy_plan',return_value=b'exact'),\
             patch.object(s,'inspect_micro_bundle',return_value=(meta,completed_c1(110),rows)),\
             patch.object(s,'freeze_token_sha256',return_value='x'*64),\
             patch('hypolab.micro_warning._sha',return_value='z'*64):
            result=s.run_strategy_v1(Path('/fake'),b'exact')
        self.assertEqual(result['schema'],s.SCHEMA)
        self.assertGreater(result['signal_count_before_execution_constraints'],0)
        self.assertEqual(result['primary']['trades'],len(result['trades']))
        self.assertEqual(len(result['trades']),len(result['opposite_control_trades']))
        self.assertIn('no_unknown_attempts',result['gates'])
        self.assertNotIn('PROVEN',result['model_status'][:10])

    def test_test_code_never_opens_original_source_or_orders(self):
        source=Path(s.__file__).read_text(encoding='utf-8')
        for forbidden in ['drive.mount(', 'Client(', 'create_order(', 'validation_2025', 'source_generations']:
            with self.subTest(forbidden=forbidden):self.assertNotIn(forbidden,source)


if __name__=='__main__':unittest.main()
