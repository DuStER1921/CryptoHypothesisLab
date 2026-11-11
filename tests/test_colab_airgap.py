"""Synthetic-only tests; NO Google credentials, actual Drive mounts or BTC rows."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from pathlib import Path
import io
import json
import tempfile
import unittest
import zipfile

from hypolab.airgap_bundle import (create_bundle,inspect_bundle,BundleError,
                                   _month_bounds,FIELD_NAMES)
from hypolab.c1c2_features import MinuteFeature
from hypolab.colab_prep import (MountedFrozenSource,PrepError,
                                _check_events_root,_check_mount_source,
                                _log_start_once)
from hypolab.offline_discovery import describe_forward_return
from hypolab.frozen import PinnedPartition


def minute(i, state='C2_PRESENT', latency=1500):
    t=datetime(2024,7,1,tzinfo=timezone.utc)+timedelta(minutes=i)
    present=state=='C2_PRESENT'
    return MinuteFeature(t,t+timedelta(minutes=1,milliseconds=latency),
                        60000+i*3.,state,10. if present else None,
                        4. if present else None,6. if present else None,
                        6/14 if present else None,7 if present else 0)

def bundle(features=None,month='2024-07'):
    return create_bundle(month=month, features=tuple(features if features is not None else [minute(i) for i in range(20)]),
                         universe_sha256='a'*64,calendar_sha256='b'*64,
                         c1_sha256='c'*64,c2_sha256='d'*64,assumed_latency_ms=1500,
                         producer_code_commit='f'*40)


def repackage(packet, mutate):
    with zipfile.ZipFile(io.BytesIO(packet)) as z:
        m=z.read('manifest.json');c=z.read('features.csv')
    m,c=mutate(m,c)
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',m)
        z.writestr('features.csv',c)
    return b.getvalue()


class BundleTests(unittest.TestCase):
    def test_roundtrip_and_descriptive_only(self):
        meta,rows=inspect_bundle(bundle())
        self.assertEqual(meta['month'],'2024-07');self.assertEqual(len(rows),20)
        x=describe_forward_return(bundle(),imbalance_threshold=.4,horizon_minutes=5)
        self.assertEqual(x['role'],'DEVELOPMENT');self.assertEqual(x['events'],15)
        self.assertEqual(x['inference'],'DESCRIPTIVE_ONLY_NOT_VALIDATED')
    def test_missing_c2_never_zero_filled(self):
        meta,rows=inspect_bundle(bundle([minute(0,'C2_UNKNOWN'),minute(1)]))
        self.assertEqual(rows[0]['c2_status'],'C2_UNKNOWN')
        self.assertEqual(rows[0]['taker_buy_base'],'')
        self.assertEqual(rows[0]['c2_aggregate_rows'],'0')
    def test_oos_and_validation_denied(self):
        for month in ('2025-01','2026-01','2026-08','2027-01','2019-12'):
            with self.subTest(month=month),self.assertRaises(BundleError):
                bundle(month=month)
    def test_future_and_month_boundary_denied(self):
        before=datetime(2024,12,31,23,59,tzinfo=timezone.utc)
        late=MinuteFeature(before,before+timedelta(minutes=1,milliseconds=1500),
                           1.0,'C2_UNKNOWN',None,None,None,None,0)
        with self.assertRaises(BundleError):bundle([late],month='2024-12')
    def test_modified_csv_digest_denied(self):
        raw=bundle()
        with self.assertRaises(BundleError):
            inspect_bundle(repackage(raw,lambda m,c:(m,c.replace(b'60000.0',b'99999.0'))))
    def test_forged_labels_not_allowed(self):
        raw=bundle()
        with self.assertRaises(BundleError):
            inspect_bundle(repackage(raw,lambda m,c:(m,c.replace(b'open_time_ms,',b'future_label,open_time_ms,'))))
    def test_unknown_state_with_buy_fake_denied(self):
        raw=bundle([minute(0,'C2_UNKNOWN'),minute(1)])
        def edit(m,c):
            return m,c.replace(b',C2_UNKNOWN,,,,,0\n',b',C2_UNKNOWN,10.0,,,,0\n')
        with self.assertRaises(BundleError):inspect_bundle(repackage(raw,edit))
    def test_gapped_and_duplicate_times_denied(self):
        for features in ([minute(0),minute(2)],[minute(0),minute(0)]):
            with self.assertRaises(BundleError):bundle(features)
    def test_count_and_availability_tamper_denied(self):
        x=minute(0)
        bad=MinuteFeature(x.open_time,x.open_time,x.close,x.c2_status,
                          x.taker_buy_base,x.taker_sell_base,x.taker_delta_base,x.taker_imbalance,x.c2_aggregate_rows)
        with self.assertRaises(BundleError):bundle([bad])
    def test_archive_rejects_extra_entries(self):
        b=io.BytesIO();raw=bundle()
        with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(b,'w') as dest:
            for n in ['manifest.json','features.csv']:
                dest.writestr(n,src.read(n),compress_type=zipfile.ZIP_DEFLATED)
            dest.writestr('private.json',b'PRIVATE')
        with self.assertRaises(BundleError):inspect_bundle(b.getvalue())
    def test_bundle_is_byte_deterministic(self):
        self.assertEqual(bundle(),bundle())
    def test_oversized_and_short_zip_denied(self):
        for raw in (b'x',b'',b'x'*(5*1024*1024+1)):
            with self.assertRaises(BundleError):inspect_bundle(raw)
    def test_horizon_validation(self):
        for h in (0,61,True):
            with self.assertRaises(BundleError):describe_forward_return(bundle(),horizon_minutes=h)


class MountedSourceTests(unittest.TestCase):
    def test_only_frozen_layout_and_dev(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'CryptoResearchLab'/'data'/'canonical'/'generations'
            base.mkdir(parents=True)
            source=MountedFrozenSource(base)
            def identity(period):
                return PinnedPartition('klines',period,'binance-usdm-BTCUSDT-klines-1m',
                                       'a'*64,'b'*64,'c'*64,'d'*64)
            m,p=source.partition_paths(identity('2024-07'))
            self.assertTrue(str(p).endswith('klines/1m/year=2024/month=07/data.parquet'))
            with self.assertRaises(PrepError):source.partition_paths(identity('2025-01'))
    def test_source_can_only_open_regular_files_readonly(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'CryptoResearchLab'/'data'/'canonical'/'generations'
            base.mkdir(parents=True)
            ident=PinnedPartition('aggTrades','2024-07',
                                  'binance-usdm-BTCUSDT-aggTrades',
                                  'a'*64,'b'*64,'c'*64,'d'*64)
            source=MountedFrozenSource(base)
            m,p=source.partition_paths(ident)
            p.parent.mkdir(parents=True)
            m.write_bytes(b'{"manifest":"test"}')
            p.write_bytes(b'PAR1synthetic')
            with source.open_pinned(ident) as (ma,pa):
                self.assertEqual(ma.read(),b'{"manifest":"test"}')
                self.assertEqual(pa.read(),b'PAR1synthetic')
            self.assertEqual(p.read_bytes(),b'PAR1synthetic')
            p.unlink()
            p.symlink_to(m)
            with self.assertRaises(PrepError):
                with source.open_pinned(ident): pass
    def test_source_rejects_staging_protected_bank_even_if_directory_exists(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'CryptoResearchLab'/'data'/'canonical'/'generations'
            base.mkdir(parents=True)
            ident=PinnedPartition('aggTrades','2026-01',
                                  'binance-usdm-BTCUSDT-aggTrades',
                                  'a'*64,'b'*64,'c'*64,'d'*64)
            with self.assertRaises(PrepError):MountedFrozenSource(base).partition_paths(ident)
    def test_untrusted_generations_root_denied(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PrepError):_check_mount_source(Path(d))
    def test_event_folder_overlap_denied(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'CryptoResearchLab'/'data'/'canonical'/'generations'
            base.mkdir(parents=True)
            new=Path(d)/'CryptoHypothesisLab'/'research-events';new.mkdir(parents=True)
            self.assertEqual(_check_events_root(new,base.resolve()),new.resolve())
    def test_no_event_replay(self):
        with tempfile.TemporaryDirectory() as d:
            events=Path(d)
            class Frozen:
                universe_sha256='a'*64
                calendar_sha256='b'*64
            class P:
                canonical_sha256='c'*64
            got=_log_start_once(events,'2024-07','pilot001',Frozen(),P(),P(),1500,'f'*40)
            self.assertEqual(len(got),64)
            with self.assertRaises(PrepError):
                _log_start_once(events,'2024-07','pilot001',Frozen(),P(),P(),1500,'f'*40)

if __name__=='__main__':unittest.main()
