"""Exact insertion-ordered fingerprint map with bounded in-memory recent rows.

No LRU deletion of logical events. Old keys remain in a private SQLite index.
The index is an ephemeral runtime optimization, never a checkpoint substitute.
"""
from collections.abc import MutableMapping
from collections import OrderedDict
import os
import sqlite3
import tempfile


class ExactFingerprintStore(MutableMapping):
    def __init__(self, memory_limit=4096, directory=None):
        if type(memory_limit) is not int or memory_limit < 1:
            raise ValueError('positive fingerprint memory limit required')
        self.memory_limit = memory_limit
        self._pending = OrderedDict()
        self._count = 0
        self._db = None
        self._temp = None
        self.directory = directory

    def _open(self):
        if self._db is None:
            self._temp = tempfile.TemporaryDirectory(prefix='pit-fingerprints-', dir=self.directory)
            try:
                self._db = sqlite3.connect(os.path.join(self._temp.name, 'exact.sqlite'))
                self._db.execute('PRAGMA cache_size=-1024')
                self._db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, key TEXT UNIQUE, value TEXT NOT NULL)')
            except BaseException:
                self.close()
                raise

    def _flush(self):
        if not self._pending:
            return
        self._open()
        with self._db:
            self._db.executemany('INSERT INTO events(key,value) VALUES (?,?)', self._pending.items())
        self._pending.clear()

    def __len__(self):
        return self._count

    def __getitem__(self, key):
        if key in self._pending:
            return self._pending[key]
        if self._db is not None:
            row = self._db.execute('SELECT value FROM events WHERE key=?', (key,)).fetchone()
            if row is not None:
                return row[0]
        raise KeyError(key)

    def __contains__(self, key):
        if key in self._pending:
            return True
        return self._db is not None and self._db.execute('SELECT 1 FROM events WHERE key=?', (key,)).fetchone() is not None

    def __setitem__(self, key, value):
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError('fingerprint key/value must be strings')
        if key in self._pending:
            self._pending[key] = value
            return
        if self._db is not None and self._db.execute('SELECT 1 FROM events WHERE key=?', (key,)).fetchone() is not None:
            with self._db:
                self._db.execute('UPDATE events SET value=? WHERE key=?', (value, key))
            return
        if len(self._pending) >= self.memory_limit:
            self._flush()
        self._pending[key] = value
        self._count += 1

    def __delitem__(self, key):
        if key in self._pending:
            del self._pending[key]
        elif self._db is not None:
            with self._db:
                cur = self._db.execute('DELETE FROM events WHERE key=?', (key,))
            if not cur.rowcount:
                raise KeyError(key)
        else:
            raise KeyError(key)
        self._count -= 1

    def __iter__(self):
        if self._db is not None:
            for key, in self._db.execute('SELECT key FROM events ORDER BY seq'):
                yield key
        yield from self._pending

    def items(self):
        if self._db is not None:
            yield from self._db.execute('SELECT key,value FROM events ORDER BY seq')
        yield from self._pending.items()

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
