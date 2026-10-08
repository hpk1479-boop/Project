"""Real Part1 parsing/queue/STAFF/monitor, replacing external I/O only."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'audit'))
from harness import World, OneCycle

@pytest.fixture
def world():
    with World() as value:
        value.isolate_specs()
        yield value


def test_raw_command_queue_staff_feature_monitor_delivery(world):
    w = world
    w.manager.handle_command('BTCUSD 1분 기울기(SMA17) > 0 알려줘', '123')
    w.clock.advance(.001)
    w.manager.handle_command('BTCUSD 1분 골든크로스(SMA3, WMA5) 알려줘', '123')
    w.commands()
    assert len(w.generic._watches) == 2
    w.feed(w.market())
    before = len(w.http.deliveries)
    monitor = w.oz.GenericConditionMonitor('BTCUSD', w.config, w.generic)
    received = []
    original_request = monitor.client.compat.request
    def capture_response(request):
        result = original_request(request)
        received.append((request, result))
        return result
    monitor.client.compat.request = capture_response
    monitor.run(OneCycle())
    delivered = w.http.deliveries[before:]
    assert len(delivered) == 1 and '기울기(SMA17)' in delivered[0]['text']
    requests = [t for t in w.bus.trace if t['endpoint'] == w.server.endpoint]
    assert {tuple(t['request']['indicators']) for t in requests} == {('SMA17',), ('SMA3', 'WMA5')}
    slope_request = next(t for t in requests if t['request']['indicators'] == ['SMA17'])
    assert slope_request['request']['watch_ma_history_rows'] == 18
    frame = next(reply['1m'] for req, reply in received if req['indicators'] == ['SMA17'])
    assert slope_request['protocol'] == 'SNAPSHOT'
    assert not hasattr(w.server, '_watch_ma_features')
    np.testing.assert_array_equal(frame['SMA17'], w.trend.sma(frame['open'], 17))
    monitor.run(OneCycle())
    assert len(w.http.deliveries) == before + 1


@pytest.mark.parametrize('text', [
    'BTCUSD 1분 SMA0 > 1 알려줘', 'BTCUSD 1분 HMA 17 > 1 알려줘',
    'BTCUSD 1분 기울기(HMA17, 3) > 0 알려줘',
    'BTCUSD 1분 골든크로스(SMA20, ABC50) 알려줘',
    'BTCUSD 1분 정배열(SMA20) 알려줘',
])
def test_bad_raw_commands_visible_error_no_watch(world, text):
    world.manager.handle_command(text, '123')
    world.commands()
    assert not world.generic._watches
    assert any('오류' in event['text'] for event in world.http.deliveries)


@pytest.mark.parametrize('text', [
    'BTCUSD 1분 EMA50/200 골크 알려줘',
    'BTCUSD 1분 200EMA가 우하향일 때 알려줘',
    'BTCUSD 1분 상단 원비 터치 알려줘',
    'BTCUSD 1분봉 마감 알려줘',
])
def test_canonical_router_does_not_capture_existing_natural_text(world, text):
    assert world.manager._parse_canonical_ma_watch(text, '123') is None


def test_native_columns_preserved_and_same_ma_reused(world):
    w = world
    raw = w.market()
    raw.loc[3, 'hma_17'] = np.nan
    w.feed(raw)
    request = {'symbol': 'BTCUSD', 'timeframes': ['1m'],
               'indicators': ['HMA17', 'EMA50', 'SMA17', 'SMA17'],
               'watch_ma_history_rows': 22}
    first = w.data_request(request)['1m']
    misses = w.compat._watch_ma_features.cache.misses
    second = w.data_request(request)['1m']
    assert w.compat._watch_ma_features.cache.misses == misses == 3
    for name, old in [('HMA17','hma_17'), ('EMA50','ema_50')]:
        np.testing.assert_array_equal(first[name], raw[old])
    assert np.isnan(first['HMA17'].iloc[3])
    pd.testing.assert_frame_equal(first, second)
    # A subsequent legacy request does not acquire canonical features or seed changes.
    legacy = w.data_request({'symbol':'BTCUSD', 'timeframes':['1m'], 'indicators':['HMA','EMA']})['1m']
    assert 'SMA17' not in legacy and 'HMA17' not in legacy
    np.testing.assert_array_equal(legacy['hma_17'], raw['hma_17'])


def test_large_period_history_accumulates_without_mutating_source(world):
    provider = world.modules['watch_ma_features'].WatchMAFeatures()
    def frame(start, end, epoch=1):
        data = pd.DataFrame({'time':pd.to_datetime(np.arange(start,end)*60,unit='s'),
                             'open':np.arange(start,end,dtype=float),
                             'close':np.arange(start,end,dtype=float)+.5})
        data.attrs['source_epoch'] = epoch
        return data
    initial = frame(0,650); frozen = initial.copy(deep=True)
    first = provider.prepare('BTCUSD','1m',initial,['SMA701'],702)
    assert first['SMA701'].isna().all()
    later = frame(70,720)
    second = provider.prepare('BTCUSD','1m',later,['SMA701'],702)
    assert len(second) == 703 and second['SMA701'].iloc[-1] == np.mean(np.arange(19.,720.))
    pd.testing.assert_frame_equal(initial, frozen)
    assert 'SMA701' not in later
    reset = provider.prepare('BTCUSD','1m',frame(720,725,epoch=2),['SMA701'],702)
    assert len(reset)==5 and reset['SMA701'].isna().all()


def test_closed_cross_bootstrap_previous_current_and_future_guard(world):
    w = world
    value = w.manager._parse_canonical_ma_watch('BTCUSD 1분 골든크로스(SMA3, EMA5) 알려줘', '123')
    value.update(persistent=True, silent=True)
    w.generic.add(value)
    spec = next(iter(w.generic._watches.values()))
    monitor = w.oz.GenericConditionMonitor('BTCUSD', w.config, w.generic)
    frame = pd.DataFrame({'time':pd.date_range('2026-01-01', periods=4, freq='min'),
                          'SMA3':[99.,99.,101.,999.], 'EMA5':[100.]*4})
    fired = []
    w.generic.fire = lambda *args, **kwargs: fired.append((args, kwargs)) or True
    monitor._ma_expression_event(spec, '1m', frame) # seed only
    assert not fired
    # Prior closed = equal, new closed >; forming extreme must not affect cross.
    frame = pd.concat([frame.iloc[:3], pd.DataFrame({'time':[frame.time.iloc[-1]],
                           'SMA3':[100.], 'EMA5':[100.]})], ignore_index=True)
    extra = pd.DataFrame({'time':[frame.time.iloc[-1]+pd.Timedelta(minutes=1)],
                          'SMA3':[101.], 'EMA5':[100.]})
    frame = pd.concat([frame,extra],ignore_index=True)
    monitor._ma_expression_event(spec,'1m',frame) # current closed equal -> false
    assert not fired
    extra = pd.DataFrame({'time':[frame.time.iloc[-1]+pd.Timedelta(minutes=1)],
                          'SMA3':[-99999.], 'EMA5':[100.]})
    frame = pd.concat([frame,extra],ignore_index=True)
    monitor._ma_expression_event(spec,'1m',frame)
    assert len(fired)==1 and fired[0][1]['direction']=='LONG'
    assert fired[0][1]['event_time'] == frame.time.iloc[-1].timestamp()
    monitor._ma_expression_event(spec,'1m',frame)
    assert len(fired)==1


def test_live_slope_edge_and_restart_persistence(world):
    w=world
    value=w.manager._parse_canonical_ma_watch('BTCUSD 1분 기울기(HMA17) > 0 알려줘','123')
    value.update(persistent=True,silent=True)
    w.generic.add(value)
    w.restart_oz()
    spec=next(iter(w.generic._watches.values()))
    assert spec.ma_expression=='기울기(HMA17) > 0.0'
    monitor=w.oz.GenericConditionMonitor('BTCUSD',w.config,w.generic)
    fired=[];w.generic.fire=lambda *a,**kw: fired.append(kw) or True
    frame=pd.DataFrame({'time':pd.date_range('2026-01-01',periods=3,freq='min'),
                        'HMA17':[1.,900.,2.]})
    monitor._ma_expression_event(spec,'1m',frame)
    monitor._ma_expression_event(spec,'1m',frame)
    assert len(fired)==1 # positive versus 2 bars ago, negative versus previous
    frame.loc[2,'HMA17']=0.
    monitor._ma_expression_event(spec,'1m',frame)
    w.clock.advance(1)
    frame.loc[2,'HMA17']=3.
    monitor._ma_expression_event(spec,'1m',frame)
    assert len(fired)==2


def test_live_imports_never_load_part2(world):
    assert all('Part2' not in str(getattr(module,'__file__',''))
               for module in world.modules.values())
    assert all(Path(module.__file__).is_relative_to(world.path)
               for module in world.modules.values())
