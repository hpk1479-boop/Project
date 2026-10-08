from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('Part3', 'Part2', 'Part1/program', 'verification')]
from lab import server, unified_backtest
from dashboard63_fixtures import CASES, create_case


def hashes(warehouse):
    return {path.relative_to(warehouse).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in warehouse.rglob('*') if path.is_file()}


@pytest.mark.parametrize('case', CASES)
def test_the_screen_is_read_from_the_trade_rows_without_file_writes(tmp_path, case):
    # 수정본165: the stored analytics.json (curves with every trade) is no longer sent; it stays an original file.
    job, result = create_case(tmp_path, case)
    before = hashes(tmp_path)
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        shown = unified_backtest.result(job['id'])
        assert 'analytics' not in shown and shown['analytics_status'] == 'ready'
        rows = {row['rr']: row for row in shown['analysis']['rr_table']}
        assert len(rows) == 9
        if case not in ('missing', 'sparse'):      # 'sparse' stores a made-up file; 'missing' stores none
            stored = json.loads((job['folder'] / 'analytics.json').read_text('utf-8'))['rr_results']
            for key, item in stored.items():
                for name, value in item['summary'].items():
                    assert rows[key]['summary'][name] == (pytest.approx(value) if isinstance(value, float) else value), name
        assert shown['trades_csv'] == result['virtual_entry']['trades_csv']
        assert shown['summary_csv'] == result['virtual_entry']['summary_csv']
        assert {file['kind'] for file in shown['files']} >= {'result', 'alerts', 'summary', 'trades'}
        assert all(not Path(file['path']).is_absolute() and '..' not in Path(file['path']).parts for file in shown['files'])
        for file in shown['files']:
            assert unified_backtest.download(job['id'], file['kind']).read_bytes() == (tmp_path / file['path']).read_bytes()
        page = unified_backtest.trades(job['id'], '2.0', limit=3)
        assert all(float(row['rr']) == 2.0 for row in page['rows'])
    assert hashes(tmp_path) == before


def test_rr_filtered_trade_paging_keeps_original_order(tmp_path):
    job, _ = create_case(tmp_path, 'normal')
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        a = unified_backtest.trades(job['id'], '1', 0, 5)
        b = unified_backtest.trades(job['id'], '1.0', a['next_offset'], 5)
        c = unified_backtest.trades(job['id'], '1.0', b['next_offset'], 5)
        assert [r['signal_id'] for r in a['rows'] + b['rows'] + c['rows']] == [str(i) for i in range(12)]
        assert c['next_offset'] is None
        for rr, offset, limit in [('NaN', 0, 10), ('1', -1, 10), ('1', 0, 201), ('abc', 0, 10)]:
            with pytest.raises(ValueError):
                unified_backtest.trades(job['id'], rr, offset, limit)


@pytest.mark.parametrize('content', ['{broken', '[]', '{"rr_results": null}'])
def test_a_damaged_analytics_file_no_longer_hides_the_screen(tmp_path, content):
    job, _ = create_case(tmp_path, 'normal')
    path = job['folder'] / 'analytics.json'; path.write_text(content, encoding='utf-8')
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        result = unified_backtest.result(job['id'])
        assert result['analytics_status'] == 'ready' and result['analysis']['rr_table']
        assert result['status'] == '완료'
        assert unified_backtest.download(job['id'], 'analytics').read_text('utf-8') == content


def test_a_damaged_trade_file_is_reported_and_kept(tmp_path):
    job, _ = create_case(tmp_path, 'normal')
    trades = job['folder'] / 'virtual_trades.csv'
    with trades.open('a', encoding='utf-8', newline='') as handle:
        handle.write('99,SPECIAL1,XAUUSD+,1m,0,LONG,1000,2000,1990,abc,WIN,60000,1\n')   # an RR that is not a number
    raw = trades.read_bytes()
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        result = unified_backtest.result(job['id'])
        assert result['analysis'] is None and result['analytics_status'] == 'unavailable'
        with pytest.raises(ValueError):
            unified_backtest.analysis(job['id'])
        assert unified_backtest.download(job['id'], 'trades').read_bytes() == raw


