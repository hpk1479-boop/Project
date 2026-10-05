"""EA-owned symbol/TF supply: LIVE input is not limited by config lists."""
import io,socket,sys,threading
from pathlib import Path
import pytest
R=Path(__file__).resolve().parents[1];P=R/'Part1/program'
sys.path[:0]=[str(P),str(R/'Part2')]
import staff_schema as wire
from event_application import create_event_engine
from event_engine import Kind,EventEngine,IngressSequencer
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import EventHost,load_staff
from event_pipe_host import PipeReceiver
from command_interpreter import CommandInterpreter
from test_event_e2_domains import snap

NO_LISTS={'TELEGRAM_CHAT_ID':'official','TELEGRAM_COMMAND_CHAT_IDS':'user','WONBI_SIGMA':'3'}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a,**k):raise AssertionError('network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)


def receiver(tmp_path,seed=()):
    staff=load_staff();cache=staff.StaffPipeCache('',gap_journal=tmp_path/'gaps.jsonl')
    engine=EventEngine(IngressSequencer(),retain_events=True)
    return engine,cache,PipeReceiver(staff,cache,StaffIngressAdapter(cache,engine.ingress),seed,threading.Event())


def send(r,symbol,tf,seq,t):
    s=snap();r.receive(io.BytesIO(wire.pack_v2(symbol,tf,s.time,s.volume,s.values,seq=seq)).read,lambda _:None,source_time=t)


def test_pipe_registers_ea_symbols_and_only_sent_timeframes(tmp_path):
    engine,cache,r=receiver(tmp_path)
    r.receive(io.BytesIO(wire.pack_hello('a'*64)).read,lambda _:None)
    for i,tf in enumerate(('1m','5m','1h')):send(r,'BTCUSD',tf,1,1790380680000+i)
    send(r,'US30.cash','1m',1,1790380681000)
    assert sorted(cache.keys())==[('BTCUSD','1h'),('BTCUSD','1m'),('BTCUSD','5m'),('US30.cash','1m')]
    assert r.symbols==('BTCUSD','US30.cash')
    engine.run()
    assert {e.payload['symbol'] for e in engine.events if e.kind==Kind.MARKET_BUNDLE}=={'BTCUSD','US30.cash'}


@pytest.mark.parametrize('symbol',['XAU USD','A,B','X\tY','BAD\x01'])
def test_pipe_rejects_malformed_symbol(tmp_path,symbol):
    engine,cache,r=receiver(tmp_path)
    with pytest.raises(ValueError,match='disallowed'):send(r,symbol,'1m',1,1790380680000)
    assert cache.keys()==() and len(engine.ingress)==0 and r.symbols==()


def test_seed_first_then_observed_and_health_uses_registry(tmp_path):
    engine,cache,r=receiver(tmp_path,seed=('XAUUSD+',))
    send(r,'BTCUSD','1m',1,1790380680000)
    assert r.symbols==('XAUUSD+','BTCUSD')
    posted=[]
    r.adapter.health=lambda **k:posted.append(k['symbol'])
    class Once:
        n=0
        def wait(self,_):
            self.n+=1;return self.n>1
    r.stop=Once();r._health_loop()
    assert posted==['XAUUSD+','BTCUSD']


def test_engine_without_config_lists_accepts_any_symbol():
    e=create_event_engine(dict(NO_LISTS),enabled_specials=())
    assert all(c.subscriptions().symbols==() for c in (*e.strategies,*e.processors))
    s=snap()
    for symbol in ('XAUUSD+','US30.cash'):
        e.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=1,source_time=1790380680000,payload={'symbol':symbol,'feeds':{'1m':s}})
    e.run();assert not e.error_log,e.error_log


def test_explicit_replay_symbols_unchanged():
    e=create_event_engine(dict(NO_LISTS),symbols=('XAUUSD+',),enabled_specials=())
    subs={c.name:c.subscriptions().symbols for c in (*e.strategies,*e.processors)}
    assert subs['COMPOSER']==('XAUUSD+',) and set(subs.values())<={('XAUUSD+',),()}


