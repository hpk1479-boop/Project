"""Cooperative cancellation shared by CLI, GUI, builders and replay workers."""
from pathlib import Path
import datetime as dt


class Cancelled(Exception):
    pass


def requested(check):
    try:
        return bool(check())
    except Cancelled:
        return True


def file_check(path):
    path = Path(path)
    def check():
        if path.exists():
            raise Cancelled('중단됨(부분 결과)')
    return check


def utc_ms(value):
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat(timespec='milliseconds')


def coverage(chunks):
    """Keep disjoint worker coverage explicit; never imply unprocessed gaps ran."""
    return [{'start': utc_ms(r['processed_start_ms']),
             'last_observation': utc_ms(r['processed_end_ms'])}
            for r in sorted(chunks, key=lambda r: r.get('task_start',''))
            if r.get('processed_start_ms') is not None]
