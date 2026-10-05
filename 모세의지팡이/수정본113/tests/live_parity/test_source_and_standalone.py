"""Fresh source-origin and standalone verification, not legacy report reuse."""
from __future__ import annotations
import ast,copy,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))


def test_part1_every_file_identical_and_no_extra_files():
    expected=json.loads((ROOT/'build/part1_immutable_sha256.json').read_text())
    current={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    # .ex5 is the MetaEditor build of THE_STAFF_OF_MOSES.mq5 (recompiled on the user PC).
    expected={k:v for k,v in expected.items() if not k.endswith('.ex5')}
    assert current==expected


def _names(node):
    if isinstance(node,(ast.FunctionDef,ast.ClassDef)):return {node.name}
    if isinstance(node,ast.Assign):return {t.id for t in node.targets if isinstance(t,ast.Name)}
    if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name):return {node.target.id}
    return set()


def _node(tree,symbol):
    if '.' in symbol:
        cls,name=symbol.split('.',1)
        body=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls).body
    else:body=tree.body;name=symbol.split(',')[0]
    return next(n for n in body if name in _names(n))


def test_replay_reference_is_the_current_part1_program():
    """LIVE_REPLAY keeps no Part1 copies; every reference module is loaded from Part1/program."""
    from live_replay import reference
    reference_dir=ROOT/'Part2/live_replay/reference'
    assert sorted(p.name for p in reference_dir.iterdir() if p.is_file())==['__init__.py','settings.json']
    for relative,wanted in reference.SOURCE_HASHES.items():
        assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==wanted,relative
    for alias,module in (('composer','manager_KIM.py'),('oz','monitor_OZ.py'),('trend','strategy_INDICATOR.py'),
                         ('indicator','strategy_INDICATOR.py'),('indicator_facts','indicator_facts.py'),
                         ('indicator_score','indicator_score.py'),
                         ('sweep','strategy_SWEEP.py'),('fvg','strategy_FVG.py'),('staff','THE STAFF OF MOSES.py')):
        loaded=Path(getattr(reference,alias).__file__)
        assert loaded.read_bytes()==(ROOT/'Part1/program'/module).read_bytes(),alias


def test_no_part2_special_copies_remain():
    assert not list((ROOT/'Part2/BACKTEST_SPECIAL').glob('BACKTEST_SPECIAL*.py'))
    for name in ('special_builtin.py','special_runtime.py','special_oz_runtime.py'):
        assert not (ROOT/'Part2/generic_backtest'/name).exists()


def _env(path):
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONPATH']=str(path)
    return env


def test_part2_new_replay_with_part1_and_without_datamanager(tmp_path):
    # Part1 is the canonical SPECIAL source: Part2 runs beside it, DataManager is not needed.
    target=tmp_path/'Part2'
    shutil.copytree(ROOT/'Part2',target,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','generic_runs'))
    shutil.copytree(ROOT/'Part1',tmp_path/'Part1',ignore=shutil.ignore_patterns('__pycache__','logs','results'))
    example=tmp_path/'synthetic.jsonl';output=tmp_path/'result.json'
    for args in [('-m','live_replay','example','--output',str(example)),
                 ('-m','live_replay','run',str(example),'--mode','실시간','--output',str(output))]:
        result=subprocess.run([sys.executable,'-B',*args],cwd=target,env=_env(target),capture_output=True,text=True,timeout=120)
        assert result.returncode==0,result.stdout+result.stderr
    payload=json.loads(output.read_text());assert payload['verification']=='SYNTHETIC' and payload['delivered_count']==2


def test_existing_part2_entry_modules_still_import(tmp_path):
    result=subprocess.run([sys.executable,'-B','-c','import generic_backtest.runner,generic_backtest.gui,live_replay.gui;print("IMPORT_OK")'],
        cwd=ROOT/'Part2',env=_env(ROOT/'Part2'),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_datamanager_standalone_import_without_part1_or_part2(tmp_path):
    target=tmp_path/'DataManager'
    shutil.copytree(ROOT/'DataManager',target,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    result=subprocess.run([sys.executable,'-B','-c','import manager.gui,manager.acquisition,manager.materialize;print("IMPORT_OK")'],
        cwd=target,env=_env(target),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_actual_gui_window_environment_gate():
    pytest.skip('ENVIRONMENT_LIMITATION: no Windows desktop/MT5; module imports are not GUI end-to-end tests')
