from pathlib import Path
import ast
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generic_backtest.ui_presentation import RawUILog, diagnose_error

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_native_progress_is_visible_without_claiming_completion():
    import json
    from generic_backtest.ui_presentation import ProgressPresentation, _korean_log_summary
    payload={'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_EXPORT_PROGRESS',
             'elapsed_seconds':25,'output_feeds':19,'export_bytes':1048576}
    view=ProgressPresentation();view.apply('PROGRESS',payload)
    assert '25초' in view.count_text and '19개' in view.count_text
    _,text=_korean_log_summary('STDOUT',json.dumps({'event_type':'PROGRESS','payload':payload}))
    assert '25초' in text and '1.0 MB' in text
    assert '완료' not in text


def test_monitor_oz_no_longer_requires_requests():
    source = (PROJECT / 'Part1' / 'program' / 'monitor_OZ.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert 'requests' not in imported


def test_nested_worker_error_reports_deep_missing_module_in_korean():
    raw = (
        "E_IPC_PROTOCOL: worker exited: File \\\"monitor_OZ.py\\\", line 75, in <module>\n"
        "    import requests\n"
        "ModuleNotFoundError: No module named 'requests'\n"
    )
    diag = diagnose_error(raw, 'PRECHECK')
    message = diag.popup('PRECHECK')
    assert '오류 단계: 전략 준비' in message
    assert "Python 모듈 'requests'" in message
    assert '실행 프로세스와 통신' not in message


def test_strategy_tester_error_is_specific():
    diag = diagnose_error('NATIVE_EXPERT_EX5_REQUIRED: C:/x/THE_STAFF_OF_MOSES.ex5', 'MT5_STRATEGY_TESTER')
    message = diag.popup('MT5_STRATEGY_TESTER')
    assert 'MT5 Strategy Tester' in message
    assert 'THE_STAFF_OF_MOSES.ex5' in message
    assert '컴파일' in message


def test_korean_detail_log_keeps_raw_trace_out_of_operator_view(tmp_path):
    log = RawUILog(tmp_path / 'log')
    log.begin_phase('PLAN')
    log.record('STDOUT', '{"event_type":"HELLO","phase":"PRECHECK","payload":{"phase":"PRECHECK"}}')
    raw = "E_IPC_PROTOCOL: worker exited:\nModuleNotFoundError: No module named 'requests'"
    log.record('ERROR', raw, 'JOB')
    shown = log.korean_path.read_text(encoding='utf-8')
    forensic = log.path.read_text(encoding='utf-8')
    assert '오류 단계: 전략 준비' in shown
    assert "Python 모듈 'requests'" in shown
    assert 'worker exited' not in shown
    assert 'worker exited' in forensic
