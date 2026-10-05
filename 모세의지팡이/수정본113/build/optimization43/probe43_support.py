"""Synthetic-only helpers; never substitute a functioning SQL backend."""
from pathlib import Path
from types import SimpleNamespace
import ast
import importlib.util
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]


def load_warehouse():
    """Load current ResultWriter unchanged; missing DuckDB can never run SQL."""
    path = ROOT / 'Part2/event_backtest/warehouse.py'
    name = 'event_backtest.warehouse'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if importlib.util.find_spec('duckdb') is None:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if not (isinstance(n, ast.Import)
                     and any(a.name == 'duckdb' for a in n.names))]
        def unavailable(*args, **kwargs):
            raise RuntimeError('SQL NOT TESTED: DuckDB is unavailable')
        module.duckdb = SimpleNamespace(connect=unavailable)
        module._validation_without_duckdb = True
        exec(compile(tree, str(path), 'exec'), module.__dict__)
    else:
        spec.loader.exec_module(module)
        module._validation_without_duckdb = False
    return module


def original_measure_consumer(original_root):
    """Extract only the actual input revision's nested timing wrapper for timing."""
    source = Path(original_root) / 'Part2/event_backtest/runner.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_chunk')
    measured = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == 'measured')
    template = ast.parse('def make(original, consumer, writer, processor):\n    pass\n')
    template.body[0].body = [measured, ast.Return(value=ast.Name(id='measured', ctx=ast.Load()))]
    namespace = {'time': time}
    exec(compile(ast.fix_missing_locations(template), str(source), 'exec'), namespace)
    return lambda original, name, writer, processor: namespace['make'](
        original, SimpleNamespace(name=name), writer, processor)


def original_market_functions(original_root):
    market_path = Path(original_root) / 'Part1/program/event_engine/market.py'
    spec = importlib.util.spec_from_file_location('_input42_market43_probe', market_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime_path = Path(original_root) / 'Part1/program/oz_engine/runtime.py'
    tree = ast.parse(runtime_path.read_text(encoding='utf-8'))
    ready = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'ready')
    import numpy as np
    namespace = {'np': np}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ready], type_ignores=[])),
                 str(runtime_path), 'exec'), namespace)
    return module, namespace['ready']


def synthetic_snapshot(seq=1, changes=(), shift=0, epoch='43', rows=650):
    import numpy as np
    from event_engine.model import FeedSnapshot
    from staff_schema import PIPE_VALUE_COLUMNS
    cols = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}
    values = np.full((rows, len(cols)), 100., dtype='<f8')
    values[:, cols['high']] = 102.
    values[:, cols['low']] = 98.
    values[:, cols['wonbi_upper']] = 103.
    values[:, cols['wonbi_lower']] = 97.
    for row, name, value in changes:
        values[row, cols[name]] = value
    return FeedSnapshot(np.arange(rows, dtype='<i8') * 60 + 1756684800 + shift,
                        np.ones(rows, dtype='<i8'), values, seq, epoch)


def make_capture(folder, snaps, symbol='XAUUSD+'):
    """Native-shaped MSP3/Wire-v2 input, explicitly not an observed market trace."""
    import struct
    import staff_schema as wire
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    start = int(snaps[0].time[-1]) * 1000
    with (folder / 'pipe_000.bin').open('wb') as stream:
        stream.write(struct.pack('<IIII', 0x4D535033, 2, len(wire.PIPE_VALUE_COLUMNS), 650))
        for i, snap in enumerate(snaps, 1):
            raw = wire.pack_v2(symbol, '1m', snap.time, snap.volume, snap.values, seq=i)
            stream.write(struct.pack('<qiI', start + i * 10000, 31, len(raw)))
            stream.write(raw)
    (folder / 'complete.txt').write_text('complete', encoding='ascii')
    (folder / 'manifest.tsv').write_text(
        'MSP3\nsymbol\t' + symbol + '\npipe_capture\tSTAFF_PIPE_V2\n'
        'pipe_observation_unit\tmilliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t'
        + str(len(snaps)) + '\n', encoding='ascii')
    return start
