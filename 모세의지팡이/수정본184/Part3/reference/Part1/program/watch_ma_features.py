"""STAFF-owned canonical WATCH features; the existing snapshot stays untouched."""
from __future__ import annotations
import pandas as pd
from watch_ma import MAFeatureCache, canonical_ma, feature_source_rows


class WatchMAFeatures:
    def __init__(self, *, source_rows=feature_source_rows):
        # Import only the pure indicator functions. No INDICATOR engine is created.
        from indicator_facts import sma, wma, ema, hma
        self.cache = MAFeatureCache({"SMA": sma, "WMA": wma, "EMA": ema, "HMA": hma})
        self.frames = {}
        self.limits = {}
        self.source_rows = source_rows

    def prepare(self, symbol, timeframe, snapshot, names, history_rows):
        names = tuple(dict.fromkeys(canonical_ma(name) for name in names))
        required = max(history_rows, *(self.source_rows(name) for name in names))
        key = (symbol, timeframe)
        incoming = snapshot.copy(deep=True)
        if incoming.empty:
            return incoming
        incoming["time"] = pd.to_datetime(incoming["time"])
        previous = self.frames.get(key)
        self.limits[key] = max(required, self.limits.get(key, 0))
        if previous is not None and not previous.empty:
            same_epoch = previous.attrs.get("source_epoch") == incoming.attrs.get("source_epoch")
            chronological = incoming["time"].iloc[-1] >= previous["time"].iloc[-1]
            overlap = incoming["time"].iloc[0] <= previous["time"].iloc[-1]
            if same_epoch and chronological and overlap:
                # New snapshot replaces overlapping rows, including forming edits
                # and source corrections. Never append a disjoint unknown gap.
                older = previous[previous["time"] < incoming["time"].iloc[0]]
                if not older.empty:
                    incoming = pd.concat([older, incoming], ignore_index=True)
        incoming = incoming.tail(self.limits[key]).reset_index(drop=True)
        incoming.attrs = dict(snapshot.attrs)
        self.frames[key] = incoming
        features = self.cache.calculate(incoming, names)
        out = incoming.copy(deep=True)
        for name, values in features.items():
            out[name] = values
        out.attrs["watch_ma_features"] = list(names)
        out.attrs["watch_ma_history_required"] = required
        out.attrs["watch_ma_history_available"] = len(incoming)
        return out
