"""Streaming workers, deterministic commands, monthly overlap, disk-backed alerts."""
from pathlib import Path
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from .settings import PROGRAM,digest,file_hash,milliseconds,months,work_periods,overlap_start,warehouse_path,relative_path,COMMAND_CONTINUOUS_REASON
from .system import deny_network,physical_cores,peak_memory,process_memory,write_progress,current_processor
from .warehouse import Warehouse,ResultWriter,FIELDS
from .cancellation import requested, file_check, coverage, utc_ms
from .build_compat import compatible_hashes,compatible_group
from .instrumentation import measure_consumer

_STOP_CHECK_SECONDS=0.5
_CPU_SAMPLE_SECONDS=0.5

def worker_count(s,cores=None,sequential=False):
    """An explicit count, then the count measured on this PC (수정본167), then its physical cores."""
    workers=1 if sequential else int(cores or s.get('cores') or _measured_workers() or physical_cores())
    if workers<1:raise ValueError('cores must be positive')
    return workers


def _measured_workers():
    from .worker_tuning import tuned_workers
    try:return tuned_workers()
    except (OSError,ValueError):return None


def execution_plan(s,cores=None,sequential=False,captures=None):
    """Keep all command-dependent state in one engine, across recording pieces.

    Otherwise each recorded period is split into blocks of equal trading days (the captures' observed
    days), at most one per worker, and the blocks of a period are joined where their states meet
    (joins.py, 수정본168). State is never carried across an unrecorded interval."""
    requested_workers=worker_count(s,cores)
    continuous=bool(s.get('commands'))
    chains=None
    if continuous or sequential:
        # Never keep WATCH/strategy state across an unrecorded interval.
        periods=[(p['start'],p['end']) for p in s['_available_periods']] if s.get('_available_periods') else [(s['start'],s['end'])]
        partition='AVAILABLE_PERIODS' if s.get('_available_periods') else 'SEQUENTIAL' if sequential else 'COMMAND_CONTINUOUS'
        workers=1
    else:
        from .joins import plan_periods,CAP_DAYS
        available=s.get('_available_periods') or [{'start':s['start'],'end':s['end']}]
        periods,chains=plan_periods(available,captures or [],requested_workers)
        partition='JOINED_BLOCKS'
        workers=min(requested_workers,len(periods))
    details={'cores':workers,'requested_cores':requested_workers,
             'requested_sequential':bool(sequential),
             'partition':partition,'execution_policy':'AVAILABLE_INDEPENDENT' if s.get('_available_periods') else 'COMMAND_CONTINUOUS' if continuous else 'SEQUENTIAL' if sequential else 'JOINED_BLOCKS',
             'execution_reason':'데이터 누락 구간을 넘어 상태를 이어 붙이지 않습니다.' if s.get('_available_periods') else COMMAND_CONTINUOUS_REASON if continuous else '', 'planned_chunks':len(periods)}
    if chains is not None:details.update(chains=chains,seam_cap_days=CAP_DAYS)
    return periods,details

def code_hash():
    root=PROGRAM.parents[1]
    # SPECIAL recipes are code too: a changed condition must change the run's code identity.
    paths=list(PROGRAM.rglob('*.py'))+list(PROGRAM.rglob('*.recipe.json'))+list((root/'Part2/event_backtest').glob('*.py'))+[root/'Part2/generic_backtest/native_mt5.py',root/'settings/strategy_registry.json']
    hashes={str(p.relative_to(root)):file_hash(p) for p in sorted(paths) if '__pycache__' not in p.parts}
    # Installed SPECIAL files live outside PROGRAM (166). Hash the definitions the strategy loader
    # reads, including literal PART3_RECIPE in .py files, without executing user code.
    from strategy_recipe.special_files import INSTALLED_DIRECTORY,read_special_entries
    if (root/INSTALLED_DIRECTORY).is_dir():
        hashes[INSTALLED_DIRECTORY]=digest(read_special_entries(root))
    return digest(hashes)


# Scenario fields only the virtual entry reads, after the replay. Every other field keys the replay,
# including any field added later, until it is named here.
AFTER_REPLAY=('virtual_entry','spread_points','result_mode','_run_id')
_REPLAY_KEY_VERSION=2


def replay_key(code,config_hash,tasks,execution=None):
    """The replay's inputs: code, configuration and every task (period, warm-up, recordings), without
    the run's own identity and folders and without the scenario fields named in AFTER_REPLAY.

    Joined blocks (수정본168) replay each period as one continuous replay wherever their seams meet,
    whatever the number of blocks: their key is the periods, each period's warm-up and the recordings,
    not the blocks; replay_partition() tells blocks apart for a run with an approximate seam."""
    own=('run_id','out','warehouse','scenario','config')
    if execution is not None and execution.get('partition')=='JOINED_BLOCKS':
        # The comparison rules are code (join_state.py): code_hash covers them.
        from .joins import CAP_DAYS
        scenario={k:v for k,v in tasks[0]['scenario'].items() if k not in AFTER_REPLAY and k!='cores'}
        chains=[{'start':tasks[chain[0]]['start'],'end':tasks[chain[-1]]['end'],'warm_start':tasks[chain[0]]['warm_start']}
                for chain in execution['chains']]
        captures=sorted({json.dumps(c,sort_keys=True,default=str) for task in tasks for c in task['captures']})
        return digest({'version':_REPLAY_KEY_VERSION,'code_hash':code,'config_hash':config_hash,'policy':'JOINED_BLOCKS',
                       'scenario':scenario,'periods':chains,'captures':captures,'transport':tasks[0].get('transport'),
                       'seam_cap_days':CAP_DAYS})
    return digest({'version':_REPLAY_KEY_VERSION,'code_hash':code,'config_hash':config_hash,
                   'tasks':[{**{k:v for k,v in task.items() if k not in own},
                             'scenario':{k:v for k,v in task['scenario'].items() if k not in AFTER_REPLAY}}
                            for task in tasks]})


def replay_partition(tasks):
    """The blocks of a joined replay; equal results need equal blocks only where a seam is approximate."""
    return digest([[task['start'],task['end'],task['warm_start']] for task in tasks])


