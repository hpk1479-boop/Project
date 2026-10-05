"""Web bridge to Part1's public engine process and shutdown API."""
from __future__ import annotations
from pathlib import Path
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def process_api():
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from Part1 import engine_processes
    return engine_processes


def current_process_identity():
    return process_api().current_process_identity()


def stop_all_engines():
    result = process_api().stop_all_engines()
    if not result['ok']:
        raise RuntimeError(result['message'])
    return result


def force_stop_engines():
    root = Path(__file__).resolve().parents[2] / 'Part1'
    result = process_api().force_all_engines(root=root)
    if not result['ok']:
        raise RuntimeError(result['message'])
    return result


# Preserve these diagnostic entry points for existing callers and tests.
def _arguments(command):
    return process_api().parse_engine_arguments(command)


def _is_engine(command):
    return process_api().is_engine_command(command)


def find_engines():
    return {row['pid']: int(row['created']) for row in process_api().find_engine_instances()}


def _hold_process(pid, created):
    return process_api().hold_process_identity(pid, created)
