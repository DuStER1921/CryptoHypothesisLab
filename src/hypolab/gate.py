"""Default-deny dataset/split gate. Protected real-data grants do not exist in V1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import Role, SPLITS, Window
from .ledger import ExperimentLedger


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ReadRequest:
    partition_id: str
    role: Role
    window: Window
    scope: str  # only SYNTHETIC_ONLY in this first implementation


class ValidationGate:
    def __init__(self, ledger: ExperimentLedger) -> None:
        self.ledger = ledger

    def before_read(self, request: ReadRequest) -> str:
        """Record denial/start before source is touched; no production approval mechanism."""
        if not isinstance(request.role, Role):
            raise AccessDenied('UNKNOWN_ROLE')
        reason = None
        if request.scope != 'SYNTHETIC_ONLY':
            reason = 'REAL_NOT_AUTHORIZED'
        elif request.role != Role.DEVELOPMENT:
            reason = 'PROTECTED_SEALED'
        elif not SPLITS[Role.DEVELOPMENT].contains(request.window.start, request.window.end):
            reason = 'OUT_OF_DEVELOPMENT'
        if reason:
            self.ledger.append('READ_DENIED', {'partition_id': request.partition_id,
                                               'role': request.role.value, 'reason': reason})
            raise AccessDenied(reason)
        return self.ledger.append('READ_STARTED', {'partition_id': request.partition_id,
                                                   'role': request.role.value,
                                                   'scope': request.scope})
