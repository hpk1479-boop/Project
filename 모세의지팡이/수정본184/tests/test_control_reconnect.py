"""The control screen and per-feed FULL bootstrap after a STAFF reconnect."""
from pathlib import Path
import hashlib
import socket
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
import staff_schema as wire
from event_host import load_staff
from module_status_state import display_state
from test_engine_optimization import snapshot


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*_args,**_kwargs):raise AssertionError('real network is forbidden')
    for name in ('connect','connect_ex','sendto'):
        monkeypatch.setattr(socket.socket,name,deny)


def test_three_display_states_require_connections_and_report_disconnect():
    modules={name:dict(status='정상',last=10,errors=0,monitoring=False)
             for name in ('STAFF','ENGINE','OZ','SWEEP','FVG','INDICATOR','WATCH','KIM')}
    data=dict(modules=modules,pipe_connected=False,pipe_seen=False,
              telegram_attempted=False,telegram_connected=False)
    assert display_state('STAFF',data)=='연결 대기'
    assert display_state('OZ',data)=='연결 대기'
    assert display_state('OZ',None,RuntimeError('host stopped'))=='연결 대기'
    data['pipe_connected']=True
    assert display_state('OZ',data)=='연결중'  # Connection readiness is independent of monitoring.
    assert display_state('KIM',data)=='연결 대기'
    assert display_state('WATCH',data)=='연결 대기'
    data['telegram_attempted']=True
    assert display_state('KIM',data)=='오류'
    assert display_state('WATCH',data)=='오류'
    data['telegram_connected']=True
    assert display_state('KIM',data)=='연결중'
    assert display_state('WATCH',data)=='연결중'
    modules['OZ']['monitoring']=True
    assert display_state('OZ',data)=='연결중'
    modules['OZ']['status']='지연'
    assert display_state('OZ',data)=='오류'
    data['pipe_connected']=False;data['pipe_seen']=True
    assert display_state('STAFF',data)=='오류'
    assert display_state('KIM',data)=='연결중'  # Telegram does not depend on STAFF.


def test_special_selection_and_telegram_connection_are_independent(tmp_path):
    import logging
    from module_diagnostics import Diagnostics
    sink=Diagnostics(tmp_path)
    try:
        sink.configure_specials(('SPECIAL1',))
        data=sink.snapshot()
        assert display_state('SPECIAL1',data)=='연결중'
        assert display_state('SPECIAL2',data)=='연결 대기'
        assert display_state('KIM',data)=='연결 대기'
        sink.telegram_state(True)
        assert display_state('KIM',sink.snapshot())=='연결중'
        sink.telegram_state(False)
        assert display_state('KIM',sink.snapshot())=='오류'
        sink.log('SPECIAL1',logging.ERROR,'전략 처리 실패')
        assert display_state('SPECIAL1',sink.snapshot())=='오류'
        assert display_state('SPECIAL2',sink.snapshot())=='연결 대기'
    finally:sink.close()


