"""Deterministic synthetic event studies, not fitted strategies or proof of edge."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
from statistics import mean

from .contracts import Role, require_utc
from .ledger import ExperimentLedger, canonical_json
from .temporal import Observation, TimeGuard, TemporalError


@dataclass(frozen=True)
class MinuteBar:
    """Synthetic 1m close + contemporaneous taker volume aggregated from synthetic ticks."""
    start: datetime
    available_at: datetime
    close: float
    buy_qty: float
    sell_qty: float

    def __post_init__(self):
        require_utc(self.start)
        require_utc(self.available_at)
        if self.start.second != 0 or self.start.microsecond != 0:
            raise TemporalError('bar start must be UTC minute aligned')
        if self.available_at < self.start + timedelta(minutes=1):
            raise TemporalError('bar published before completion')
        if any(not math.isfinite(x) for x in (self.close, self.buy_qty, self.sell_qty)):
            raise ValueError('non-finite bar data')
        if self.close <= 0 or self.buy_qty < 0 or self.sell_qty < 0:
            raise ValueError('invalid market data')


@dataclass(frozen=True)
class EventSpec:
    id: str
    imbalance_threshold: float
    # Positive buy/sell order-flow imbalance. Threshold fixed before read.
    def __post_init__(self):
        if not self.id or not self.id.replace('-', '').replace('_', '').isalnum():
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
    """Pre-register every event/horizon trial; no p-values or blind claims."""

    def __init__(self, ledger: ExperimentLedger):
        self.ledger = ledger

    @staticmethod
    def _validate_bars(bars: list[MinuteBar]) -> None:
        if len(bars) < 3:
            raise ValueError('at least three consecutive bars required')
        for a, b in zip(bars, bars[1:]):
            if b.start != a.start + timedelta(minutes=1):
                raise TemporalError('missing, duplicated, out-of-order minute')
            if b.available_at < a.available_at:
                raise TemporalError('availability order reversal')

    def run(self, *, bars: list[MinuteBar], specs: tuple[EventSpec, ...],
            horizons: tuple[int, ...], batch_id: str) -> tuple[Finding, ...]:
        self._validate_bars(bars)
        if not specs or not horizons or len(set(s.id for s in specs)) != len(specs):
            raise ValueError('nonempty unique specs and horizons required')
        if len(set(horizons)) != len(horizons) or any(type(x) is not int or not 1 <= x <= 1440 for x in horizons):
            raise ValueError('unique horizons of 1..1440 minutes required')
        # Reserve one result per trial *before* analyzing any outcome.
        definitions = {'specs': [vars(s) for s in specs], 'horizons': list(horizons),
                       'batch_id': batch_id, 'scope': 'SYNTHETIC_ONLY'}
        self.ledger.append('BATCH_REGISTERED', {'batch_id': batch_id,
                                               'trials': len(specs) * len(horizons),
                                               'definition_sha': hashlib.sha256(canonical_json(definitions)).hexdigest()})
        findings = []
        for spec in specs:
            for h in horizons:
                event_returns, baseline_returns = [], []
                for i in range(len(bars) - h):
                    current, future = bars[i], bars[i + h]
                    # Future bar must also be completed and known at labeling time.
                    obs = Observation(current.start, current.start + timedelta(minutes=1),
                                      current.available_at, current.available_at,
                                      current.available_at + timedelta(microseconds=1), future.available_at)
                    try:
                        TimeGuard.check(obs, Role.DEVELOPMENT)
                    except TemporalError:
                        # Out-of-bank labels cannot be scored as Development.
                        continue
                    val = (current.buy_qty - current.sell_qty) / (current.buy_qty + current.sell_qty) if (current.buy_qty + current.sell_qty) else 0.0
                    # Descriptive forward market move from last *known* close;
                    # NOT an executable fill after the bar closed.
                    forward_return = future.close / current.close - 1.0
                    baseline_returns.append(forward_return)
                    if val >= spec.imbalance_threshold:
                        event_returns.append(forward_return)
                finding = Finding(f'{spec.id}-h{h}', len(event_returns), len(baseline_returns),
                                  mean(event_returns) if event_returns else None,
                                  mean(baseline_returns) if baseline_returns else None,
                                  mean(x > 0 for x in event_returns) if event_returns else None,
                                  'SYNTHETIC_DESCRIPTIVE_ONLY' if len(event_returns) >= 30 else 'INSUFFICIENT_SYNTHETIC_SAMPLES')
                findings.append(finding)
                self.ledger.append('TRIAL_RESULT', {'batch_id': batch_id, 'trial_id': finding.trial_id,
                                                    'status': 'DONE' if finding.events >= 30 else 'INSUFFICIENT',
                                                    'result_sha': hashlib.sha256(canonical_json(vars(finding))).hexdigest()})
        return tuple(findings)
