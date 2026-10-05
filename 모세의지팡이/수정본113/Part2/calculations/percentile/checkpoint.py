"""Versioned, checksummed JSON checkpoints with exact float64 bit encoding.

No pickle, terminal state inference, or independent reconstruction of PIT bars.
Restoration requires the identical retained market view and binding identities.
"""
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
import json
import struct
from collections.abc import Mapping

from ...contracts import PitError, digest
from ...models import AsOfToken, BarState
from .contracts import NativeCell, CalcEvent
from .derived import StrategyState, SourceBarArrow, ObservedEvents
from .provider import BundleBinding, FeatureBundle, FeatureDatum, PROVIDER_VERSION

SCHEMA = "PERCENTILE_CHECKPOINT_V1"
_TYPES = {cls.__name__: cls for cls in (AsOfToken, BarState, NativeCell, CalcEvent,
    StrategyState, SourceBarArrow, ObservedEvents, BundleBinding, FeatureBundle, FeatureDatum)}


@dataclass(frozen=True)
class SessionCheckpoint:
    schema: str
    provider_version: str
    owner: str
    prefix_commitment: str | None
    payload: bytes
    sha256: str


def _encode(value):
    if is_dataclass(value):
        return {"$type": type(value).__name__, "fields": {field.name: _encode(getattr(value, field.name)) for field in fields(value)}}
    if isinstance(value, float):
        return {"$float64": struct.pack("<d", value).hex()}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, Mapping):
        return {"$map": [[_encode(k), _encode(v)] for k, v in value.items()]}
    if value is None or isinstance(value, (bool, str, int)):
        return value
    raise PitError("E_CHECKPOINT_PREFIX", f"unsupported checkpoint type {type(value).__name__}")


def _decode(value):
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$type" in value:
        cls = _TYPES[value["$type"]]
        return cls(**{name: _decode(v) for name, v in value["fields"].items()})
    if "$tuple" in value:
        return tuple(_decode(v) for v in value["$tuple"])
    if "$map" in value:
        return {_decode(k): _decode(v) for k, v in value["$map"]}
    if "$float64" in value:
        return struct.unpack("<d", bytes.fromhex(value["$float64"]))[0]
    raise ValueError("unknown checkpoint object")


def snapshot_session(session):
    state = {"owner": session.owner, "token": session._token,
             "market_signature": session._market_signature,
             "bindings": session._bindings, "history": session._history,
             "rows": {h: tuple(rows) for h, rows in session._rows.items()}, "seen": session._seen,
             "returned": session._returned, "epochs": session._epochs,
             "sequences": session._sequences,
             "kernels": {h: kernel.state.snapshot() for h, kernel in session._kernels.items()},
             "trackers": {h: tracker.snapshot() for h, tracker in session._trackers.items()}}
    payload = json.dumps(_encode(state), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return SessionCheckpoint(SCHEMA, PROVIDER_VERSION, session.owner,
                             session._token.prefix_commitment if session._token else None,
                             payload, sha256(payload).hexdigest())


def restore_session(provider, checkpoint, view):
    from .profiles import freeze_source_profile, verify_source_hashes
    from .provider import _profile_identity
    from .session import PercentileFeatureSession
    from ...contracts import ROOT
    if (not isinstance(checkpoint, SessionCheckpoint) or checkpoint.schema != SCHEMA
            or checkpoint.provider_version != PROVIDER_VERSION
            or sha256(checkpoint.payload).hexdigest() != checkpoint.sha256):
        raise PitError("E_CHECKPOINT_PREFIX", "schema/version/checksum mismatch")
    try:
        state = _decode(json.loads(checkpoint.payload))
    except (ValueError, KeyError, TypeError, struct.error) as exc:
        raise PitError("E_CHECKPOINT_PREFIX", "invalid checkpoint payload") from exc
    token = state["token"]
    if (state["owner"] != checkpoint.owner or token != view.token
            or token.prefix_commitment != checkpoint.prefix_commitment
            or state["market_signature"] != digest(view)):
        raise PitError("E_CHECKPOINT_PREFIX", "exact market prefix/view required")
    verify_source_hashes(ROOT)
    existing = provider._sessions.get(checkpoint.owner)
    if existing is not None:
        if existing._token is not None:
            raise PitError("E_CHECKPOINT_PREFIX", "restore requires a fresh owner session")
        if existing._bindings and existing._bindings != state["bindings"]:
            raise PitError("E_CHECKPOINT_PREFIX", "bound source/profile/seed differs")
    candidate = PercentileFeatureSession(checkpoint.owner, provider)
    for handle, binding in state["bindings"].items():
        profile = freeze_source_profile(binding.family, dict(binding.params), binding.seed_policy,
                                        calculation_policy=binding.calc_policy)
        if _profile_identity(profile) != binding.profile_identity:
            raise PitError("E_CHECKPOINT_PREFIX", "source/profile/seed ABI changed")
        expected = digest((PROVIDER_VERSION, binding.owner, binding.symbol, binding.timeframe,
                           binding.family, binding.profile_identity, binding.seed_policy,
                           binding.seed_id, binding.calc_policy))
        if handle != expected or binding.handle != handle or binding.owner != checkpoint.owner:
            raise PitError("E_CHECKPOINT_PREFIX", "binding identity mismatch")
        candidate._bind(binding, profile)
    required = set(candidate._bindings)
    if any(set(state[name]) != required for name in ("seen", "returned", "sequences", "kernels", "trackers")):
        raise PitError("E_CHECKPOINT_PREFIX", "incomplete private state")
    for handle, kernel in candidate._kernels.items():
        kernel.state.restore(state["kernels"][handle])
        binding = candidate._bindings[handle]
        bars = state["history"][(binding.symbol, binding.timeframe)]
        if tuple(kernel.state.bar_ids) != tuple(bar.bar_id for bar in bars):
            raise PitError("E_CHECKPOINT_PREFIX", "kernel history and PIT bars differ")
        if kernel.state.last_return != state["returned"][handle]:
            raise PitError("E_CHECKPOINT_PREFIX", "kernel prior return differs")
        candidate._trackers[handle].restore(state["trackers"][handle])
    for name in ("history", "rows", "returned", "epochs", "sequences"):
        setattr(candidate, "_" + name, state[name])
    for handle, entries in state["seen"].items():
        candidate._seen[handle].update(entries)
    candidate._token, candidate._market_signature = token, state["market_signature"]
    provider._sessions[checkpoint.owner] = candidate
    for handle, profile in candidate._profiles.items():
        provider._profiles[candidate._bindings[handle].profile_identity] = profile
    return candidate
