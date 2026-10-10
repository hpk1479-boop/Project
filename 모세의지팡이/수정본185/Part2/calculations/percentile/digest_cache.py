"""One immutable market observation, shared only inside a private registry."""
from ...contracts import digest


class MarketDigestCache:
    def __init__(self):
        self._view = None
        self._digest = None

    def __call__(self, view):
        if view is not self._view:
            value = digest(view)
            self._view, self._digest = view, value
        return self._digest
