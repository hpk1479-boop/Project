import copy
import hashlib
import io
import json
import struct
from dataclasses import replace
from pathlib import Path
import pytest

from generic_backtest.canonical import encode, bits, plain
from generic_backtest.context import AsOfContext, TransportContextDecoder
from generic_backtest.worker_protocol import DeltaContextEncoder, DeltaContextReceiver
from generic_backtest.contracts import GenericError, ROOT
from generic_backtest.worker import StrategyWorker
from generic_backtest.plugins import parse_literal_metadata, PluginMetadata
from generic_backtest.fast import memo_client
from generic_backtest.fast.frame_cache import OpenHMACache
from generic_backtest.fast.feature_memo import CommonFeatureMemo
from generic_backtest.ipc import JobProtocol, Cancelled
from ipc_fixtures import Contexts


def signature(ctx):
    return encode(bits({'token':ctx.token,'history':dict(ctx.history),
        'quote':dict(ctx.quote),'phase':ctx.phase,'ready':ctx.ready}))


def test_decoder_default_capacity_keeps_nineteen_native_histories():
    from generic_backtest.contracts import TIMEFRAMES
    decoder=TransportContextDecoder()
    ctx=next(iter(Contexts(1,682,tuple(TIMEFRAMES),features=False)))
    first=decoder.decode(ctx);misses=decoder.misses
    second=decoder.decode(ctx)
    assert decoder.misses==misses
    assert decoder.hits==19*682
    assert signature(first)==signature(second)

@pytest.mark.parametrize('limit',[9,8192])
def test_decoder_same_fields_bits_and_fresh_identity(limit):
    encoder=DeltaContextEncoder();receiver=DeltaContextReceiver();decoder=TransportContextDecoder(limit)
    previous={};count=0
    for ctx in Contexts(120,32,features=False):
        prepared=encoder.prepare(ctx,(), 'observe',32*1024*1024)
        request,seq=receiver.decode(json.loads(prepared.wire));encoder.commit(prepared,seq)
        ref=AsOfContext.decode(request['context']);actual=decoder.decode(request['context'])
        assert signature(ref)==signature(actual)
        for tf,rows in actual.history.items():
            for index,row in enumerate(rows):
                assert row is not ref.history[tf][index]
                assert row is not previous.get(row.bar_id)
                previous[row.bar_id]=row;count+=1
        assert len(decoder.templates)<=limit
    assert count==120*32*3
    if limit==8192:assert decoder.hits>10000

@pytest.mark.parametrize('kind',['future_open','future_ordinal','future_completed_end','missing','extra','defaults','signed_zero'])
def test_decoder_guard_and_errors(kind):
    ctx=next(iter(Contexts(1,4,('1m',),features=False)))
    ctx=json.loads(encode(ctx));row=ctx['history']['1m'][0]
    if kind=='future_open':row['open_ns']=ctx['token']['now_ns']+1
    if kind=='future_ordinal':row['last_ordinal']=1
    if kind=='future_completed_end':row['nominal_end_ns']=ctx['token']['now_ns']+1
    if kind=='missing':del row['bar_id']
    if kind=='extra':row['bogus']=0
    if kind=='defaults':
        for k in ('quality','gap_before','complete_at_order','seed_quality'):row.pop(k,None)
    if kind=='signed_zero':row['open']=-0.0
    d=TransportContextDecoder()
    for _ in range(2):
        try:expected=AsOfContext.decode(ctx)
        except Exception as exc:
            with pytest.raises(type(exc)) as result:d.decode(ctx)
            assert str(result.value)==str(exc)
        else:assert signature(expected)==signature(d.decode(ctx))


def test_decoder_rechecks_cached_history_against_new_token():
    ctx=json.loads(encode(next(iter(Contexts(1,4,('1m',),features=False)))))
    d=TransportContextDecoder();d.decode(ctx)
    old=dict(ctx,token=dict(ctx['token'],now_ns=ctx['history']['1m'][0]['open_ns']-1))
    with pytest.raises(GenericError,match='E_FUTURE_READ'):d.decode(old)


def test_decoder_does_not_cache_mutable_unusual_fields():
    ctx=json.loads(encode(next(iter(Contexts(1,4,('1m',),features=False)))))
    row=ctx['history']['1m'][0];row['prefix']=['unusual']
    d=TransportContextDecoder();ref=AsOfContext.decode(ctx);result=d.decode(ctx)
    assert signature(ref)==signature(result)
    assert id(row) not in d.templates


