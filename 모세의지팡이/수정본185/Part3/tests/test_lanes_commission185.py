"""185: a lanes plan's commission (dollars per lot, round trip) reaches its runs and its report's costs."""
import pytest

from test_lanes_cli184 import PLAN, finish, lanes, stage, trades, write  # noqa: F401 (stage is a fixture)
from test_lanes_cli184 import ROOT


def test_the_plan_takes_a_commission_and_its_runs_have_it():
    plan = lanes.normalize(dict(PLAN, commission=7))
    assert plan['commission'] == 7.0 and lanes.normalize(PLAN)['commission'] == 0.0
    s, _, _ = lanes.build(plan, lanes.every_lane(plan), ROOT)
    assert s['commission'] == {'XAUUSD+': 7.0} and s['spread_points'] == {'XAUUSD+': 20.0}
    for bad in (-1, 'seven', True, float('nan')):
        with pytest.raises(ValueError, match='commission은 0 이상'):
            lanes.normalize(dict(PLAN, commission=bad))


def test_twice_the_cost_takes_the_spread_and_the_commission_off_once_more(stage):
    plan_file, warehouse, jobs, _, _ = stage
    write(plan_file, dict(PLAN, strategies=['SPECIAL2'], triggers=['올존'], spread_points=20, commission=7))
    first = lanes.start(plan_file)
    row, = finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE')])
    folder = warehouse / 'runs' / row['run_id']
    # The run's R already has 0.2 spread and 0.07 commission off (stop 1 away).
    trades(folder, [(i, 2.0, 'WIN' if i % 5 < 3 else 'LOSS', 1.73 if i % 5 < 3 else -1.27) for i in range(40)])
    write(folder / 'result.json', {'status': 'COMPLETE', 'virtual_entry': {
        'trades_csv': f'runs/{row["run_id"]}/virtual_trades.csv',
        'pricing': {'spread_price': 0.2, 'commission_price': 0.07}}})
    jobs[first['job_id']] = {'phase': 'complete', 'active': False}
    _, found = lanes.rows(plan_file)
    assert found[0]['all']['average_r'] == pytest.approx((24 * 1.73 - 16 * 1.27) / 40)
    assert found[0]['double_cost']['average_r'] == pytest.approx((24 * 1.46 - 16 * 1.54) / 40)
    text = lanes.report(plan_file)
    assert '스프레드 20포인트 · 수수료 1랏 왕복 7달러를 넣고 계산한 값' in text


def test_a_zero_spread_warning_names_the_commission_it_has(stage):
    plan_file, _, _, _, _ = stage
    write(plan_file, dict(PLAN, spread_points=0, commission=7))
    text = lanes.report(plan_file)
    assert '스프레드 0으로 돌렸습니다' in text and '수수료 1랏 왕복 7달러는 넣었습니다' in text
