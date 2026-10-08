"""Part2 connector contracts; network is never permitted."""
from pathlib import Path
import csv,datetime as dt,json,sys,struct
import numpy as np
import pytest
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]
from event_backtest.settings import fragments,default_dates,scenario,overlap_start
from event_backtest.warehouse import Warehouse,ResultWriter
from event_backtest.bridge import CaptureInputs,continuous
from event_engine.model import Input,Kind,FeedSnapshot,Resolution,Subscriptions,Event
from event_engine.replay import select_inputs
import staff_schema as wire

def test_calendar_fragments_and_defaults():
    assert default_dates(dt.date(2026,9,27))==('2025-09-01','2026-09-01')
    assert list(fragments('2026-08-20','2026-09-03',dt.date(2026,9,27)))==[
        {'start':'2026-08-01','end':'2026-09-01','unit':'MONTH'},
        {'start':'2026-09-01','end':'2026-09-02','unit':'DAY'},
        {'start':'2026-09-02','end':'2026-09-03','unit':'DAY'}]
    assert overlap_start('2026-09-28',3)=='2026-09-23'

@pytest.mark.parametrize('path',['C:/Users/name/file','D:\\warehouse\\x','../outside','/absolute','\\\\server\\share'])
def test_warehouse_path_rejects_nonportable(tmp_path,path):
    from event_backtest.settings import warehouse_path
    with pytest.raises(ValueError):warehouse_path(tmp_path,path)

def test_metadata_rejects_absolute_paths():
    from event_backtest.portable import validate
    with pytest.raises(ValueError):validate({'path':'C:/Users/name/file'})
    with pytest.raises(ValueError):validate({'path':'../outside'})
    assert validate({'path':'captures/month/pipe_000.bin.gz'})

def test_progress_reader_lock_cannot_abort_replay(tmp_path,monkeypatch):
    from event_backtest.system import write_progress
    path=tmp_path/'progress.json';assert write_progress(path,{'bundles':1})
    replace=Path.replace
    def locked(*args):raise PermissionError('reader denies delete sharing')
    monkeypatch.setattr(Path,'replace',locked)
    assert write_progress(path,{'bundles':2}) is False
    assert json.loads(path.read_text())=={'bundles':1}
    monkeypatch.setattr(Path,'replace',replace)
    assert write_progress(path,{'bundles':3})
    assert json.loads(path.read_text())=={'bundles':3}

def test_overlap_uses_observed_days_not_weekdays():
    from event_backtest.calendar import warm_start
    captures=[{'observed_days':['2025-12-22','2025-12-23','2025-12-24','2025-12-26']}]
    assert warm_start('2025-12-29',3,captures)=='2025-12-23'
    with pytest.raises(ValueError):warm_start('2025-12-23',3,captures)

def test_month_and_day_coverage_without_duplicates():
    from event_backtest.catalog_plan import coverage,missing_pieces
    month={'start':'2026-08-01','end':'2026-09-01','unit':'MONTH'}
    day={'start':'2026-08-25','end':'2026-08-26','unit':'DAY'}
    selected,missing=coverage([day,month],'2026-08-01','2026-09-01')
    assert selected==[month] and missing==[]
    selected,missing=missing_pieces(month,[day])
    assert selected==[day] and len(missing)==30
    assert not any(p['start']=='2026-08-25' for p in missing)

def snap(bar,seq=1):
    t=np.arange(bar-120,bar+1,60,dtype='int64');v=np.ones(3,dtype='int64')
    x=np.ones((3,len(wire.PIPE_VALUE_COLUMNS)));x[:,:4]=100
    return FeedSnapshot(t,v,x,seq,'epoch',{'PRICE':True})

def item(bar,observed,seq=1):
    return Input('staff',seq,observed,Kind.MARKET_BUNDLE,{'symbol':'XAUUSD+','feeds':{'1m':snap(bar,seq)}},0)

