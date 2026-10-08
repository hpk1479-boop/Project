"""The place line of a finished strategy's alert, through the common display path."""
import numpy as np
import pytest

from recipe_harness114 import Harness, feed, SYMBOL
from strategy_recipe.alerts import condition_text, render_strategy_alert
from strategy_recipe.runtime import IntentMachine

NOW = 1790000100.
FORBIDDEN = ('• 조건:', '• 트리거:', '• 등급:', 'BO_BREAK', 'A급')


def rising(tf, now, *, sign=1):
    """A 15m trend that points one way: SMA20 (of open) and the native HMA50 both moving."""
    n = 40
    ramp = 2400. + sign * np.arange(n) * 1.5
    return feed(tf, now - now % 900, n, open=ramp, hma_50=ramp)


def special7(direction):
    h = Harness.special('SPECIAL7')
    h.publish(NOW, {'15m': rising('15m', int(NOW), sign=1 if direction == 'LONG' else -1)})
    machine = next(m for m in h.machines if m.direction == direction)
    assert machine.active, 'the 15m trend should have armed the final watch'
    return h, machine


@pytest.mark.parametrize('direction,side', [('LONG', '매수'), ('SHORT', '매도')])
def test_special7_alert_names_the_15m_trend_as_its_place(direction, side):
    h, machine = special7(direction)
    assert h.final_oz(machine, NOW)['ok']
    text = h.messages[-1]
    lines = text.splitlines()
    assert lines[0].startswith('[15분 추세 · 1분 브레이커 올존]') and side in lines[0]
    assert '• 위치: 15분봉 추세' in lines
    assert '• 주기: 1분 브레이커 올존' in text or '• 주기: 1분' in text
    assert not any(word in text for word in FORBIDDEN)


def test_a_trend_alongside_another_place_is_listed_with_it():
    machine = IntentMachine({'steps': [], 'final': {'kind': 'NOTIFY'}}, SYMBOL, 'LONG')
    for tfs, text in ((['1h'], '1시간봉 추세'), (['1h', '4h'], '1시간/4시간봉 추세')):
        assert condition_text({'kind': 'TREND', 'tfs': tfs}, machine) == text
    both = {'steps': [{'kind': 'TREND', 'tfs': ['1h']}, {'kind': 'WONBI_TOUCH', 'tfs': ['1h'], 'side': 'LOWER'}],
            'final': {'kind': 'NOTIFY'}}
    from strategy_recipe.contract import execution_plan
    plan = execution_plan({**both, 'symbols': [SYMBOL], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS'})['meaning']
    text = render_strategy_alert('내부유동성', IntentMachine(plan, SYMBOL, 'LONG'), {'symbol': SYMBOL, 'source_tf': '1h'})
    assert '• 위치: 1시간봉 추세 / 1시간 하단 원비 터치' in text.splitlines()


def test_the_trend_place_is_the_same_whatever_the_strategy():
    # The display never branches on which strategy it is.
    from pathlib import Path
    import re
    source = (Path(__file__).resolve().parents[1] / 'Part1/program/strategy_recipe/alerts.py').read_text('utf-8')
    assert not re.search(r'SPECIAL\s*\d', source)
