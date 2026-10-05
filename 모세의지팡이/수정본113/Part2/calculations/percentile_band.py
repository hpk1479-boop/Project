"""Public facade over the UPLOADED native-expression kernels, not a new formula.

The implementation tree is ``percentile/``. The local ``pit.features.percentile``
compatibility package resolves exclusively to that application-owned tree.
Callers must provide the original chronological observation prefix and call
schedule. A final broker M1 candle is NOT an intrabar observation.
"""
from __future__ import annotations
from dataclasses import asdict
from pit.features.percentile.kernels.price import PriceSourceKernel
from pit.features.percentile.kernels.rsi import RsiSourceKernel
from pit.features.percentile.kernels.stochastic import StoSourceKernel
from pit.features.percentile.kernels.disparity import DiSourceKernel
from pit.features.percentile.profiles import freeze_source_profile
from pit.features.percentile.lifecycle import SourceCallScheduler

KERNELS = {'PRICE': PriceSourceKernel, 'RSI': RsiSourceKernel,
           'STO': StoSourceKernel, 'DI': DiSourceKernel}


def make_kernel(family: str, parameters: dict | None = None):
    """Preserve SOURCE_DEFINED_ONLY, including undefined native storage."""
    return KERNELS[family](freeze_source_profile(family, parameters))


def required_history(family: str, parameters: dict | None = None) -> dict:
    """Readiness is not a finite replacement for the recurrence prestate."""
    p = freeze_source_profile(family, parameters).params
    minimum = p['InpDomCycle'] + 12 if family == 'PRICE' else (
        p['BandPeriod'] + int((p['Vibration'] - 1) / 2) + p[family + 'Length'] + 1)
    return {'first_ready_bar_count': minimum, 'exact_history': 'SOURCE_START_OR_EXACT_CHECKPOINT',
            'finite_overlap_is_sufficient': False, 'seed_policy': 'SOURCE_DEFINED_ONLY'}


class NativeSequence:
    """Exact append/resume of explicit original kernel invocations.

    No rolling-tail reset, NaN conversion, dtype coercion or artificial seed.
    A checkpoint contains the complete recurrence state, NOT just warmup bars.
    """
    def __init__(self, family: str, parameters: dict | None = None):
        self.family = family
        self.kernel = make_kernel(family, parameters)
        self.scheduler = SourceCallScheduler()

    def advance(self, bars, *, source_ordinal=0, market_asof_token=None,
                point=0.01, history_epoch='0'):
        event = self.scheduler.event(len(bars), history_epoch, source_ordinal,
                                     market_asof_token, point)
        result = self.kernel.invoke(bars, event)
        self.scheduler.commit(result)
        return result

    def checkpoint(self) -> dict:
        return {'family': self.family, 'parameters': dict(self.kernel.profile.params),
                'kernel': self.kernel.snapshot(),
                'scheduler': {'previous_return': self.scheduler.previous_return,
                              'sequence': self.scheduler.sequence,
                              'history_epoch': self.scheduler.history_epoch}}

    @classmethod
    def restore(cls, checkpoint: dict):
        result = cls(checkpoint['family'], checkpoint['parameters'])
        result.kernel.restore(checkpoint['kernel'])
        for key in ('previous_return', 'sequence', 'history_epoch'):
            setattr(result.scheduler, key, checkpoint['scheduler'][key])
        return result


def export_result(result, *, latest_only=False) -> dict:
    """Lossless NativeCell representation. Bits/origin/taint are all retained."""
    return {'returned': result.returned, 'metadata': dict(result.metadata),
            'buffers': {name: [asdict(cell) for cell in (cells[:1] if latest_only else cells)]
                        for name, cells in result.buffers.items()},
            'writes': [asdict(write) for write in result.writes] if not latest_only else None}