def test_stream_selector_no_chunk_boundary_artifacts():
    rows=[item(600+i//3*60,1000+i,i+1) for i in range(13)]
    subs=[Subscriptions(resolution=Resolution.BAR_CLOSE)]
    for size in (1,2,3,8,100):
        assert [x.source_time for x in select_inputs(iter(rows),subs,Resolution.BAR_CLOSE,chunk_size=size)]==[1000,1003,1006,1009,1012]

def test_stream_selector_bounded_consumption():
    count=[0]
    def source():
        for i in range(100000):count[0]+=1;yield item(600+i*60,1000+i,i+1)
    stream=select_inputs(source(),[Subscriptions()],Resolution.BAR_CLOSE,chunk_size=3)
    next(stream);assert count[0]==3

def write_capture(root,rows):
    root.mkdir();p=root/'pipe_000.bin'
    with p.open('wb') as f:
        f.write(struct.pack('<IIII',0x4D535033,2,len(wire.PIPE_VALUE_COLUMNS),650))
        for observed,s in rows:
            raw=wire.pack_v2('XAUUSD+','1m',s.time,s.volume,s.values,seq=s.seq)
            f.write(struct.pack('<qiI',observed,31,len(raw)));f.write(raw)
    (root/'complete.txt').write_text('complete')
    (root/'manifest.tsv').write_text('MSP3\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\npipe_observation_unit\tmilliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t'+str(len(rows))+'\n')

def test_retention_is_lossless_atomic_relative(tmp_path):
    import gzip
    from event_backtest.recording import retain_capture
    source=tmp_path/'source';target=tmp_path/'retained'
    write_capture(source,[(600000,snap(600))]);progress=[]
    size=retain_capture(source,target,progress=lambda *row:progress.append(row))
    assert gzip.open(target/'pipe_000.bin.gz','rb').read()==(source/'pipe_000.bin').read_bytes()
    assert size==(source/'pipe_000.bin').stat().st_size and progress==[(1,1)]
    assert not list(tmp_path.glob('*.partial_*'))
    assert 'pipe_000.bin.gz' in (target/'manifest.tsv').read_text()

def test_completed_empty_capture_has_no_invented_market_input(tmp_path):
    from event_backtest.calendar import capture_calendar
    from event_engine.capture_io import capture_bundles
    source=tmp_path/'source';write_capture(source,[])
    assert list(capture_bundles(source))==[]
    assert capture_calendar(source)['observed_days']==[]

def test_completed_piece_reuse_does_not_start_or_stop_mt5(tmp_path,monkeypatch):
    from event_backtest import recording
    from event_backtest.settings import file_hash
    root=tmp_path/'warehouse';build=root/'builds'/'source';build.mkdir(parents=True)
    binary=build/'THE_STAFF_OF_MOSES.ex5';binary.write_bytes(b'test-only compiled identity')
    sha=file_hash(binary);(build/'ready.json').write_text(json.dumps({'files':{binary.name:sha}}))
    capture=root/'captures'/'piece';capture.parent.mkdir();write_capture(capture,[(600000,snap(600))])
    from event_backtest.storage import convert
    capture,storage_metadata=convert(capture,root/'captures','delta_piece')
    c={'capture_id':'piece','symbol':'XAUUSD+','start':'2025-09-01','end':'2025-10-01','unit':'MONTH','mode':'BAR',
       'ea_build_hash':sha,'schema_id':wire.WIRE_SCHEMA_ID,'tick_evidence':{'actual':'REAL_TICKS'},'path':capture.relative_to(root).as_posix(),
       'files':{p.name:file_hash(p) for p in capture.iterdir()},'stored_bytes':sum(p.stat().st_size for p in capture.iterdir()),
       'recorded_at':'2026-09-27T00:00:00+00:00'}
    c.update(storage_metadata)
    w=Warehouse(root);w.register(c);w.close()
    monkeypatch.setattr(recording,'source_hash',lambda:'source')
    def forbidden(*a,**k):raise AssertionError('reused capture must not touch MT5')
    monkeypatch.setattr(recording,'installed_build',forbidden)
    s=scenario(start=c['start'],end=c['end'],strategies=['SPECIAL1'],overlap_trading_days=0)
    assert recording.prepare(s,root)==[c]

@pytest.mark.parametrize('transport',['replay','live'])
def test_seam_retains_transition_and_epoch(tmp_path,transport):
    from event_host import load_staff
    from event_engine import EventEngine,IngressSequencer
    from event_engine.model import Signal
    a=tmp_path/'a';b=tmp_path/'b'
    write_capture(a,[(600000,snap(600,1)),(660000,snap(660,2))])
    write_capture(b,[(720000,snap(720,1)),(780000,snap(780,2))])
    staff=load_staff();clock=[0.];cache=staff.StaffPipeCache('',monotonic=lambda:clock[0],health_session='TEST',gap_journal=tmp_path/'gap')
    feed=CaptureInputs(staff,cache,[a,b],transport=transport,clock=clock)
    rows=list(feed)
    assert len(rows)==4 and all(x.kind==Kind.MARKET_BUNDLE for x in rows)
    assert [x.payload['feeds']['1m'].seq for x in rows]==[1,2,3,4]
    assert len({x.payload['feeds']['1m'].source_epoch for x in rows})==1
    assert feed.seams[0]['continuous'] and not cache.wire_diagnostics()['gaps']
    class Probe:
        name='BOUNDARY'
        def subscriptions(self):return Subscriptions()
        def on_event(self,event,board,state,emit):
            if event.kind==Kind.MARKET_BUNDLE:
                bar=int(board.snapshot('XAUUSD+','1m').time[-1])
                if state.get('bar')==660 and bar==720:emit(Signal('XAUUSD+','seam',{'message':'transition'}))
                state['bar']=bar
    e=EventEngine(IngressSequencer(),[Probe()])
    from event_engine.replay import replay
    replay(e,rows);assert len(e.signals)==1 and e.signals[0].source_time==720000

def test_discontinuous_fragment_emits_reconnect(tmp_path):
    from event_host import load_staff
    a=tmp_path/'a';b=tmp_path/'b';write_capture(a,[(600000,snap(600))])
    write_capture(b,[(2000000,snap(2000))])
    staff=load_staff();clock=[0.];cache=staff.StaffPipeCache('',monotonic=lambda:clock[0],gap_journal=tmp_path/'gap')
    feed=CaptureInputs(staff,cache,[a,b],clock=clock)
    assert Kind.FEED_HEALTH in [r.kind for r in feed]
    assert not feed.seams[0]['continuous']

def test_seam_uses_market_history_not_rolling_indicator_seed():
    old=snap(660,2);new=snap(720,1);values=new.values.copy();values[0,8:]=np.nan
    raw=wire.pack_v2('XAUUSD+','1m',new.time,new.volume,values,seq=1)
    assert continuous(old,wire.decode_v2(raw))
    values[0,0]+=1
    raw=wire.pack_v2('XAUUSD+','1m',new.time,new.volume,values,seq=1)
    assert not continuous(old,wire.decode_v2(raw))

def test_alert_storage_and_comparison(tmp_path):
    w=Warehouse(tmp_path/'warehouse')
    for run,t in [('a',1000),('b',1100)]:
        out=ResultWriter(tmp_path/(run+'.csv'),{'run_id':run},0,10000)
        p={'strategy':'OZ','symbol':'XAUUSD+','signal_id':run,'content':{'type':'NOTIFICATION','message':'hello','recipients':['1'],'direction':'UP'}}
        out.accept(Event(1,0,'strategy',None,t,Kind.SIGNAL,p));out.close();w.import_results(out.path)
    assert w.compare('a','b',tmp_path/'diff.csv')==1
    assert w.db.execute('select count(*) from alerts').fetchone()[0]==2
    assert set(r[0] for r in w.db.execute('show tables').fetchall())=={'captures','runs','alerts','timings'}
    w.close()

def test_external_command_is_error_and_domain_facts_not_alerts(tmp_path):
    out=ResultWriter(tmp_path/'alerts.csv',{'run_id':'a'},0,10000)
    for kind in ('DOMAIN_FACT','EXTERNAL_REQUEST'):
        out.accept(Event(1,0,'strategy',None,1,Kind.SIGNAL,{'strategy':'COMPOSER','symbol':'XAUUSD+','signal_id':'a','content':{'type':kind,'text':'ambiguous'}}))
    out.close();assert out.external_error and out.count==0

def test_comparison_missing_repeat_does_not_shift_later_matches(tmp_path):
    w=Warehouse(tmp_path/'warehouse')
    for run,times in [('a',[1000,2000,3000]),('b',[1000,3000])]:
        out=ResultWriter(tmp_path/(run+'.csv'),{'run_id':run},0,10000)
        for t in times:
            p={'strategy':'OZ','symbol':'XAUUSD+','signal_id':str(t),'content':{'type':'NOTIFICATION','message':'same','recipients':['1']}}
            out.accept(Event(1,0,'strategy',None,t,Kind.SIGNAL,p))
        out.close();w.import_results(out.path)
    assert w.compare('a','b',tmp_path/'diff.csv')==1
    with (tmp_path/'diff.csv').open(encoding='utf-8') as f:
        row=next(csv.DictReader(f));assert row['difference']=='REMOVED' and row['before_ms']=='2000'
    w.close()

def test_export_context_does_not_mutate_signal(tmp_path):
    from event_engine.model import Signal,signal_id
    out=ResultWriter(tmp_path/'alerts.csv',{'run_id':'a'},0,10000)
    parent=Event(1,0,'oz',None,1000,Kind.SIGNAL,{'content':{'event':{'strategy':'OZ','grade':'S','validation_mode':'NORMAL','trigger_mode':'TRUE','kind':'FINAL_ALERT','source_tf':'1m','direction':'LONG'}}})
    signal=Signal('XAUUSD+','key',{'type':'NOTIFICATION','message':'[7. notification]','recipients':['1']})
    original=dict(signal.content);out.note_emission('COMPOSER',parent,signal)
    out.accept(Event(2,0,'composer',None,1000,Kind.SIGNAL,{'strategy':'COMPOSER','symbol':signal.symbol,'signal_id':signal_id('COMPOSER',signal.symbol,1000,'key'),'content':signal.content}));out.close()
    with out.path.open(encoding='utf-8',newline='') as f:row=next(csv.DictReader(f))
    assert (row['strategy'],row['profile'],row['grade'])==('SPECIAL7','NORMAL/TRUE','S')
    assert (row['tf'],row['direction'])==('1m','LONG')
    assert dict(signal.content)==original and not out.context

# 수정본162: the comparison of the EA live block with 수정본23 moved to 비교측정시험/test_ea_live_block_past.py.