def capture_build_provenance(captures,current_ea_hash):
    """Preserve each recorded hash while distinguishing approved compatibility."""
    actual={c.get('ea_build_hash','') for c in captures}
    approved=compatible_hashes(current_ea_hash)
    return {'ea_builds':sorted(actual),
            'current_ea_build_hash':current_ea_hash,
            'capture_ea_build_hashes':{c.get('capture_id',c['path']):c.get('ea_build_hash','') for c in captures},
            'ea_build_compatibility_applied':any(h!=current_ea_hash and h in approved for h in actual),
            'mixed_unapproved_ea_builds':not compatible_group(actual)}

def usable_captures(catalog,s,*,ea_build_hash=None):
    """The recordings a replay of this scenario may read, those of the current EA build first."""
    mode='BAR' if s['mode']=='BAR' else 'TIMER'
    candidates=catalog.available(s['symbol'],mode)
    from .build_plan import current_build,schema_id
    ea_build_hash=ea_build_hash or current_build(catalog.root)
    approved_builds=compatible_hashes(ea_build_hash)
    candidates=[c for c in candidates if c['schema_id']==schema_id() and c.get('reconstruction_verified') and not c.get('history_missing') and int(c.get('timer_ms',1000))==int(s['timer_ms'])
                and c['ea_build_hash'] in approved_builds]
    candidates.sort(key=lambda c:c['ea_build_hash']!=ea_build_hash)
    return candidates

def following_capture(catalog,s,captures):
    """The recording with the first observation after the period's end, unless the run's own recordings
    already reach past the end (수정본177). The virtual entry reads only that observation: it closes the
    last M1 candle that opened inside the period."""
    if any(c['end']>s['end'] for c in captures):return []
    later=[c for c in usable_captures(catalog,s) if c['start']>=s['end']]
    return [min(later,key=lambda c:c['start'])] if later else []

def resolve_captures(catalog,s,*,ea_build_hash=None):
    end=s['end']
    candidates=usable_captures(catalog,s,ea_build_hash=ea_build_hash)
    from .calendar import warm_start
    try:start=warm_start(s['start'],s['overlap_trading_days'],candidates)
    except ValueError:start=overlap_start(s['start'],s['overlap_trading_days'])
    chosen={}
    for c in candidates:
        if c['end']<=start or c['start']>=end or int(c.get('timer_ms',1000))!=int(s['timer_ms']):continue
        key=(c['start'],c['end'])
        if key not in chosen:chosen[key]=c
    from .catalog_plan import coverage
    return coverage(list(chosen.values()),start,end)

