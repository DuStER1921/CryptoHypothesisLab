"""Optional PyArrow integration on wholly synthetic staged C1/C2 months."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone

from hypolab.frozen import PinnedPartition
from hypolab.staging import StagedPartition
from hypolab.micro_bundle import (build_micro_bundle_from_staged,inspect_micro_bundle,
                                  MicroBundleError)
from hypolab.colab_micro_prep import prepare_one_micro_month
from hypolab.colab_prep import PrepError
from hypolab.parquet_window import schema_for

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa=pq=None


class ApprovalGateTests(unittest.TestCase):
    def test_incorrect_authorization_denied_before_source_access(self):
        with self.assertRaises(PrepError):
            prepare_one_micro_month(metadata_root=Path('/does/not/exist'),
                generations_root=Path('/does/not/exist'),events_root=Path('/does/not/exist'),
                private_cache_parent=Path('/does/not/exist'),
                private_scratch=Path('/does/not/exist'),private_packets=Path('/does/not/exist'),
                month='2024-07',attempt_id='abc123',confirmation='AUTHORIZE_DEVELOPMENT_2024-07',
                expected_universe_sha256='a'*64,expected_calendar_sha256='b'*64,
                producer_code_commit='c'*40)
    def test_2025_not_authorized_even_with_micro_confirmation(self):
        # The frozen Development gate would deny protected 2025. A monthly
        # writer also enforces its own Development-only month in the schema.
        from hypolab.airgap_bundle import _month_bounds,BundleError
        with self.assertRaises(BundleError):_month_bounds('2025-01')


@unittest.skipUnless(pa is not None,'PyArrow is optional in local development; installed by GitHub CI')
class MicroStageIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        cls.scratch=cls.root/'scratch';cls.output=cls.root/'output'
        cls.scratch.mkdir(mode=0o700);cls.output.mkdir(mode=0o700)
        cls.data=cls.root/'data';cls.data.mkdir()
        base=datetime(2024,7,1,tzinfo=timezone.utc)
        times=[base+timedelta(minutes=i) for i in range(44640)]
        klines={'open_time':times,
                'open':[60000.]*len(times),'high':[60002.]*len(times),
                'low':[59998.]*len(times),'close':[60000.]*len(times),
                'volume':[10.]*len(times),
                'close_time':[t+timedelta(seconds=59,milliseconds=999) for t in times],
                'quote_volume':[600000.]*len(times),'count':[25]*len(times),
                'taker_buy_base':[6.]*len(times),'taker_buy_quote':[360000.]*len(times),
                'ignore':[0.]*len(times)}
        rows=[]
        for i,second in enumerate((1,2,3,6,11,16,40,61,66,74)):
            rows.append({'agg_trade_id':i+100,'price':60000.+i*.1,
                         'quantity':.04+(i%4)*.01,'first_trade_id':i+1000,
                         'last_trade_id':i+1000,
                         'transact_time':base+timedelta(seconds=second),
                         'is_buyer_maker':i%3==0})
        agg={k:[row[k] for row in rows] for k in schema_for('aggTrades',pa).names}
        for family,obj in (('klines',klines),('aggTrades',agg)):
            schema=schema_for(family,pa)
            t=pa.Table.from_arrays([pa.array(obj[n],type=schema.field(n).type)
                                   for n in schema.names],schema=schema)
            dest=cls.data/(family+'.parquet')
            pq.write_table(t,dest,compression='NONE',version='2.6')
        def stage(family):
            dest=cls.data/(family+'.parquet')
            digest=hashlib.sha256(dest.read_bytes()).hexdigest()
            ident=PinnedPartition(family,'2024-07',
                                  'binance-usdm-BTCUSDT-klines-1m' if family=='klines'
                                  else 'binance-usdm-BTCUSDT-aggTrades',
                                  'a'*64,digest,'b'*64,'c'*64)
            folder=cls.data/family;folder.mkdir()
            link=folder/'data.parquet'
            link.write_bytes(dest.read_bytes())
            return StagedPartition(ident,folder,True)
        cls.c1=stage('klines');cls.c2=stage('aggTrades')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_exact_generation_staged_one_pass_build(self):
        packet=self.output/'private-v2-synthetic.zip'
        ret=build_micro_bundle_from_staged(self.c1,self.c2,
             scratch_dir=self.scratch,target=packet,assumed_latency_ms=1500,
             producer_code_commit='f'*40,universe_sha256='1'*64,
             calendar_sha256='2'*64,start_event_sha256='3'*64)
        self.assertEqual(ret['counts']['c1_1m'],44640)
        m,c1,flows=inspect_micro_bundle(packet)
        self.assertEqual(m['frozen_universe_sha256'],'1'*64)
        self.assertEqual(len(c1),44640)
        self.assertEqual(ret['counts']['c2_5s'],len(flows))
        self.assertEqual(ret['sha256'],hashlib.sha256(packet.read_bytes()).hexdigest())
        with self.assertRaises(MicroBundleError):
            build_micro_bundle_from_staged(self.c1,self.c2,
                scratch_dir=self.scratch,target=packet,assumed_latency_ms=1500,
                producer_code_commit='f'*40,universe_sha256='1'*64,
                calendar_sha256='2'*64,start_event_sha256='3'*64)

if __name__=='__main__':unittest.main()
