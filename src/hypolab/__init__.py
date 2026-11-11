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

from .frozen import FrozenIndex, PinnedPartition, FrozenError
from .staging import ReadOnlyStager, StagedPartition, StageError
from .parquet_window import ParquetWindowReader, ParquetWindowError
from .admission import VerifiedDevelopmentGrant, verify_development_grant, GrantError
from .admitted_reader import GovernedRealReader, ResearchAccessNotReady
from .c1c2_features import C1C2MinuteBuilder, MinuteFeature, FeatureError
from .private_output import PrivateOutputStore, PrivateOutputError
