"""Every MA period is literal and uses one shared calculation path.

No 21 -> 20 rewrite anywhere, and the short "N M 골크" cross (WATCH and timed chain) is no
longer limited to EA-sent periods. EA-sent periods (EMA20/50/200, HMA6/17/50/168) still come
from the EA (role table: fixed indicators in MT5); every other period is computed by the shared
WATCH MA Fact store.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]


def test_name_and_command_text_keep_21():
    from watch_ma import canonical_ma, parse_ma_name
    from command_interpreter import CommandInterpreter
    assert parse_ma_name('EMA21') == ('EMA', 21)
    assert canonical_ma('EMA21') == 'EMA21'
    # Normalized text is lower case for every period (as EMA22 always was).
    text = CommandInterpreter({}).normalize_command_text('골드 1분 EMA21 21EMA 골든 크로스 알람')
    assert 'ema21' in text and '21ema' in text and '20' not in text


def parse(text):
    from command_interpreter import CommandInterpreter
    from domain_clock import event_scope
    from kim_secretary import KimSecretary
    secretary = KimSecretary(CommandInterpreter({'SYMBOLS': 'XAUUSD+'}), {'LONDON': '08:00-16:00'})
    with event_scope(1790380680000, 'ema21', {}):
        return secretary.parse(text, 'user', external_retry=False)


@pytest.mark.parametrize('text,expression', [
    ('골드 1분 골든크로스(EMA21,EMA50) 알려줘', '골든크로스(EMA21, EMA50)'),
    ('골드 1분 EMA21 > EMA50 알려줘', 'EMA21 > EMA50'),
    ('골드 1분 기울기(EMA21) > 0 알려줘', '기울기(EMA21) > 0.0'),
])
def test_free_length_watch_keeps_ema21(text, expression):
    value = parse(text)
    assert value.kind == 'WATCH'
    assert value.value['watch_type'] == 'MA_EXPRESSION'
    assert value.value['ma_expression'] == expression


@pytest.mark.parametrize('text,family,fast,slow', [
    ('골드 1분 EMA21 50 골크 알려줘', 'EMA', 21, 50),
    ('골드 1분 EMA22 50 데크 알려줘', 'EMA', 22, 50),
    ('골드 1분 EMA20 50 골크 알려줘', 'EMA', 20, 50),
    ('골드 1분 HMA18 30 골크 알려줘', 'HMA', 18, 30),
])
def test_short_cross_takes_any_period_literally(text, family, fast, slow):
    value = parse(text)
    assert value.kind == 'WATCH'
    assert (value.value['watch_type'], value.value['ma_family']) == (family + '_CROSS', family)
    assert (value.value['fast_period'], value.value['slow_period']) == (fast, slow)


def test_timed_chain_cross_takes_any_period_and_rejects_an_invalid_name():
    chain = parse('골드 1분 EMA21 50 골크나면 30분 동안 5분 하단 원비 알려줘')
    assert chain.kind == 'REGISTER_CHAIN'
    assert (chain.value.triggers[0].fast_period, chain.value.triggers[0].slow_period) == (21, 50)
    assert parse('골드 1분 EMA0 50 골크 알려줘').kind == 'ERROR'
    from watch_orchestrator import ChainTriggerSpec
    with pytest.raises(ValueError):
        ChainTriggerSpec('EMA_CROSS', '1m', ma_family='EMA', fast_period=21, slow_period=21).validate()


@pytest.mark.parametrize('kind,source,fast,slow,native', [
    ('EMA_CROSS', 'close', 21, 50, {'ema_50': 100.}),   # computed fast vs EA slow
    ('HMA_CROSS', 'open', 18, 30, {}),                   # both computed from OPEN
])
def test_short_cross_fires_from_the_shared_path_only_on_a_closed_bar(kind, source, fast, slow, native):
    from test_numpy_processors import view, edit, board, monitor
    from indicator_facts_numpy import ma_array
    family = kind.split('_')[0]
    n = 200
    m, c, _ = monitor(kind, ma_family=family, fast_period=fast, slow_period=slow, evaluation_mode='CLOSE')
    flat = np.full(n, 90.)
    v = view(n, **{source: flat}, **native)
    m.evaluate(board({'1m': v}), 1000.);m.evaluate(board({'1m': v}), 1001.)
    assert not c.events
    forming = flat.copy();forming[-1] = 300.                     # only the forming bar jumps
    v = edit(v, advance=60, **{source: forming})
    m.evaluate(board({'1m': v}), 1060.)
    assert not c.events
    closed = flat.copy();closed[-2:] = 300.                      # the closed bar jumps
    v = edit(v, advance=60, **{source: closed})
    m.evaluate(board({'1m': v}), 1120.)
    assert len(c.events) == 1
    _, message, meta = c.events[0]
    assert meta['direction'] == 'LONG' and f'{family}{fast}/{slow}' in message
    # The same crossing follows from the full formula on the same inputs.
    slow_values = np.full(n, native['ema_50']) if native else ma_array(closed, family, slow)
    fast_values = ma_array(closed, family, fast)
    assert fast_values[-3] <= slow_values[-3] and fast_values[-2] > slow_values[-2]


def test_shared_store_computes_ema21_like_the_full_formula_and_not_the_ea_ema20():
    from watch_array_facts import WatchMAStore
    from watch_ma import feature_source_rows
    from event_engine.market import MarketView, COLUMNS
    from event_engine import FeedSnapshot
    from indicator_facts import ma_array
    from test_engine_optimization import snapshot
    # 수정본162: the store through forming changes, corrections and new bars is checked once in
    # tests/test_recipe_repeat132.py; this keeps only the literal period, unlike the EA's EMA20.
    store = WatchMAStore();base = snapshot()
    close = COLUMNS['close']
    values = base.values.copy()
    view = MarketView(FeedSnapshot(base.time, base.volume, values, 0, 'epoch:1', base.indicator_validity))
    got = store.get('XAUUSD+', '1m', view, ['EMA21', 'EMA22'])
    for name, period in (('EMA21', 21), ('EMA22', 22)):
        expected = ma_array(values[-feature_source_rows(name):, close], 'EMA', period)
        assert got[name].tobytes() == expected.tobytes(), name
    assert not np.array_equal(got['EMA21'], view.column('ema_20'), equal_nan=True)
