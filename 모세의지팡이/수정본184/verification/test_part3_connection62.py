"""Public plugin connections, real Fact validation and generated LIVE/replay semantics."""
import ast
import copy
from pathlib import Path
import types

import pytest
from event_application import create_event_engine
from event_engine.model import Kind
from lab.ai.schema import recipe_from_intent
from lab.compiler import compile_recipe
from test_ai_intent_v2 import intent
from test_common_recipe60 import market, notify, price_step

CONFIG = dict(WONBI_SIGMA="3", TELEGRAM_TOKEN="", TELEGRAM_CHAT_ID="TEST",
              STAFF_ALLOWED_SYMBOLS="XAUUSD+", TARGET_SYMBOLS="XAUUSD+")
STAMP = 1790000040


def engine(plugins, live=False):
    return create_event_engine(CONFIG, plugins=plugins, enabled_specials=tuple(plugins),
                               symbols=("XAUUSD+",), backtest=not live, oz_evaluation="all")


def send(e, stamp=STAMP, feeds=None):
    e.ingress.post(Kind.MARKET_BUNDLE, source="public-api-test", source_seq=stamp,
                   source_time=stamp * 1000,
                   payload=dict(symbol="XAUUSD+", feeds=feeds or {"1m": market(stamp)}))
    e.run()
    assert not e.error_log, e.error_log
    return e.strategy_state["COMPOSER"]["kernels"]["XAUUSD+"]


def notices(e):
    return [(s.source_time, dict(s.payload["content"])) for s in e.signals
            if s.payload.get("content", {}).get("type") == "NOTIFICATION"]


def module(raw, number):
    name = f"Test_SPECIAL{number}"
    source = compile_recipe(recipe_from_intent(raw), name + ".py")
    mod = types.ModuleType(name)
    mod.__file__ = str(Path(__file__).resolve().parents[1] / "Part3/generated" / (name + ".py"))
    exec(compile(source, name + ".py", "exec"), mod.__dict__)
    return mod, source


class PublicManager:
    """A generated strategy gets only the documented capability, no private manager."""
    __slots__ = ("special_api",)

    def __init__(self, api):
        self.special_api = api

    def __getattr__(self, name):
        raise AssertionError("Undocumented manager access: " + name)


@pytest.mark.parametrize("base", ["CUSTOM", "SPECIAL4", "SPECIAL5"])
def test_generated_registration_needs_only_public_manager_capability(base):
    raw = notify(price_step(relation="BREAK_UP"), direction="LONG")
    if base != "CUSTOM":
        raw["interpretation"] = dict(preset=base, symbols=["XAUUSD+"])
    mod, _ = module(raw, 771)
    wrapper = types.ModuleType("Test_SPECIAL771")
    wrapper.register = lambda manager: mod.register(PublicManager(manager.special_api))
    e = engine({wrapper.__name__: wrapper})
    kernel = send(e)
    # These methods are inherited from Part1, never assigned to the live instance.
    assert "_desired_subscriptions_locked" not in vars(kernel.manager)
    assert "_handle_fact_event" not in vars(kernel.manager)
    assert "_part3_intent_facts" not in vars(kernel.manager)


def test_current_generated_sources_do_not_read_or_write_part1_private_members():
    for base in ("CUSTOM", "SPECIAL4", "SPECIAL5"):
        raw = notify(price_step())
        if base != "CUSTOM":
            raw["interpretation"] = dict(preset=base, symbols=["XAUUSD+"])
        _, source = module(raw, 772)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                if isinstance(node.value, ast.Name):
                    assert node.value.id not in ("manager", "board"), ast.unparse(node)
                elif isinstance(node.value, ast.Attribute):
                    assert not (node.value.attr == "manager" and
                                isinstance(node.value.value, ast.Name) and node.value.value.id == "self"), ast.unparse(node)


def payload(wid, tf):
    return dict(action="FVG_WATCH", watch_id=wid, symbol="XAUUSD+", source_tf=tf)


