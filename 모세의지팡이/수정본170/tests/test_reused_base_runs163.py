"""163: every run requested together names the others, a base frame that reused a finished replay included."""
import json

from test_variant_runs161 import backtest, on_base, scenario  # noqa: F401  (backtest is the fake-replay fixture)


def test_a_reused_base_frame_is_still_tested_with_the_others(backtest):
    from event_backtest.settings import variant_scenarios
    run_many, warehouse, _ = backtest
    (done,), _ = run_many([scenario(virtual_entry=on_base('2m'))])
    results, seen = run_many(variant_scenarios(scenario(virtual_bases=['1m', '2m', '5m'])))
    assert seen == [['SIGNAL', '5m']] * 2 and results[1]['replay_reused_from'] == done['run_id']
    ids = [result['run_id'] for result in results]
    for result in results:
        others = sorted(run_id for run_id in ids if run_id != result['run_id'])
        assert sorted(result['tested_with']) == others
        stored = json.loads((warehouse / result['result_path']).read_text('utf-8'))
        assert sorted(stored['tested_with']) == others
    assert results[0]['replayed_with'] == [ids[2]]  # the replay itself was shared by the two replayed runs only


def test_a_run_requested_alone_names_no_others(backtest):
    run_many, _, _ = backtest
    (alone,), _ = run_many([scenario(virtual_entry=on_base('2m'))])
    assert 'tested_with' not in alone
