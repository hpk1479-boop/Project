"""Exact, content-addressed STAFF response records (local trusted fixtures only)."""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import pickle
import re
import sqlite3
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

EPOCH = re.compile(r'^[0-9a-f]{32}:(\d+)$')


def normalize(value):
    """Remove only the documented random STAFF session prefix; retain its counter."""
    if isinstance(value, str):
        match = EPOCH.fullmatch(value)
        return 'STAFF_SESSION:' + match[1] if match else value
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def json_bytes(value):
    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str).encode('utf-8')


def frame_digest(frame):
    """Include values, index, dtypes, column order, and attrs; no float rounding."""
    digest = hashlib.sha256()
    def put(value):
        raw = value if isinstance(value, bytes) else json_bytes(value)
        digest.update(len(raw).to_bytes(8, 'little'))
        digest.update(raw)
    put({'columns': list(frame.columns), 'dtypes': [str(t) for t in frame.dtypes],
         'attrs': frame.attrs, 'index_type': type(frame.index).__name__,
         'index_dtype': str(frame.index.dtype), 'index_names': frame.index.names,
         'column_names': frame.columns.names})
    for array in [frame.index.to_numpy(), *(frame[c].to_numpy() for c in frame.columns)]:
        if array.dtype.kind in 'biufcmM':
            put(np.ascontiguousarray(array.astype(array.dtype.newbyteorder('<'))).tobytes())
        else:
            put(array.tolist())
    return digest.hexdigest()


def normalized_frame(frame):
    result = frame.copy(deep=True)
    result.attrs = normalize(copy.deepcopy(frame.attrs))
    return result


class Recorder:
    def __init__(self, path):
        path = Path(path)
        if 'staff_s0' in path.resolve().parts:
            raise PermissionError('S0 evidence is frozen; record candidate outputs in a new stage directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(path)
        self.db = sqlite3.connect(path)
        self.db.executescript('CREATE TABLE frames (sha TEXT PRIMARY KEY, data BLOB NOT NULL);'
                              'CREATE TABLE arrays (sha TEXT PRIMARY KEY, data BLOB NOT NULL);'
                              'CREATE TABLE cases (name TEXT PRIMARY KEY, response TEXT NOT NULL);')
        self.frames = set()
        self.arrays = set()

    def array(self, value):
        raw = pickle.dumps(value, protocol=5)
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in self.arrays:
            self.db.execute('INSERT INTO arrays VALUES (?,?)', (sha, zlib.compress(raw, level=1)))
            self.arrays.add(sha)
        return sha

    def add(self, name, response):
        if isinstance(response, pd.DataFrame):
            response = {'frame': response}
        result = {}
        for key, value in response.items():
            if isinstance(value, pd.DataFrame):
                sha = frame_digest(value)
                if sha not in self.frames:
                    frame = normalized_frame(value)
                    # Matrix combinations share almost all columns. Store each
                    # typed Series once, retaining extension dtypes and indexes.
                    document = {'columns': frame.columns, 'index': frame.index, 'attrs': frame.attrs,
                                'series': [self.array(frame[c]) for c in frame.columns]}
                    blob = zlib.compress(pickle.dumps(document, protocol=5), level=1)
                    self.db.execute('INSERT INTO frames VALUES (?,?)', (sha, blob))
                    self.frames.add(sha)
                result[key] = {'frame_sha256': sha}
            else:
                result[key] = normalize(value)
        self.db.execute('INSERT INTO cases VALUES (?,?)', (name, json_bytes(result).decode('utf-8')))

    def close(self):
        self.db.commit()
        rows = self.db.execute('SELECT name,response FROM cases ORDER BY name').fetchall()
        summary = {'cases': len(rows), 'unique_frames': len(self.frames),
                   'unique_arrays': len(self.arrays),
                   'sha256': hashlib.sha256(json_bytes(rows)).hexdigest()}
        self.db.close()
        return summary


def read_frame(db, blob, array_loader=None):
    value = pickle.loads(zlib.decompress(blob))
    if isinstance(value, pd.DataFrame):  # initial S0 smoke format
        return value
    def load(sha):
        return pickle.loads(zlib.decompress(db.execute('SELECT data FROM arrays WHERE sha=?',
                            (sha,)).fetchone()[0]))
    columns = [(array_loader or load)(sha) for sha in value['series']]
    frame = pd.concat(columns, axis=1)
    frame.columns, frame.index, frame.attrs = value['columns'], value['index'], value['attrs']
    return frame


def compare(left, right):
    """Read only locally generated, trusted SQLite/pickle records."""
    for path in (left, right):
        if not Path(path).is_file():
            raise FileNotFoundError(path)
    with sqlite3.connect(Path(left).resolve().as_uri() + '?mode=ro', uri=True) as a, \
         sqlite3.connect(Path(right).resolve().as_uri() + '?mode=ro', uri=True) as b:
        aa = dict(a.execute('SELECT name,response FROM cases'))
        bb = dict(b.execute('SELECT name,response FROM cases'))
        for db, cases in ((a, aa), (b, bb)):
            stored = {row[0] for row in db.execute('SELECT sha FROM frames')}
            referenced = {value['frame_sha256'] for response in cases.values()
                          for value in json.loads(response).values()
                          if isinstance(value, dict) and 'frame_sha256' in value}
            if not referenced.issubset(stored):
                raise AssertionError('Golden is missing referenced frames')
        differences = [k for k in sorted(aa.keys() | bb.keys()) if aa.get(k) != bb.get(k)]
        # Independently check stored frames even when content hashes match.
        checked = 0
        @functools.lru_cache(maxsize=2048)
        def array(db, sha):
            row = db.execute('SELECT data FROM arrays WHERE sha=?', (sha,)).fetchone()
            if row is None:
                raise AssertionError('Golden is missing referenced arrays')
            return pickle.loads(zlib.decompress(row[0]))
        for sha, data in a.execute('SELECT sha,data FROM frames'):
            other = b.execute('SELECT data FROM frames WHERE sha=?', (sha,)).fetchone()
            if other is None:
                continue
            fa = read_frame(a, data, lambda key: array(a, key))
            fb = read_frame(b, other[0], lambda key: array(b, key))
            if frame_digest(fa) != sha or frame_digest(fb) != sha:
                raise AssertionError('Corrupt golden frame: ' + sha)
            pd.testing.assert_frame_equal(fa, fb, check_exact=True, check_dtype=True,
                                          check_index_type=True, check_column_type=True)
            assert fa.attrs == fb.attrs
            checked += 1
            if checked % 1000 == 0:
                print(f'exact DataFrame comparisons: {checked}', flush=True)
        return {'equal': not differences, 'left_cases': len(aa), 'right_cases': len(bb),
                'exact_frames_checked': checked, 'different_cases': differences}
