"""monitor_OZ / strategy_FVG optimization: results must not change.

Same synthetic day, same STAFF input, same SPECIAL7 trigger slot and the same Watches
(OZ profiles NORMAL/BLIND x OZ/BREAKER/REGIME/SUPER, FVG condition, FVG creation):

  BEFORE-LIVE   : the complete frozen revision-6 Part1/program, hash verified;
                  STAFF payload published every second. No current modules are mixed in.
  AFTER-LIVE    : current Part1 (common OZ Facts shared by the 16 profiles, FVG structure
                  only on closed-bar change), same LIVE publication.
  AFTER-BACKTEST: current Part1 fed from the EA STAFF_PIPE_V1 capture of the same seconds
                  (the BACKTEST CONTROL input path).

Every final alert, every Telegram text, the complete state of all 16 OZ monitors, the
FVG engine state and every event the manager received from FVG must be identical in all
three runs. CPU performance must meet the S1-S8 frozen repeated-measurement rule
and must actually reuse Facts. The original one-shot wall result remains evidence.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from part1_host import capture, engine
from staff_golden import baseline_host as runtime
from part1_host.synthetic import SyntheticMarket, TIMEFRAMES

PART1 = ROOT.parent / 'Part1'
FIXTURES = PART1 / 'audit' / 'fixtures'
SYMBOL = 'XAUUSD+'
START = int(dt.datetime(2026, 9, 22, 0, 0, tzinfo=dt.timezone.utc).timestamp())   # 09:00 KST
PATTERN = ((0, 0), (180, -0.5), (360, 3.0), (420, 3.2), (540, 1.0), (600, 0.8), (720, 3.1), (780, 2.5), (900, 1.0))
SEED = 0
WINDOW = 240
SPECIALS = ['SPECIAL7']
TRIGGERS = {'SPECIAL7': '무지성 올존'}
WATCHES = (
    ('881001', '골드 1분 매도 올존 알려줘'),                       # NORMAL / OZ
    ('881002', '골드 1분 무지성 올존 알려줘'),                     # BLIND / OZ
    ('881003', '골드 1분 브레이커 올존 알려줘'),                 # NORMAL / BREAKER
    ('881004', '골드 1분 무지성 레짐 브레이커 올존 알려줘'),     # BLIND / BREAKER+REGIME
    ('881005', '골드 3분 슈퍼 레짐 올존 알려줘'),                  # NORMAL / REGIME+SUPER
    ('881006', '골드 2분 무지성 슈퍼 올존 알려줘'),                # BLIND / SUPER
    ('881007', '골드 5분 FVG 터치일때 1분 매도 올존 알려줘'),      # FVG condition -> OZ
    ('881008', '골드 1분 FVG 생성 알려줘'),                        # FVG creation watch
)


def legacy_root(tmp: Path) -> Path:
    """Whole frozen revision-6 program, with no current modules mixed into BEFORE."""
    root = tmp / 'Part1'
    frozen = ROOT.parent / '검증결과/staff_s0/baseline_input/Part1/program'
    shutil.copytree(frozen, root / 'program')
    # Validate the whole input tree against S0's original revision-6 manifest.
    import hashlib
    manifest = json.loads((ROOT.parent / '검증결과/staff_s0/source6_manifest.json').read_text('utf-8-sig'))
    expected = {r['path'].removeprefix('Part1/program/'): r['sha256'] for r in manifest
                if r['path'].startswith('Part1/program/')}
    actual = {p.relative_to(root / 'program').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (root / 'program').rglob('*') if p.is_file()}
    if actual != expected:
        raise RuntimeError('BEFORE program differs from frozen revision 6')
    return root


def _plain(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def run(market, *, part1_root, feed):
    rt = runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START - 1, specials=SPECIALS,
                              trigger_overrides=TRIGGERS, part1_root=part1_root,
                              log_level=logging.CRITICAL)
    try:
        logging.disable(logging.CRITICAL)
        for chat, text in WATCHES:
            rt.manager.handle_command(text, chat)
        rt.start_services()
        fvg_events = []
        original = rt._manager_handle

        def spy(request):
            if isinstance(request, dict) and request.get('strategy') == 'FVG':
                event = {k: v for k, v in request.items() if k != 'event_id'}
                health = event.get('source_health')
                if isinstance(health, dict):
                    # STAFF source_epoch = '<random per-process session uuid>:<counter>'.
                    # Only the random session part differs between runs.
                    event['source_health'] = dict(health, sources={
                        tf: (v.split(':', 1)[1] if isinstance(v, str) and ':' in v else v)
                        for tf, v in (health.get('sources') or {}).items()})
                fvg_events.append(_plain(event))
            return original(request)

        rt.bus.handlers[rt.manager.alert_endpoint] = spy
        wall = time.perf_counter()
        cpu = time.process_time()
        for second in range(START, START + WINDOW):
            rt.clock.set(second)
            if feed == 'live':
                for tf in TIMEFRAMES:
                    rt.publish(capture.pack_wire(SYMBOL, tf, *market.payload(tf, second)))
            else:
                feed.advance_to(second)
                for _tf, wire in feed.wires():
                    rt.publish(wire)
            rt.run_until(second + 0.999)
        cpu = time.process_time() - cpu
        wall = time.perf_counter() - wall
        oz = rt.modules['monitor_OZ']
        monitors = rt.oz_monitors[SYMBOL]
        oz_state = {f'{m.validation_mode}/{m.trigger_mode}': json.dumps(
            {name: oz._observed_encode(getattr(m, name)) for name in m._checkpoint_fields},
            sort_keys=True, default=str) for m in monitors}
        fvg = rt.engines['FVG'].engine
        facts = getattr(monitors[0], '_oz_facts', None)
        cache = getattr(rt.modules['strategy_FVG'], 'STRUCTURE_CACHE', None)
        return {
            'finals': [_plain({k: v for k, v in a.to_json().items() if k != 'event_id'}) for a in rt.alerts],
            'special': [a.strategy for a in rt.special_alerts()],
            'telegram': [(d['virtual_time'], str(d['data'].get('chat_id')), str(d['data'].get('text', '')))
                         for d in rt.http.deliveries],
            'oz_state': oz_state,
            'fvg_state': _plain({'active': {'|'.join(k): v for k, v in fvg._active_zones.items()},
                                 'touch': fvg._touch_state, 'seen': sorted(fvg._seen_created),
                                 'closed': {'|'.join(k): v for k, v in fvg._last_closed_time.items()}}),
            'fvg_events': fvg_events,
            'wall': wall,
            'cpu': cpu,
            'oz_fact_memo': None if facts is None else {'computed': facts.computed, 'reused': facts.reused},
            'fvg_structure': None if cache is None else {'builds': cache.structure_builds,
                                                         'reuses': cache.structure_reuses},
        }
    finally:
        logging.disable(logging.NOTSET)
        rt.close()


@pytest.fixture(scope='module')
def results(tmp_path_factory):
    market = SyntheticMarket(SYMBOL, START, START + WINDOW, history_days=30, seed=SEED, pattern=PATTERN)
    export = tmp_path_factory.mktemp('ea-capture')
    writer = capture.CaptureWriter(export, SYMBOL, TIMEFRAMES)
    for second in range(START, START + WINDOW):
        for index, tf in enumerate(TIMEFRAMES):
            writer.write(index, second, *market.payload(tf, second))
    writer.close()
    before = run(market, part1_root=legacy_root(tmp_path_factory.mktemp('legacy')), feed='live')
    after = run(market, part1_root=PART1, feed='live')
    backtest = run(market, part1_root=PART1, feed=engine.SecondFeed(export, SYMBOL))
    out = ROOT / 'generic_runs' / 'oz_fvg_optimization'
    out.mkdir(parents=True, exist_ok=True)
    summary = {name: {k: (v if k in ('wall', 'cpu', 'oz_fact_memo', 'fvg_structure', 'special') else len(v))
                      for k, v in r.items()} for name, r in (('before_live', before), ('after_live', after),
                                                             ('after_backtest', backtest))}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'telegram.json').write_text(json.dumps(after['telegram'], ensure_ascii=False, indent=2), encoding='utf-8')
    return before, after, backtest


@pytest.fixture(scope='module')
def performance_results(request):
    """S5/S8 gate; other stages record one timing without judging speed."""
    import hashlib
    import os
    sys.path.insert(0, str(ROOT.parent / 'build'))
    from staff_performance_policy import POLICY, evaluate, scenario_digest
    from staff_performance_protocol import program_hashes, sha
    policy = json.loads(POLICY.read_text('utf-8'))
    assert scenario_digest() == policy['scenario_contract_sha256']
    stage = os.environ.get('STAFF_STAGE', 'S1')
    assert stage in policy['stages']
    if stage not in policy['gating_stages']:
        reference = os.environ.get('STAFF_PERFORMANCE_REFERENCE')
        if reference:
            saved = json.loads(Path(reference).read_text('utf-8'))
            assert saved['source_sha256'] == program_hashes(PART1)
            before, after = saved['before_live'], saved['after_live']
        else:
            before, after, _ = request.getfixturevalue('results')
        return [({'scenario': before}, {'scenario': after})], after['wall']/before['wall'], None
    folder = Path(os.environ.get('STAFF_PERFORMANCE_RESULTS',
                  ROOT.parent / '검증결과/staff_s1/performance_protocol'))
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    load = lambda name: json.loads((folder / name).read_text('utf-8'))
    comparison = load('comparison.json')
    assert comparison['stage'] == stage
    assert sha(POLICY) == comparison['policy_sha256']
    assert digest(ROOT.parent / 'build/staff_performance_protocol.py') == policy['measurement_runner_sha256']
    current = {p.relative_to(PART1 / 'program').as_posix(): digest(p)
               for p in (PART1 / 'program').rglob('*') if p.is_file()}
    pairs = [(load(f'pair_{i}_s0.json'), load(f'pair_{i}_candidate.json')) for i in range(1,6)]
    for before, after in pairs:
        assert before['source_sha256'] == policy['baseline_source']
        assert after['source_sha256'] == current
        assert before['environment'] == after['environment'] == policy['environment']
    key = 'oz_fvg_live_seconds'
    checked = evaluate(policy, pairs, current)
    assert checked['metrics'] == comparison['metrics']
    assert checked['pass'], checked
    return pairs, checked['metrics'][key]['ratio'], policy['limits'][key]


FIELDS = ('finals', 'special', 'telegram', 'oz_state', 'fvg_state', 'fvg_events')


@pytest.mark.parametrize('field', FIELDS)
def test_before_and_after_identical(results, field):
    before, after, _ = results
    assert before[field] == after[field]


@pytest.mark.parametrize('field', FIELDS)
def test_live_and_backtest_identical_after_optimization(results, field):
    _, after, backtest = results
    assert after[field] == backtest[field]


def test_scenario_is_not_vacuous(results):
    _, after, _ = results
    assert after['special'], 'SPECIAL7 must alert'
    assert after['finals']
    assert any('FVG' in text for _, _, text in after['telegram']) or after['fvg_events']
    assert any(e.get('kind') != 'FVG_CURRENT' for e in after['fvg_events'])
    assert len(after['oz_state']) == 16
    assert any('"Candidate"' in state for state in after['oz_state'].values())


def test_after_is_faster_and_reuses_common_facts(performance_results, record_property):
    pairs, ratio, limit = performance_results
    for before, after in pairs:
        memo = after['scenario']['oz_fact_memo']
        assert memo['reused'] > memo['computed'] > 0          # 16 profiles share one calculation
        structure = after['scenario']['fvg_structure']
        assert structure['reuses'] > structure['builds'] > 0  # FVG structure only on closed-bar change
    record_property('performance', json.dumps({'ratio':ratio,'limit':limit,'samples':len(pairs)}))
    if limit is not None:
        assert ratio <= limit, {'cpu_median_ratio': ratio, 'frozen_limit': limit, 'pairs': len(pairs)}
