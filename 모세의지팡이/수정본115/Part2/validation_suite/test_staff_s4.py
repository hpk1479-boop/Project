"""S4 client ownership, transport and immutable strategy boundary checks."""
import ast
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from test_staff_s3 import env, publish, equal, legacy
from test_staff_s2 import view


@pytest.mark.parametrize('owner', ['strategy_SWEEP', 'strategy_FVG', 'strategy_INDICATOR'])
def test_strategy_uses_snapshot_without_server_calculations(env, owner):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    mod = rt.modules[owner]
    requests = []
    def transport(parts):
        requests.append(parts)
        return server.dispatch_multipart(parts)
    actual = mod.StaffClient(transport=transport)
    with patch.object(staff, 'apply_requested_features', side_effect=AssertionError('server calculation'), create=True):
        frames = actual.request('BTCUSD', ['1m'], mod.REQUIRED_INDS)
    assert len(requests) == 1 and requests[0][0] == b'STAFF_SNAPSHOT_V1'
    expected = view(cache).get('BTCUSD', '1m')
    pd.testing.assert_frame_equal(frames['1m'], expected, check_exact=True)
    assert frames['1m'].attrs == expected.attrs
    assert 'atr_14' not in frames['1m'] and 'wonbi_upper' not in frames['1m']
    frames['1m'].iloc[0, 1] = -999
    pd.testing.assert_frame_equal(actual.request('BTCUSD', ['1m'], mod.REQUIRED_INDS)['1m'], expected, check_exact=True)
    assert actual.request('BTCUSD', ['5m'], mod.REQUIRED_INDS) is None


def assert_client_only_changes(before, after, allowed):
    """Every non-client AST node is exact, including all strategy calculations."""
    def stripped(path):
        tree = ast.parse(path.read_bytes())
        tree.body = [n for n in tree.body if getattr(n, 'name', None) not in allowed]
        return ast.dump(tree)
    assert stripped(before) == stripped(after)


@pytest.mark.parametrize('owner', ['strategy_SWEEP', 'strategy_FVG', 'strategy_INDICATOR'])
def test_strategy_logic_exact_s3(owner):
    root = Path(__file__).resolve().parents[2]
    assert_client_only_changes(root.parent / '수정본10/Part1/program' / (owner+'.py'),
                              root / 'Part1/program' / (owner+'.py'), {'StaffClient'})


def test_watch_client_exact_history_epoch_and_forming_corrections(env):
    rt, staff, cache, server, lib, client, compat = env
    actual = rt.modules['monitor_OZ'].StaffClient('', transport=server.dispatch_multipart)
    req = {'symbol':'BTCUSD', 'timeframes':['1m'], 'indicators':['SMA701', 'EMA9', 'HMA17'],
           'watch_ma_history_rows':703}
    for seq, shift in [(1,0), (2,70*60), (3,70*60), (4,140*60)]:
        publish(cache, seq, rows=650, shift=shift)
        expected = legacy(server, req)
        observed = actual.request('BTCUSD', ['1m'], req['indicators'], watch_ma_history_rows=703)
        equal(expected, observed)
        assert actual.compat._watch_ma_features is not server._test_reference._watch_ma_features
        assert actual.compat._watch_ma_features.frames is not server._test_reference._watch_ma_features.frames
    cache.reconnect()
    publish(cache, 1, rows=650, shift=200*60)
    equal(legacy(server, req), actual.request('BTCUSD', ['1m'], req['indicators'], watch_ma_history_rows=703))
    assert len(actual.compat._watch_ma_features.frames[('BTCUSD','1m')]) == 650


def test_watch_computes_only_in_client(env):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    actual = rt.modules['monitor_OZ'].StaffClient('', transport=server.dispatch_multipart)
    with patch.object(staff, 'apply_requested_features', side_effect=AssertionError('server calculation'), create=True):
        result = actual.request('BTCUSD', ['1m'], ['SMA17'], watch_ma_history_rows=18)
    assert 'SMA17' in result['1m'] and not hasattr(server, '_watch_ma_features')
    assert result['1m'].attrs['watch_ma_history_required'] == 650
    first = actual.compat._watch_ma_features.cache.misses
    actual.request('BTCUSD', ['1m'], ['SMA17'], watch_ma_history_rows=18)
    assert actual.compat._watch_ma_features.cache.misses == first


