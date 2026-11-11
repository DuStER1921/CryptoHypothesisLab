"""Event-stream pure synthetic tests: no original source or private C2 reads."""
import unittest
from datetime import datetime,timedelta,timezone
from hypolab.c2_microstructure import rollup_aggtrades,MicrostructureError

class MicroBucketsTests(unittest.TestCase):
    def setUp(self):
        self.base=datetime(2024,7,1,tzinfo=timezone.utc)

    def row(self,i,t,side=False,px=100.,qty=1.):
        return dict(agg_trade_id=i,transact_time=self.base+timedelta(seconds=t),
                    is_buyer_maker=side,price=px,quantity=qty)

    def run_bins(self,rows):
        return list(rollup_aggtrades(rows,period_start=self.base,
                                    period_end=self.base+timedelta(days=1)))

    def test_5_15_60s_same_source_pass_one_time(self):
        raw=[self.row(1,1,False,100,2),self.row(2,3,True,101,1),
             self.row(3,6,True,99,3),self.row(4,16,False,100,1)]
        data=self.run_bins(iter(raw))
        five=[b for sec,b in data if sec==5]
        fifteen=[b for sec,b in data if sec==15]
        minute=[b for sec,b in data if sec==60]
        self.assertEqual([b['aggregate_rows'] for b in five],[2,1,1])
        self.assertEqual([b['aggregate_rows'] for b in fifteen],[3,1])
        self.assertEqual([b['aggregate_rows'] for b in minute],[4])
        self.assertAlmostEqual(five[0]['taker_delta_base'],1.)
        self.assertEqual(five[0]['longest_same_taker_side_agg_rows'],1)
        self.assertEqual(five[0]['book_depth'],None)
        self.assertEqual(five[0]['status'],'C2_OBSERVED_TRADES_ONLY')
        self.assertNotIn('bid_ask_spread',five[0])

    def test_gaps_not_zero_filled(self):
        data=self.run_bins([self.row(1,0),self.row(2,45)])
        five=[b['start_ms'] for sec,b in data if sec==5]
        self.assertEqual(len(five),2)
        self.assertEqual(five[1]-five[0],45000)

    def test_invalid_time_id_side_price_qty_rejected(self):
        cases=[
            [self.row(1,2),self.row(1,3)],
            [self.row(1,3),self.row(2,2)],
            [self.row(1,2,side=None)],
            [self.row(1,2,px=0)],
            [self.row(1,2,qty=-1)],
            [self.row(1,-1)],
        ]
        for rows in cases:
            with self.subTest(rows=rows),self.assertRaises(MicrostructureError):
                self.run_bins(rows)

    def test_protected_period_and_invalid_bin_denied(self):
        with self.assertRaises(MicrostructureError):
            list(rollup_aggtrades([],period_start=datetime(2025,1,1,tzinfo=timezone.utc),
                                 period_end=datetime(2025,1,2,tzinfo=timezone.utc)))
        with self.assertRaises(MicrostructureError):
            list(rollup_aggtrades([],period_start=self.base,
                                 period_end=self.base+timedelta(days=1),seconds=(3,)))

if __name__=='__main__':unittest.main()
