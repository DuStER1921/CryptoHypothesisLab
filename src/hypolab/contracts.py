"""Versioned, half-open UTC time banks. No authority to read real data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

UTC = timezone.utc


def utc(text: str) -> datetime:
    result = datetime.fromisoformat(text.replace('Z', '+00:00'))
    require_utc(result)
    return result


def require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError('timezone-aware UTC timestamp required')


class Role(str, Enum):
    DEVELOPMENT = 'DEVELOPMENT'
    VALIDATION = 'VALIDATION'
    FINAL_OOS = 'FINAL_OOS'


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime  # exclusive

    def __post_init__(self) -> None:
        require_utc(self.start)
        require_utc(self.end)
        if self.start >= self.end:
            raise ValueError('invalid half-open window')

    def contains(self, start: datetime, end: datetime) -> bool:
        require_utc(start)
        require_utc(end)
        return self.start <= start < end <= self.end


# RULES_V1: deliberately fixed, not inferred from the data or performance.
SPLITS = {
    Role.DEVELOPMENT: Window(utc('2020-01-01T00:00:00Z'), utc('2025-01-01T00:00:00Z')),
    Role.VALIDATION: Window(utc('2025-01-01T00:00:00Z'), utc('2026-01-01T00:00:00Z')),
    Role.FINAL_OOS: Window(utc('2026-01-01T00:00:00Z'), utc('2026-09-01T00:00:00Z')),
}
