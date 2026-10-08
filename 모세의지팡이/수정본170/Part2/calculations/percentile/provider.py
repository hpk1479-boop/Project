"""Shared implementation registry; mutable calculation state lives in sessions."""
from dataclasses import dataclass
from types import MappingProxyType

from ...contracts import PitError, TIMEFRAMES, digest

PROVIDER_VERSION = "PIT_PERCENTILE_SOURCE_PORT_V1"
FAMILIES = ("PRICE", "RSI", "STO", "DI")
SEED_POLICIES = ("SOURCE_DEFINED_ONLY", "NATIVE_STATE_CONDITIONED", "NATIVE_RAW_DIAGNOSTIC")
CALC_POLICIES = ("PIT_TICK", "NATIVE_TRACE")


@dataclass(frozen=True)
class BundleBinding:
    handle: str
    owner: str
    symbol: str
    timeframe: str
    family: str
    params: tuple
    seed_policy: str
    seed_id: str
    calc_policy: str
    profile_identity: str


@dataclass(frozen=True)
class FeatureDatum:
    field: str
    native_cell: object
    value: float | None
    validity: str
    unavailable_reason: str | None
    source_accessor: str
    shift: int

    @property
    def float64_bits(self):
        return self.native_cell.bits


@dataclass(frozen=True)
class FeatureBundle:
    handle: str
    family: str
    provider_version: str
    kernel_version: str
    source_hash: str
    params_hash: str
    symbol: str
    timeframe: str
    bar_id: str
    bar_time: int
    bar_role: str
    asof_token: object
    source_ordinal: int
    calc_sequence: int
    calc_event_id: str
    rates_total_equivalent: int
    prev_calculated_equivalent: int
    history_epoch: str
    seed_id: str
    seed_policy: str
    calculation_policy: str
    fields: tuple
    strategy_state: object
    source_signals: object
    observed_events: object
    revision_id: str
    kernel_metadata: tuple = ()
    parity_status: str = "UNVERIFIED_NATIVE_PARITY"

    @property
    def values(self):
        return MappingProxyType(dict(self.fields))

    def field(self, name):
        try:
            return self.values[name]
        except KeyError as exc:
            raise PitError("E_UNDECLARED_DEPENDENCY", name) from exc

    @property
    def staff_columns(self):
        return self.strategy_state.columns


def _profile_identity(profile):
    # The complete immutable profile binds source, inputs, numeric and seed ABI.
    return digest(profile)


class PercentileFeatureProvider:
    """Explicit provider registry usable by any strategy owner.

    Existing ``pit.features.session.FeatureSession`` has no plugin hook. This
    facade deliberately composes alongside it without changing OPEN HMA state.
    """
    version = PROVIDER_VERSION

    def __init__(self, *, materialization="EAGER"):
        if materialization not in ("EAGER", "DEMAND"):
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "materialization policy")
        self.materialization = materialization
        from .profiles import verify_source_hashes
        from ...contracts import ROOT
        verify_source_hashes(ROOT)
        self._sessions = {}
        self._profiles = {}

    def register_source_profiles(self):
        from .profiles import freeze_source_profile
        for family in FAMILIES:
            profile = freeze_source_profile(family)
            self._profiles[_profile_identity(profile)] = profile
        return MappingProxyType(dict(self._profiles))

    def session(self, owner):
        from .session import PercentileFeatureSession
        if not isinstance(owner, str) or not owner:
            raise PitError("E_ID_COLLISION", "owner is required")
        if owner not in self._sessions:
            self._sessions[owner] = PercentileFeatureSession(owner, self)
        return self._sessions[owner]

    def bind_bundle(self, owner, symbol, tf, params=None,
                    seed="SOURCE_DEFINED_ONLY", calc_policy="PIT_TICK"):
        from .profiles import freeze_source_profile
        session = self.session(owner)
        if session._token is not None:
            raise PitError("E_UNDECLARED_DEPENDENCY", "bind before execution")
        if not symbol or tf not in TIMEFRAMES:
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "symbol/timeframe")
        if calc_policy not in CALC_POLICIES:
            raise PitError("E_CALCULATION_POLICY_MISMATCH", calc_policy)
        if isinstance(seed, str):
            seed_policy, seed_id = seed, "NO_CAPTURED_SEED"
        else:
            seed_policy, seed_id = seed.get("policy"), seed.get("id", "")
        if seed_policy not in SEED_POLICIES or not seed_id:
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "seed policy/id")
        if seed_policy == "NATIVE_STATE_CONDITIONED" and seed_id == "NO_CAPTURED_SEED":
            raise PitError("E_UNDEFINED_SOURCE_STATE", "diagnostic capture requires explicit seed id")
        params = params or {}
        if not set(params).issubset(FAMILIES):
            raise PitError("E_UNSUPPORTED_INPUT_PROFILE", "params must map family names to inputs")
        prepared = []
        for family in FAMILIES:
            values = dict(params.get(family, {}))
            profile = freeze_source_profile(family, values, seed_policy=seed_policy,
                                            calculation_policy=calc_policy)
            identity = _profile_identity(profile)
            handle = digest((self.version, owner, symbol, tf, family, identity,
                             seed_policy, seed_id, calc_policy))
            binding = BundleBinding(handle, owner, symbol, tf, family,
                                    tuple(sorted(values.items())), seed_policy, seed_id,
                                    calc_policy, identity)
            prepared.append((binding, profile))
        for binding, profile in prepared:
            self._profiles[binding.profile_identity] = profile
            session._bind(binding, profile)
        return MappingProxyType({binding.family: binding.handle for binding, _ in prepared})

    def on_market(self, owner, view, change=None, **kwargs):
        if change is not None and change.token != view.token:
            raise PitError("E_FUTURE_READ", "market change and immutable view differ")
        return self.session(owner).advance(view, **kwargs)

    def restore(self, checkpoint, view):
        from .checkpoint import restore_session
        return restore_session(self, checkpoint, view)
