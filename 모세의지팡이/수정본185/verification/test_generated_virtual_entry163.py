"""163: a generated strategy (run under its file name, Test_SPECIAL777) takes a virtual entry.

Its library ID is TEST_SPECIAL777, a name its external loader cannot take, so the virtual entry reads the
recipe beside the file by file name; on another base frame the replay tests a copy moved to that frame.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from test_generated_backtest_adapter67 import adapter, data, entry, project  # noqa: F401  (project is a fixture)


@pytest.fixture
def library(project, monkeypatch):
    import ast
    from strategy_recipe import user_catalog
    code = project.strategy.read_text('utf-8')
    recipe = ast.literal_eval(code.split('PART3_RECIPE = ', 1)[1].split('\n', 1)[0])
    project.strategy.with_suffix('.recipe.json').write_text(json.dumps(
        {'filename': project.strategy.name, 'recipe': recipe,
         'sha256': hashlib.sha256(project.strategy.read_bytes()).hexdigest()}), encoding='utf-8')
    read = user_catalog.generated_entries
    monkeypatch.setattr(user_catalog, 'generated_entries', lambda root=None, errors=None: read(project.root, errors))
    return project


def other_base(profile):
    return next(tf for tf in profile['bases'] if tf != profile['base'])


def test_the_virtual_entry_reads_the_recipe_beside_the_file(library):
    from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
    profile = strategy_profile('Test_SPECIAL777')
    assert profile == strategy_profile('TEST_SPECIAL777')          # the same library entry
    assert profile['base'] == '1m' and profile['timeframes'] == ['1m']
    target = other_base(profile)
    scenario, _ = adapter.prepare_generated(data(library, result_mode='VIRTUAL_ENTRY',
                                                 virtual_entry=recipe_on_base(profile, target)), library.root)
    assert scenario['strategies'] == ['Test_SPECIAL777'] and scenario['virtual_entry']['tf'] == target
    assert scenario['base_frames'] == {'Test_SPECIAL777': target}
    with pytest.raises(ValueError, match='등록되지 않은 preset'):
        strategy_profile('Test_SPECIAL778')                         # no such file: still refused


def test_the_replay_tests_a_copy_moved_to_the_chosen_base_frame(library, monkeypatch):
    import event_application
    from event_backtest.base_frames import moved_plugins
    from event_backtest.virtual_defaults import strategy_profile
    from strategy_recipe import port
    target = other_base(strategy_profile('Test_SPECIAL777'))
    generator = entry(library, monkeypatch)
    next(generator)
    try:
        _, _, plugins = event_application.load_strategy_inputs({}, ('Test_SPECIAL777',), {})
        moved = moved_plugins(plugins, {'Test_SPECIAL777': target})['Test_SPECIAL777']
        meaning = moved.recipe['strategy_intent']
        assert meaning['steps'][0]['tfs'] == [target] and meaning['final']['tfs'] == [target]
        assert moved.OZ_DECLARATIONS == frozenset({(target, 'NORMAL', 'OZ')})
        installed = []
        monkeypatch.setattr(port.IntentPort, 'install', lambda self: installed.append(self))
        api = SimpleNamespace(market_context=lambda: (None, 'XAUUSD+', 0), shared_resource=lambda name, factory: factory(),
            register_subscription_provider=lambda *a: None, register_fact_observer=lambda *a: None,
            register_strategy_state_provider=lambda *a: None, register_watch_handler=lambda *a: None,
            register_oz_handler=lambda *a: None, snapshot_oz_watches=lambda **k: [])
        manager = SimpleNamespace(special_api=api)
        moved.register(manager)
        plugins['Test_SPECIAL777'].register(manager)                # the file's own plan is unchanged
        assert [item.meaning['final']['tfs'] for item in installed] == [[target], ['1m']]
        assert {item.namespace for item in installed} == {'Test_SPECIAL777'}
    finally:
        generator.close()
