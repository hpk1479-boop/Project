"""Immutable page snapshots. NativeCell values and source writes are unchanged."""
from collections.abc import Sequence
from dataclasses import dataclass

PAGE_SIZE = 64


@dataclass(frozen=True, eq=False)
class FrozenCells(Sequence):
    pages: tuple
    size: int

    def __len__(self):
        return self.size

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self.size)))
        if index < 0:
            index += self.size
        if not 0 <= index < self.size:
            raise IndexError(index)
        return self.pages[index // PAGE_SIZE][index % PAGE_SIZE]

    def __iter__(self):
        for page in self.pages:
            yield from page

    def __eq__(self, other):
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))


class TrackedCells(list):
    """List-compatible native storage; control writes dirty pages too.

    Only storage identity changes. Source writes ledger is maintained by
    SourceStateStore.write exactly as before, not by this class.
    """
    def __init__(self, values=()):
        super().__init__(values)
        self._pages = None
        self._dirty = set()

    def __setitem__(self, index, value):
        super().__setitem__(index, value)
        if isinstance(index, slice):
            self._pages = None
            self._dirty.clear()
        else:
            if index < 0:
                index += len(self)
            self._dirty.add(index // PAGE_SIZE)

    def _invalidate(self):
        self._pages = None
        self._dirty.clear()

    def append(self, value):
        super().append(value); self._invalidate()

    def extend(self, values):
        super().extend(values); self._invalidate()

    def insert(self, index, value):
        super().insert(index, value); self._invalidate()

    def __delitem__(self, index):
        super().__delitem__(index); self._invalidate()

    def pop(self, index=-1):
        value = super().pop(index); self._invalidate(); return value

    def clear(self):
        super().clear(); self._invalidate()

    def remove(self, value):
        super().remove(value); self._invalidate()

    def reverse(self):
        super().reverse(); self._invalidate()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs); self._invalidate()

    def __iadd__(self, values):
        self.extend(values); return self

    def __imul__(self, count):
        super().__imul__(count); self._invalidate(); return self

    def freeze(self):
        if self._pages is None:
            self._pages = tuple(tuple(self[i:i + PAGE_SIZE]) for i in range(0, len(self), PAGE_SIZE))
        elif self._dirty:
            pages = list(self._pages)
            for i in self._dirty:
                pages[i] = tuple(self[i * PAGE_SIZE:(i + 1) * PAGE_SIZE])
            self._pages = tuple(pages)
        self._dirty.clear()
        return FrozenCells(self._pages, len(self))
