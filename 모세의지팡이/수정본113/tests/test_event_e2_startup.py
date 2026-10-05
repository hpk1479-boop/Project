"""Same canonical startup inputs; polling state remains strictly read-only."""
import hashlib
import json
import shutil
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_application import load_modules,create_event_engine
from event_startup import load_startup,write_event_state_files
from event_composition import symbol_restore_files
from event_engine.restoration import indicator_state,fvg_state,sweep_state
from event_engine import Kind
from moses_language import command_language, language_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import types,socket
    def deny(*args,**kwargs):raise AssertionError('E2 startup network forbidden')
    requests=types.ModuleType('requests');requests.get=deny;requests.post=deny;requests.Session=deny
    monkeypatch.setitem(sys.modules,'requests',requests)
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_startup_same_inputs_and_only_event_output(tmp_path):
    program=tmp_path/'Part1/program';logs=program/'logs';logs.mkdir(parents=True)
    (program/'SPECIAL').mkdir()
    for i in range(1,8):(program/'SPECIAL'/f'SPECIAL{i}.py').write_text('')
    (program/'config.txt').write_text('STAFF_ALLOWED_SYMBOLS=XAUUSD+,NAS100,BTCUSD\nTARGET_SYMBOLS=XAUUSD+,NAS100,BTCUSD\nWONBI_SIGMA=3\nCOMMAND_ALIASES_FILE=language.json\n',encoding='utf-8')
    shutil.copyfile(language_path(),program/'language.json')
    (program.parent/'special_settings.json').write_text(json.dumps({'specials':{'SPECIAL2':{'enabled':False},
        'SPECIAL7':{'enabled':True,'trigger':'무지성 올존'}}}),encoding='utf-8')
    (logs/'trend_watch_state.json').write_text(json.dumps({'watches':{'a':['XAUUSD+','1m',['sma17_slope']],
        'b':['BTCUSD','5m',[]]}}))
    (logs/'trend_stream.json').write_text('{"version":1,"records":{"generation":7}}')
    (logs/'sweep_event.jsonl').write_bytes(b'{"action":"SWEEP_EVENT","event_id":"old"}\nBAD\n{"action":"SWEEP_EVENT"')
    (logs/'oz_watch_command.jsonl').write_text('{"action":"RESET_ALL"}\n')
    before=hashes(program.parent)
    modules=load_modules();inputs=load_startup(program,modules)
    assert inputs.config['STAFF_ALLOWED_SYMBOLS']=='XAUUSD+,NAS100,BTCUSD'
    assert inputs.aliases == command_language()
    assert inputs.enabled_specials==('SPECIAL1','SPECIAL3','SPECIAL4','SPECIAL5','SPECIAL6','SPECIAL7')
    assert inputs.triggers=={'SPECIAL7':'무지성 올존'}
    assert inputs.sweep_events==({'action':'SWEEP_EVENT','event_id':'old'},)
    assert 'oz_watch_command.jsonl' not in inputs.state_files
    state={};indicator_state(modules['strategy_INDICATOR'],modules['durable_protocol'],state,inputs.state_files)
    assert set(state['watches'])=={'a','b'}
    assert state['stream']=={'generation':8,'sequence':0}
    output=tmp_path/'event_state'
    write_event_state_files({'trend_stream.json':'{"generation":8}'},output_directory=output,polling_program=program)
    assert json.loads((output/'trend_stream.json').read_text())=={'generation':8}
    assert hashes(program.parent)==before
    for target in (program,logs,tmp_path):
        with pytest.raises(ValueError):write_event_state_files({},output_directory=target,polling_program=program)
    with pytest.raises(ValueError):write_event_state_files({'../escape.json':'{}'},output_directory=output,polling_program=program)
    assert hashes(program.parent)==before