def runtime_config(s):
    sys.path.insert(0,str(PROGRAM))
    from event_composer_domain import load_config
    config=load_config(str(PROGRAM/'config.txt'))
    config.update(SYMBOLS=s['symbol'],TELEGRAM_TOKEN='OFFLINE',
                  TELEGRAM_CHAT_ID='BACKTEST',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    return config

def applied_strategy_settings(s,config):
    """Report registry defaults/overrides; no source-file or market evaluation."""
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from special_settings_model import SESSIONS
    from strategy_recipe.registry import list_presets, default_settings
    available=list_presets('Part2')
    selected=list(available) if s['strategies']==['ALL'] else [name for name in s['strategies'] if name in available]
    applied={}
    for name in selected:
        default_trigger,default_time=default_settings(name)
        time_inputs=s.get('special_time_filters',{})
        applied[name]={'trigger':s.get('triggers',{}).get(name) or default_trigger,
                       'final_alert_time_filters':time_inputs.get(name,default_time),
                       'time_source':'override' if name in time_inputs else 'preset_default',
                       'time_basis':'KST','session_config':{key:config.get(key,'') for key in SESSIONS}}
    return applied


def _unstarted_chunk(task,out,began):
    """Persist an empty, explicit cancellation result without loading an engine."""
    writer=ResultWriter(out/'alerts.csv',{'run_id':task['run_id']},milliseconds(task['start']),milliseconds(task['end']))
    writer.close()
    result={'watch_dependencies':{},'cancelled':True,'not_started':True,
            'processed_start_ms':None,'processed_end_ms':None,'bundles':0,'mean_ms':0,'p99_ms_upper_bound':0,
            'pid':os.getpid(),'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
            'task_started':began,'task_finished':time.perf_counter(),'first_bundle_seconds':None,
            'warmup_bundles':0,'keyframe_seek':{},'logical_cpu_bundle_samples':{},
            'logical_cpu_sampling_seconds':_CPU_SAMPLE_SECONDS,'stop_check_seconds':_STOP_CHECK_SECONDS,
            'wonbi_sigma':float(task['config'].get('WONBI_SIGMA',3)),'strategies':list(task['scenario']['strategies']),
            'elapsed_seconds':time.perf_counter()-began,'cpu_seconds':0,'max_memory_bytes':peak_memory(),
            'approximate':False,'approximate_consumers':[],'calendar_month_measurements':{},'processor_timings':{},
            'notifications':0,'alerts':0,'alert_months':{},
            'alerts_csv':relative_path(task.get('warehouse',PROGRAM.parents[1]),writer.path),'seams':[]}
    write_progress(out/'progress.json',{'pid':os.getpid(),'bundles':0,'percent':0,
                                     'max_memory_bytes':result['max_memory_bytes'],'cancelled':True})
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def unprocessed_periods(tasks,results):
    """State the unprocessed suffix after the last observed market bundle."""
    finished={(row['task_start'],row['task_end']):row for row in results}
    remaining=[]
    for task in tasks:
        row=finished.get((task['start'],task['end']))
        if row is not None and not row.get('cancelled'):continue
        last=None if row is None else row.get('processed_end_ms')
        if last is not None:
            # The engine's real time back on the recorded (server) clock of the period (수정본172).
            from .recording_time import recording_clock
            last=recording_clock(task.get('captures',())).to_server_ms(last)
        remaining.append({'start':task['start'] if last is None else utc_ms(last),
                          'end':task['end'],'start_exclusive':last is not None,
                          'reason':'NOT_STARTED' if row is None or row.get('not_started') else 'INTERRUPTED'})
    return remaining

class _EveryFeedNeeded(Exception):
    """A strategy asked for a timeframe this replay did not publish; the period is replayed with all."""
    def __init__(self,reasons):super().__init__(', '.join(reasons));self.reasons=reasons


def _worker_started():
    # A worker inherits the backtest's priority class, but not its opt-out of Windows
    # efficiency throttling (4-5x slower when the program's window is not in front).
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from process_priority import full_speed
    full_speed()


def input_end(task):
    """Where a task's input ends: its own end, or where its overrun stops at the latest (joins.py)."""
    join=task.get('join')
    return join['until'] if join and join.get('until') else task['end']


def lanes_of(task):
    """The runs one chunk replays: its own, or several runs (variants) sharing the recording reading and STAFF.

    Each lane keeps its own engine, input selection, alerts file and result, exactly as when replayed
    alone; only the capture reading and the STAFF publication (the union of their timeframes) are shared.
    """
    return task.get('lanes') or [{'scenario':task['scenario'],'run_id':task['run_id'],'out':task['out']}]


def run_chunk(task):
    """Publish only the timeframes the strategies read; replay with every feed if that proves short.

    A task with lanes returns one result per lane, in lane order; otherwise its single result."""
    lanes=lanes_of(task)
    try:results=_run_lanes(task,True)
    except _EveryFeedNeeded as need:
        for lane in lanes:
            gaps=Path(lane['out'])/'seq_gaps.jsonl'
            if gaps.exists():gaps.unlink()
        results=_run_lanes(task,False)
        for lane,result in zip(lanes,results):
            result['timeframe_fallback']=need.reasons
            (Path(lane['out'])/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return results if task.get('lanes') else results[0]


class _Lane:
    """One run's engine inside a chunk: its strategies, selection state, alerts and measurements.

    start_ms and real (수정본172): the engine runs in real time; real() converts a recorded server time."""
    def __init__(self,lane,task,config,limited,diagnostics,start_ms,real=lambda stamp:stamp):
        from event_application import create_event_engine
        from event_engine.model import Kind,Resolution
        self.run_id=lane['run_id'];self.out=Path(lane['out']);s=self.scenario=lane['scenario']
        plugins=None;moved=[]
        if s.get('base_frames') or s.get('virtual_strategy') is not None:
            # A virtual entry on another base frame, or with edited strategy conditions, tests a copy of
            # the strategy (base_frames.py); the recipe file stays as written.
            from .base_frames import engine_plugins
            plugins=engine_plugins(config,s);moved=[plugins[s['strategies'][0]].recipe['strategy_intent']]
        engine=self.engine=create_event_engine(config,symbols=(s['symbol'],),selection=s['strategies'],trigger_overrides=s['triggers'],backtest=True,
                                  oz_evaluation=s.get('oz_evaluation','selected'),time_overrides=s.get('special_time_filters',{}),
                                  plugins=plugins)
        diagnostics.configure_specials(engine.selection.specials)
        engine.retain_signals=False
        engine._logger=lambda details:diagnostics.log(details["strategy"],logging.ERROR,"처리 오류: %s",details,exc_info=sys.exc_info())
        # A joined block also writes the alerts of its overrun; the join keeps the continuous ones (joins.py).
        writer=self.writer=ResultWriter(self.out/'alerts.csv',{'run_id':self.run_id},real(milliseconds(task['start'])),
                                        real(milliseconds(input_end(task))),row_log=True)
        try:self._start(engine,writer,s,config,limited,start_ms,moved)
        except BaseException:
            writer.close();raise

    def _start(self,engine,writer,s,config,limited,start_ms,moved):
        from event_engine.model import Kind,Resolution
        registrations=[]
        def startup_signal(event):
            content=event.payload.get('content',{})
            if content.get('type')=='WATCH_COMMAND':registrations.append(content['command'])
            writer.accept(event)
        engine.signal_sink=startup_signal
        self.processor={}
        for consumer in (*engine.processors,*engine.strategies):
            consumer.on_event=measure_consumer(consumer.on_event,consumer.name,writer,self.processor)
        for ordinal,command in enumerate(s['commands']):
            engine.ingress.post(Kind.COMMAND,source='scenario',source_seq=ordinal,source_time=start_ms-1,
                payload={'symbol':s['symbol'],**command})
        engine.run()
        if writer.external_error:raise ValueError(writer.external_error)
        if engine.error_log:raise RuntimeError(str(engine.error_log))
        from event_watch_selection import specialize
        self.watch_dependencies=specialize(engine,registrations)
        engine.signal_sink=writer.accept
        self.resolution=resolution={'BAR':Resolution.BAR_CLOSE,'EVENT':Resolution.CONDITION,'TICK':Resolution.TICK}[s['mode']]
        subs=self.subs=tuple(c.subscriptions() for c in engine.strategies+engine.processors)
        from .timeframe_selection import required_timeframes
        # A moved copy reads its own frames; the list is only an upper bound, so the recipe's own may stay in it.
        timeframes=required_timeframes(engine,[*registrations,*moved])
        # A filtered (BAR/EVENT) replay keeps a bundle when any interested feed opened a bar. With 1m
        # interested and opening on every bundle that choice cannot depend on the unpublished feeds;
        # every bundle is checked below, and without an interested 1m nothing is left out.
        if timeframes is not None and resolution!=Resolution.TICK and not any(
                (not sub.symbols or s['symbol'] in sub.symbols) and (not sub.timeframes or '1m' in sub.timeframes) for sub in subs):
            timeframes=None
        self.timeframes=timeframes if limited else None
        # The feeds a join compares (join_state): the same whether or not this replay fell back to every
        # feed, so such a block still meets the blocks next to it.
        self.digest_timeframes=timeframes
        self.approximate=[c.name for c in engine.strategies+engine.processors if resolution<c.subscriptions().resolution]
        self.previous={}       # select_inputs' one prior snapshot per feed, for this lane's selection
        self.processed_start=self.processed_end=None
        self.count=0;self.total_ns=0;self.hist={};self.first_bundle_seconds=None;self.warmup_bundles=0
        self.month_counts={}


def _run_chunk(task,limited):
    results=_run_lanes(task,limited)
    return results if task.get('lanes') else results[0]


def _run_lanes(task,limited):
    """One result per lane, in lane order."""
    task_started=time.perf_counter()
    deny_network();sys.dont_write_bytecode=True;sys.path.insert(0,str(PROGRAM));logging.disable(logging.NOTSET)
    lanes=lanes_of(task);outs=[Path(lane['out']) for lane in lanes]
    from .system import restrict_writes
    restrict_writes(*outs)
    # A pool worker can pull several jobs. Switch its write boundary before
    # creating the next job directory, not after the old guard rejects it.
    for folder in outs:folder.mkdir(parents=True,exist_ok=True)
    if any((folder.parent/'stop.request').exists() for folder in outs):
        return [_unstarted_chunk({**task,'scenario':lane['scenario'],'run_id':lane['run_id']},Path(lane['out']),task_started)
                for lane in lanes]
    out=outs[0]
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    from module_diagnostics import configure
    diagnostics=configure(out,trace=False)
    os.environ['TMP']=os.environ['TEMP']=str(out)
    from event_engine.model import Kind,Resolution
    from event_engine.replay import _select_batch
    from event_host import load_staff
    from .bridge import CaptureInputs
    from itertools import islice
    import numpy as np
    config=task['config'];s=lanes[0]['scenario']
    # The recordings' broker clock (수정본172): the engine runs in real time, as LIVE does. The period
    # and its warm-up name the recorded (server) days; the engine's times are compared with them converted.
    from .recording_time import recording_clock,clocks_of
    import server_time
    broker=server_time.activate(recording_clock(task['captures']));real=broker.to_utc_ms
    # A joined block reads past its own end (its overrun) until it meets its follower (joins.py).
    start_ms=milliseconds(task['warm_start']);end_ms=milliseconds(input_end(task));own_end_ms=milliseconds(task['end'])
    real_start_ms,real_own_end_ms=real(start_ms),real(own_end_ms)
    states=[]
    try:
        for lane in lanes:states.append(_Lane(lane,task,config,limited,diagnostics,real_start_ms,real))
        resolution=states[0].resolution
        if any(state.resolution!=resolution for state in states):raise ValueError('함께 재생하는 실행은 재생 방식이 같아야 합니다.')
        # Every lane's feeds are published: the union, or every feed when any lane needs every feed.
        timeframes=None if any(state.timeframes is None for state in states) else frozenset().union(*(state.timeframes for state in states))
        for state in states:state.engine.board.timeframe_limit=timeframes
        clock=[0.0];staff=load_staff();cache=staff.StaffPipeCache('',health_session='BACKTEST',monotonic=lambda:clock[0],gap_journal=out/'seq_gaps.jsonl',
                                                                 published_timeframes=timeframes)
        capture_paths=[warehouse_path(task['warehouse'],c['path']) if task.get('warehouse') else c['path'] for c in task['captures']]
        inputs=CaptureInputs(staff,cache,capture_paths,transport=task.get('transport','replay'),
                             clock=clock,start_ms=start_ms,end_ms=end_ms,capture_start=s.get('capture_start','keyframe'),
                             server_times=clocks_of(task['captures']))
        def guarded(items):
            opened={}
            for item in items:
                if item.kind==Kind.MARKET_BUNDLE:
                    feed=item.payload['feeds'].get('1m');symbol=item.payload['symbol']
                    current=None if feed is None else int(feed.time[-1])
                    if current is None or opened.get(symbol)==current:
                        states[0].engine.board.timeframe_misses.add('a bundle without a new 1m bar')
                    opened[symbol]=current
                yield item
        source=guarded(inputs) if timeframes is not None and resolution!=Resolution.TICK else inputs
        def misses():
            found=set(cache.shadow_fallbacks)
            for state in states:found|=state.engine.board.timeframe_misses
            return sorted(found)
        stop=[file_check(folder.parent/'stop.request') for folder in outs]
        join=task.get('join')
        for state in states:
            state.join=None
            if join:
                from .joins import JoinLane,real_plan
                from .join_state import digests
                state.join=JoinLane(state.out,real_plan(join,real),1 if limited else 2,lambda:any(requested(check) for check in stop))
                state.join.running()
                state.digest=lambda state=state:digests(state.engine,timeframes=state.digest_timeframes)
        interrupted=False
        began=time.perf_counter();cpu=time.process_time();last_progress=began
        next_stop_check=began;next_cpu_sample=began
        month_metrics={};month_key=None;month_start_ms=None;next_month_ms=None;month_wall=began;month_cpu=cpu
        output_start_ms=real(milliseconds(task['start']));cpu_bundles={}
        iterator=iter(source)
        # select_inputs: batches of 8 inputs, each lane with its own carried snapshots; TICK passes each input on.
        while batch:=list(islice(iterator,1 if resolution==Resolution.TICK else 8)):
            kept=[None if resolution==Resolution.TICK else {id(item) for item in _select_batch(batch,state.subs,resolution,state.previous)}
                  for state in states]
            for item in batch:
                # A lane that met its follower replays no further.
                chosen=[state for state,ids in zip(states,kept) if (ids is None or id(item) in ids)
                        and not (state.join is not None and state.join.stopped)]
                if not chosen:continue
                loop_now=time.perf_counter()
                if loop_now>=next_stop_check:
                    next_stop_check=loop_now+_STOP_CHECK_SECONDS
                    if any(requested(check) for check in stop):interrupted=True;break
                # Keep both bounds so a backwards source-time jump is also classified
                # exactly as before. Calendar conversion is only needed at a boundary.
                if month_key is None or item.source_time<month_start_ms or item.source_time>=next_month_ms:
                    month=dt.datetime.fromtimestamp(item.source_time/1000,dt.timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
                    following=month.replace(year=month.year+1,month=1) if month.month==12 else month.replace(month=month.month+1)
                    month_start_ms=int(month.timestamp()*1000);next_month_ms=int(following.timestamp()*1000)
                    now_wall=time.perf_counter();now_cpu=time.process_time()
                    if month_key is not None:
                        month_metrics[month_key]={'elapsed_seconds':now_wall-month_wall,'cpu_seconds':now_cpu-month_cpu,
                                                  'bundles':[state.count-state.month_counts.get(month_key,0) for state in states]}
                    month_key=month.strftime('%Y-%m');month_wall=now_wall;month_cpu=now_cpu
                    for state in states:state.month_counts[month_key]=state.count
                for state in chosen:
                    if state.join is not None:state.join.before_input(item.source_time,state.writer.count)
                    engine=state.engine
                    t=time.perf_counter_ns()
                    engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
                    engine.run()
                    if timeframes is not None and (engine.board.timeframe_misses or cache.shadow_fallbacks):
                        raise _EveryFeedNeeded(misses())
                    if state.writer.external_error:raise ValueError(state.writer.external_error)
                    if engine.error_log:raise RuntimeError(str(engine.error_log[-1]))
                    if item.kind==Kind.MARKET_BUNDLE:
                        elapsed=time.perf_counter_ns()-t;state.total_ns+=elapsed;state.count+=1
                        if state.first_bundle_seconds is None:state.first_bundle_seconds=time.perf_counter()-task_started
                        if item.source_time<output_start_ms:state.warmup_bundles+=1
                        elif item.source_time<real_own_end_ms:
                            if state.processed_start is None:state.processed_start=item.source_time
                            state.processed_end=item.source_time
                        # Bounded histogram, 0.1ms bins; exact average, p99 upper bound.
                        key=(elapsed+99999)//100000;state.hist[key]=state.hist.get(key,0)+1
                        if state.join is not None:state.join.after_bundle(item.source_time,state.writer.count,state.digest)
                if timeframes is not None and states[0].engine.board.timeframe_misses:
                    raise _EveryFeedNeeded(misses())
                now=time.perf_counter()
                if item.kind==Kind.MARKET_BUNDLE and now>=next_cpu_sample:
                    logical_cpu=current_processor();cpu_bundles[logical_cpu]=cpu_bundles.get(logical_cpu,0)+1
                    next_cpu_sample=now+_CPU_SAMPLE_SECONDS
                if any(state.count==1 for state in chosen) or now-last_progress>=5:
                    for state in states:
                        progress={'pid':os.getpid(),'bundles':state.count,'time_ms':item.source_time,
                                  'percent':min(100,100*(item.source_time-real_start_ms)/(real_own_end_ms-real_start_ms)),
                                  'elapsed_seconds':now-began,'max_memory_bytes':peak_memory()}
                        write_progress(state.out/'progress.json',progress)
                    last_progress=now
            if interrupted:break
            if join and all(state.join.stopped for state in states):break
        if month_key is not None:
            month_metrics[month_key]={'elapsed_seconds':time.perf_counter()-month_wall,'cpu_seconds':time.process_time()-month_cpu,
                                      'bundles':[state.count-state.month_counts.get(month_key,0) for state in states]}
        # The last bundles may have reached no engine input at all (only unread timeframes).
        if timeframes is not None and misses():
            raise _EveryFeedNeeded(misses())
        for state in states:
            if state.join is not None:state.join.close(interrupted=interrupted,rows=state.writer.count)
        gaps=out/'seq_gaps.jsonl'
        results=[]
        for index,state in enumerate(states):
            if index and gaps.exists():
                # One STAFF read every lane's input: each run keeps its own copy of the gap journal.
                (state.out/'seq_gaps.jsonl').write_bytes(gaps.read_bytes())
            accumulated=0;p99=0
            for value,amount in sorted(state.hist.items()):
                accumulated+=amount
                if accumulated>=state.count*.99:p99=value/10;break
            processed_end=state.processed_end
            write_progress(state.out/'progress.json',{'pid':os.getpid(),'bundles':state.count,'percent':100 if not interrupted else (100*(processed_end-real_start_ms)/(real_own_end_ms-real_start_ms) if processed_end else 0),'max_memory_bytes':peak_memory()})
            count=state.count
            result={'watch_dependencies':state.watch_dependencies,'timeframes':sorted(timeframes) if timeframes is not None else None,
                    'cancelled':interrupted,'processed_start_ms':state.processed_start,'processed_end_ms':processed_end,'bundles':count,'mean_ms':state.total_ns/max(1,count)/1e6,'p99_ms_upper_bound':p99,
                    'pid':os.getpid(),'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
                    'task_started':task_started,'task_finished':time.perf_counter(),
                    'first_bundle_seconds':state.first_bundle_seconds,'warmup_bundles':state.warmup_bundles,'keyframe_seek':inputs.seek_info,
                    'logical_cpu_bundle_samples':cpu_bundles,'logical_cpu_sampling_seconds':_CPU_SAMPLE_SECONDS,'stop_check_seconds':_STOP_CHECK_SECONDS,
                    'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0)),'strategies':list(state.engine.selection.names),
                    'elapsed_seconds':time.perf_counter()-began,'cpu_seconds':time.process_time()-cpu,
                    'max_memory_bytes':peak_memory(),'approximate':bool(state.approximate),'approximate_consumers':state.approximate,
                    'calendar_month_measurements':{key:{**value,'bundles':value['bundles'][index]} for key,value in month_metrics.items()},
                    'processor_timings':{n:{**v,'ms_per_bundle':v['ns']/max(1,count)/1e6} for n,v in state.processor.items()},
                    'notifications':state.writer.notifications,'alerts':len(state.writer.alert_ids),'alert_months':dict(sorted(state.writer.alert_months.items())),'alerts_csv':relative_path(task.get('warehouse',PROGRAM.parents[1]),state.writer.path),
                    'seams':[{**seam,'path':relative_path(task.get('warehouse',PROGRAM.parents[1]),seam['path'])} for seam in inputs.seams]}
            if state.join is not None:result.update(join=state.join.summary(),input_end=input_end(task))
            if len(states)>1:result['shared_lanes']=len(states)
            (state.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            results.append(result)
        return results
    except BaseException:
        # A block waiting for this one must not wait any longer (joins.JoinLane.reference).
        for state in states:
            if getattr(state,'join',None) is not None and state.join.stopped is None:
                state.join.close(failed=True,rows=state.writer.count)
        raise
    finally:
        for state in states:state.writer.close()


# Scenario fields that shape the replay itself: runs replayed together must agree on every one.
SHARED_REPLAY=('symbol','start','end','mode','timer_ms','overlap_trading_days','capture_start','commands',
               '_available_periods','period_adjustment','excluded_periods','cores')


def run(s,warehouse,*,cores=None,sequential=False,captures=None,verified=False,transport='replay',emit=lambda *a:None,cancel=lambda:None):
    return run_many([s],warehouse,cores=cores,sequential=sequential,captures=captures,verified=verified,transport=transport,
                    emit=emit,cancel=cancel)[0]


class _Run:
    """One scenario's run record, prepared exactly as a run of its own."""
    def __init__(self,s,root,captures,periods,execution,transport,catalog):
        self.s=s;run_id=self.run_id=s.get('_run_id') or uuid.uuid4().hex
        if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')
        out=self.out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
        config=self.config=runtime_config(s)
        from event_selection import resolve
        resolve(s['strategies'],config)
        from .history_check import tick_warning
        if s.get('result_mode')=='VIRTUAL_ENTRY':
            from .virtual_entry import pricing
            pricing(s,captures,config)
        warnings=list(s['warnings'])
        if s.get('period_adjustment'):warnings.append(s['period_adjustment']['message'])
        if len(s.get('_available_periods',[]))>1:warnings.append('데이터가 없는 기간을 제외하고 각 보유 구간을 독립적으로 재생했습니다.')
        if s.get('commands') and COMMAND_CONTINUOUS_REASON not in warnings:warnings.append(COMMAND_CONTINUOUS_REASON)
        from .build_plan import current_build
        build_provenance=capture_build_provenance(captures,current_build(root))
        if build_provenance.pop('mixed_unapproved_ea_builds'):warnings.append('서로 다른 EA 빌드의 녹화 조각이 섞였습니다.')
        for c in captures:
            warning=tick_warning(c,s['mode'])
            if warning:warnings.append(warning)
            if c.get('history_evidence_warning'):warnings.append(c['history_evidence_warning'])
            if c.get('empty') or c.get('records')==0:warnings.append(f"{c.get('start','')}~{c.get('end','')}: EA가 완료했으나 시장 관측이 0개인 조각입니다.")
        applied=applied_strategy_settings(s,config)
        # The recordings' broker clock (수정본172): one for every recording of the run.
        from .recording_time import recording_clock
        broker=recording_clock(captures)
        # What this run tests, kept as it is now (수정본169): later recipe or setting changes leave it.
        from .tested_settings import tested_settings,FILE
        tested=tested_settings(s,config,applied,server_time=broker.record())
        (out/FILE).write_text(json.dumps(tested,ensure_ascii=False),encoding='utf-8')
        self.data={'progress_log':relative_path(root,out/'progress.jsonl'),'result_path':relative_path(root,out/'result.json'),'result_mode':s.get('result_mode','ALERT_ONLY'),'scenario':{**s,'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0))},'config_hash':digest(config),'code_hash':code_hash(),'cores':execution['cores'],
              'applied_special_settings':applied,'tested_settings':tested,
              'captures':[c.get('capture_id',c['path']) for c in captures],'warnings':warnings,
              'strategies':list(s['strategies']),'period_adjustment':s.get('period_adjustment'),
              'excluded_periods':s.get('excluded_periods',[]),'available_periods':s.get('_available_periods',[]),**build_provenance,
              'schema_ids':sorted({c.get('schema_id',0) for c in captures}),'wonbi_sigma':float(config.get('WONBI_SIGMA',3)),**execution}
        from .calendar import warm_start
        from .joins import join_plans
        plans=join_plans(periods,execution.get('chains'),captures)
        self.tasks=[]
        for index,(start,end) in enumerate(periods):
            warm=warm_start(start,s['overlap_trading_days'],captures)
            plan=plans.get(index)
            # A joined block also reads the recordings of its overrun.
            last=plan['until'] if plan and plan.get('until') else end
            pieces=[c for c in captures if c.get('end',last)>warm and c.get('start',start)<last]
            task={'scenario':s,'config':config,'start':start,'end':end,'warm_start':warm,'captures':pieces,
                  'run_id':run_id,'out':str(out/f'chunk_{index:03d}'),'transport':transport,'warehouse':str(root)}
            if plan:task['join']=plan
            self.tasks.append(task)
        self.data['replay_key']=replay_key(self.data['code_hash'],self.data['config_hash'],self.tasks,execution)
        self.data['replay_partition']=replay_partition(self.tasks)
        # A finished run with the same replay inputs already holds these alerts (a deterministic replay).
        self.source=catalog.finished_replay(self.data['replay_key'],self.data['code_hash'],self.data['config_hash'],
                                            partition=self.data['replay_partition'])
        self.results=[]


def run_many(scenarios,warehouse,*,cores=None,sequential=False,captures=None,verified=False,transport='replay',
             emit=lambda *a:None,cancel=lambda:None,lead=None,group=None):
    """Runs that differ only in their strategies (variants), replayed together; one scenario is an ordinary run.

    Each chunk reads the recordings and publishes STAFF once for all of them, while every scenario keeps
    its own engine and complete run record (its own run ID, alerts, virtual entry and result), as when
    replayed alone. Returns their results in scenario order.

    verified=True: the caller hashed the given recordings in this process just before (workflow._execute),
    so they are not hashed again (수정본170). Recordings looked up here are always hashed.

    lead, group: a request replayed in several groups of runs (workflow.run_groups, 수정본184) names its
    first run and all its runs, so every run of every group refers to the same request.
    """
    requested_at=time.perf_counter()
    if not scenarios:raise ValueError('실행할 시나리오가 없습니다.')
    first=scenarios[0]
    if any(any(other.get(key)!=first.get(key) for key in SHARED_REPLAY) for other in scenarios[1:]):
        raise ValueError('함께 재생하는 실행은 종목·기간·재생 방식·명령이 같아야 합니다.')
    if len(scenarios)>1 and first.get('commands'):raise ValueError('명령이 있는 실행은 함께 재생할 수 없습니다.')
    root=Path(warehouse);catalog=Warehouse(root)
    if captures is None:
        verified=False
        captures,missing=resolve_captures(catalog,first)
        if missing:catalog.close();raise ValueError('누락된 녹화 조각: '+str(missing))
    # Blocks of equal trading days need the recordings' observed days (joins.plan_periods).
    periods,execution=execution_plan(first,cores,sequential,captures)
    workers=execution['cores']
    from .history_check import require_complete
    try:require_complete(captures)
    except BaseException:catalog.close();raise
    for c in [] if verified else captures:
        if c.get('capture_id') and not catalog.find_capture(c['capture_id']):
            catalog.close();raise ValueError('녹화 무결성 오류: '+c['capture_id'])
    # Looked up here, where the recordings' catalog is open; only a virtual entry reads it.
    try:
        following=following_capture(catalog,first,captures) if any(s.get('result_mode')=='VIRTUAL_ENTRY' for s in scenarios) else []
        if following:
            from .recording_time import recording_clock
            broker=recording_clock(captures)
            # Match calculate's clock guard before hashing a recording it will not read.
            following=[c for c in following if recording_clock([c])==broker]
            # verified=True covers only the caller's captures, never this new lookup (수정본178).
            for c in following:
                if c.get('capture_id') and not catalog.find_capture(c['capture_id']):
                    raise ValueError('녹화 무결성 오류: '+c['capture_id'])
    except BaseException:catalog.close();raise
    runs=[]
    try:
        catalog.close();catalog=Warehouse(root,results=True)
        for s in scenarios:runs.append(_Run(s,root,captures,periods,execution,transport,catalog))
        if len({item.run_id for item in runs})!=len(runs):raise ValueError('함께 재생하는 실행의 ID가 겹칩니다.')
        together=list(group) if group else [item.run_id for item in runs]
        if len(together)>1:
            # Every run requested together, including one that reuses a finished replay (수정본163).
            for item in runs:item.data['tested_with']=[other for other in together if other!=item.run_id]
        for item in runs:catalog.run(item.run_id,'RUNNING',item.data)
        owner=lead or runs[0].run_id
        for item in runs:
            # The first run stands for the request until the others write their results.
            if item.run_id!=owner:(item.out/'lane.json').write_text(json.dumps({'lead_run_id':owner}),encoding='utf-8')
    finally:catalog.close()
    lead=runs[0];run_id=lead.run_id
    emit('RUN_START',{'run_id':run_id,**execution,**({'runs':[item.run_id for item in runs]} if len(runs)>1 else {})})
    began=time.perf_counter();max_total_memory=0;first_progress=None
    replaying=[item for item in runs if item.source is None]
    try:
        if replaying:
            # One task per period: an ordinary task for one run, or one task whose lanes are the runs.
            tasks=[]
            for index,task in enumerate(replaying[0].tasks):
                if len(replaying)==1:tasks.append(task)
                else:tasks.append({**task,'lanes':[{'scenario':item.s,'run_id':item.run_id,'out':item.tasks[index]['out']} for item in replaying]})
            from .worker_schedule import plan_dispatch,BoundedTasks,ProgressRows,worker_pool,worker_pids,priority_keeper
            dispatch_order,scheduling=plan_dispatch(tasks,workers)
            for item in replaying:item.data['worker_scheduling']=scheduling
            pool_type=concurrent.futures.ProcessPoolExecutor
            keeper=priority_keeper()
            with worker_pool(min(workers,len(tasks)),factory=lambda **k:pool_type(initializer=_worker_started,**k)) as pool:
                queue=BoundedTasks(pool,run_chunk,tasks,min(workers,len(tasks)),dispatch_order)
                progress_rows=ProgressRows(tasks)
                def stop_submissions():
                    if requested(cancel) or any((item.out/'stop.request').exists() for item in runs):
                        for item in runs:(item.out/'stop.request').touch(exist_ok=True)
                        queue.stop()
                    return queue.stopped
                if not stop_submissions():queue.fill()
                done_count=0
                while queue.pending:
                    if stop_submissions() and not queue.pending:break
                    done,_=concurrent.futures.wait(queue.pending,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
                    completed=queue.collect(done)
                    for _,result in completed:
                        for item,lane_result in zip(replaying,result if len(replaying)>1 else [result]):item.results.append(lane_result)
                        done_count+=1;emit('CHUNK_COMPLETE',{'done':done_count,'total':len(tasks),'run_id':run_id})
                    # Cancellation may have arrived while waiting or in a callback.
                    if not stop_submissions():queue.fill()
                    keeper.update(worker_pids(pool))
                    memory=process_memory(peak=False);progress=[];sampled_pids=set()
                    for row in progress_rows.read(queue.submitted,queue.completed):
                        try:
                            if row['pid'] not in sampled_pids:
                                memory+=process_memory(row['pid'],peak=False);sampled_pids.add(row['pid'])
                            progress.append(row)
                        except (OSError,ValueError,KeyError):pass
                    max_total_memory=max(max_total_memory,memory)
                    if progress and first_progress is None:first_progress=time.perf_counter()-requested_at
                    if progress:emit('RUN_PROGRESS',{'run_id':run_id,'percent':sum(r['percent'] for r in progress)/len(tasks),
                        'elapsed_seconds':time.perf_counter()-began,'sampled_peak_total_memory_bytes':max_total_memory,**execution})
            scheduling['max_pending_observed']=queue.high_water
            scheduling['submitted_chunks']=len(queue.submitted)
            scheduling['cancelled_before_start']=len(queue.cancelled)
            if len(replaying)>1:
                for item in replaying:item.data['replayed_with']=[other.run_id for other in replaying if other is not item]
        for item in runs:
            if item.source is None:continue
            # The source's chunk results describe the replay these alerts came from.
            reused,source_data=item.source
            item.results=[dict(row) for row in source_data['chunks']]
            item.data['worker_scheduling']={'policy':'REUSED','reused_from':reused}
            item.data['replay_reused_from']=reused
            item.data['warnings'].append('알림 재생: 같은 조건으로 끝난 실행의 결과를 썼습니다.')
            emit('REPLAY_REUSED',{'run_id':item.run_id,'source':reused})
    except BaseException as exc:
        for item in runs:_failed(item,root,began,exc)
        raise
    output=[]
    for item in runs:
        try:output.append(_finish(item,root,captures,following,execution,requested_at,began,first_progress,max_total_memory,
                                  sequential,emit,cancel))
        except BaseException as exc:
            _failed(item,root,began,exc)
            for other in runs[len(output)+1:]:_failed(other,root,began,exc)
            raise
    return output


def _import_joined(catalog,item,execution):
    """Import the rows of one continuous replay from joined blocks (joins.stitch), and count its alerts."""
    from .joins import stitch,kept_rows
    from .alert_stats import month_key
    joins=[];months={};seen=set()
    for chain in execution['chains']:
        folders=[item.out/f'chunk_{index:03d}' for index in chain]
        segments,found=stitch(folders)
        for folder,first,end in segments:
            target=folder/'alerts_joined.csv'
            rows=kept_rows(folder,first,end,target)
            catalog.import_results(target)
            target.unlink()
            for time_ms,signal_id,notification in rows:
                # One alert per signal_id, as ResultWriter counts them.
                if notification and signal_id not in seen:
                    seen.add(signal_id);key=month_key(time_ms);months[key]=months.get(key,0)+1
        for join in found:
            if join.get('at_ms') is not None:join['at']=dt.datetime.fromtimestamp(join['at_ms']/1000,dt.timezone.utc).isoformat(timespec='minutes')
            # Block numbers of the run (chunk_NNN), not places in the chain.
            for key in ('from_block','to_block','after_block'):
                if key in join:join[key]=chain[join[key]]
        joins.extend(found)
    return {'joins':joins,'exact':all(join['status']=='CONVERGED' for join in joins),
            'capped':sum(join['status']=='CAPPED' for join in joins),'alert_months':months}


def _finish(item,root,captures,following,execution,requested_at,began,first_progress,max_total_memory,sequential,emit,cancel):
    s=item.s;data=item.data;run_id=item.run_id;out=item.out;results=item.results;config=item.config
    workers=execution['cores']
    # Dispatch/completion order never becomes alert import order.
    results.sort(key=lambda r:(r['task_start'],r['task_end']))
    catalog=Warehouse(root,results=True)
    try:
        joined=None
        if item.source is None and any(r.get('join') for r in results):
            joined=_import_joined(catalog,item,execution)
        elif item.source is None:
            for result in results:catalog.import_results(warehouse_path(root,result['alerts_csv']))
        else:catalog.copy_alerts(item.source[0],run_id)
        if joined is not None:
            data.update(joins=joined['joins'],joins_exact=joined['exact'])
            if joined['capped']:
                data['warnings'].append(f"블록 경계 {joined['capped']}곳은 {execution.get('seam_cap_days')}거래일을 더 재생해도 상태가 같아지지 않아 "
                                        '그 지점에서 이어 붙였습니다(근사).')
        elif item.source is not None:
            data.update({key:item.source[1][key] for key in ('joins','joins_exact') if key in item.source[1]})
        # Streaming, deterministic chronological export. DuckDB may spill to disk.
        export=out/'alerts.csv'
        data['alert_rows']=catalog.export_results(run_id,export)
        names={name for r in results for name in r['processor_timings']}
        for name in names:
            bundles=sum(r['bundles'] for r in results);ns=sum(r['processor_timings'].get(name,{}).get('ns',0) for r in results)
            catalog.db.execute('INSERT INTO timings VALUES (?,?,?,?,?)',[run_id,name,bundles,ns/max(1,bundles)/1e6,max(r['max_memory_bytes'] for r in results)])
        data.update(run_id=run_id,elapsed_seconds=time.perf_counter()-requested_at,replay_seconds=time.perf_counter()-began,
                    first_progress_seconds=first_progress,logical_cpu_sampling_seconds=_CPU_SAMPLE_SECONDS,
                    logical_cpu_bundle_samples={str(cpu):sum(r.get('logical_cpu_bundle_samples',{}).get(cpu,0) for r in results)
                        for cpu in {key for r in results for key in r.get('logical_cpu_bundle_samples',{})}},
                    worker_distribution={str(pid):[{'start':r['task_start'],'end':r['task_end'],'bundles':r['bundles'],
                        'warmup_bundles':r['warmup_bundles'],'elapsed_seconds':r['elapsed_seconds']} for r in results if r.get('pid')==pid]
                        for pid in sorted({r['pid'] for r in results if 'pid' in r})},
                    verification_and_setup_seconds=began-requested_at,approximate=any(r['approximate'] for r in results),
                    chunks=results,alerts_csv=relative_path(root,export),max_worker_memory_bytes=max((r['max_memory_bytes'] for r in results),default=0),
                    concurrent_memory_upper_bound_bytes=sum(sorted((r['max_memory_bytes'] for r in results),reverse=True)[:workers]),
                    sampled_peak_total_memory_bytes=max_total_memory,memory_sampling_seconds=2)
        interrupted=requested(cancel) or (out/'stop.request').exists() or any(r.get('cancelled') for r in results)
        data['processed_periods']=coverage(results)
        data['unprocessed_periods']=unprocessed_periods(item.tasks,results)
        from .alert_stats import summarize
        if joined is not None:
            # The kept rows of each block, not every row a block wrote in its overrun.
            data['alert_statistics']=summarize(s['start'],s['end'],[{'alert_months':joined['alert_months']}],periods=s.get('_available_periods'))
        elif item.source is not None and item.source[1].get('joins') is not None and item.source[1].get('alert_statistics'):
            data['alert_statistics']=item.source[1]['alert_statistics']
        else:
            data['alert_statistics']=summarize(s['start'],s['end'],results,periods=s.get('_available_periods'))
        if s.get('result_mode')=='VIRTUAL_ENTRY' and not interrupted:
            from .virtual_entry import calculate
            # Its own worker processes, as many as the replay asked for (one when sequential).
            data['virtual_entry']=calculate(export,captures,root,s,config,out,emit=emit,cancel=cancel,
                                            workers=1 if sequential else execution['requested_cores'],following=following)
            interrupted=data['virtual_entry'].get('cancelled',False)
        data.update(status='CANCELLED' if interrupted else 'COMPLETE',status_label='중단됨(부분 결과)' if interrupted else '완료',
                    completed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds'))
        if interrupted and s.get('result_mode')=='VIRTUAL_ENTRY' and 'virtual_entry' not in data:
            data['virtual_entry']={'cancelled':True,'not_started':True,'summary':[],'processed_signals':0}
        catalog.run(run_id,data['status'],data)
    finally:catalog.close()
    (out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data


def _failed(item,root,began,exc):
    """Record one run as failed; tracebacks can contain workstation paths, so keep them out of the warehouse."""
    import traceback
    import re
    out=item.out;run_id=item.run_id;data=item.data
    diagnostics=out/'errors';diagnostics.mkdir(parents=True,exist_ok=True)
    details=traceback.format_exc().replace(str(PROGRAM.parents[1]),'<project>').replace(str(root),'<warehouse>')
    details=re.sub(r'File "[^"\n]+[\\/]([^\\/"\n]+)"',r'File "\1"',details)
    (diagnostics/(run_id+'.log')).write_text(details,encoding='utf-8')
    catalog=Warehouse(root,results=True)
    try:
        data.update(error=type(exc).__name__,error_reference=run_id,elapsed_seconds=time.perf_counter()-began)
        catalog.run(run_id,'FAILED',data)
    finally:catalog.close()
