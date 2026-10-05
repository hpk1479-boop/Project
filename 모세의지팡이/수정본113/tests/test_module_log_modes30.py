"""The live viewer shows actual monitoring state without per-bundle trace noise."""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from module_diagnostics import Diagnostics,DiagnosticServer,read_snapshot


def test_core_and_detail_are_separate_and_all_view_is_ordered(tmp_path):
    sink=Diagnostics(tmp_path)
    class NeverFormat:
        def __str__(self):raise AssertionError('detail formatted while off')
    sink.log('STAFF',logging.INFO,'MT5 연결')
    sink.log('OZ',logging.INFO,'%s',NeverFormat(),detail=True)
    sink.log('OZ',logging.INFO,'OZ 감시 시작')
    rows=sink.snapshot('ALL',modules=('STAFF','OZ'))['lines']
    assert len(rows)==2 and '[STAFF]' in rows[0][1] and '[OZ]' in rows[1][1]
    sink.set_detail(True)
    sink.log('OZ',logging.INFO,'묶음마다 검사',detail=True)
    assert len(sink.snapshot('OZ')['lines'])==2
    sink.set_detail(False)
    assert len(sink.snapshot('OZ')['lines'])==1
    sink.close()


def test_monitoring_count_changes_only_and_trend_is_indicator(tmp_path):
    sink=Diagnostics(tmp_path)
    event=SimpleNamespace(kind=SimpleNamespace(value='MARKET_BUNDLE'),payload={'symbol':'XAUUSD+'},engine_seq=1)
    controller=SimpleNamespace(_watches={})
    state={'runtime':SimpleNamespace(controller=controller)}
    sink.begin('WATCH_CONDITIONS');sink.success('WATCH_CONDITIONS',event,state)
    assert sink.snapshot()['modules']['WATCH']['status']=='대기'
    before=len(sink.snapshot('WATCH')['lines'])
    sink.begin('WATCH_CONDITIONS');sink.success('WATCH_CONDITIONS',event,state)
    assert len(sink.snapshot('WATCH')['lines'])==before
    controller._watches['watch-1']=object()
    sink.begin('WATCH_CONDITIONS');sink.success('WATCH_CONDITIONS',event,state)
    assert sink.snapshot()['modules']['WATCH']['status']=='정상'
    assert 'WATCH 감시 1건' in sink.snapshot('WATCH')['lines'][-1][1]
    sink.begin('INDICATOR');sink.success('INDICATOR',event,{'watches':{'trend-1':object()}})
    assert 'INDICATOR 감시 1건' in sink.snapshot('INDICATOR')['lines'][-1][1]
    assert not any('TREND 감시' in line for _,line in sink.snapshot('INDICATOR')['lines'])
    sink.begin('OZ_STATE');sink.success('OZ_STATE',event,{'runtime':object()})
    sink.close()


def test_viewer_detail_switch_uses_local_diagnostic_pipe(tmp_path):
    sink=Diagnostics(tmp_path);server=DiagnosticServer(sink)
    try:
        assert not read_snapshot(tmp_path,'ALL',modules=('OZ',))['detail_enabled']
        assert read_snapshot(tmp_path,'OZ',detail=True)['detail_enabled']
        assert not read_snapshot(tmp_path,'OZ',detail=False)['detail_enabled']
    finally:server.close();sink.close()
