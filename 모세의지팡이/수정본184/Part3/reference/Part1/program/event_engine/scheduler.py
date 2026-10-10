"""Per-symbol source-clock timer queues; equal deadlines retain request order."""
import heapq


class Scheduler:
    def __init__(self):
        self._queues = {}
        self._order = 0
        self._times = {}

    def request(self, strategy, request):
        previous = self._times.get(request.symbol)
        if previous is not None and request.due_time < previous:
            raise ValueError('timer deadline precedes committed symbol time')
        self._order += 1
        heapq.heappush(self._queues.setdefault(request.symbol, []),
                       (request.due_time, self._order, strategy, request))

    def advances(self, symbol, timestamp):
        previous = self._times.get(symbol)
        if previous is not None and timestamp < previous:
            raise ValueError('symbol source_time moved backwards')
        return previous is None or timestamp > previous

    def due(self, symbol, timestamp, *, equal):
        queue = self._queues.get(symbol, [])
        # Snapshot eligible timers. Newly requested timers never spin recursively.
        result = []
        while queue and (queue[0][0] == timestamp if equal else queue[0][0] < timestamp):
            result.append(heapq.heappop(queue))
        return result

    def commit(self, symbol, timestamp):
        self._times[symbol] = timestamp

    def checkpoint(self):
        return {'order': self._order, 'times': dict(self._times),
                'queues': {key: list(value) for key, value in self._queues.items()}}

    def restore(self, state):
        self._order = state['order']; self._times = dict(state['times'])
        self._queues = {key: list(value) for key, value in state['queues'].items()}