def test_symbol_restore_and_startup_reconcile_never_cancel_other_symbol():
    files={name:json.dumps({'watches':{'x':['XAUUSD+','1m',[]],'b':['BTCUSD','5m',[]]}})
           for name in ('trend_watch_state.json','fvg_watch_state.json')}
    files['sweep_watch_state.json']=json.dumps({'watches':{'x':{'symbol':'XAUUSD+'},'b':{'symbol':'BTCUSD'}}})
    scoped=symbol_restore_files(files,'XAUUSD+')
    assert all(set(json.loads(scoped[n])['watches'])=={'x'} for n in files)
    assert all(set(json.loads(files[n])['watches'])=={'x','b'} for n in files)
    engine=create_event_engine({'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+,BTCUSD',
        'TARGET_SYMBOLS':'XAUUSD+,BTCUSD','TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'},state_files=files)
    engine.ingress.post(Kind.COMMAND,source='test',source_seq=1,source_time=1790380680000,
        payload={'symbol':'XAUUSD+','chat_id':'offline','text':'골드 1분 기울기(SMA17) > 0 알려줘'})
    engine.run()
    assert not engine.error_log,engine.error_log
    commands=[s.payload['content'].get('command',{}) for s in engine.signals]
    assert any(c.get('action')=='CANCEL_TREND' and c.get('watch_id')=='x' for c in commands)
    assert not any(c.get('action','').startswith('CANCEL_') and c.get('watch_id')=='b' for c in commands)


def test_canonical_registry_restore_and_corrupt_fallback():
    modules=load_modules();protocol=modules['durable_protocol']
    files={'fvg_watch_state.json':json.dumps({'watches':{'f':['BTCUSD','5m']}}),
           'fvg_stream.json':'{"version":1,"records":{"generation":4}}',
           'sweep_watch_state.json':json.dumps({'watches':{'s':{'symbol':'BTCUSD','source_tf':'1m','levels':['PDH']}}}),
           'sweep_stream.json':'{"version":1,"records":{"generation":9}}',
           'sweep_detector_state.json':'{broken', 'trend_watch_state.json':'{broken'}
    f={};s={};t={}
    fvg_state(modules['strategy_FVG'],protocol,f,files)
    sweep_state(modules['strategy_SWEEP'],protocol,s,files)
    indicator_state(modules['strategy_INDICATOR'],protocol,t,files)
    assert f['watches']=={'f':('BTCUSD','5m')}
    assert f['stream']['generation']==5 and s['stream']['generation']==10
    assert s['watches']['s']['symbol']=='BTCUSD'
    assert t['watches']=={} and s['core']['detectors']=={}


def test_restored_real_watch_crosses_midnight_and_checkpoint_without_other_symbol_clock():
    from watch_orchestrator import TimedChainSpec,ChainTriggerSpec
    from test_event_e2_domains import snap
    # 2026-09-24 23:59:59 KST, then midnight. No host clock is read.
    t=1790261999000;due=t+1000
    chain=TimedChainSpec('CHAIN:MIDNIGHT','offline','XAUUSD+',
        (ChainTriggerSpec('BAR_CLOSE','1m'),),final_action='NOTIFY',
        created_at=t/1000,start_at=due/1000,started=False)
    chain.validate()
    files={'composer_timed_chains.json':json.dumps({'version':2,'chains':[chain.to_json()]})}
    config={'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+,BTCUSD',
            'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'}
    def make():return create_event_engine(config,enabled_specials=(),state_files=files)
    e=make()
    def publish(engine,symbol,when,seq):
        engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=seq,source_time=when,
                            payload={'symbol':symbol,'feeds':{'1m':snap(seq,seq)}})
        engine.run()
        assert not engine.error_log,engine.error_log
    publish(e,'XAUUSD+',t,1)
    publish(e,'BTCUSD',due+1000,2)
    assert not e.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager.timed_chains[chain.chain_id].started
    r=make();r.restore(e.checkpoint());offset=len(e.signals)
    for engine in (e,r):publish(engine,'XAUUSD+',due+2000,3)
    assert e.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager.timed_chains[chain.chain_id].started
    signature=lambda values:[(s.source_time,s.payload['signal_id'],s.payload['content']) for s in values]
    assert signature(e.signals[offset:])==signature(r.signals)
    notices=[s for s in r.signals if s.payload['content'].get('type')=='NOTIFICATION'
             and '예약 Watch 시작' in s.payload['content']['message']]
    assert len(notices)==1 and notices[0].source_time==due
