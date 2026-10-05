"""Current summary assets and meaning survive the real source-free payload.

Temporary packaging, real static HTTP handlers, and synthetic editor contracts
are used without an installer, provider, MT5, or personal setting writes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.runtime_bundle import build_code_bundle

ASSETS = ('Part3/web/ai_editor.js', 'Part3/web/ai_editor.css',
          'Part3/web/ai_display.js', 'Part3/web/index.html')
CODE = {'Part3/lab/server.py', 'Part3/lab/ai/research_editor.py',
        'Part3/lab/ai/schema.py', 'Part1/program/strategy_recipe/contract.py'}


def synthetic_strategies():
    first = {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'symbols': ['XAUUSD+'], 'direction': 'SHORT', 'order_mode': 'SIMULTANEOUS',
        'steps': [{'kind': 'CANDLE_STATE', 'tfs': ['1h'], 'side': 'BULL', 'bar_state': 'FORMING'}],
        'final_conditions': [{'kind': 'CANDLE_STATE', 'tfs': ['15m'], 'side': 'BEAR', 'bar_state': 'CLOSED'}],
        'persistent': False, 'lifecycle': {'expires': {'seconds': 3600},
            'replace': {'scope': 'SYMBOL_DIRECTION'}, 'first_success': True,
            'snapshots': {'atr_anchor': {'tf': '15m', 'field': 'ATR14', 'bar_state': 'CLOSED'}}},
        'time_filters': {'MAIN_LONDON': '1600-1800'},
        'final_time_filters': {'MAIN_NEWYORK': {'enabled': True, 'start': '2100', 'end': '2400'}},
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '합성 배포 검증'}
    second = copy.deepcopy(first)
    second['interpretation'].update({'direction': 'BOTH', 'steps': [],
        'final': {'kind': 'NOTIFY'}, 'branches': [
            {'direction': 'LONG', 'steps': [{'kind': 'CANDLE_STATE', 'tfs': ['4h'],
                'side': 'BULL', 'bar_state': 'CLOSED'}], 'final': {'kind': 'NOTIFY'},
             'persistent': True, 'final_conditions': []},
            {'direction': 'SHORT', 'steps': [{'kind': 'CANDLE_STATE', 'tfs': ['1h'],
                'side': 'BEAR', 'bar_state': 'FORMING'}], 'final': {'kind': 'NOTIFY'}}]})
    return [first, second]


PROBE = r'''
import copy, hashlib, json, os, socket, sys
from pathlib import Path
root, tooling, mode, fixture = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
def no_network(*args, **kwargs):
    raise AssertionError('Summary release probe must not access a real network')
socket.create_connection = no_network
socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network
sys.path[:0] = [tooling,str(root),str(root/'Part3'),str(root/'Part2'),str(root/'Part1/program')]
bundle = None
if mode == 'bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle = RuntimeBundle(root).install()
try:
    from lab import server
    from lab.ai import research_editor, schema
    from strategy_recipe.contract import execution_plan
    inputs = json.loads(fixture.read_text('utf-8'))
    served = {}
    for name in ('ai_editor.js','ai_editor.css','ai_display.js','index.html'):
        handler = server.Handler.__new__(server.Handler)
        handler.path = '/'+name
        replies = []
        handler.send = lambda status,body,ctype='application/json':replies.append((status,body,ctype))
        handler.do_GET()
        assert len(replies) == 1
        status,body,ctype = replies[0]
        assert status == 200 and isinstance(body,bytes), (name,status,body)
        served[name] = {'status':status,'sha256':hashlib.sha256(body).hexdigest(),
                       'bytes':len(body),'content_type':ctype}
    snapshot = {'today':'2026-10-05','options':{'symbols':['XAUUSD+']}}
    meanings, plans, contracts = [], [], []
    before = copy.deepcopy(inputs)
    for value in inputs:
        contract = research_editor.contract(snapshot,value)
        # Developer test strategies intentionally do not ship; only the
        # corresponding selector enum is outside this summary comparison.
        contract['schema']['properties']['interpretation']['anyOf'][0]['properties']['preset']['enum'] = []
        checked,errors = research_editor.check_strategy(value)
        assert errors == [],errors
        assert checked['interpretation'] == value['interpretation']
        meanings.append(checked)
        plans.append(execution_plan(checked['interpretation'],schema.contract_context()))
        contracts.append(contract)
    assert inputs == before
    print(json.dumps({'served':served,'meanings':meanings,'plans':plans,'contracts':contracts,
        'physical_python_source_exists':os.path.isfile(root/'Part3/lab/server.py')},ensure_ascii=False))
finally:
    if bundle is not None:bundle.uninstall()
'''


def run_probe(root,work,mode):
    result = subprocess.run([sys.executable,'-I','-B','-X','utf8','-c',PROBE,
        str(root),str(TOOLS),mode,str(work/'synthetic_strategies.json')],
        cwd=work,capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode == 0,result.stderr[-12000:] + result.stdout[-2000:]
    return json.loads(result.stdout.strip())


def meaning_of_schema(value):
    if isinstance(value,list):return [meaning_of_schema(item) for item in value]
    if not isinstance(value,dict):return value
    result = {key:meaning_of_schema(item) for key,item in value.items()}
    if isinstance(result.get('enum'),list):
        result['enum'] = sorted(result['enum'],key=lambda item:json.dumps(item,sort_keys=True))
    return result


@pytest.fixture(scope='module')
def release_payload(tmp_path_factory):
    work = tmp_path_factory.mktemp('summary_release109')
    payload = work/'installed'
    (work/'synthetic_strategies.json').write_text(json.dumps(synthetic_strategies(),ensure_ascii=False),encoding='utf-8')
    config = ROOT/'Part1/program/config.txt'
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    sources = list(builder.source_paths(ROOT))
    manifest = resources.discover_resources(ROOT,sources)
    code = builder.prepare_data(ROOT,payload,sources)
    compiled = build_code_bundle(ROOT,payload/'runtime/code.bundle',paths=code)
    resources.validate_payload(payload,manifest,code)
    developer = run_probe(ROOT,work,'developer')
    installed = run_probe(payload,work,'bundle')
    assert hashlib.sha256(config.read_bytes()).hexdigest() == before
    return {'work':work,'payload':payload,'sources':sources,'code':code,
            'compiled':compiled,'developer':developer,'installed':installed}


def test_latest_summary_assets_are_selected_and_served_by_source_free_application(release_payload):
    evidence = release_payload
    selected = {path.as_posix() for path,_ in evidence['sources']}
    assert set(ASSETS) <= selected
    assert CODE <= set(evidence['compiled']['entries'])
    for relative in ASSETS:
        current = (ROOT/relative).read_bytes()
        assert (evidence['payload']/relative).read_bytes() == current
        served = evidence['installed']['served'][Path(relative).name]
        assert served['sha256'] == hashlib.sha256(current).hexdigest()
        assert served['bytes'] == len(current) and served['status'] == 200
    html = (evidence['payload']/'Part3/web/index.html').read_text('utf-8')
    assert 'src="ai_editor.js"' in html and 'href="ai_editor.css"' in html
    assert evidence['installed']['served'] == evidence['developer']['served']
    assert evidence['developer']['physical_python_source_exists']
    assert not evidence['installed']['physical_python_source_exists']


def test_editor_contract_and_canonical_meanings_match_without_provider_or_source(release_payload):
    evidence = release_payload
    for key in ('meanings','plans','contracts'):
        assert meaning_of_schema(evidence['installed'][key]) == meaning_of_schema(evidence['developer'][key]),key
    assert evidence['installed']['meanings'] == synthetic_strategies()
    first,second = evidence['installed']['meanings']
    assert first['interpretation']['final']['validation_mode'] == 'BLIND'
    assert first['interpretation']['final']['trigger_mode'] == 'BREAKER'
    assert first['interpretation']['steps'][0]['bar_state'] == 'FORMING'
    assert first['interpretation']['final_conditions'][0]['bar_state'] == 'CLOSED'
    assert len(second['interpretation']['branches']) == 2
    assert second['interpretation']['branches'][0]['persistent'] is True
    assert second['interpretation']['branches'][0]['final_conditions'] == []
    assert not [path for path in evidence['payload'].rglob('*') if path.is_file() and path.suffix.lower() in {'.py','.pyw','.mq5','.mqh'}]


def test_packaged_javascript_explains_retained_meanings_without_mutation(release_payload):
    node = shutil.which('node')
    if not node:pytest.skip('Node is only needed for the JavaScript presentation probe')
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=process.argv[1],fixtures=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const snapshot=JSON.stringify(fixtures),box={window:{},document:{createElement(){throw Error('No DOM rendering in the release semantics probe');}}};
for(const name of ['ai_display.js','ai_editor.js'])vm.runInNewContext(fs.readFileSync(path.join(root,'Part3/web',name),'utf8'),box,{filename:name});
if(typeof box.window.part3IntentEditor?.create!=='function')throw Error('Packaged editor registration missing');
const explained=fixtures.map(value=>box.window.part3IntentDisplay.explain(value.interpretation));
if(JSON.stringify(fixtures)!==snapshot)throw Error('Presentation mutated the canonical strategy');
process.stdout.write(JSON.stringify(explained));
'''
    evidence = release_payload
    results = []
    for root in (ROOT,evidence['payload']):
        result = subprocess.run([node,'-e',script,str(root),str(evidence['work']/'synthetic_strategies.json')],
            cwd=evidence['work'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode == 0,result.stderr[-6000:]
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
    text = ' '.join(results[1][0])
    for meaning in ('양봉','진행봉 기준','음봉','확정봉 기준','매도','무지성','브레이커','1회 감시','atr_anchor','2100'):
        assert meaning in text,meaning
    branch_text = ' '.join(results[1][1])
    for meaning in ('독립 분기 1','독립 분기 2','매수','매도'):
        assert meaning in branch_text,meaning