def test_host_commands_use_ea_symbols_without_config_lists():
    e=create_event_engine(dict(NO_LISTS),enabled_specials=())
    h=EventHost(e,dict(NO_LISTS),CommandInterpreter(dict(NO_LISTS)),transport=lambda d:None)
    assert h.inputs.symbols==()
    h.inputs.symbol_source=lambda:('XAUUSD+','BTCUSD')
    assert h.inputs.interpreter.parse_symbol('BTCUSD 1분 올존 알려줘')=='BTCUSD'
    msg={'chat':{'id':'user','type':'private'},'text':'1분 올존 알려줘','message_id':5,'date':1790380679}
    assert h.inputs.telegram_update({'update_id':5,'message':msg})
    assert e.ingress.take_input(e).payload['symbol']=='XAUUSD+'


MT5=P/'MT5'


def test_ea_market_data_uses_broker_symbol_and_wire_uses_logical():
    import re
    ea=(MT5/'THE_STAFF_OF_MOSES.mq5').read_text('utf-8-sig')
    assert '#include "STAFF_Symbol_Map.mqh"' in ea
    data=re.findall(r'\b(?:CopyRates|SeriesInfoInteger|SymbolSelect|iMA)\(\s*([\w.]+)|\bCreateCustom\([A-Z_]+,\s*([\w.]+)',ea)
    args={a or b for a,b in data}
    assert len(data)>=15 and args<={'f.broker_symbol','broker'},args
    frames=re.findall(r'Staff(?:Feed|Bundle)Frame\(\s*([\w.\[\]]+)',ea)
    assert frames and all(x.endswith('.symbol') or x=='g_pending_symbol' for x in frames),frames
    assert 'g_pending_symbol=g_feeds[first].symbol' in ea


def test_ea_live_loop_unchanged_from_previous_revision():
    ea=(MT5/'THE_STAFF_OF_MOSES.mq5').read_text('utf-8-sig')
    old=(R.parent/'수정본34/Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8-sig')
    assert ea[ea.index('int OnInit()'):]==old[old.index('int OnInit()'):]


def test_symbol_map_header_is_deployed_to_tester():
    from generic_backtest import native_mt5
    from event_backtest import recording
    assert (MT5/'STAFF_Symbol_Map.mqh').is_file() and (MT5/'STAFF_Symbol_Map_Test.mq5').is_file()
    assert "'STAFF_Symbol_Map.mqh'" in Path(native_mt5.__file__).read_text('utf-8')
    assert any(p.name=='STAFF_Symbol_Map.mqh' for p in recording.deployment_targets({'data_root':'x'}))


def test_part2_scenario_accepts_any_well_formed_logical_symbol():
    from event_backtest.settings import scenario
    assert scenario(symbol='US30.cash',start='2025-09-01',end='2025-09-02')['symbol']=='US30.cash'
    for bad in ('XAU USD','A,B',''):
        with pytest.raises(ValueError,match='symbol form'):scenario(symbol=bad,start='2025-09-01',end='2025-09-02')


def test_part2_tester_symbol_mapping():
    from event_backtest.settings import tester_symbol
    assert tester_symbol('XAUUSD+',{})=='XAUUSD+'
    assert tester_symbol('XAUUSD+',{'broker_symbols':{'XAUUSD+':'GOLD.pro'}})=='GOLD.pro'
    assert tester_symbol('NAS100',{'broker_symbols':{'XAUUSD+':'GOLD.pro'}})=='NAS100'
    with pytest.raises(ValueError):tester_symbol('XAUUSD+',{'broker_symbols':{'XAUUSD+':'GOLD pro'}})