def test_oz_direct_facts_exact_legacy_and_cache_invalidation(env):
    rt, staff, cache, server, lib, client, compat = env
    oz = rt.modules['monitor_OZ']
    actual = oz.StaffClient('', transport=server.dispatch_multipart)
    req = {'symbol':'BTCUSD','timeframes':['1m'],'indicators':oz.REQUIRED_INDS}
    for seq, sigma in [(1,3.0), (1,2.5), (2,2.5)]:
        publish(cache, seq, dirty=True)
        server.wonbi_state.set_sigma(sigma)
        expected = legacy(server, req)
        with patch.object(staff, 'apply_requested_features', side_effect=AssertionError('server calculation'), create=True):
            result = actual.request('BTCUSD',['1m'])
        equal(expected, result)
        facts = actual.oz_features.facts
        computed = facts.computed
        result['1m'].iloc[0,1] = -999
        for _ in range(3):
            equal(expected, actual.request('BTCUSD',['1m']))
        assert facts.computed == computed
        assert facts.reused > facts.computed
    cache.reconnect()
    publish(cache, 1)
    equal(legacy(server, req), actual.request('BTCUSD',['1m']))


def test_oz_external_liquidity_uses_general_atr_fact(env):
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    oz = rt.modules['monitor_OZ']
    actual = oz.StaffClient('', transport=server.dispatch_multipart)
    raw = view(cache).get('BTCUSD','1m')
    expected = rt.modules['indicator_facts'].standalone_frame(raw,'1m').get('ATR14_GENERAL')
    result = actual.request('BTCUSD',['1m'])['1m']
    pd.testing.assert_series_equal(result[oz.EXTERNAL_ATR_COLUMN], expected.rename(oz.EXTERNAL_ATR_COLUMN), check_exact=True)
    shared = oz.SharedOZStaffClient('', transport=server.dispatch_multipart)
    assert shared.facts is shared.client.oz_features.facts
    a = shared.request('BTCUSD',['1m'])['1m']
    b = shared.request('BTCUSD',['1m'])['1m']
    assert a is b


def test_manager_and_special_lanes_use_compat(env):
    from types import SimpleNamespace
    rt, staff, cache, server, lib, client, compat = env
    publish(cache)
    kim = rt.modules['manager_KIM']
    lanes = [kim.StaffClientV2('', transport=server.dispatch_multipart) for _ in range(2)]
    api = kim.SpecialPluginAPI(SimpleNamespace(staff=lanes[0], _special_event_staff=lanes[1]))
    req={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['PRICE','WONBI','SMA17']}
    expected=legacy(server, req)
    with patch.object(staff, 'apply_requested_features', side_effect=AssertionError('server calculation'), create=True):
        for lane in ('maintenance','event'):
            equal(expected, api.staff_request('BTCUSD',['1m'],req['indicators'],lane=lane))
    assert lanes[0].compat is not lanes[1].compat
    assert lanes[0].compat._watch_ma_features is not lanes[1].compat._watch_ma_features


def test_special_and_calculation_owners_unchanged():
    root=Path(__file__).resolve().parents[2]
    old=root.parent/'수정본10/Part1/program'
    new=root/'Part1/program'
    for name in ['indicator_facts.py','watch_ma.py','watch_ma_features.py','watch_orchestrator.py',
                 'staff_compat.py',*[f'SPECIAL/SPECIAL{i}.py' for i in range(1,8)]]:
        assert (old/name).read_bytes() == (new/name).read_bytes()
    assert_client_only_changes(old/'manager_KIM.py',new/'manager_KIM.py',{'StaffClientV2'})
    assert_client_only_changes(old/'monitor_OZ.py',new/'monitor_OZ.py',
                              {'StaffClient','SharedOZStaffClient','OZSnapshotFeatures'})
    # S5 removes only the explicitly authorized server calculation/storage nodes.
    from test_staff_s5 import assert_staff_scope
    assert_staff_scope()
    # Shared caching/TTL and the manager control/health contract remain exact.
    def methods(path, cls):
        tree=ast.parse(path.read_bytes())
        node=next(n for n in tree.body if getattr(n,'name',None)==cls)
        return {n.name:ast.dump(n) for n in node.body if isinstance(n,ast.FunctionDef)}
    for file,cls,names in [('monitor_OZ.py','SharedOZStaffClient',['request']),
                           ('manager_KIM.py','StaffClientV2',['health'])]:
        a,b=methods(old/file,cls),methods(new/file,cls)
        assert all(a[n]==b[n] for n in names)
