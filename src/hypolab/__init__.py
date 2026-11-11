"""CryptoHypothesisLab V1: synthetic-only research safety foundation."""
from .contracts import Role, SPLITS
from .discovery import DiscoveryEngine, EventSpec, Finding, MinuteBar
from .gate import AccessDenied, ReadRequest, ValidationGate
from .gateway import DataGateway, DataIntegrityError, FrozenPartition
from .ledger import ExperimentLedger, LedgerError
from .temporal import Observation, TemporalError, TimeGuard

__all__ = ['Role', 'SPLITS', 'DiscoveryEngine', 'EventSpec', 'Finding', 'MinuteBar',
           'AccessDenied', 'ReadRequest', 'ValidationGate', 'DataGateway',
           'DataIntegrityError', 'FrozenPartition', 'ExperimentLedger',
           'LedgerError', 'Observation', 'TemporalError', 'TimeGuard']
