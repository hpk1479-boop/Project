"""GUI 데이터 품질: 틱=매 Tick, 실시간=0.5초 주기, 봉마감=전략 최하위 TF 봉 마감."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from generic_backtest import quality
from generic_backtest.evaluation import TICK, LIVE_PARITY, ONE_MINUTE_CLOSE, evaluation_base_tf
from generic_backtest.live_parity import configuration, timeline


def test_labels_are_exactly_three_modes():
    assert quality.QUALITY_LABELS == {'틱': TICK, '실시간': LIVE_PARITY, '봉마감': ONE_MINUTE_CLOSE}
    assert quality.quality_label(TICK) == '틱 · 매 Tick'
    assert quality.quality_label(LIVE_PARITY) == '실시간 · 0.5초 주기'
    assert quality.quality_label(ONE_MINUTE_CLOSE).startswith('봉마감')


def test_realtime_is_half_second_poll_and_matches_previous_default():
    settings = quality.live_parity_settings(LIVE_PARITY)
    assert settings == {'poll_ms': 500}
    assert configuration(settings) == configuration(None)  # identical results to before
    assert quality.live_parity_settings(TICK) is None and quality.live_parity_settings(ONE_MINUTE_CLOSE) is None


def test_realtime_evaluates_every_500ms():
    ticks = [SimpleNamespace(time_msc=ms) for ms in (0, 1300, 2600)]
    events = [(k, t) for k, t, _ in timeline(ticks, 0, 3_000_000_000, quality.live_parity_settings(LIVE_PARITY))]
    evaluate = [t for k, t in events if k == 'EVALUATE']
    assert evaluate == [i * 500_000_000 for i in range(6)]


def test_bar_close_uses_lowest_strategy_timeframe():
    assert evaluation_base_tf(('15m', '5m', '1h')) == '5m'
    assert evaluation_base_tf(('1h', '4h')) == '1h'


def test_gui_config_carries_realtime_setting():
    source = (Path(__file__).resolve().parents[1]/'generic_backtest'/'gui.py').read_text(encoding='utf-8')
    assert "'live_parity':live_parity_settings(mode)" in source
    assert 'values=tuple(QUALITY_LABELS)' in source
