"""PIT source percentile namespace; native parity remains separately gated."""
from .contracts import NativeCell, CalcEvent, KernelResult, SourceProfile
from .profiles import freeze_source_profile, verify_source_hashes
from .provider import PercentileFeatureProvider, FeatureBundle, FeatureDatum
from .session import PercentileFeatureSession, PercentileSessionView
from .checkpoint import SessionCheckpoint, snapshot_session, restore_session
from .derived import (StrategyStateProjector, SourceSignalProjector,
                      ObservedBoundaryTracker, ObservedEvents)

__all__ = ["NativeCell", "CalcEvent", "KernelResult", "SourceProfile", "freeze_source_profile",
    "verify_source_hashes", "PercentileFeatureProvider", "PercentileFeatureSession",
    "PercentileSessionView", "FeatureBundle", "FeatureDatum", "SessionCheckpoint",
    "snapshot_session", "restore_session", "StrategyStateProjector", "SourceSignalProjector",
    "ObservedBoundaryTracker", "ObservedEvents"]
