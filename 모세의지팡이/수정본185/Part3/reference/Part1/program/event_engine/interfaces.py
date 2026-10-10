"""Domain protocols. Engine owns all mutable processor/strategy state."""
from typing import Protocol
from .model import Subscriptions


class Strategy(Protocol):
    name: str
    def subscriptions(self) -> Subscriptions: ...
    def on_event(self, event, board, state, emit) -> None: ...


class StatefulProcessor(Protocol):
    name: str
    def subscriptions(self) -> Subscriptions: ...
    def on_event(self, event, board, state) -> None: ...
