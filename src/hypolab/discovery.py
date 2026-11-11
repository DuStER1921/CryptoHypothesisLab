"""Causal descriptive event discovery on synthetic minute bars only.

Sort once per outcome horizon, then answer many order-flow threshold queries via
binary search. This is *not* a significance test or a strategy backtest.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
import re

from .contracts import Role, SPLITS, require_utc
from .ledger import ExperimentLedger, canonical_json
from .temporal import Observation, TimeGuard, TemporalError


@dataclass(frozen=True)
class MinuteBar:
    """Synthetic completed 1m close and synthetic taker buy/sell quantities."""
    start: datetime
    available_at: datetime
    close: float
    buy_qty: float
    sell_qty: float

    def __post_init__(self):
        require_utc(self.start)
        require_utc(self.available_at)
        if self.start.second or self.start.microsecond:
            raise TemporalError('bar start must be UTC minute aligned')
        if self.available_at < self.start + timedelta(minutes=1):
            raise TemporalError('bar published before its close')
        if any(not math.isfinite(x) for x in (self.close, self.buy_qty, self.sell_qty)):
            raise ValueError('nonfinite market input')
        if self.close <= 0 or self.buy_qty < 0 or self.sell_qty < 0:
            raise ValueError('invalid market input')


@dataclass(frozen=True)
class EventSpec:
    id: str
    imbalance_threshold: float

    def __post_init__(self):
        if not isinstance(self.id, str) or re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', self.id) is None:
            raise ValueError('invalid spec ID')
        if not math.isfinite(self.imbalance_threshold) or not 0 <= self.imbalance_threshold <= 1:
            raise ValueError('threshold must be 0..1')


@dataclass(frozen=True)
class Finding:
    trial_id: str
    events: int
    baseline_samples: int
    event_mean_return: float | None
    baseline_mean_return: float | None
    event_up_fraction: float | None
    status: str = 'SYNTHETIC_DESCRIPTIVE_ONLY'


class DiscoveryEngine:
    """Pre-register the exact trial set before computing any forward outcomes."""

    def __init__(self, ledger: ExperimentLedger):
        self.ledger = ledger

    @staticmethod
    def _validate_bars(bars: list[MinuteBar]) -> None:
        if len(bars) < 3:
            raise ValueError('at least three consecutive bars required')
        for a, b in zip(bars, bars[1:]):
            if b.start != a.start + timedelta(minutes=1):
                raise TemporalError('missing, duplicate or unordered minute')
            if b.available_at < a.available_at:
                raise TemporalError('bar availability goes backwards')

    @staticmethod
    def _make_table(bars: list[MinuteBar], horizon: int):
        bank = SPLITS[Role.DEVELOPMENT]
        points: list[tuple[float, float]] = []
        all_returns: list[float] = []
        for i in range(len(bars) - horizon):
            current, future = bars[i], bars[i + horizon]
            if (current.start < bank.start or future.available_at > bank.end or
                    current.available_at >= future.available_at):
                # Non-scorable bank boundaries / unavailable future outcomes.
                continue
            observation = Observation(
                current.start, current.start + timedelta(minutes=1),
                current.available_at, current.available_at,
                current.available_at + timedelta(microseconds=1), future.available_at)
            # Any other causal violation is a bug and must fail, not be hidden.
            TimeGuard.check(observation, Role.DEVELOPMENT)
            ret = future.close / current.close - 1.0
            if not math.isfinite(ret):
                raise TemporalError('non-finite forward outcome')
            all_returns.append(ret)
            total_qty = current.buy_qty + current.sell_qty
            if total_qty > 0:
                imbalance = (current.buy_qty - current.sell_qty) / total_qty
                points.append((imbalance, ret))
        points.sort(key=lambda pair: pair[0])
        values = [p[0] for p in points]
        # Deterministic prefix arrays; O(n) storage per horizon, O(log n) per spec.
        cumulative_return = [0.0]
        cumulative_up = [0]
        for _, ret in points:
            cumulative_return.append(cumulative_return[-1] + ret)
            cumulative_up.append(cumulative_up[-1] + int(ret > 0))
        return values, cumulative_return, cumulative_up, all_returns

    def run(self, *, bars: list[MinuteBar], specs: tuple[EventSpec, ...],
            horizons: tuple[int, ...], batch_id: str) -> tuple[Finding, ...]:
        self._validate_bars(bars)
        if not specs or not horizons or len({s.id for s in specs}) != len(specs):
            raise ValueError('nonempty unique specs and horizons required')
        if (len(set(horizons)) != len(horizons) or
                any(type(h) is not int or not 1 <= h <= 1440 for h in horizons)):
            raise ValueError('unique horizons of 1..1440 minutes required')
        trial_ids = [f'{s.id}-h{h}' for s in specs for h in horizons]
        if len(set(trial_ids)) != len(trial_ids):
            raise ValueError('duplicate trial identity')
        plan = {'specs': [vars(s) for s in specs], 'horizons': list(horizons),
                'batch_id': batch_id, 'scope': 'SYNTHETIC_ONLY'}
        self.ledger.append('BATCH_REGISTERED', {'batch_id': batch_id,
                                               'trials': len(trial_ids),
                                               'trial_ids': trial_ids,
                                               'definition_sha': hashlib.sha256(
                                                   canonical_json(plan)).hexdigest()})
        # One scan and one sort for each horizon, regardless of spec count.
        per_horizon = {h: self._make_table(bars, h) for h in horizons}
        findings = []
        for spec in specs:
            for horizon in horizons:
                values, sums, up, baseline = per_horizon[horizon]
                pos = bisect_left(values, spec.imbalance_threshold)
                count = len(values) - pos
                sum_return = sums[-1] - sums[pos]
                up_count = up[-1] - up[pos]
                finding = Finding(
                    f'{spec.id}-h{horizon}', count, len(baseline),
                    sum_return / count if count else None,
                    math.fsum(baseline) / len(baseline) if baseline else None,
                    up_count / count if count else None,
                    'SYNTHETIC_DESCRIPTIVE_ONLY' if count >= 30 else
                    'INSUFFICIENT_SYNTHETIC_SAMPLES')
                findings.append(finding)
        # Register all outcomes in bounded, durably acknowledged batches.
        payloads = [('TRIAL_RESULT', {'batch_id': batch_id, 'trial_id': f.trial_id,
                                     'status': 'DONE' if f.events >= 30 else 'INSUFFICIENT',
                                     'result_sha': hashlib.sha256(canonical_json(vars(f))).hexdigest()})
                    for f in findings]
        for offset in range(0, len(payloads), 1000):
            self.ledger.append_many(payloads[offset:offset + 1000])
        return tuple(findings)
