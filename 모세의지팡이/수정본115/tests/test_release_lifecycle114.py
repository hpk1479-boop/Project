"""The shared lifecycle features are the same in a developer run, a source-free release and a moved one.

The release keeps no .py source: the contract, state machine, port, display, OZ gate and AI schema
come from the code bundle, and the recipes come from the one source folder.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.runtime_bundle import build_code_bundle

CODE = {'Part1/program/strategy_recipe/' + name + '.py' for name in ('contract', 'runtime', 'port', 'alerts')}
CODE |= {'Part1/program/oz_engine/' + name + '.py' for name in ('common', 'controllers', 'market', 'runtime')}
CODE |= {'Part3/lab/ai/schema.py', 'Part3/lab/ai/research_presets.py'}

PROBE = r'''
import copy, json, os, socket, sys
from pathlib import Path
from types import SimpleNamespace as NS
root, tooling, mode, work = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
physical = [name for _, _, names in os.walk(root) for name in names if name.endswith(('.py', '.pyw'))]
def deny(*args, **kw): raise AssertionError('Actual network prohibited')
socket.create_connection = deny
socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = deny
sys.path[:0] = [tooling, str(root), str(root/'Part3'), str(root/'Part2'), str(root/'Part1/program')]
bundle = None
if mode == 'bundle':
    from releasekit.runtime_bundle import RuntimeBundle
    bundle = RuntimeBundle(root).install()
try:
    import numpy as np
    from event_engine.model import FeedSnapshot
    from strategy_recipe import registry
    from strategy_recipe.alerts import condition_text, render_strategy_alert
    from strategy_recipe.contract import execution_plan
    from strategy_recipe.runtime import IntentMachine
    from oz_engine.common import ExternalLiquidityState, sweep_external_spec
    from oz_engine.controllers import ExternalLiquidityController
    from oz_engine.market import COLUMNS, OZMarketView
    from lab.ai import research_presets
    from lab.ai.schema import output_schema, recipe_from_intent

    # 1. What the one recipe source declares, lowered by the shared contract.
    declared = {}
    for name, entry in registry.builtin_entries().items():
        meaning = copy.deepcopy(entry['recipe']['strategy_intent']); meaning['symbols'] = ['XAUUSD+']
        plan = execution_plan(meaning)['meaning']
        unit = (plan.get('branches') or [plan])[0]
        declared[name] = {'gate': unit['final'].get('level_gate'),
            'touch_rule': [s.get('_level_gate') for s in unit['steps'] if s.get('_level_gate')],
            'from': plan.get('final_window_from'), 'expires': (plan.get('lifecycle') or {}).get('expires')}

    # 2. Waiting time: from the first condition, or from when all are gathered.
    def ev(token, at, matched=True):
        return {'ready': True, 'matched': matched, 'event': True, 'token': token, 'at': at, 'source_tf': '5m'}
    waits = {}
    for anchor in ('ALL_CONDITIONS', 'FIRST_CONDITION'):
        raw = {'symbols': ['T'], 'direction': 'LONG', 'order_mode': 'UNORDERED', 'within_sec': 300,
            'final_window_sec': 600, 'final_window_from': anchor, 'persistent': True,
            'steps': [{'kind': 'MA_CROSS', 'tfs': ['5m'], 'ma_left': 'EMA50', 'ma_right': 'EMA200', 'direction': 'LONG'},
                      {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'direction': 'LONG'}],
            'final': {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ'}}
        m = IntentMachine(execution_plan(raw)['meaning'], 'T', 'LONG')
        m.advance(100, [ev('a', 100), ev('b', 100, False)])
        actions = m.advance(160, [ev('a', 100, False), ev('b', 160)])
        waits[anchor] = [actions, m.active_until, m.final_allowed(m.active_until, [ev('a', 0, False), ev('b', 0, False)]),
                         m.final_allowed(m.active_until + 1, [ev('a', 0, False), ev('b', 0, False)])]

    # 3. Preconditions checked only at the start, or all the while.
    checks = {}
    for mode_name in ('WHILE_ACTIVE', 'AT_START'):
        raw = {'symbols': ['T'], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS', 'persistent': True,
            'steps': [{'kind': 'MA_STATE', 'tfs': ['5m'], 'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200, 'side': 'ABOVE'}],
            'lifecycle': {'precondition_check': mode_name},
            'final': {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ'}}
        m = IntentMachine(execution_plan(raw)['meaning'], 'T', 'LONG')
        up = {'ready': True, 'matched': True, 'event': False, 'token': None, 'at': 0, 'source_tf': '5m'}
        down = dict(up, matched=False)
        checks[mode_name] = [m.advance(1, [up]), m.final_allowed(2, [down]), m.advance(3, [down]), m.active]

    # 4. The OZ gate with the strategy's own ATR rule.
    n = 60
    spread = np.r_[np.full(45, 1.), np.full(n - 45, 3.)]
    values = np.full((n, len(COLUMNS)), 100.)
    values[:, COLUMNS['high']] = 100 + spread; values[:, COLUMNS['low']] = 100 - spread
    snap = FeedSnapshot(1790380000 + np.arange(n, dtype=np.int64) * 60, np.ones(n, dtype=np.int64), values, 1, 'e', {})
    view = OZMarketView(snap, atr_provider=lambda: np.full(n, 2.))
    ext = ExternalLiquidityController(NS(seconds=0.), sweep_registry={})
    ext._specs['w'] = sweep_external_spec({'watch_id': 'w', 'symbol': 'T', 'source_tf': '1m',
        'external_atr_period': 50, 'external_atr_mult': 2.})
    state = ExternalLiquidityState('w', 'PENDING_ATR', 'LONG', 'level', level_price=100., event_time=int(snap.time[-2]))
    ext._states['w|LONG|level'] = state
    ext.update_market('T', {'1m': view}, ['w'])
    limit = state.max_distance
    gate = {'status': state.status, 'atr': state.atr_snapshot, 'limit': limit,
            'inside': ext.validate_true_b0('w', 'LONG', 100. - (limit - 1e-6)),
            'outside': ext.validate_true_b0('w', 'LONG', 100. - (limit + 1e-6)), 'after': state.status}

    # 5. What the alert says about places.
    machine = IntentMachine({'steps': [], 'final': {'kind': 'NOTIFY'}}, 'T', 'LONG')
    shown = {'trend': condition_text({'kind': 'TREND', 'tfs': ['15m']}, machine),
             'level': condition_text({'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['5m'], 'side': 'LOW', 'level': 'ALL'}, machine,
                {'matched': True, 'touched_levels': ({'code': 'PREV_4H_LOW', 'name': 'x', 'price': 3842.15, 'at': 1.},)})}

    # 6. The AI side of the same contract.
    schema = output_schema()
    meaning_schema = schema['properties']['interpretation']['anyOf'][0]['properties']
    spec2 = copy.deepcopy(registry.builtin_entries()['SPECIAL2']['recipe']['strategy_intent']); spec2['symbols'] = ['XAUUSD+']
    recipe = recipe_from_intent({'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': spec2,
        'needs_clarification': False, 'clarification_question': None, 'message_ko': ''})
    ai = {'anchors': meaning_schema['final_window_from']['enum'],
          'checks': meaning_schema['lifecycle']['properties']['precondition_check']['enum'],
          'gate_fields': sorted(meaning_schema['final']['properties']['level_gate']['properties']),
          'recipe_gate': recipe['strategy_intent']['final']['level_gate'],
          'explained': '외부유동성 가격과 최종 올존의 ATR 거리 검사' in research_presets.example(spec2, 'x')}
    print(json.dumps({'declared': declared, 'waits': waits, 'checks': checks, 'gate': gate, 'shown': shown, 'ai': ai,
                      'physical_source_count': len(physical)}, ensure_ascii=False, sort_keys=True))
finally:
    if bundle: bundle.uninstall()
'''


def run_probe(root, work, mode):
    result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROBE, str(root), str(TOOLS), mode, str(work)],
        cwd=work, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-9000:] + result.stdout[-1000:]
    return json.loads(result.stdout)


@pytest.fixture(scope='module')
def evidence(tmp_path_factory):
    work = tmp_path_factory.mktemp('lifecycle_release114')
    payload = work / '모세트레이딩시스템'
    selected = list(builder.source_paths(ROOT))
    manifest = resources.discover_resources(ROOT, selected)
    code = builder.prepare_data(ROOT, payload, selected)
    compiled = build_code_bundle(ROOT, payload / 'runtime/code.bundle', paths=code)
    resources.validate_payload(payload, manifest, code)
    developer = run_probe(ROOT, work, 'developer')
    installed = run_probe(payload, work, 'bundle')
    relocated = work / 'moved' / '수정본999'
    relocated.parent.mkdir()
    payload.rename(relocated)
    moved = run_probe(relocated, work, 'bundle')
    return {'compiled': compiled, 'payload': relocated, 'developer': developer, 'installed': installed, 'moved': moved}


def test_the_code_of_every_shared_feature_is_in_the_source_free_bundle(evidence):
    assert CODE <= set(evidence['compiled']['entries'])
    assert evidence['developer']['physical_source_count'] > 0
    assert evidence['installed']['physical_source_count'] == evidence['moved']['physical_source_count'] == 0


@pytest.mark.parametrize('key', ['declared', 'waits', 'checks', 'gate', 'shown', 'ai'])
def test_developer_release_and_moved_release_do_the_same(evidence, key):
    assert evidence['developer'][key] == evidence['installed'][key] == evidence['moved'][key]


def test_what_they_all_do(evidence):
    probe = evidence['moved']
    declared = probe['declared']
    assert declared['SPECIAL2']['gate'] == {'ref': 'sweep', 'atr_period': 14, 'atr_mult': 1.5}
    assert declared['SPECIAL2']['touch_rule'] == [{'atr_period': 14, 'atr_mult': 1.5}]
    assert declared['SPECIAL4']['from'] == 'FIRST_CONDITION'
    assert declared['SPECIAL5']['expires'] == {'bars': 10, 'bars_setting': 'MAX_BARS_AFTER_B0', 'tf': 'FINAL'}
    assert probe['waits']['ALL_CONDITIONS'][:2] == [['ARM'], 160 + 600] and probe['waits']['FIRST_CONDITION'][:2] == [['ARM'], 100 + 600]
    assert probe['waits']['FIRST_CONDITION'][2:] == [True, False]
    assert probe['checks']['WHILE_ACTIVE'] == [['ARM'], False, ['CANCEL'], False]
    assert probe['checks']['AT_START'] == [['ARM'], True, [], True]
    gate = probe['gate']
    assert gate['status'] == 'ACTIVE' and gate['inside'] is True and gate['outside'] is False and gate['after'] == 'INVALID'
    assert probe['shown'] == {'trend': '15분봉 추세', 'level': '4시간 저가 · 3,842.15'}
    assert probe['ai']['anchors'] == ['ALL_CONDITIONS', 'FIRST_CONDITION'] and probe['ai']['explained'] is True
    assert probe['ai']['gate_fields'] == ['atr_mult', 'atr_period', 'ref']


def test_the_release_gets_its_recipes_from_the_one_source_folder(evidence):
    from strategy_recipe.special_files import read_special_entries
    shipped = json.loads((evidence['payload'] / 'settings/strategy_registry.json').read_text('utf-8'))['presets']
    source = read_special_entries(ROOT)
    assert {row['id']: row for row in shipped} == source
    assert 'level_gate' in json.dumps(source['SPECIAL2']) and 'bars_setting' in json.dumps(source['SPECIAL5'])
    recipes = evidence['payload'] / 'Part1/program/SPECIAL'
    assert sorted(path.name for path in recipes.glob('SPECIAL*.recipe.json')) == sorted(
        path.name for path in (ROOT / 'Part1/program/SPECIAL').glob('SPECIAL*.recipe.json'))


def test_the_release_carries_the_ai_documents_that_explain_the_new_fields(evidence):
    payload = evidence['payload']
    language = (payload / 'Part3/ai_context/STRATEGY_LANGUAGE.md').read_text('utf-8')
    schema = (payload / 'Part3/docs/AI_INTENT_SCHEMA.md').read_text('utf-8')
    for word in ('final_window_from', 'precondition_check', 'bars_setting', 'level_gate'):
        assert word in language + schema, word
    for relative in ('Part3/ai_context/STRATEGY_LANGUAGE.md', 'Part3/docs/AI_INTENT_SCHEMA.md'):
        assert (payload / relative).read_bytes() == (ROOT / relative).read_bytes()