def test_subscription_owners_merge_replace_and_cancel_through_existing_commands():
    api_holder = []

    def register(manager):
        api = manager.special_api
        api_holder.append(api)
        api.register_subscription_provider("one", lambda: {"FVG": {"one": payload("one", "1m")}})
        api.register_subscription_provider("two", lambda: {"FVG": {"two": payload("two", "5m")}})

    probe = types.ModuleType("subscription_probe")
    probe.register = register
    e = engine({probe.__name__: probe})
    send(e, feeds={tf: market(STAMP, tf=tf) for tf in ("1m", "5m", "15m")})
    commands = [dict(s.payload["content"]["command"]) for s in e.signals
                if s.payload.get("content", {}).get("type") == "WATCH_COMMAND"]
    assert {c["watch_id"] for c in commands if c["action"] == "FVG_WATCH"} >= {"one", "two"}
    before = len(e.signals)
    api_holder[0].register_subscription_provider("one", lambda: {"FVG": {"replacement": payload("replacement", "15m")}})
    send(e, STAMP + 60, {tf: market(STAMP + 60, tf=tf) for tf in ("1m", "5m", "15m")})
    commands = [dict(s.payload["content"]["command"]) for s in e.signals[before:]
                if s.payload.get("content", {}).get("type") == "WATCH_COMMAND"]
    assert any(c["action"] == "CANCEL_FVG" and c["watch_id"] == "one" for c in commands)
    assert any(c["action"] == "FVG_WATCH" and c["watch_id"] == "replacement" for c in commands)
    assert not any(c["action"] == "CANCEL_FVG" and c["watch_id"] == "two" for c in commands)


def test_fact_observers_receive_only_accepted_fresh_owned_snapshots():
    first, second, holder = [], [], []

    def register(manager):
        api = manager.special_api
        holder.append(api)
        api.register_subscription_provider("facts", lambda: {"FVG": {"facts": payload("facts", "1m")}})

        def observer(event):
            if event.get("kind") == "FACT_SNAPSHOT":
                first.append(copy.deepcopy(event))
                event["facts"].append({"mutation": "must not reach another observer"})

        api.register_fact_observer("one", observer)
        api.register_fact_observer("two", lambda event: second.append(copy.deepcopy(event))
                                   if event.get("kind") == "FACT_SNAPSHOT" else None)

    probe = types.ModuleType("fact_probe")
    probe.register = register
    e = engine({probe.__name__: probe})
    send(e)
    send(e, STAMP + 60)
    assert first and second
    assert not any(f.get("mutation") for f in second[-1]["facts"])
    raw = copy.deepcopy(second[-1])
    raw.pop("event_id", None)
    raw["fact_revision"] = [raw["fact_revision"][0], raw["fact_revision"][1] + 1]
    count = len(second)

    def post(event):
        e.ingress.post(Kind.SIGNAL, source="public-fact-test", source_seq=len(e.events) + 1,
                       source_time=STAMP * 1000, payload=dict(symbol="XAUUSD+",
                       content=dict(type="DOMAIN_FACT", family="FVG", event=event)))
        e.run()
        assert not e.error_log, e.error_log

    post(dict(raw, complete=False))
    assert len(second) == count  # Invalid input was never delivered to observers.
    post(raw)
    assert len(second) == count + 1
    post(raw)
    assert len(second) == count + 1  # Equal revision is stale even without event_id.
    replaced = []
    holder[0].register_fact_observer("two", replaced.append)
    raw["fact_revision"][1] += 1
    post(raw)
    assert len(replaced) == 1 and len(second) == count + 1
    assert replaced[0]["facts"] == raw["facts"]


def test_shared_fact_resources_are_manager_local_and_built_once():
    seen, builds = [], []

    def register(manager):
        api = manager.special_api

        def create():
            builds.append(1)
            return dict(values=[])

        first = api.shared_resource("shared-test-fact", create)
        second = api.shared_resource("shared-test-fact", create)
        assert first is second
        seen.append(first)

    probe = types.ModuleType("resource_probe")
    probe.register = register
    send(engine({probe.__name__: probe}))
    send(engine({probe.__name__: probe}))
    assert len(builds) == 2 and seen[0] is not seen[1]