def test_absent_analytics_file_and_no_trade_csv(tmp_path):
    job, result = create_case(tmp_path, 'normal')
    (job['folder'] / 'analytics.json').unlink()
    result['virtual_entry'] = {}
    (job['folder'] / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        shown = unified_backtest.result(job['id'])
        assert shown['analytics_status'] == 'missing' and shown['analysis'] is None
        assert unified_backtest.trades(job['id'])['rows'] == []


def test_missing_alert_csv_does_not_hide_completed_analytics(tmp_path):
    job, _ = create_case(tmp_path, 'normal')
    (job['folder'] / 'alerts.csv').unlink()
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        shown = unified_backtest.result(job['id'])
        assert shown['analytics_status'] == 'ready'
        assert shown['alerts_preview'] == [] and shown['warnings']


@pytest.mark.parametrize('relative', ['../escape.csv', 'C:/outside.csv', '/outside.csv'])
def test_download_rejects_escaping_paths(tmp_path, relative):
    job, result = create_case(tmp_path, 'normal')
    result['analytics_path'] = relative
    (job['folder'] / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
        result = unified_backtest.result(job['id'])
        assert result['analytics_path'] is None
        with pytest.raises(ValueError): unified_backtest.download(job['id'], 'analytics')
        with pytest.raises(ValueError): unified_backtest.download(job['id'], '../secret')


def test_warehouse_move_preserves_read_and_download(tmp_path):
    original = tmp_path / 'original'; moved = tmp_path / 'moved'
    job, _ = create_case(original, 'normal')
    shutil.copytree(original, moved)
    new_job = {**job, 'warehouse': moved, 'folder': moved / 'runs' / job['id']}
    with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True): old = unified_backtest.result(job['id'])
    with patch.dict(unified_backtest.JOBS, {job['id']: new_job}, clear=True):
        assert unified_backtest.result(job['id']) == old
        assert unified_backtest.download(job['id'], 'trades').read_bytes() == (job['folder'] / 'virtual_trades.csv').read_bytes()


def test_http_result_trades_download_authentication_and_raw_bytes(tmp_path):
    job, _ = create_case(tmp_path, 'normal')
    host = server.LabServer(0)
    thread = threading.Thread(target=host.serve_forever, daemon=True); thread.start()
    def get(path, authorized=True):
        client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
        client.request('GET', path, headers={'X-Lab-Token': host.token if authorized else ''})
        response = client.getresponse(); code, data, headers = response.status, response.read(), dict(response.getheaders())
        client.close(); return code, data, headers
    try:
        with patch.dict(unified_backtest.JOBS, {job['id']: job}, clear=True):
            for endpoint in ('result', 'trades', 'download', 'analysis'):
                path = f'/api/mo/backtest/{endpoint}?id={job["id"]}&kind=trades'
                assert get(path, False)[0] == 403
                assert get(path)[0] == 200
            code, data, _ = get(f'/api/mo/backtest/analysis?id={job["id"]}&period=2026&rr=2')
            assert code == 200 and json.loads(data)['rr'] == '2.0' and json.loads(data)['period'] == '2026'
            assert get(f'/api/mo/backtest/analysis?id={job["id"]}&period=26')[0] == 400
            code, data, headers = get(f'/api/mo/backtest/download?id={job["id"]}&kind=trades')
            assert data == (job['folder'] / 'virtual_trades.csv').read_bytes()
            assert 'attachment' in headers['Content-Disposition']
            assert get(f'/api/mo/backtest/download?id={job["id"]}&kind=secret')[0] == 400
            assert get(f'/api/mo/backtest/result?id={"f" * 32}')[0] == 400
            assert get('/backtest_dashboard.js')[0] == 200
    finally:
        host.shutdown(); host.server_close(); thread.join(5)
