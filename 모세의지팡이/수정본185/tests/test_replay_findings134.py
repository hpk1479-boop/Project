"""수정본134: 외부 검토에서 재현된 백테스트 문제 세 가지.

1. 쓰는 시간봉만 처리할 때, 쓰지 않는 시간봉만 가진 묶음은 엔진에 아무것도 넣지 못한다(시각·타이머·
   TICK 판단이 빠짐). 그 구간은 모든 시간봉으로 다시 실행한다. 마지막 묶음이어도 마찬가지.
2. Part3 생성 전략(프리셋 목록 밖에서 불러온 전략)은 시간봉 목록을 만들 수 없으므로 모든 시간봉으로 실행한다.
3. 실행 기록의 코드 식별값(code_hash)은 스페셜 레시피가 바뀌면 바뀐다.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from test_limited_timeframes131 import caches, send, full, row, beat   # noqa: E402
from test_limited_timeframes131 import offline                          # noqa: E402,F401  (no network)


def bundles(adapter):
    return [i for i in adapter.ingress.pending if i.kind.name == 'MARKET_BUNDLE']


def test_a_bundle_with_only_unread_timeframes_asks_for_every_feed(tmp_path):
    (every, every_adapter), (limited, limited_adapter) = caches(tmp_path)
    for adapter in (every_adapter, limited_adapter):
        send(adapter, [full('1m', 1), full('5m', 1, 5.)], 1)
        send(adapter, [row('1m', 2, 1.), row('5m', 2, 6.)], 2)
    assert not limited.shadow_fallbacks                     # the read 1m is in these bundles
    for adapter in (every_adapter, limited_adapter):
        send(adapter, [row('5m', 3, 7.)], 3)                # only the unread 5m
    assert len(bundles(every_adapter)) == 3 and len(bundles(limited_adapter)) == 2
    assert limited.shadow_fallbacks == ['a bundle with only unread timeframes']
    assert every.shadow_fallbacks == []                     # LIVE / every feed: never


def test_heartbeat_of_a_read_timeframe_is_not_a_fallback(tmp_path):
    (_, every_adapter), (limited, limited_adapter) = caches(tmp_path)
    for adapter in (every_adapter, limited_adapter):
        send(adapter, [full('1m', 1), full('5m', 1, 5.)], 1)
        send(adapter, [beat('1m', 2), row('5m', 2, 6.)], 2)
    assert not limited.shadow_fallbacks
    assert len(bundles(every_adapter)) == len(bundles(limited_adapter)) == 2


SCRIPT = r'''
import json, sys
from pathlib import Path
from event_backtest import runner, bridge
from event_backtest.settings import scenario
root, capture, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
s = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', mode='TICK', strategies=['SPECIAL7'], overlap_trading_days=0)
config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'OFFLINE'}
if mode == 'unread_last':
    # The capture's final bundle carried only unread timeframes: no engine input, one STAFF record.
    original = bridge.CaptureInputs.__iter__
    def iterate(self):
        yield from original(self)
        if self.cache._published is not None:self.cache.shadow_fallbacks.append('a bundle with only unread timeframes')
    bridge.CaptureInputs.__iter__ = iterate
task = {'scenario': s, 'config': config, 'out': str(root / ('job_' + mode)), 'run_id': mode, 'start': s['start'], 'end': s['end'],
        'warm_start': s['start'], 'captures': [{'path': capture.relative_to(root).as_posix()}], 'warehouse': str(root)}
result = runner._run_chunk(task, False) if mode == 'every' else runner.run_chunk(task)
rows = (root / ('job_' + mode) / 'alerts.csv').read_text('utf-8').splitlines()
print(json.dumps({'bundles': result['bundles'], 'alerts': result['alerts'], 'timeframes': result.get('timeframes'),
                  'fallback': result.get('timeframe_fallback'), 'rows': [r.replace(mode, '') for r in rows]}))
'''


def test_unread_last_bundle_replays_the_period_with_every_feed(tmp_path):
    from test_parallel_oz import keyframe_fixture
    _, _, capture = keyframe_fixture(tmp_path)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(ROOT / 'Part1/program'), str(ROOT / 'Part2')))}
    out = {}
    for mode in ('every', 'unread_last'):
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', SCRIPT, str(tmp_path), str(capture), mode],
                              env=env, capture_output=True, text=True, encoding='utf-8')
        assert done.returncode == 0, done.stderr
        out[mode] = json.loads(done.stdout.strip().splitlines()[-1])
    assert out['unread_last']['fallback'] == ['a bundle with only unread timeframes']
    assert out['unread_last']['timeframes'] is None
    assert (out['unread_last']['bundles'], out['unread_last']['alerts'], out['unread_last']['rows']) == \
           (out['every']['bundles'], out['every']['alerts'], out['every']['rows'])


def test_generated_strategy_runs_with_every_feed_instead_of_failing():
    import event_application
    from event_selection import resolve
    from event_backtest.timeframe_selection import required_timeframes
    from event_backtest.runner import runtime_config
    from event_backtest.settings import scenario
    key = 'Test_SPECIAL001'
    event_application.register_strategy_loader(key, lambda: None, dependencies=('RECIPE',))
    try:
        config = runtime_config(scenario(symbol='XAUUSD+', start='2026-09-15', end='2026-09-16', mode='BAR', strategies=[key]))
        selection = resolve([key], config)
        assert selection.specials == (key,)
        engine = SimpleNamespace(selection=selection, processors=(), strategy_state={}, strategies=())
        assert required_timeframes(engine) is None
    finally:
        event_application.unregister_strategy_loader(key)


def test_code_identity_changes_with_a_recipe(tmp_path, monkeypatch):
    from event_backtest import runner
    program = tmp_path / 'Part1/program'
    for path, text in ((program / 'engine.py', 'x = 1\n'), (program / 'SPECIAL/SPECIAL8.recipe.json', '{"ma": "HMA17"}'),
                       (tmp_path / 'Part2/event_backtest/runner.py', 'y = 1\n'),
                       (tmp_path / 'Part2/generic_backtest/native_mt5.py', 'z = 1\n'),
                       (tmp_path / 'settings/strategy_registry.json', '{}')):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, 'utf-8')
    monkeypatch.setattr(runner, 'PROGRAM', program)
    before = runner.code_hash()
    (program / 'SPECIAL/SPECIAL8.recipe.json').write_text('{"ma": "HMA18"}', 'utf-8')
    assert runner.code_hash() != before
