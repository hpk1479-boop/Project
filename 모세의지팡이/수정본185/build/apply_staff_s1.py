"""One-shot byte-preserving extraction from the frozen S0 source."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
OUT = ROOT / '검증결과/staff_s1'


def definitions(raw):
    lines = raw.splitlines(keepends=True)
    return {n.name: (n, b''.join(lines[n.lineno - 1:n.end_lineno]))
            for n in ast.parse(raw).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def main():
    staff = PROGRAM / 'THE STAFF OF MOSES.py'
    raw = staff.read_bytes()
    defs = definitions(raw)
    owners = {
        'indicator_facts.py': ('add_ema_derived', 'add_atr14_feature', 'add_supertrend', 'add_mt5_basis_slopes'),
        'monitor_OZ.py': tuple('add_' + x + '_band_state_features' for x in ('price', 'rsi', 'sto', 'di')),
        'strategy_FVG.py': ('add_fvg_features',),
    }
    moved = []
    for filename, names in owners.items():
        path = PROGRAM / filename
        dest = path.read_bytes()
        assert not set(names).intersection(definitions(dest)), 'Already migrated'
        blocks = b'\n\n# S1: pure legacy STAFF facts, function bodies preserved verbatim.\n'
        if filename == 'indicator_facts.py':
            blocks += b'ATR14_LENGTH = 14\n\n'
        if filename == 'monitor_OZ.py':
            blocks += b'import numpy as np\n\n'
        for name in names:
            block = defs[name][1]
            blocks += block + b'\n\n'
            raw = raw.replace(block, b'', 1)
            moved.append({'function': name, 'from': 'THE STAFF OF MOSES.py', 'to': filename,
                          'source_block_sha256': hashlib.sha256(block).hexdigest()})
        if filename == 'indicator_facts.py':
            blocks += b'ema21_slope = add_ema_derived\nbasis_slopes = add_mt5_basis_slopes\nlegacy_staff_supertrend = add_supertrend\n\n'
            # Place registration before FACT_INPUTS is computed.
            marker = b'# --- OPEN based'
            reg = (b'@fact("ATR14_GENERAL", "tr", description="STAFF ATR14: TR[0] ewm seed")\n'
                   b'def ATR14_GENERAL(frame, tr):\n    return rma(tr, 14)\n\n\n')
            assert marker in dest
            dest = dest.replace(marker, reg + marker, 1)
        elif filename == 'strategy_FVG.py':
            blocks += b'legacy_staff_fvg_features = add_fvg_features\n\n'
            old = definitions(dest)['_wilder_atr'][1]
            new = old.replace(b'def _wilder_atr(', b'def FVG_WILDER_ATR(', 1)
            dest = dest.replace(old, new + b'\n\n_wilder_atr = FVG_WILDER_ATR\n', 1)
        # Insert before main guard; imports never start service loops.
        marker = b'if __name__ == "__main__":'
        if marker in dest:
            dest = dest.replace(marker, blocks + marker, 1)
        else:
            dest += blocks
        path.write_bytes(dest)
    imports = b'\n# Pure Fact owners. Public legacy function names remain compatible.\n'
    for filename, names in owners.items():
        imports += ('from ' + Path(filename).stem + ' import ' + ', '.join(names) + '\n').encode()
    marker = b'# Python-only derived features (same public column names as old MOSES)'
    offset = raw.index(b'\n', raw.index(marker)) + 1
    raw = raw[:offset] + imports + raw[offset:]
    staff.write_bytes(raw)
    # Keep the original wonbi function and constants byte-for-byte in both common modules.
    for part in ('Part2', 'Part3'):
        common = ROOT / part / 'calculations/common.py'
        code = common.read_bytes()
        local = definitions(code)
        names = tuple(name for group in owners.values() for name in group if name in local)
        for name in names:
            code = code.replace(local[name][1], b'', 1)
        exports = b'\n# S1: use the exact LIVE Fact functions instead of a second implementation.\nfrom generic_backtest.live_source import load_live_program_module as _load_fact_owner\n'
        for filename, group in owners.items():
            selected = [name for name in group if name in names]
            if not selected:
                continue
            var = '_' + Path(filename).stem
            exports += f'{var} = _load_fact_owner("{Path(filename).stem}")\n'.encode()
            for name in selected:
                exports += f'{name} = {var}.{name}\n'.encode()
        common.write_bytes(code + exports)
    loader = ROOT / 'Part3/generic_backtest/live_source.py'
    assert not loader.exists()
    loader.write_bytes((ROOT / 'Part2/generic_backtest/live_source.py').read_bytes())
    (OUT / 'extraction.json').write_text(json.dumps(moved, indent=2), encoding='utf-8')
    print('Moved', len(moved), 'functions; wonbi untouched')


if __name__ == '__main__':
    main()