def test_telegram_getupdates_success_and_failure_drive_kim_state(tmp_path):
    import threading
    from event_host import HTTPServices
    from module_diagnostics import Diagnostics
    sink=Diagnostics(tmp_path)
    stop=threading.Event()
    service=HTTPServices({'TELEGRAM_TOKEN':'offline-test'},None,diagnostics=sink)
    class Response:
        status_code=200
        def json(self):return {'ok':True,'result':[]}
    class FakeHTTP:
        calls=0
        def get(self,*_args,**_kwargs):
            self.calls+=1
            if self.calls==2:
                assert display_state('KIM',sink.snapshot())=='연결중'
                stop.set()
            return Response()
    try:
        service.http=FakeHTTP()
        service.telegram_loop(None,stop)
        assert service.http.calls==2
        assert display_state('KIM',sink.snapshot())=='연결중'
        stop.clear()
        class FailingHTTP:
            def get(self,*_args,**_kwargs):
                stop.set();raise OSError('offline test')
        service.http=FailingHTTP()
        service.telegram_loop(None,stop)
        assert display_state('KIM',sink.snapshot())=='오류'
        stop.clear()
        class IncomingHTTP:
            calls=0
            def get(self,*_args,**_kwargs):
                self.calls+=1
                if self.calls==1:return Response()
                stop.set()
                return type('UpdateResponse',(),{'status_code':200,
                    'json':lambda self:{'ok':True,'result':[{'update_id':1}]}})()
        class BadInputs:
            def telegram_update(self,_update):raise ValueError('invalid command')
        service.http=IncomingHTTP()
        import logging
        logger=logging.getLogger()
        logger.addHandler(sink)
        try:service.telegram_loop(BadInputs(),stop)
        finally:logger.removeHandler(sink)
        assert sink.snapshot()['telegram_connected'] is True
        assert display_state('KIM',sink.snapshot())=='오류'  # Processing failure is visible even with a live connection.
    finally:sink.close()


def test_every_feed_needs_full_after_reconnect(tmp_path):
    s=snapshot()
    cache=load_staff().StaffPipeCache('',gap_journal=tmp_path/'gaps.jsonl')
    def feed(tf,seq,kind=wire.WIRE_FULL):
        return wire.pack_v2('XAUUSD+',tf,
            s.time if kind==wire.WIRE_FULL else s.time[-1:],
            s.volume if kind==wire.WIRE_FULL else s.volume[-1:],
            s.values if kind==wire.WIRE_FULL else s.values[-1:],seq=seq,kind=kind)
    cache.reconnect()
    cache.publish_frame(wire.pack_bundle('XAUUSD+',
        [feed('1m',1),feed('5m',1)],seq=1))
    cache.reconnect()
    cache.publish_frame(wire.pack_bundle('XAUUSD+',[feed('1m',2)],seq=2))
    with pytest.raises(wire.WireError,match='requires FULL after connect'):
        cache.publish_frame(wire.pack_bundle('XAUUSD+',
            [feed('5m',2,wire.WIRE_HEARTBEAT)],seq=3))
    cache.publish_frame(wire.pack_bundle('XAUUSD+',[feed('5m',2)],seq=4))
    cache.publish_frame(wire.pack_bundle('XAUUSD+',
        [feed('1m',3,wire.WIRE_HEARTBEAT),feed('5m',3,wire.WIRE_HEARTBEAT)],seq=5))
    assert cache.wire_diagnostics()['feeds'][('XAUUSD+','1m')]['accepted']==3
    assert cache.wire_diagnostics()['feeds'][('XAUUSD+','5m')]['accepted']==3


def test_ea_reconnect_bootstrap_is_per_feed():
    source=(ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8')
    assert 'g_wire_reconnected' not in source
    ensure=source.split('bool EnsureStaffPipe()',1)[1].split('bool WritePipeSnapshot',1)[0]
    flush=source.split('bool FlushStaffBundle()',1)[1].split('bool StaffHistoryStable',1)[0]
    assert 'g_feeds[i].wire_needs_full=true' in ensure
    assert 'g_feeds[g_pending_feeds[k]].wire_needs_full' in flush
    assert 'g_feeds[g_pending_feeds[k]].wire_needs_full=false' in flush


def test_ea_hello_build_hash_matches_changed_source():
    folder=ROOT/'Part1/program/MT5'
    digest=hashlib.sha256()
    for path in sorted(folder.iterdir()):
        if path.suffix not in ('.mq5','.mqh') or path.name=='STAFF_Wire_Schema.mqh':continue
        name=path.name.encode();raw=path.read_bytes()
        digest.update(len(name).to_bytes(4,'little')+name+len(raw).to_bytes(8,'little')+raw)
    header=(folder/'STAFF_Wire_Schema.mqh').read_text('utf-8')
    assert f'STAFF_EA_BUILD_HASH = "{digest.hexdigest()}"' in header