@pytest.mark.parametrize('transport',['DELTA_V1','FULL'])
@pytest.mark.parametrize('role',['SIGNAL','ENTRY'])
def test_isolated_workers_order_positive_outputs(transport,role):
    record=parse_literal_metadata(ROOT/'backtest_specials/GENERIC_EXAMPLE_V1.py')
    params={'every_ticks':3,'timeframe':'1m'}
    ref=StrategyWorker(record,params,{},role,{'worker_decode_cache':False,'worker_cache_batch':False,'worker_transport':transport})
    opt=StrategyWorker(record,params,{},role,{'worker_decode_cache':True,'worker_cache_batch':True,'worker_transport':transport,'profile_ipc':True})
    events=[]
    try:
        for i,ctx in enumerate(Contexts(80,12,('1m',),features=False)):
            op='warmup' if i<4 else 'observe'
            if i==40:ref.mark_observation_gap();opt.mark_observation_gap()
            alerts=[{'direction':'LONG' if i%2 else 'SHORT','event_key':str(i),'event_id':f'event-{i}'}]
            a=ref.observe(ctx,alerts,op);b=opt.observe(ctx,alerts,op)
            assert encode(a)==encode(b);events.extend(a)
        assert encode(ref.observe(ctx,op='finish'))==encode(opt.observe(ctx,op='finish'))
        assert events
        assert {e['direction'] for e in events}=={'LONG','SHORT'}
        assert opt.metrics.snapshot()['tx_messages']>1
        assert opt.worker_metrics['callbacks']>1
        assert ref.process.pid!=opt.process.pid
    finally:ref.close();opt.close()


def test_worker_denial_still_active():
    raw=b"import os\ndef requirements(p,s): return {}\n"
    record=PluginMetadata('FORBIDDEN.py',hashlib.sha256(raw).hexdigest(),{},raw)
    with pytest.raises(GenericError,match='E_CAPABILITY_DENIED'):
        StrategyWorker(record,{}, {}, 'SIGNAL', {})


def test_transport_ack_resource_limits_and_future():
    ctx=next(iter(Contexts(1,8,('1m',),features=False)))
    enc=DeltaContextEncoder();rx=DeltaContextReceiver()
    with pytest.raises(GenericError,match='E_RESOURCE_LIMIT'):enc.prepare(ctx,(),'observe',32)
    p=enc.prepare(ctx,(),'observe',32*1024*1024)
    with pytest.raises(GenericError,match='ACK'):enc.commit(p,2)
    req=json.loads(p.wire);rx.decode(req)
    with pytest.raises(GenericError,match='base/sequence'):rx.decode(req)


def fake_memo(monkeypatch,batch):
    store={};calls=[]
    def key(k):return json.dumps(k,sort_keys=True,separators=(',',':'))
    def exchange(op,k=None,value=None,items=None):
        calls.append(op)
        def handle(item):
            name=key(item['key'])
            if item['op']=='get':return copy.deepcopy(store.get(name))
            store[name]=copy.deepcopy(item['value']);return None
        if op=='batch':return [handle(item) for item in items]
        return handle({'op':op,'key':k,'value':value})
    monkeypatch.setattr(memo_client,'_ENABLED',True)
    monkeypatch.setattr(memo_client,'_BATCH',batch)
    monkeypatch.setattr(memo_client,'_VALUES',memo_client.OrderedDict())
    monkeypatch.setattr(memo_client,'_exchange',exchange)
    return store,calls


def hma_sequence(kind,maxsize,batch,monkeypatch):
    store,calls=fake_memo(monkeypatch,batch)
    owner=OpenHMACache(maxsize,timeframe='1m');outputs=[]
    for i,ctx in enumerate(Contexts(24,210,('1m',),features=False)):
        bars=list(AsOfContext.decode(ctx).history['1m'])
        if kind=='constant':bars=[replace(b,open=100.) for b in bars]
        if kind=='duplicates':bars=[replace(b,open=float(j%3)+100.) for j,b in enumerate(bars)]
        if kind=='nan':bars=[replace(b,open=float('nan') if j%31==0 else b.open) for j,b in enumerate(bars)]
        if kind=='infinity':bars=[replace(b,open=float('inf') if j%31==0 else b.open) for j,b in enumerate(bars)]
        if kind=='signed_zero':bars=[replace(b,open=-0.0 if j%2 else 0.0) for j,b in enumerate(bars)]
        if kind=='correction' and i%3==0:bars[10]=replace(bars[10],open=102.0+i)
        if kind=='reindex' and i>12:bars=bars[12:]
        row=[]
        for period in (6,17,50,168):
            values=owner.history(tuple(bars),period)
            row.append(tuple(None if v is None else struct.pack('>d',v).hex() for v in values))
        outputs.append(row)
    return outputs,store,calls

