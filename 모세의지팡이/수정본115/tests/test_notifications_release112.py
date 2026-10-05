"""Current display changes survive release selection and source-free loading.

Synthetic strategy events only: no MT5, Telegram delivery, or live engines.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.runtime_bundle import build_code_bundle

CODE = {'Part1/program/strategy_recipe/alerts.py',
        'Part1/program/event_economy_host.py', 'Part3/lab/unified_settings.py'}
ASSETS = ('Part3/web/index.html', 'Part3/web/unified.js', 'Part3/web/style.css')

PROBE = r'''
import copy, datetime as dt, hashlib, json, os, socket, sys
from pathlib import Path
from types import SimpleNamespace as NS
root, tooling, mode, work = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
physical_sources = [name for _,_,names in os.walk(root) for name in names if name.endswith(('.py','.pyw'))]
def no_network(*args,**kwargs):raise AssertionError('Real network prohibited')
socket.create_connection=no_network
socket.socket.connect=socket.socket.connect_ex=socket.socket.sendto=no_network
sys.path[:0]=[tooling,str(root),str(root/'Part3'),str(root/'Part2'),str(root/'Part1/program')]
bundle=None
if mode=='bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle=RuntimeBundle(root).install()
try:
    from lab import server, unified_settings, unified_backtest, storage
    from lab.ai_compiler import compile_ai
    from lab.ai.schema import contract_context
    from strategy_recipe.alerts import render_strategy_alert
    from strategy_recipe.contract import execution_plan
    from strategy_recipe.registry import builtin_entries, units
    from strategy_recipe.runtime import IntentMachine
    from event_economy_host import format_indicator_title, EconomyWorker, KST

    # Exercise the current Settings GET route with only its unrelated inputs isolated.
    config=work/'config.txt'
    config.write_text('TELEGRAM_TOKEN=\nTELEGRAM_CHAT_ID=\nTELEGRAM_COMMAND_CHAT_IDS=\nSYMBOLS=XAUUSD+\n',encoding='utf-8')
    unified_settings.LIVE_CONFIG=config
    unified_backtest._part2=lambda:(NS(settings=lambda:{}),)
    unified_settings._machine_roots=lambda:NS(load_live_root=lambda:None)
    storage.connections=lambda:{}
    server.ai_settings=lambda:{}
    replies=[]
    handler=server.Handler.__new__(server.Handler)
    handler.path='/api/mo/settings'
    handler.server=NS(last_seen=0)
    handler.authorized=lambda:True
    handler.send=lambda status,body,ctype='application/json':replies.append((status,body,ctype))
    handler.do_GET()
    assert len(replies)==1 and replies[0][0]==200,replies
    revision=replies[0][1]['app_revision']
    assert type(revision) is int and revision>0,revision

    served={}
    for name in ('index.html','unified.js','style.css'):
        replies.clear();handler.path='/'+name;handler.do_GET()
        assert len(replies)==1 and replies[0][0]==200 and isinstance(replies[0][1],bytes),replies
        served[name]=hashlib.sha256(replies[0][1]).hexdigest()

    rows=[]
    for name,entry in builtin_entries().items():
        raw=copy.deepcopy(entry['recipe']['strategy_intent']);raw['symbols']=['XAUUSD+']
        plan=execution_plan(raw)['meaning']
        for number,unit in enumerate(units(plan)):
            for direction in ('LONG','SHORT'):
                machine=IntentMachine(unit,'XAUUSD+',direction)
                machine.source_tf=next((tf for step in unit['steps'] for tf in step['tfs'] if tf not in ('SOURCE','FINAL')),None)
                machine.final_tf='1m'
                event={'direction':direction,'symbol':'XAUUSD+','source_tf':'1m',
                    'grade':'A','trigger_name':'BO_BREAK','indicators_text':'PRICE·RSI·STO',
                    'current_price':2400.25,'validation_mode':unit['final'].get('validation_mode'),
                    'trigger_mode':unit['final'].get('trigger_mode')}
                previous=copy.deepcopy((machine.checkpoint(),machine.meaning,event))
                text=render_strategy_alert(entry['name'],machine,event)
                assert all(value not in text for value in ('• 조건:','• 등급:','• 트리거:','BO_BREAK','A급')),text
                assert '• 현재 가격: 2,400.25' in text and '• 지표: PRICE·RSI·STO' in text
                assert (machine.checkpoint(),machine.meaning,event)==previous
                rows.append({'id':name,'branch':number,'direction':direction,'text':text,'meaning':unit})

    recipe={'schema_version':2,'base':'AI','name':'새로운 전략',
        'strategy_intent':{'symbols':['XAUUSD+'],'direction':'LONG',
            'steps':[{'kind':'MA_PRICE_TOUCH','tfs':['5m'],'ma_family':'EMA','slow_period':50}],
            'final':{'kind':'OZ','tfs':['1m'],'validation_mode':'NORMAL','trigger_mode':'OZ'}}}
    context=contract_context();assert 'XAUUSD+' in context['symbols']
    before=copy.deepcopy(recipe)
    source=compile_ai(recipe,'Test_SPECIAL001.py')
    namespace={};exec(compile(source,'Test_SPECIAL001.py','exec'),namespace)
    machine=IntentMachine(namespace['_INTENT_PLAN'],'XAUUSD+','LONG')
    generated_text=render_strategy_alert(recipe['name'],machine,{
        'direction':'LONG','symbol':'XAUUSD+','source_tf':'1m','grade':'B','trigger_name':'BO_BREAK',
        'indicators_text':'PRICE·RSI','current_price':2400.25})
    assert all(value not in generated_text for value in ('• 조건:','• 등급:','• 트리거:','BO_BREAK','B급'))
    assert recipe==before

    titles=['CPI m/m','Core CPI y/y','Advance GDP q/q','Non-Farm Employment Change',
        'FOMC Meeting Minutes','Unemployment Rate','Unknown indicator m/m']
    translations={name:format_indicator_title(name) for name in titles}
    assert translations['CPI m/m']=='소비자물가지수(CPI) · 전월 대비',translations
    assert translations['Core CPI y/y']=='근원 소비자물가지수(Core CPI) · 전년 대비',translations
    worker=EconomyWorker.__new__(EconomyWorker)
    events=[{'country':'USD','impact':'High','date':'2026-10-05T21:30:00+09:00','title':name}
            for name in titles[:4]]
    briefing=worker.build_briefing(dt.date(2026,10,5),events)
    for name in titles[:4]:assert translations[name] in briefing
    print(json.dumps({'revision':revision,'served':served,'alerts':rows,
        'generated_source_hash':hashlib.sha256(source.encode()).hexdigest(),
        'generated_alert':generated_text,'translations':translations,'briefing':briefing,
        'physical_source_count':len(physical_sources)},ensure_ascii=False))
finally:
    if bundle:bundle.uninstall()
'''


def run_probe(root, work, mode):
    result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROBE,
        str(root), str(TOOLS), mode, str(work)], cwd=work, capture_output=True,
        text=True, encoding='utf-8', errors='replace', timeout=90,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-10000:] + result.stdout[-2000:]
    return json.loads(result.stdout)


@pytest.fixture(scope='module')
def release_evidence(tmp_path_factory):
    work = tmp_path_factory.mktemp('display_release112')
    payload = work / '모세트레이딩시스템'
    selected = list(builder.source_paths(ROOT))
    manifest = resources.discover_resources(ROOT, selected)
    code = builder.prepare_data(ROOT, payload, selected)
    compiled = build_code_bundle(ROOT, payload / 'runtime/code.bundle', paths=code)
    resources.validate_payload(payload, manifest, code)
    developer = run_probe(ROOT, work, 'developer')
    installed = run_probe(payload, work, 'bundle')
    # Moving an installed release must retain its source revision and output.
    relocated = work / 'moved' / '수정본999'
    relocated.parent.mkdir()
    payload.rename(relocated)
    moved = run_probe(relocated, work, 'bundle')
    return {'selected': {rel.as_posix() for rel, _ in selected}, 'compiled': compiled,
        'payload': relocated, 'developer': developer, 'installed': installed, 'moved': moved}


def test_current_sources_and_assets_are_included_without_python_source(release_evidence):
    evidence = release_evidence
    assert CODE <= set(evidence['compiled']['entries'])
    assert set(ASSETS) <= evidence['selected']
    assert evidence['developer']['physical_source_count'] > 0
    assert evidence['installed']['physical_source_count'] == evidence['moved']['physical_source_count'] == 0
    for relative in ASSETS:
        assert (ROOT / relative).read_bytes() == (evidence['payload'] / relative).read_bytes()


@pytest.mark.parametrize('key', ['revision', 'served', 'alerts', 'generated_source_hash',
                                'generated_alert', 'translations', 'briefing'])
def test_developer_source_free_release_and_relocated_release_match(release_evidence, key):
    evidence = release_evidence
    assert evidence['developer'][key] == evidence['installed'][key] == evidence['moved'][key]
    if key == 'revision':
        # The reported revision is the source folder's number, whichever revision this is.
        match = re.fullmatch(r'수정본([1-9][0-9]*)', ROOT.name)
        if match: assert evidence['developer'][key] == int(match[1])
    if key == 'alerts':
        # Every shipped SPECIAL recipe file renders, whatever its number or final kind.
        shipped = {path.name.split('.')[0] for path in (ROOT / 'Part1/program/SPECIAL').glob('SPECIAL*.recipe.json')}
        assert shipped and {row['id'] for row in evidence['installed'][key]} == shipped