def test_oz_registration_scope_captures_only_new_owners_and_unwinds():
    holder = []
    probe = types.ModuleType("oz_scope_probe")
    probe.register = lambda manager: holder.append(manager.special_api)
    send(engine({probe.__name__: probe}))
    api = holder[0]
    handler = types.SimpleNamespace(handle_oz_event=lambda event: dict(ok=True, delivered=True))
    api.register_oz_handler("existing", handler)
    with pytest.raises(RuntimeError), api.capture_oz_handlers() as captured:
        api.register_oz_handler("new", handler)
        api.register_oz_handler("existing", handler)
        raise RuntimeError("registration scope ended")
    assert captured == {"new": handler}
    with api.capture_oz_handlers() as fresh:
        api.register_oz_event_handler("next", handler)  # Existing public name stays compatible.
    assert fresh == {"next": handler}


def test_two_generated_strategies_checkpoint_keep_live_replay_notifications():
    plugins = {}
    for number, tf in ((773, "1m"), (774, "5m")):
        mod, _ = module(notify(price_step(tf=tf, relation="BREAK_UP"), direction="LONG"), number)
        plugins[mod.__name__] = mod
    live, replay = engine(plugins, live=True), engine(plugins)
    feeds = {tf: market(STAMP, current=98., previous=98., tf=tf) for tf in ("1m", "5m")}
    send(live, feeds=feeds)
    send(replay, feeds=feeds)
    state = replay.checkpoint()
    resumed = engine(plugins)
    resumed.restore(state)
    next_stamp = STAMP + 300
    feeds = {tf: market(next_stamp, current=102., previous=98., tf=tf) for tf in ("1m", "5m")}
    for e in (live, replay, resumed):
        send(e, next_stamp, feeds)
        send(e, next_stamp + 1, feeds)
        assert len(notices(e)) == 2
    assert notices(live) == notices(replay) == notices(resumed)
    assert {n[1]["source_tf"] for n in notices(live)} == {"1m", "5m"}
    assert all(n[1]["direction"] == "LONG" for n in notices(live))


def test_public_loader_preserves_host_function_and_loads_only_selected_plugins():
    import event_application
    original = event_application.load_strategy_inputs
    calls = []
    plugin = types.ModuleType("Test_SPECIAL775")
    plugin.register = lambda manager: None

    def load():
        calls.append(1)
        return plugin

    event_application.register_strategy_loader(plugin.__name__, load, dependencies=("OZ", "WATCH"))
    try:
        assert event_application.load_strategy_inputs is original
        assert not event_application.load_strategy_inputs({}, (), {})[2]
        assert calls == []
        assert event_application.load_strategy_inputs({}, (plugin.__name__,), {})[2] == {plugin.__name__: plugin}
        assert calls == [1]
        # A preset may use configured symbols; an empty configuration must not
        # invent one just to produce a plugin. A valid configuration loads it.
        assert not event_application.load_strategy_inputs({}, ("SPECIAL1",), {})[2]
        assert set(event_application.load_strategy_inputs({}, ("SPECIAL1",), {}, config=CONFIG)[2]) == {"SPECIAL1"}
        assert calls == [1]
    finally:
        assert event_application.unregister_strategy_loader(plugin.__name__)
    assert not event_application.unregister_strategy_loader(plugin.__name__)


@pytest.mark.parametrize("method", ["register_fact_observer", "register_subscription_provider", "shared_resource"])
def test_public_connections_reject_empty_owner_and_noncallables(method):
    holder = []
    probe = types.ModuleType("invalid_connection_probe")
    probe.register = lambda manager: holder.append(manager.special_api)
    send(engine({probe.__name__: probe}))
    callback = getattr(holder[0], method)
    with pytest.raises(ValueError):
        callback("", lambda: {})
    with pytest.raises(ValueError):
        callback("owner", None)