@pytest.mark.parametrize('kind',['normal','constant','duplicates','nan','infinity','signed_zero','correction','reindex'])
@pytest.mark.parametrize('maxsize',[64,8192])
def test_batched_hma_bit_exact(kind,maxsize,monkeypatch):
    ref,_,oldcalls=hma_sequence(kind,maxsize,False,monkeypatch)
    opt,_,newcalls=hma_sequence(kind,maxsize,True,monkeypatch)
    assert opt==ref
    if kind=='normal' and maxsize==8192:assert len(newcalls)<len(oldcalls)/10


def test_real_cache_batch_checks_and_scope(tmp_path):
    cache=CommonFeatureMemo({'fixture':1},root=tmp_path)
    k=memo_client.make_key('OPEN_HMA','1m',{'period':6},'a'*64)
    try:
        assert cache.handle({'op':'batch','items':[{'op':'get','key':k}]})==[None]
        assert cache.handle({'op':'batch','items':[{'op':'put','key':k,'value':{'value':-0.0}},{'op':'get','key':k}]})==[None,{'value':-0.0}]
        assert struct.pack('>d',cache.handle({'op':'get','key':k})['value']).hex()=='8000000000000000'
        for items in ([],[{'op':'get','key':k}]*129):
            with pytest.raises(GenericError,match='E_RESOURCE_LIMIT'):cache.handle({'op':'batch','items':items})
        with pytest.raises(GenericError,match='E_CAPABILITY_DENIED'):
            cache.handle({'op':'batch','items':[{'op':'batch','items':[]} ]})
        with pytest.raises(GenericError,match='E_CAPABILITY_DENIED'):
            cache.handle({'op':'batch','items':[{'op':'get','key':dict(k,kind='ALERT')}]})
        other=CommonFeatureMemo({'fixture':2},root=tmp_path)
        try:assert other.handle({'op':'get','key':k}) is None
        finally:other.close()
        cache.connection.execute("UPDATE numeric_inputs SET checksum='broken'")
        assert cache.handle({'op':'batch','items':[{'op':'get','key':k}]})==[None]
        assert cache.stats['errors']==['INVALIDATED_CHECKSUM']
    finally:cache.close()


def test_progress_deduplicates_only_unchanged_values_and_preserves_control():
    output=io.StringIO();p=JobProtocol('job',output)
    p.emit('HELLO')
    for i in range(1000):p.emit('PROGRESS',{'phase':'COMPUTE','done':i//10,'total':100,'replay_time_ns':(i//10)*60})
    p.accept_cancel({'job_id':'job','command':'CANCEL','request_id':'r'})
    p.accept_cancel({'job_id':'job','command':'CANCEL','request_id':'r'})
    p.emit('PHASE',{'phase':'FINAL'})
    p.emit('PROGRESS',{'phase':'FINAL','done':99,'total':100})
    p.emit('CANCELLED')
    rows=[json.loads(x) for x in output.getvalue().splitlines()]
    assert p.suppressed_progress==900
    assert [r['sequence'] for r in rows]==list(range(1,len(rows)+1))
    assert len([r for r in rows if r['event_type']=='CANCEL_ACK'])==2
    assert rows[-1]['event_type']=='CANCELLED'
    with pytest.raises(Cancelled):p.check()


def test_watch_local_integrity_and_semantic_plan():
    from generic_backtest.watch.engine_capability import reference_status
    from generic_backtest.watch.compiler import compile_watch,verify_plan
    assert reference_status(verify=True)['native_live_signal_parity']=='UNVERIFIED'
    for command in ('1분 올존 알려줘','15분 추세 상승이고 1분 올존 알려줘','3분봉 마감 알려줘'):
        plan=compile_watch(command,'TEST');assert plan['status']=='READY';assert verify_plan(plan)==plan
