"""In-memory replay and numpy prefilter; no clock waits or file access here."""
import numpy as np
from itertools import islice
from staff_schema import PIPE_VALUE_COLUMNS
from .model import Kind, Resolution


def select_inputs(inputs, subscriptions, resolution, *, chunk_size=8):
    """Bounded pre-ingress batches. Carry one prior snapshot per feed, not history."""
    if chunk_size < 1:raise ValueError('positive chunk_size required')
    iterator=iter(inputs);previous={};resolution=Resolution(resolution)
    if resolution == Resolution.TICK:
        yield from iterator
        return
    while items:=list(islice(iterator,chunk_size)):
        yield from _select_batch(items,subscriptions,resolution,previous)


def _select_batch(items, subscriptions, resolution, previous):
    if resolution == Resolution.TICK:
        return items
    keep = np.array([item.kind != Kind.MARKET_BUNDLE for item in items], dtype=bool)
    # Group by feed while retaining global arrival order. numpy comparisons run
    # in batches before ingress, never by discarding numbered engine events.
    groups = {}
    for i, item in enumerate(items):
        if item.kind != Kind.MARKET_BUNDLE:
            continue
        for tf, snapshot in item.payload['feeds'].items():
            groups.setdefault((item.payload['symbol'], tf), []).append((i, snapshot))
    for (symbol, tf), rows in groups.items():
        key=(symbol,tf)
        last=rows[-1][1]
        if key in previous:rows=[(-1,previous[key])]+rows
        previous[key]=last
        interested = [s for s in subscriptions if (not s.symbols or symbol in s.symbols)
                      and (not s.timeframes or tf in s.timeframes)]
        if not interested:
            continue
        index = np.array([i for i, _ in rows], dtype=int)
        opens = np.array([s.time[-1] for _, s in rows])
        changed = np.r_[True, opens[1:] != opens[:-1]]
        if resolution == Resolution.CONDITION:
            boundaries = [b for sub in interested for b in sub.boundaries if b.timeframe == tf]
            # Missing boundary declarations cannot safely filter an event-mode
            # strategy. Preserve all input for that feed instead.
            if any(s.resolution >= Resolution.CONDITION and not s.boundaries for s in interested):
                changed[:] = True
            for boundary in boundaries:
                def values(column):
                    if isinstance(column, (float, int)):
                        return np.full(len(rows), column)
                    offset = -2 if boundary.closed_bar else -1
                    return np.array([s.values[offset, PIPE_VALUE_COLUMNS.index(column)] if len(s.time) >= abs(offset)
                                     else np.nan for _, s in rows])
                delta = values(boundary.left) - values(boundary.right)
                signs = np.where(np.isnan(delta), 2, np.sign(delta))
                changed |= np.r_[True, signs[1:] != signs[:-1]]
            # Readiness / source epochs are semantic transitions, not numeric
            # boundary noise; preserve their observation even in filtered mode.
            meta = [(s.source_epoch, tuple(s.indicator_validity.items())) for _, s in rows]
            changed |= np.array([True] + [meta[j] != meta[j-1] for j in range(1, len(meta))])
        keep[index[changed & (index>=0)]] = True
    return [item for item, accepted in zip(items, keep) if accepted]


def replay(engine, inputs, resolution=Resolution.TICK):
    subscriptions = tuple(c.subscriptions() for c in engine.strategies + engine.processors)
    approximate = tuple(c.name for c in engine.strategies + engine.processors
                        if Resolution(resolution) < c.subscriptions().resolution)
    selected = select_inputs(inputs, subscriptions, resolution)
    count=0
    for item in selected:
        engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                            source_time=item.source_time, payload=item.payload)
        engine.run()
        count+=1
    return {'resolution': Resolution(resolution).name, 'approximate': bool(approximate),
            'label': '근사치' if approximate else '선언된 해상도 충족',
            'approximate_consumers': approximate, 'input_count': count,
            'signals': tuple(engine.signals)}
