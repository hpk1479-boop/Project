"""Unmodified supplied Part2 input iterator, retained ONLY as a test/benchmark oracle."""
from dataclasses import replace
from pit.archive.reader import FrozenTickReader
from generic_backtest.history.cache import GenericArchiveReader


class LegacyArchiveReader(GenericArchiveReader):
    def __iter__(self):
        ordinal = 0
        for d in self.manifest['chunks']:
            for tick in FrozenTickReader.open(self.root / d['file'], d):
                ordinal += 1
                yield replace(tick, stream_id=self.manifest['stream_namespace'], source_ordinal=ordinal)
