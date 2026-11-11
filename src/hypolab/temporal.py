"""Causal availability, label boundaries, and purged chronological evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .contracts import Role, SPLITS, require_utc


class TemporalError(ValueError):
    pass


@dataclass(frozen=True)
class Observation:
    feature_start: datetime
    feature_end: datetime
    available_at: datetime
    decision_at: datetime
    label_start: datetime
    label_end: datetime


class TimeGuard:
    @staticmethod
    def check(observation: Observation, role: Role = Role.DEVELOPMENT) -> None:
        o = observation
        for value in vars(o).values():
            require_utc(value)
        if not (o.feature_start < o.feature_end <= o.available_at <= o.decision_at
                < o.label_start < o.label_end):
            raise TemporalError('future feature, lookahead or invalid outcome timeline')
        # The label *and feature* must remain strictly inside the designated bank.
        if not SPLITS[role].contains(o.feature_start, o.label_end):
            raise TemporalError('observation crosses role boundary')

    @staticmethod
    def purge_forward(observations: Sequence[Observation], evaluation_start: datetime,
                      evaluation_end: datetime, embargo: timedelta = timedelta(0)) -> dict[str, tuple[int, ...]]:
        require_utc(evaluation_start)
        require_utc(evaluation_end)
        if (not evaluation_start < evaluation_end or embargo < timedelta(0) or
                not SPLITS[Role.DEVELOPMENT].contains(evaluation_start, evaluation_end)):
            raise TemporalError('evaluation must stay inside Development bank')
        training, purged, evaluation, future = [], [], [], []
        for i, o in enumerate(observations):
            TimeGuard.check(o, Role.DEVELOPMENT)
            if evaluation_start <= o.decision_at < evaluation_end:
                # Never score test labels past the evaluation bank.
                if o.label_end > evaluation_end:
                    purged.append(i)
                else:
                    evaluation.append(i)
            elif o.decision_at < evaluation_start:
                # Remove all prior observations whose *labels* touch future test
                # features/outcomes, plus an optional policy-defined embargo.
                (training if o.label_end + embargo <= evaluation_start else purged).append(i)
            else:
                future.append(i)
        return {k: tuple(v) for k, v in [('training', training), ('purged', purged),
                                          ('evaluation', evaluation), ('future_excluded', future)]}
