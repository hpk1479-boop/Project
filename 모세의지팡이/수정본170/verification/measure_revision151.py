"""Isolated, read-only-source backtest diagnosis; never run the production supervisor."""
from pathlib import Path
import sys, os, json, time, hashlib, shutil, functools, argparse

REVISION = Path(__file__).resolve().parents[1]
WORKSPACE = REVISION.parent
HERE = REVISION / '검증결과/revision151/performance'
CODE = HERE / 'baseline_source'

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def prepare():
    if CODE.exists():
        raise RuntimeError('Diagnostic source snapshot already exists')
    original = WORKSPACE / '수정본150'
    trees = ['Part1/program', 'Part2/event_backtest', 'Part2/generic_backtest',
             'settings', 'moses_language', 'common_ai']
    extensions = {'.py', '.json', '.txt', '.lark', '.toml', '.yaml', '.yml'}
    forbidden = {'__pycache__', '.pytest_cache', '검증결과', 'logs', 'tests', 'runtime', 'build'}
    hashes = {}
    for tree in trees:
        for source in (original / tree).rglob('*'):
            if not source.is_file() or source.suffix.lower() not in extensions:
                continue
            relative = source.relative_to(original)
            if forbidden.intersection(relative.parts):
                continue
            content = source.read_bytes()
            hashes[relative.as_posix()] = hashlib.sha256(content).hexdigest()
            target = CODE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    # Do not benchmark a source tree that changed during the snapshot.
    for relative, expected in hashes.items():
        if hashlib.sha256((original / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Source changed during copy: ' + relative)
    import duckdb
    catalog = duckdb.connect(str(WORKSPACE / '데이터창고/captures.duckdb'), read_only=True)
    try:
        rows = catalog.execute('SELECT metadata FROM captures WHERE symbol=? AND mode=? ORDER BY recorded_at DESC',
                               ['XAUUSD+', 'BAR']).fetchall()
    finally:
        catalog.close()
    selected = next(json.loads(row[0]) for row in rows
                    if json.loads(row[0]).get('start') == '2025-09-01'
                    and json.loads(row[0]).get('reconstruction_verified')
                    and not json.loads(row[0]).get('history_missing'))
    selected['path'] = '데이터창고/' + selected['path']
    save(HERE / 'capture_metadata.json', selected)
    save(HERE / 'source_snapshot.json', {'revision':'수정본150','files':hashes})
    print(json.dumps({'snapshot_files':len(hashes),'capture':selected['path']}, ensure_ascii=False), flush=True)

def case(name, profile=False, run_label=''):
    sys.dont_write_bytecode = True
    measured_files = ['Part1/program/THE STAFF OF MOSES.py', 'Part1/program/watch_array_facts.py',
                      'Part1/program/strategy_recipe/port.py', 'Part2/event_backtest/delta.py',
                      'Part2/event_backtest/bridge.py', 'Part2/event_backtest/virtual_entry.py']
    source_hashes = {path:hashlib.sha256((CODE/path).read_bytes()).hexdigest() for path in measured_files}
    sys.path[:0] = [str(CODE / 'Part2'), str(CODE / 'Part1/program'), str(CODE)]
    from event_backtest import runner, settings, virtual_defaults, calendar
    capture = json.loads((HERE / 'capture_metadata.json').read_text('utf-8'))
    variants = {'special8_1m':('SPECIAL8','1m'), 'special8_1h':('SPECIAL8','1h'),
                'special9_1m':('SPECIAL9','1m'), 'special7_multi':('SPECIAL7','1m'),
                'special1_multi':('SPECIAL1','SIGNAL'), 'special8_custom_1h':('SPECIAL8','1h')}
    strategy, base = variants[name]
    strategy_profile = virtual_defaults.strategy_profile(strategy)
    policy = virtual_defaults.recipe_on_base(strategy_profile, base)
    edited_strategy = None
    if name == 'special8_custom_1h':
        edited_strategy = virtual_defaults.strategy_on_base(strategy_profile, base)
        def change_averages(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ('ma_left','ma_right') and item in ('HMA17','HMA50'):
                        value[key] = {'HMA17':'HMA18','HMA50':'HMA51'}[item]
                    elif key == 'slow_period' and item == 50:
                        value[key] = 51
                    elif key == 'fast_period' and item == 17:
                        value[key] = 18
                    else:
                        change_averages(item)
            elif isinstance(value, list):
                for item in value:
                    change_averages(item)
        change_averages(edited_strategy)
        for row in policy['conditions']:
            if row['kind'] == 'MA_POSITION':
                row['period'] = 51
        for row in policy['filters']:
            if row['kind'] == 'ENVIRONMENT':
                row['fast'], row['slow'] = 18, 51
    scenario = settings.scenario(symbol='XAUUSD+', start='2025-09-08', end='2025-09-10', mode='BAR',
                                 strategies=[strategy], result_mode='VIRTUAL_ENTRY', virtual_entry=policy,
                                 virtual_strategy=edited_strategy, cores=1)
    config = runner.runtime_config(scenario)
    out = HERE / 'cases' / (CODE.name + '_' + name + ('_'+run_label if run_label else '') + ('_profile' if profile else ''))
    if out.exists():
        raise RuntimeError('Case already exists: ' + out.name)
    out.mkdir(parents=True)
    task = {'warehouse':str(WORKSPACE), 'captures':[capture], 'out':str(out),
            'run_id':hashlib.sha256(out.name.encode()).hexdigest()[:32],
            'start':scenario['start'], 'end':scenario['end'],
            'warm_start':calendar.warm_start(scenario['start'], 3, [capture]),
            'scenario':scenario, 'config':config, 'transport':'replay'}
    counters = {}
    def wrap(owner, attribute, label):
        original = getattr(owner, attribute)
        @functools.wraps(original)
        def measured(*args, **kwargs):
            began = time.perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                row = counters.setdefault(label, {'calls':0,'ns':0})
                row['calls'] += 1
                row['ns'] += time.perf_counter_ns() - began
        setattr(owner, attribute, measured)
    def wrap_iterator(owner, attribute, label):
        original = getattr(owner, attribute)
        @functools.wraps(original)
        def measured(*args, **kwargs):
            iterator = iter(original(*args, **kwargs))
            try:
                while True:
                    began = time.perf_counter_ns()
                    try:
                        value = next(iterator)
                    except StopIteration:
                        return
                    finally:
                        row = counters.setdefault(label, {'calls':0,'ns':0})
                        row['calls'] += 1
                        row['ns'] += time.perf_counter_ns() - began
                    yield value
            finally:
                close = getattr(iterator, 'close', None)
                if close:
                    close()
        setattr(owner, attribute, measured)
    from event_backtest import delta, bridge
    from event_engine import replay
    from event_engine.engine import EventEngine
    from event_engine.staff_adapter import StaffIngressAdapter
    import event_host
    load_staff = event_host.load_staff
    def timed_staff():
        staff = load_staff()
        wrap(staff.StaffPipeCache, 'inspect_publication', 'staff_inspect')
        if hasattr(staff.StaffPipeCache, '_receive_v2'):
            wrap(staff.StaffPipeCache, '_receive_v2', 'staff_receive')
        return staff
    event_host.load_staff = timed_staff
    wrap(delta.DeltaCodec, 'decode', 'delta_decode')
    wrap(bridge, 'remap_bundle', 'remap')
    wrap(StaffIngressAdapter, 'publish', 'staff_publish')
    wrap(EventEngine, 'run', 'engine_run')
    wrap_iterator(bridge.CaptureInputs, '__iter__', 'capture_next')
    wrap_iterator(replay, 'select_inputs', 'select_next')
    runner._worker_started()
    profiler = None
    if profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    print(json.dumps({'case':out.name,'stage':'replay_start','warm_start':task['warm_start'],
                      'base':base,'environment_frames':strategy_profile['env_timeframes']}, ensure_ascii=False), flush=True)
    began = time.perf_counter()
    cpu_began = time.process_time()
    result = runner.run_chunk(task)
    replay_wall = time.perf_counter() - began
    replay_cpu = time.process_time() - cpu_began
    main_counters = {key:dict(value) for key,value in counters.items()}
    print(json.dumps({'case':out.name,'stage':'replay_complete','seconds':round(replay_wall,3),
                      'bundles':result['bundles'],'alerts':result['alerts'],
                      'fallback':result.get('timeframe_fallback')}, ensure_ascii=False), flush=True)
    from event_backtest.virtual_entry import calculate
    from event_backtest.virtual_facts import VirtualFacts
    wrap(VirtualFacts, 'frame', 'virtual_frame')
    began = time.perf_counter()
    cpu_began = time.process_time()
    virtual = calculate(out/'alerts.csv', [capture], WORKSPACE, scenario, config, out)
    virtual_wall = time.perf_counter() - began
    virtual_cpu = time.process_time() - cpu_began
    if any(hashlib.sha256((CODE/path).read_bytes()).hexdigest() != digest for path,digest in source_hashes.items()):
        raise RuntimeError('Measured source changed during this case')
    if profiler:
        profiler.disable()
        import pstats
        stats = pstats.Stats(profiler)
        rows = []
        for (filename,line,function), (primitive,total,exclusive,cumulative,callers) in stats.stats.items():
            path = Path(filename)
            if path.is_absolute():
                label = path.relative_to(CODE).as_posix() if path.is_relative_to(CODE) else path.name
            else:
                label = filename
            rows.append({'file':label,'line':line,'function':function,'calls':total,
                         'exclusive_seconds':exclusive,'cumulative_seconds':cumulative})
        save(out/'profile.json', sorted(rows,key=lambda row:row['cumulative_seconds'],reverse=True)[:100])
    summary = {'case':out.name,'strategy':strategy,'base':base,'single_core':True,
               'period':{'start':scenario['start'],'end_exclusive':scenario['end'],'warm_start':task['warm_start']},
               'source_revision':('수정본151' if CODE == REVISION else '수정본150'),'capture_path':capture['path'],
               'source_hashes':source_hashes,
               'replay_wall_seconds':replay_wall,'virtual_wall_seconds':virtual_wall,
               'replay_cpu_seconds':replay_cpu,'virtual_cpu_seconds':virtual_cpu,
               'replay_counters':main_counters,'all_counters':counters,
               'replay':result,'virtual':virtual,'scenario':scenario,
               'note':'Inclusive timers overlap. select_next includes capture_next; capture_next includes decode and STAFF. Profiled cases include profiler overhead.'}
    save(out/'diagnosis.json', summary)
    print(json.dumps({'case':out.name,'stage':'complete','replay_seconds':round(replay_wall,3),
                      'virtual_seconds':round(virtual_wall,3),'virtual_signals':virtual['eligible_signals'],
                      'virtual_bundles':virtual['read_bundles']}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','case'])
    parser.add_argument('--name')
    parser.add_argument('--source',choices=['150','151'],default='151')
    parser.add_argument('--run-label',default='')
    parser.add_argument('--profile',action='store_true')
    args = parser.parse_args()
    HERE.mkdir(parents=True,exist_ok=True)
    if args.action == 'prepare':
        prepare()
    else:
        if args.source == '151':
            CODE = REVISION
        if args.run_label and (not args.run_label.replace('_','').isalnum()):
            raise ValueError('run-label must contain only letters, digits and underscores')
        case(args.name,args.profile,args.run_label)
