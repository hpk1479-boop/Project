"""Consumer timing and emission context, with one lazy accumulator per wrapper."""
import time


def measure_consumer(original, name, writer, processor):
    """Keep timing every call; publish a timing row only when the consumer runs.

    ``processor`` belongs to one run_chunk and is not cleared during dispatch.
    Emission context must be recorded before forwarding an output to the engine.
    """
    item = None

    def measured(*args, **kwargs):
        nonlocal item
        began = time.perf_counter_ns()
        try:
            if len(args) == 4:
                event, board, state, emit = args

                def output(value):
                    writer.note_emission(name, event, value)
                    emit(value)

                return original(event, board, state, output, **kwargs)
            return original(*args, **kwargs)
        finally:
            # Lazy publication preserves missing/uninvoked rows and first-call
            # order, including calls that raise. No new dict on subsequent calls.
            if item is None:
                item = processor.setdefault(name, {'ns': 0, 'calls': 0})
            item['ns'] += time.perf_counter_ns() - began
            item['calls'] += 1

    return measured
