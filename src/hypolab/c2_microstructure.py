"""One-pass, bounded-state C2 aggTrades 5/15/60-second taker-flow rollups.

Input must be externally owner-authorized, pinned, staged and SHA-verified.
aggTrades contain executed aggressive trades; they CANNOT reveal resting L2
orders, cancellations, queue priority, spreads, or actual trade arrival times.
A missing time bucket is UNKNOWN, never an invented zero-volume observation.
No external IO and no file writes in the rollup generator.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from .contracts import Role, SPLITS
from .c1c2_features import _verify_staging
from .parquet_window import schema_for
from .staging import StagedPartition


class MicrostructureError(ValueError):
    pass


SECONDS = (5, 15, 60)
MAX_EVENT_ROWS = 200_000_000


def _ms(dt):
    if type(dt) is not datetime or dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise MicrostructureError('aggTrade event time must be UTC')
    if dt.microsecond % 1000:
        raise MicrostructureError('timestamp must have millisecond precision')
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return int((dt - epoch).total_seconds() * 1000)


def _snapshot(b, seconds):
    buy = b['taker_buy_base']
    sell = b['taker_sell_base']
    total = buy + sell
    return {
        'resolution_seconds': seconds, 'start_ms': b['start_ms'],
        'end_ms_exclusive': b['start_ms'] + seconds * 1000,
        'first_transact_ms': b['first_transact_ms'],
        'last_transact_ms': b['last_transact_ms'],
        'first_agg_trade_id': b['first_agg_trade_id'],
        'last_agg_trade_id': b['last_agg_trade_id'],
        'aggregate_rows': b['aggregate_rows'],
        'taker_buy_base': buy, 'taker_sell_base': sell,
        'taker_delta_base': buy - sell,
        'taker_imbalance': (buy - sell) / total,
        'executed_quote_notional': b['executed_quote_notional'],
        'max_agg_trade_notional_quote': b['max_agg_trade_notional_quote'],
        'max_buy_agg_trade_notional_quote': b['max_buy_agg_trade_notional_quote'],
        'max_sell_agg_trade_notional_quote': b['max_sell_agg_trade_notional_quote'],
        'max_interarrival_ms': b['max_interarrival_ms'],
        'first_taker_side': b['first_taker_side'],
        'last_taker_side': b['last_side'],
        'first_trade_price': b['first_trade_price'],
        'last_trade_price': b['last_trade_price'],
        'min_trade_price': b['min_trade_price'],
        'max_trade_price': b['max_trade_price'],
        'first_to_last_trade_bps': (b['last_trade_price'] / b['first_trade_price'] - 1) * 10000,
        'longest_same_taker_side_agg_rows': b['longest_same_taker_side_agg_rows'],
        'status': 'C2_OBSERVED_TRADES_ONLY',
        'book_depth': None,
    }


def rollup_aggtrades(trades, *, period_start: datetime, period_end: datetime,
                      seconds: tuple[int,...] = SECONDS):
    """Yield (resolution_seconds, bucket) incrementally, without time-gap filling.

    Event timestamps and IDs are independently checked here even if the upstream
    canonicalized source already verified them. The last valid bin is only
    yielded after exhausting the input iterator.
    """
    if (type(seconds) is not tuple or not seconds or len(set(seconds)) != len(seconds)
            or not set(seconds).issubset(SECONDS)):
        raise MicrostructureError('only canonical 5/15/60-second bins allowed')
    lo, hi = _ms(period_start), _ms(period_end)
    if not lo < hi or not SPLITS[Role.DEVELOPMENT].contains(period_start, period_end):
        raise MicrostructureError('only pre-authorized Development event time')
    active = {s: None for s in seconds}
    last_id = -1
    last_ms = None
    count = 0
    for row in trades:
        if type(row) is not dict:
            raise MicrostructureError('unexpected aggTrade row')
        try:
            stamp = _ms(row['transact_time'])
            idx = row['agg_trade_id']
            px = row['price']; qty = row['quantity']
            buyer_maker = row['is_buyer_maker']
        except (KeyError,TypeError,AttributeError) as exc:
            raise MicrostructureError('missing aggTrade field') from exc
        if (type(idx) is not int or idx <= last_id or
                type(buyer_maker) is not bool or
                type(px) not in (float,int) or type(qty) not in (float,int) or
                not math.isfinite(px) or not math.isfinite(qty) or px <= 0 or qty <= 0 or
                stamp < lo or stamp >= hi or last_ms is not None and stamp < last_ms):
            raise MicrostructureError('noncanonical aggTrades stream')
        count += 1
        if count > MAX_EVENT_ROWS:
            raise MicrostructureError('maximum source rows exceeded')
        last_id, last_ms = idx, stamp
        side = -1 if buyer_maker else 1  # buyer is resting maker -> taker SELL
        for s in seconds:
            begin = stamp - stamp % (s * 1000)
            b = active[s]
            if b is not None and b['start_ms'] != begin:
                yield s, _snapshot(b,s)
                b = None
            if b is None:
                b = {'start_ms':begin,'first_transact_ms':stamp,
                     'last_transact_ms':stamp,'first_agg_trade_id':idx,
                     'last_agg_trade_id':idx,'aggregate_rows':0,
                     'taker_buy_base':0.,'taker_sell_base':0.,
                     'executed_quote_notional':0.,
                     'first_trade_price':float(px),'last_trade_price':float(px),
                     'min_trade_price':float(px),'max_trade_price':float(px),
                     'last_side':side,'first_taker_side':side,'side_run':0,
                     'longest_same_taker_side_agg_rows':0,
                     'max_agg_trade_notional_quote':0.,
                     'max_buy_agg_trade_notional_quote':0.,
                     'max_sell_agg_trade_notional_quote':0.,
                     'max_interarrival_ms':0}
            if b['aggregate_rows']:
                b['max_interarrival_ms'] = max(b['max_interarrival_ms'],stamp-b['last_transact_ms'])
            b['aggregate_rows'] += 1
            b['last_transact_ms'] = stamp
            b['last_agg_trade_id'] = idx
            b['last_trade_price'] = float(px)
            b['min_trade_price'] = min(b['min_trade_price'],px)
            b['max_trade_price'] = max(b['max_trade_price'],px)
            quote = px * qty
            if not math.isfinite(quote) or quote <= 0:
                raise MicrostructureError('non-finite trade notional')
            b['executed_quote_notional'] += quote
            b['max_agg_trade_notional_quote'] = max(b['max_agg_trade_notional_quote'],quote)
            key = 'max_buy_agg_trade_notional_quote' if side > 0 else 'max_sell_agg_trade_notional_quote'
            b[key] = max(b[key],quote)
            b['taker_buy_base' if side > 0 else 'taker_sell_base'] += qty
            b['side_run'] = b['side_run'] + 1 if b['last_side']==side else 1
            b['last_side'] = side
            b['longest_same_taker_side_agg_rows'] = max(b['longest_same_taker_side_agg_rows'],b['side_run'])
            active[s] = b
    for s in seconds:
        if active[s] is not None:
            yield s, _snapshot(active[s],s)


def iter_verified_staged_c2_micro_buckets(stage: StagedPartition,
                                           seconds: tuple[int,...] = SECONDS):
    """Compute from an ALREADY authorized/staged C2 file, never fetch a source.

    If consumption fails, downstream MUST discard partial artifacts; caller
    must not persist a partial generator as a complete dataset.
    """
    if type(stage) is not StagedPartition or stage.identity.family != 'aggTrades':
        raise MicrostructureError('verified C2 staged source required')
    _verify_staging(stage,'aggTrades')
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MicrostructureError('pyarrow is required for staged C2') from exc
    def rows():
        with pq.ParquetFile(stage.parquet_path) as source:
            if not source.schema_arrow.equals(schema_for('aggTrades',pa)):
                raise MicrostructureError('C2 schema mismatch')
            for batch in source.iter_batches(batch_size=16_384,
                                             columns=['agg_trade_id','transact_time','price','quantity','is_buyer_maker']):
                yield from batch.to_pylist()
    lo, hi = stage.identity.bounds
    yield from rollup_aggtrades(rows(),period_start=lo,period_end=hi,seconds=seconds)
    _verify_staging(stage,'aggTrades')
