"""Only timing exception: latency instrumentation, never domain decisions."""
from time import perf_counter_ns


def measured_time():
    return perf_counter_ns()


class Metrics:
    def __init__(self):
        self.processed = 0
        self.queue_length = 0
        self.peak_queue = 0
        self.last_delay_ns = 0
        self.max_delay_ns = 0
        self.bundle_ns = []
        self.bundle_count = 0
        self.bundle_total_ns = 0

    def queued(self, count):
        self.queue_length = count
        self.peak_queue = max(self.peak_queue, count)

    def received(self, event):
        self.processed += 1
        self.last_delay_ns = max(0, measured_time() - event.engine_time)
        self.max_delay_ns = max(self.max_delay_ns, self.last_delay_ns)
