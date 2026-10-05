"""Engine-owned OZ candidate state; legacy decision functions remain canonical.

The module is injected at composition/startup, so importing this module does not
start the legacy monitor's logging or transport services.
"""
from .model import Kind, Subscriptions, Resolution
from staff_schema import legacy_frame


class OZCandidateProcessor:
    """First E2 migration: OUT/IN episodes, HMA cross and B0 survival only."""
    name = 'OZ_CANDIDATE'

    def __init__(self, oz_module, config, *, symbols=(), timeframes=('1m',)):
        self.oz = oz_module
        self.config = dict(config)
        self.symbols = tuple(symbols)
        self.timeframes = tuple(timeframes)

    def subscriptions(self):
        return Subscriptions(symbols=self.symbols, timeframes=self.timeframes,
                             kinds=(Kind.MARKET_BUNDLE,), resolution=Resolution.TICK)

    def on_event(self, event, board, state):
        symbol = event.payload['symbol']
        monitor = self.oz.OZMonitor(symbol, self.config, None, None,
            staff_client=False, persistence=False, source_time=event.source_time / 1000)
        monitor.base_tfs = list(self.timeframes)
        previous = state.get(symbol)
        if previous is not None:
            monitor.restore_event_state(previous)
        monitor._in_cycle = True
        try:
            for tf in self.timeframes:
                if (symbol, tf) not in board.feeds:
                    continue
                snap = board.snapshot(symbol, tf)
                raw = legacy_frame(symbol, tf, snap)
                frame = self.oz.OZSnapshotFeatures.compose(raw, tf, 3.0)
                monitor._process_hma_cross(tf, frame)
                monitor._process_out_in(tf, frame)
                for direction in ('LONG', 'SHORT'):
                    monitor._maintain_base_candidate(tf, direction, frame)
        finally:
            monitor._in_cycle = False
        state[symbol] = monitor.export_event_state()
