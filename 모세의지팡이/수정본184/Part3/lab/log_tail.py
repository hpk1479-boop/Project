"""Bounded, read-only log transport for the web interface."""
from pathlib import Path


def read_tail(path, max_bytes=40000):
    """Read only the end of a log, including while its writer is running."""
    try:
        with Path(path).open('rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - max_bytes))
            return handle.read(max_bytes).decode('utf-8', errors='replace')
    except FileNotFoundError:
        return ''
