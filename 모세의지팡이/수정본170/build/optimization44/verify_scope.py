"""Check input preservation and the exact virtual-entry-only operating delta."""
from pathlib import Path
from zipfile import ZipFile
import argparse
import ast
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '검증결과/가상진입최적화44'
OPERATING = 'Part2/event_backtest/virtual_entry.py'
TESTS = {'tests/test_virtual_optimization44.py', 'tests/test_virtual_recorded44.py'}
REPORT = '수정본44_가상진입최적화_완료보고서.md'


def ignored(name):
    p = Path(name)
    return any(x in ('검증결과', '__pycache__', '.pytest_cache') for x in p.parts) or p.suffix == '.pyc'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized_delta(text):
    """Undo only the declared optimization syntax in memory for an AST scope check."""
    changes = [
        ("                targets={rr:entry+(1 if a['direction']=='LONG' else -1)*rr*risk for rr in RATIOS}\n", ''),
        ("trade.update(status='ENTERED',entry_time=bar_ms,entry_price=entry,risk=risk,targets=targets)",
         "trade.update(status='ENTERED',entry_time=bar_ms,entry_price=entry,risk=risk)"),
        ("target=trade['targets'][rr]", "target=trade['entry_price']+(1 if long else -1)*rr*trade['risk']"),
        ("                if len(trade['exits'])==len(RATIOS):\n                    self.open.remove(trade)\n                    del trade['targets']  # Completed trades no longer need the extra cache.\n",
         "                if len(trade['exits'])==len(RATIOS):self.open.remove(trade)\n"),
        ("            entries=[t for t in trades if t['entry_time'] is not None]\n            cross=sum(t['status']=='PASS_CROSS' for t in trades);risk=sum(t['status']=='PASS_RISK' for t in trades)\n            waiting=sum(t['status']=='WAITING' for t in trades)\n            for rr in RATIOS:\n",
         "            for rr in RATIOS:\n                entries=[t for t in trades if t['entry_time'] is not None]\n"),
        ("                summaries.append(dict(strategy=strategy,rr=rr,alerts=len(trades),entries=len(entries),passes=cross+risk,\n",
         "                cross=sum(t['status']=='PASS_CROSS' for t in trades);risk=sum(t['status']=='PASS_RISK' for t in trades)\n                summaries.append(dict(strategy=strategy,rr=rr,alerts=len(trades),entries=len(entries),passes=cross+risk,\n"),
        ("pass_cross=cross,pass_risk=risk,waiting=waiting,", "pass_cross=cross,pass_risk=risk,waiting=sum(t['status']=='WAITING' for t in trades),"),
        ("                symbol_feeds={}\n", ''),
        ("                    if symbol not in symbol_feeds:\n                        symbol_feeds[symbol]={tf:v for (sym,tf),v in feeds.items() if sym==symbol}\n                    current.observe(stamp,symbol_feeds[symbol],end_ms=end)\n",
         "                    current.observe(stamp,{tf:v for (sym,tf),v in feeds.items() if sym==symbol},end_ms=end)\n"),
    ]
    for old, new in changes:
        assert text.count(old) == 1, ('Unexpected optimization syntax', old)
        text = text.replace(old, new)
    return ast.dump(ast.parse(text), include_attributes=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-zip', type=Path, required=True)
    args = parser.parse_args()
    with ZipFile(args.input_zip) as archive:
        before = {name.split('/', 1)[1]: sha(archive.read(name)) for name in archive.namelist()
                  if '/' in name and not name.endswith('/') and not ignored(name.split('/', 1)[1])}
        entry = next(n for n in archive.namelist() if n.endswith('/' + OPERATING))
        original_text = archive.read(entry).decode('utf-8-sig').replace('\r\n', '\n')
    after = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in ROOT.rglob('*')
             if p.is_file() and not ignored(p.relative_to(ROOT).as_posix())}
    modified = sorted(n for n in before.keys() & after.keys() if before[n] != after[n])
    missing = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    assert modified == [OPERATING], modified
    assert not missing, missing
    assert TESTS.issubset(added), added
    for name in added:
        assert name in TESTS | {REPORT} or name.startswith('build/optimization44/'), name
    new_text = (ROOT / OPERATING).read_text(encoding='utf-8-sig')
    assert normalized_delta(new_text) == ast.dump(ast.parse(original_text), include_attributes=False)
    for name in [OPERATING, *TESTS, *[n for n in added if n.endswith('.py')]]:
        compile((ROOT / name).read_text(encoding='utf-8-sig'), name, 'exec')
    protected = ['Part1/program/event_engine/facts.py', 'Part1/program/event_engine/market.py',
                 'Part1/program/oz_engine/runtime.py', 'Part2/event_backtest/instrumentation.py',
                 'Part2/event_backtest/runner.py', 'Part2/event_backtest/settings.py',
                 'Part2/event_backtest/virtual_source.py', 'Part2/event_backtest/virtual_msd.py']
    result = {'input_archive': args.input_zip.name, 'input_sha256': sha(args.input_zip.read_bytes()),
              'modified_existing_files': modified, 'missing_files': missing, 'added_files': added,
              'excluded_from_copy': ['previous verification folders', 'Python bytecode and pytest caches'],
              'source_hashes': {OPERATING: {'input43': before[OPERATING], 'revision44': after[OPERATING]}},
              'authorized_ast_delta_only': True, 'source_compilation_ok': True,
              'part1_files_unchanged': all(before[n] == after[n] for n in before if n.startswith('Part1/')),
              'part3_files_preserved_no_execution': all(before[n] == after[n] for n in before if n.startswith('Part3/')),
              'protected_sources_unchanged': {n: before[n] == after[n] for n in protected},
              'hash_buffer_candidate_not_applied': before[protected[5]] == after[protected[5]]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'scope.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    diff = ''.join(difflib.unified_diff(original_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                     fromfile='input43/' + OPERATING, tofile='revision44/' + OPERATING))
    (OUT / 'changes.diff').write_text(diff, encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
