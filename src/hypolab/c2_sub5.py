"""Optional, synthetic-first sub-5s C2 feature over an already verified stage.

This is NOT a market-data access grant or a new owner preparer. The owner must
separately authorize and stage one Development partition before calling the
stage adapter. Existing frozen 5/15/60s data formats remain unchanged.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime

from .c1c2_features import _verify_staging
from .c2_microstructure import MicrostructureError, _ms, rollup_aggtrades
from .staging import StagedPartition


BUCKET_MS = 5_000
WINDOW_MS = 1_000
MIN_ROWS = 5
MAX_BIN_ROWS = 200_000  # Defensive implementation bound; overflow aborts, never truncates.
PILOT_MONTH = '2024-12'
FEATURE = 'peak_1000ms_agg_row_share'


class _PeakTracker:
    """O(rows) two-pointer tracker, storing only the current 1s window."""

    def __init__(self):
        self.start = None
        self.count = 0
        self.max_count = 0
        self.window = deque()
        self.prev_id = None
        self.prev_ms = None
        self.closed = deque()

    def _finish_bin(self):
        if self.start is not None:
            self.closed.append((self.start,
                                self.max_count / self.count if self.count >= MIN_ROWS else None))

    def observe(self, row):
        if type(row) is not dict:
            raise MicrostructureError('unexpected C2 row')
        try:
            stamp = _ms(row['transact_time'])
            idx = row['agg_trade_id']
        except (KeyError, TypeError, AttributeError) as exc:
            raise MicrostructureError('missing C2 timestamp/id') from exc
        if (type(idx) is not int or idx < 0 or
                self.prev_id is not None and idx != self.prev_id + 1 or
                self.prev_ms is not None and stamp < self.prev_ms):
            # Any uncertain missing-id lineage blocks this whole stream.
            raise MicrostructureError('unverified C2 ID continuity/order')
        self.prev_id, self.prev_ms = idx, stamp
        start = stamp - stamp % BUCKET_MS
        if self.start != start:
            self._finish_bin()
            self.start, self.count, self.max_count = start, 0, 0
            self.window.clear()
        self.count += 1
        if self.count > MAX_BIN_ROWS:
            raise MicrostructureError('C2 5s row cap exceeded; discard partial output')
        while self.window and stamp - self.window[0] >= WINDOW_MS:
            self.window.popleft()
        self.window.append(stamp)
        self.max_count = max(self.max_count, len(self.window))
        if len(self.closed) > 1:
            raise MicrostructureError('sub5 output not consumed in lockstep')

    def take(self, start):
        if not self.closed or self.closed[0][0] != start:
            raise MicrostructureError('C2 sub5/baseline alignment mismatch')
        return self.closed.popleft()[1]


def iter_sub5_5s(trades, *, period_start: datetime, period_end: datetime):
    """Yield original validated 5s C2 snapshots + exactly one optional feature.

    The baseline rollup consumes the source iterator ONCE. A gap in time is
    never zero-filled; ambiguous aggTrade ID continuity fails closed. A
    downstream writer must discard *all* output if iteration raises.
    """
    tracker = _PeakTracker()

    def observed():
        for row in trades:
            tracker.observe(row)
            yield row
        tracker._finish_bin()

    for seconds, bucket in rollup_aggtrades(
            observed(), period_start=period_start, period_end=period_end,
            seconds=(5,)):
        if seconds != 5:
            raise MicrostructureError('unexpected sub5 resolution')
        yield {**bucket, FEATURE: tracker.take(bucket['start_ms'])}
    if tracker.closed:
        raise MicrostructureError('orphan sub5 bucket')


def iter_verified_staged_c2_sub5_5s(stage: StagedPartition):
    """Owner-stage-only adapter, with one verified, bounded Parquet pass.

    It never mounts Drive, opens a canonical warehouse path, writes outputs,
    or authorizes a read. Use only after a separate, explicit month-specific
    owner admission and READ_STARTED evidence. This does not change V2 ZIPs.
    """
    if (type(stage) is not StagedPartition or not stage.source_verified or
            stage.identity.family != 'aggTrades' or
            stage.identity.period != PILOT_MONTH):
        raise MicrostructureError('verified 2024-12 C2 owner stage required')
    _verify_staging(stage, 'aggTrades')
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MicrostructureError('optional pyarrow runtime required') from exc
    from .parquet_window import schema_for

    def rows():
        with pq.ParquetFile(stage.parquet_path) as source:
            if not source.schema_arrow.equals(schema_for('aggTrades', pa)):
                raise MicrostructureError('C2 staged Parquet schema mismatch')
            for batch in source.iter_batches(
                    batch_size=16_384,
                    columns=['agg_trade_id', 'transact_time', 'price',
                             'quantity', 'is_buyer_maker']):
                yield from batch.to_pylist()

    lo, hi = stage.identity.bounds
    yield from iter_sub5_5s(rows(), period_start=lo, period_end=hi)
    _verify_staging(stage, 'aggTrades')
