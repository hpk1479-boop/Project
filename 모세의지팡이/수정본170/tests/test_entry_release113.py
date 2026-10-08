"""Developer and source-free moved release use the same entry/AI contracts."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'통합설치'
sys.path.insert(0,str(TOOLS))
from releasekit import builder,resources
from releasekit.runtime_bundle import build_code_bundle

CODE={f'Part2/event_backtest/{name}.py' for name in ('virtual_entry','virtual_contract','virtual_facts','virtual_rules','virtual_source','virtual_msd','warehouse')}
CODE|={'Part1/program/indicator_facts.py','Part1/program/oz_engine/market.py',
    'Part1/program/event_signal_context.py','Part1/program/event_composer_domain.py',
    'Part1/program/watch_orchestrator.py','Part1/program/event_composition.py',
    'Part1/program/oz_engine/profile.py','Part1/program/strategy_recipe/port.py','Part1/program/strategy_recipe/runtime.py',
    'Part1/program/strategy_recipe/state_judge.py','Part2/event_backtest/virtual_defaults.py',
    'Part3/lab/ai/backtest_commands.py','Part3/lab/ai/research_backtest.py',
    'Part3/lab/ai/research_interpreter.py','Part3/lab/unified_backtest.py','common_ai/security.py'}
ASSETS=('Part3/web/index.html','Part3/web/unified.js','Part3/web/style.css',
    'Part3/web/ai_chat.js','Part3/web/ai_display.js','Part3/web/ai_editor.js','Part3/web/ai_editor.css','Part3/web/backtest_dashboard.js',
    '매뉴얼/모세_사용자_매뉴얼.pdf')

PROBE=r'''
import hashlib,json,os,socket,sys
from pathlib import Path
from types import SimpleNamespace as NS
root,tooling,mode,work=Path(sys.argv[1]),sys.argv[2],sys.argv[3],Path(sys.argv[4])
physical=[name for _,_,names in os.walk(root) for name in names if name.endswith(('.py','.pyw'))]
def deny(*args,**kw):raise AssertionError('Actual network prohibited')
socket.create_connection=deny
socket.socket.connect=socket.socket.connect_ex=socket.socket.sendto=deny
sys.path[:0]=[tooling,str(root),str(root/'Part3'),str(root/'Part2'),str(root/'Part1/program')]
bundle=None
if mode=='bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle=RuntimeBundle(root).install()
try:
    import numpy as np
    from event_backtest.virtual_entry import VirtualEntry
    from event_backtest.virtual_contract import immediate_virtual_entry,normalize_virtual_entry,schema
    from event_backtest import ui_model
    from lab.ai import backtest_commands,research_interpreter
    from lab import unified_settings
    columns=('open','high','low','close','hma_6','hma_17')
    values=np.array([[100.,102.,98.,101.,99.,100.]]*20)
    feed=NS(time=np.arange(20,dtype='int64')*60,values=values,columns=columns)
    oz={'mode':'CONFIRM','conditions':[{'kind':'CANDLE_CLOSE'},{'kind':'MA_POSITION','family':'HMA','period':6}],
        'stop':{'kind':'OZ_B0'}}
    policies={'OZ':normalize_virtual_entry(oz),'SIGNAL':immediate_virtual_entry()}
    policies['SIGNAL']['stop']['period']=2
    base={'signal_id':'one','strategy':'SPECIAL1','symbol':'TEST','tf':'1m',
        'direction':'LONG','time_ms':960001,'signal_price':100.,'b0_price':95.}
    outcomes={}
    for source in ('OZ','SIGNAL'):
        engine=VirtualEntry([{**base,'signal_source':source}],config=policies[source])
        engine.observe(1020000,{'1m':feed},end_ms=1140000)
        rows,details=engine.results()
        outcomes[source]={'summary':rows,'details':details}
        assert rows[0]['entries']==1,rows
        assert details[0]['entry_time']==(1020000 if source=='OZ' else 960001),details
    # HMA6 below HMA17: the recipe's shared state rule finds the environment broken.
    broken=normalize_virtual_entry({**oz,'filters':[{'kind':'ENVIRONMENT','condition':'MA_STATE','family':'HMA',
        'fast':6,'slow':17,'tf':'SIGNAL','bar_state':'CLOSED'}]})
    passed=VirtualEntry([{**base,'signal_source':'OZ'}],config=broken)
    passed.observe(1020000,{'1m':feed},end_ms=1140000)
    assert passed.results()[0][0]['pass_env']==1
    ui_model.UI_PATH=work/(mode+'-backtest.json')
    ui_model.save_virtual_entry(policies['OZ'],'SPECIAL1',ui_model.UI_PATH)
    loaded=ui_model.load(ui_model.UI_PATH)
    restored=loaded['virtual_entry']
    values={**restored,**{group:[{key:value for key,value in row.items() if key!='recipe_index'}
                              for row in restored[group]] for group in ('conditions','filters')}}
    assert values==policies['OZ'] and loaded['virtual_entry_target']=='SPECIAL1'
    command=backtest_commands.command_schema()
    research=research_interpreter.response_schema('BUILD')
    assert command['properties']['request']['properties']['virtual_entry']['anyOf'][0]==schema()
    revision=unified_settings.app_revision()
    assert type(revision) is int and revision>0,revision
    assets={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
        ('Part3/web/index.html','Part3/web/unified.js','Part3/web/style.css','Part3/web/ai_chat.js',
         'Part3/web/ai_display.js','Part3/web/ai_editor.js','Part3/web/ai_editor.css','Part3/web/backtest_dashboard.js',
         '매뉴얼/모세_사용자_매뉴얼.pdf')}
    print(json.dumps({'outcomes':outcomes,'contract':schema(),'command':command,'research':research,
        'revision':revision,'assets':assets,'physical_source_count':len(physical)},ensure_ascii=False))
finally:
    if bundle:bundle.uninstall()
'''


def probe(root,work,mode):
    result=subprocess.run([sys.executable,'-I','-B','-X','utf8','-c',PROBE,
        str(root),str(TOOLS),mode,str(work)],cwd=work,capture_output=True,text=True,
        encoding='utf-8',errors='replace',timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stderr[-9000:]+result.stdout[-1000:]
    return json.loads(result.stdout)


def canonical_schema(value,key=''):
    # JSON Schema's enum/required/combinator order carries no meaning. The
    # contract builds some of them from sets, independently in each process.
    if isinstance(value,dict):return {k:canonical_schema(v,k) for k,v in value.items()}
    if isinstance(value,list):
        rows=[canonical_schema(item) for item in value]
        return sorted(rows,key=lambda item:json.dumps(item,sort_keys=True,ensure_ascii=False)) if key in {'enum','required','anyOf','oneOf','allOf','type'} else rows
    return value


def test_entry_contract_results_and_all_assets_survive_source_free_release_move(tmp_path):
    payload=tmp_path/'모세트레이딩시스템'
    selected=list(builder.source_paths(ROOT))
    manifest=resources.discover_resources(ROOT,selected)
    paths=builder.prepare_data(ROOT,payload,selected)
    compiled=build_code_bundle(ROOT,payload/'runtime/code.bundle',paths=paths)
    resources.validate_payload(payload,manifest,paths)
    assert CODE<=set(compiled['entries'])
    assert set(ASSETS)<={rel.as_posix() for rel,_ in selected}
    developer=probe(ROOT,tmp_path,'developer')
    installed=probe(payload,tmp_path,'bundle')
    moved=tmp_path/'moved'/'수정본999';moved.parent.mkdir();payload.rename(moved)
    relocated=probe(moved,tmp_path,'bundle')
    # The reported revision is the source folder's number, whichever revision this is.
    match=re.fullmatch(r'수정본([1-9][0-9]*)',ROOT.name)
    if match:assert developer['revision']==int(match[1])
    assert developer.pop('physical_source_count')>0
    assert installed.pop('physical_source_count')==relocated.pop('physical_source_count')==0
    for result in (developer,installed,relocated):
        for key in ('contract','command','research'):result[key]=canonical_schema(result[key])
    assert developer==installed==relocated
    (tmp_path/'release_evidence.json').write_text(json.dumps({
        'passed':True,'revision':developer['revision'],'compiled_modules':len(compiled['entries']),
        'required_code':sorted(CODE),'assets':developer['assets'],
        'physical_release_python_sources':0,'developer_installed_moved_equal':True,
        'outcomes':developer['outcomes']},ensure_ascii=False,indent=2),encoding='utf-8')
