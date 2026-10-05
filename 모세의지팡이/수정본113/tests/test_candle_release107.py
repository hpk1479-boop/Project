"""Candle contracts survive the real source-free release preparation path.

Only temporary payloads and synthetic prices are used. No installer, MT5,
provider request, user strategy mutation, or application source write occurs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.runtime_bundle import build_code_bundle

REQUIRED_CODE = {
    'Part1/program/indicator_facts.py',
    'Part1/program/indicator_facts_numpy.py',
    'Part1/program/event_engine/facts.py',
    'Part1/program/strategy_recipe/contract.py',
    'Part1/program/strategy_recipe/port.py',
    'Part3/lab/ai/schema.py',
    'Part3/lab/ai/tools.py',
    'Part3/lab/ai_compiler.py',
    'common_ai/security.py',
    'moses_language/__init__.py',
}

PROBE = r'''
import json, os, socket, sys
from pathlib import Path
from types import SimpleNamespace

root, tooling, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
def no_network(*args, **kwargs):
    raise AssertionError('release candle probe must not access network/MT5/Telegram')
socket.create_connection = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.socket.sendto = no_network
sys.path[:0] = [str(tooling), str(root), str(root/'Part3'), str(root/'Part2'), str(root/'Part1/program')]
bundle = None
if mode == 'bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle = RuntimeBundle(root).install()
try:
    import numpy as np
    from indicator_facts import ArrayFactFrame, candle_direction_values
    from event_engine.market import COLUMNS, MarketView
    from event_engine.model import FeedSnapshot
    from event_engine.facts import EventFacts
    from strategy_recipe.port import IntentPort
    from strategy_recipe.contract import execution_plan
    from strategy_recipe.registry import builtin_entries
    from lab.ai.schema import output_schema, contract_context
    from lab.ai.tools import ReadOnlyWorkspace
    from lab.ai.agent import external_system_prompt
    from lab.ai_compiler import compile_ai
    from common_ai.security import external_payload
    from common_ai.external_prompt import language_reference
    from moses_language import public_vocabulary

    workspace = ReadOnlyWorkspace()
    vocabulary = workspace.vocabulary()
    schema = output_schema()
    symbol = vocabulary['symbols'][0]
    meanings, generated = [], []
    for side in ('BULL', 'BEAR'):
        for state in ('FORMING', 'CLOSED'):
            meaning = {'symbols': [symbol], 'direction': 'SHORT', 'order_mode': 'SIMULTANEOUS',
                'steps': [{'kind':'CANDLE_STATE','tfs':['1h'],'side':side,'bar_state':state}],
                'final': {'kind':'NOTIFY'}}
            plan = execution_plan(meaning, contract_context())
            meanings.append(plan)
            recipe = {'schema_version':2,'base':'AI','name':'release candle test',
                'description':'synthetic candle contract','symbols':[symbol],
                'strategy_intent':meaning}
            generated.append(compile_ai(recipe, 'Test_SPECIAL999.py'))

    def market(last_close, sequence):
        opens = np.array([100.,100.,100.])
        closes = np.array([101.,99.,last_close])
        values = np.full((3,len(COLUMNS)),100.,dtype=float)
        values[:,COLUMNS['open']] = opens
        values[:,COLUMNS['close']] = closes
        values[:,COLUMNS['high']] = np.maximum(opens,closes)+1.
        values[:,COLUMNS['low']] = np.minimum(opens,closes)-1.
        snapshot = FeedSnapshot(np.arange(3,dtype='int64')*3600+1791158400,
            np.ones(3,dtype='int64'),values,sequence,'release107')
        board = SimpleNamespace(feeds={(symbol,'1h'):snapshot},health={},source_time=None,
            observed={},_frame_cache={},publication_token=sequence,
            snapshot=lambda *args:snapshot)
        return board, MarketView(snapshot)

    port = IntentPort.__new__(IntentPort)
    port.candle_frames, port._step_ids = {}, {}
    port._publication, port._observation_cache, port.oz_sequence = None, {}, 0
    steps = {(side,state):{'kind':'CANDLE_STATE','tfs':['1h'],'side':side,'bar_state':state}
        for side in ('BULL','BEAR') for state in ('FORMING','CLOSED')}
    observations, facts = [], []
    for sequence,last_close in enumerate((101.,99.,100.),1):
        board, view = market(last_close,sequence)
        events = EventFacts()
        events.invalidate({(symbol,'1h'):view.snapshot})
        facts.append({'array':ArrayFactFrame(view,'1h')['candle_direction'].tolist(),
            'event':events.get(symbol,'1h',view.snapshot,'candle_direction','release-test').tolist()})
        for side in ('BULL','BEAR'):
            for state in ('FORMING','CLOSED'):
                for trade in ('LONG','SHORT'):
                    step = steps[side,state]
                    answer = port.observe(board,symbol,step,trade,1791165600+sequence)
                    observations.append({'price':last_close,'side':side,'bar_state':state,
                        'trade':trade,'ready':answer['ready'],'matched':answer['matched'],
                        'event':answer['event']})

    messages, tools, transported = external_payload([
        {'role':'system','content':external_system_prompt(workspace)},
        {'role':'user','content':'GOLD 1시간 진행봉 양봉이고 올존이면 숏 알림'}],[],schema)
    external_contract = json.loads(messages[0]['content'])['moses_contract']
    # Personal host visibility and promoted strategy lists intentionally do not
    # ship. Compare the full shared contract after removing only those lists.
    vocabulary.pop('presets',None)
    external_contract.pop('presets',None)
    external_contract.get('vocabulary',{}).pop('presets',None)
    schema['properties']['interpretation']['anyOf'][0]['properties']['preset']['enum'] = []
    print(json.dumps({'vocabulary':vocabulary,'schema':schema,
        'external_contract':external_contract,'dictionary':public_vocabulary(),
        'language_reference':language_reference('1시간 진행봉 양봉 음봉 올존 숏'),
        'plans':meanings,'generated':generated,'facts':facts,'observations':observations,
        'builtins':list(builtin_entries()),
        'physical_source_exists':os.path.isfile(root/'Part1/program/strategy_recipe/port.py')},
        ensure_ascii=False,allow_nan=False))
finally:
    if bundle is not None:
        bundle.uninstall()
'''


def run_probe(root, work, mode):
    result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROBE,
        str(root), str(TOOLS), mode], cwd=work, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=90,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-12000:] + result.stdout[-2000:]
    return json.loads(result.stdout.strip())


def schema_meaning(value):
    """Enum ordering from Python sets changes no accepted input or constraint."""
    if isinstance(value, list):
        return [schema_meaning(item) for item in value]
    if isinstance(value, dict):
        result = {key:schema_meaning(item) for key,item in value.items()}
        if isinstance(result.get('enum'), list):
            result['enum'] = sorted(result['enum'],key=lambda item:json.dumps(item,sort_keys=True))
        return result
    return value


@pytest.fixture(scope='module')
def release_payload(tmp_path_factory):
    work = tmp_path_factory.mktemp('candle_release107')
    payload = work / 'installed'
    sources = list(builder.source_paths(ROOT))
    manifest = resources.discover_resources(ROOT, sources)
    code_paths = builder.prepare_data(ROOT, payload, sources)
    compiled = build_code_bundle(ROOT, payload / 'runtime/code.bundle', paths=code_paths)
    resources.validate_payload(payload, manifest, code_paths)
    return {'work':work,'payload':payload,'sources':sources,'code':code_paths,
        'manifest':compiled,'developer':run_probe(ROOT,work,'developer'),
        'distributed':run_probe(payload,work,'bundle')}


def test_candle_resources_and_code_are_selected_by_current_release_pipeline(release_payload):
    evidence = release_payload
    selected = {path.as_posix() for path,_ in evidence['sources']}
    assert REQUIRED_CODE <= selected
    assert REQUIRED_CODE <= set(evidence['manifest']['entries'])
    assert 'moses_language/language.json' in selected
    assert (evidence['payload']/'moses_language/language.json').read_bytes() == (ROOT/'moses_language/language.json').read_bytes()


def test_developer_and_source_free_candle_contract_have_same_meaning(release_payload):
    original, installed = release_payload['developer'],release_payload['distributed']
    assert original['physical_source_exists'] is True
    assert installed['physical_source_exists'] is False
    for key in ('vocabulary','schema','external_contract','dictionary','language_reference',
                'plans','generated','facts','observations'):
        assert schema_meaning(installed[key]) == schema_meaning(original[key]), key
    candle = installed['vocabulary']['candle_state']
    assert candle['sides'] == ['BULL','BEAR']
    assert candle['bar_states'] == ['FORMING','CLOSED']
    assert 'CANDLE_STATE' in installed['external_contract']['vocabulary']['intent_kinds']
    assert installed['external_contract']['vocabulary']['candle_state'] == candle
    assert all(word in installed['dictionary']['condition_terms']['CANDLE_STATE'] for word in ('양봉','음봉'))
    assert 'CANDLE_STATE' in installed['language_reference']['conditions']
    for item in installed['observations']:
        expected = (item['price']>100 if item['side']=='BULL' else item['price']<100) if item['bar_state']=='FORMING' else item['side']=='BEAR'
        assert item['ready'] and item['matched']==expected and not item['event'],item
    for fact,expected in zip(installed['facts'],([1.,-1.,1.],[1.,-1.,-1.],[1.,-1.,0.])):
        assert fact['array']==fact['event']==expected


def test_source_text_test_strategies_and_promotion_state_remain_out_of_release(release_payload):
    evidence = release_payload
    assert not [p for p in evidence['payload'].rglob('*') if p.is_file() and p.suffix.lower() in {'.py','.pyw','.mq5','.mqh'}]
    for relative in (*evidence['code'],*evidence['manifest']['entries']):
        assert not any(part.casefold() in {'test_special','generated'} for part in Path(relative).parts)
    assert (evidence['payload']/'Part3/TEST_SPECIAL').is_dir()
    assert not list((evidence['payload']/'Part3/TEST_SPECIAL').iterdir())
    assert not (evidence['payload']/'settings/strategy_visibility.json').exists()
    registry = json.loads((evidence['payload']/'settings/strategy_registry.json').read_text('utf-8'))
    assert [row['id'] for row in registry['presets']] == evidence['distributed']['builtins']
    assert all(not row.get('generated') for row in registry['presets'])
    with zipfile.ZipFile(evidence['payload']/'runtime/code.bundle') as archive:
        assert all(name=='manifest.json' or name.startswith('code/') and name.endswith('.bin') for name in archive.namelist())
        for relative in REQUIRED_CODE:
            member = evidence['manifest']['entries'][relative]['member']
            assert (ROOT/relative).read_bytes() not in archive.read(member)
