"""Pickle-free Snapshot transport and per-publication client cache (S3)."""
from dataclasses import dataclass
import json
import threading
from types import MappingProxyType

import numpy as np
from staff_schema import PROTOCOL, REQUEST_TAG, SCHEMA_ID, PIPE_VALUE_COLUMNS, legacy_frame


@dataclass(frozen=True, eq=False)
class Snapshot:
    symbol: str
    tf: str
    seq: int
    source_epoch: str
    received_at: float
    indicator_validity: object
    time: np.ndarray
    volume: np.ndarray
    values: np.ndarray
    max_bars: int = 650
    schema_id: str = SCHEMA_ID
    kind: str = 'FULL'

    @property
    def identity(self):
        # Epoch and receive time distinguish writer restarts with the same seq.
        return (self.symbol, self.tf, self.schema_id, self.seq, self.source_epoch,
                self.received_at, self.max_bars)


@dataclass(frozen=True)
class SnapshotBatch:
    feeds: object
    sigma: float
    closed: bool = False
    error: object = None


def encode_reply(feeds=(), *, sigma=3.0, max_bars=650, closed=False, error=None):
    """Only JSON and fixed little-endian primitive buffers cross this boundary."""
    metadata = {'protocol': PROTOCOL, 'schema_id': SCHEMA_ID, 'kind': 'FULL',
                'wonbi_sigma': sigma, 'max_bars': max_bars, 'closed': closed,
                'error': error, 'feeds': []}
    buffers = []
    for symbol, tf, snap in feeds:
        metadata['feeds'].append({'symbol': symbol, 'tf': tf, 'seq': snap.seq,
            'source_epoch': snap.source_epoch, 'received_at': snap.received_at,
            'indicator_validity': dict(snap.indicator_validity), 'bars': len(snap.time)})
        buffers.extend((snap.time.astype('<i8', copy=False).tobytes(),
                        snap.volume.astype('<i8', copy=False).tobytes(),
                        snap.values.astype('<f8', copy=False).tobytes()))
    return [json.dumps(metadata, allow_nan=False, separators=(',', ':')).encode('utf-8'), *buffers]