def test_tester_runs_broker_symbol_but_ea_request_keeps_logical(tmp_path,monkeypatch):
    from generic_backtest import native_mt5 as n
    exe=tmp_path/'terminal64.exe';exe.write_bytes(b'')
    seen={}
    real=n._write_request
    def request(common,session,symbol,*a):
        seen['request']=symbol;return real(common,session,symbol,*a)
    monkeypatch.setattr(n,'_write_request',request)
    monkeypatch.setattr(n,'locate_compiled_expert',lambda p:tmp_path/'e.ex5')
    monkeypatch.setattr(n,'tester_expert_name',lambda p,e:'THE_STAFF_OF_MOSES')
    monkeypatch.setattr(n,'common_files_root',lambda:tmp_path/'common')
    def stop(args,**k):
        seen['ini']=Path(args[1].split(':',1)[1]).read_text('ascii');raise RuntimeError('stop before launch')
    monkeypatch.setattr(n.subprocess,'Popen',stop)
    with pytest.raises(RuntimeError,match='stop before launch'):
        n.run_native_tester({'executable':str(exe)},'GOLD.pro',0,86400*10**9,tmp_path/'work',logical_symbol='XAUUSD+',
                            tester_inputs={'InpTesterLogicalSymbol':'XAUUSD+'})
    assert 'Symbol=GOLD.pro' in seen['ini'] and 'InpTesterLogicalSymbol=XAUUSD+' in seen['ini']
    assert seen['request']=='XAUUSD+'


def test_part2_recording_passes_logical_identity_and_bridge_has_no_fixed_list(tmp_path,monkeypatch):
    rec=(R/'Part2/event_backtest/recording.py').read_text('utf-8')
    assert "run_native_tester(profile,tester," in rec and "logical_symbol=scenario['symbol']" in rec
    assert "'InpTesterLogicalSymbol':scenario['symbol']" in rec and "'symbol':scenario['symbol']" in rec
    assert "('XAUUSD+','NAS100','BTCUSD')" not in (R/'Part2/event_backtest/bridge.py').read_text('utf-8')
    # The current web options use actual captures symbol directories,
    # including broker-specific symbols that were not part of the old GUI list.
    import duckdb
    sys.path.insert(0,str(R/'Part3'))
    from lab import unified_backtest,unified_live
    from event_backtest import ui_model
    monkeypatch.setattr(unified_backtest,'warehouse',lambda:tmp_path)
    monkeypatch.setattr(ui_model,'load',lambda:{})
    monkeypatch.setattr(unified_live,'control',lambda:{
        'special_code_default_trigger':lambda name:'올존'})
    assert unified_backtest.options()['symbols'] == []
    path=tmp_path/'captures.duckdb'
    db=duckdb.connect(str(path))
    try:
        db.execute('CREATE TABLE captures (capture_id VARCHAR, symbol VARCHAR)')
        db.execute("INSERT INTO captures VALUES ('a','US30.cash'),('b','GER40'),('c','US30.cash'),('d','XAUUSD+')")
    finally:db.close()
    for symbol in ('US30.cash', 'GER40', 'XAUUSD+'):
        (tmp_path/'captures'/symbol).mkdir(parents=True)
    before=path.read_bytes()
    options=unified_backtest.options()
    assert options['symbols']==['GER40','US30.cash','XAUUSD+']
    assert options['symbol'] in options['symbols']
    assert path.read_bytes()==before


def test_stored_symbols_from_catalog(tmp_path):
    import duckdb
    from event_backtest.settings import stored_symbols
    assert stored_symbols(tmp_path)==[]
    db=duckdb.connect(str(tmp_path/'captures.duckdb'))
    db.execute('CREATE TABLE captures (capture_id VARCHAR, symbol VARCHAR)')
    db.execute("INSERT INTO captures VALUES ('a','XAUUSD+'),('b','NAS100'),('c','XAUUSD+')");db.close()
    assert stored_symbols(tmp_path)==['NAS100','XAUUSD+']


def test_config_hint_keeps_default_symbol_order():
    config={**NO_LISTS,'STAFF_ALLOWED_SYMBOLS':'NAS100, BTCUSD'}
    e=create_event_engine(config,enabled_specials=())
    h=EventHost(e,config,CommandInterpreter(config),transport=lambda d:None)
    h.inputs.symbol_source=lambda:('XAUUSD+','NAS100')
    assert h.inputs.symbols==('NAS100','BTCUSD','XAUUSD+')
