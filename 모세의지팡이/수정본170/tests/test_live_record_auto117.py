"""LIVE alert records: the folder is made automatically inside the program's own folder.

Developing, that is the revision folder; released, the installed folder. Nobody picks a location.
LIVE_RECORD_ENABLED in config.txt turns recording on (default) or off.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import csv, datetime as dt, logging, sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from event_engine.model import Kind
from live_alert_recording import LiveAlertRecorder, FIELDS, RECORD_FOLDER, record_enabled
from manager_KIM import SignalOutput


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket

    def fail(*a, **k):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', fail)
    monkeypatch.setattr(socket.socket, 'sendto', fail)


def event(key='one', stamp='2026-09-29T14:59:59+00:00'):
    return NS(kind=Kind.SIGNAL, source_time=int(dt.datetime.fromisoformat(stamp).timestamp() * 1000), payload={
        'signal_id': key, 'strategy': 'SPECIAL1', 'symbol': 'XAUUSD+',
        'content': {'type': 'NOTIFICATION', 'message': '테스트 알림', 'recipients': ['TEST'],
                    'event': {'source_tf': '1m', 'direction': 'LONG', 'grade': 'A', 'b0_price': 3500., 'b0_time': 1750000000}}})


def program_in(folder):
    program = folder / 'Part1' / 'program'
    program.mkdir(parents=True)
    return program


def rows(path):
    with path.open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def transport(calls):
    def send(data):
        calls.append(data)
        return NS(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': len(calls)}})
    return send


def test_the_folder_is_made_inside_the_program_folder_and_nobody_picks_it(tmp_path):
    install = tmp_path / '수정본500'
    program = program_in(install)
    assert not (install / RECORD_FOLDER).exists()
    recorder = LiveAlertRecorder({}, program=program)
    assert (install / RECORD_FOLDER).is_dir(), 'created at start, with no folder chosen anywhere'
    recorder.record(event(), 'TEST', '전송됨')
    csv_path = install / RECORD_FOLDER / 'alerts' / '2026-09-29.csv'
    assert [row['delivery_result'] for row in rows(csv_path)] == ['전송됨']
    assert tuple(rows(csv_path)[0]) == FIELDS
    assert not (tmp_path / RECORD_FOLDER).exists(), 'not above the program folder'


def test_a_released_folder_keeps_its_records_when_it_is_moved_or_renamed(tmp_path):
    install = tmp_path / '모세'
    LiveAlertRecorder({}, program=program_in(install)).record(event(), 'TEST', '전송됨')
    moved = tmp_path / '다른 위치' / '모세 새이름'
    moved.parent.mkdir()
    install.rename(moved)
    LiveAlertRecorder({}, program=moved / 'Part1' / 'program').record(event('two'), 'TEST', '전송됨')
    files = list((moved / RECORD_FOLDER / 'alerts').glob('*.csv'))
    assert len(files) == 1 and len(rows(files[0])) == 2
    assert not any(str(tmp_path) in text for text in (path.read_text('utf-8') for path in files)), 'no absolute path is written'


@pytest.mark.parametrize('config', [{}, {'LIVE_RECORD_ENABLED': 'true'}, {'LIVE_RECORD_ENABLED': ' TRUE '},
                                    {'LIVE_RECORD_ENABLED': 'yes'}, {'LIVE_RECORD_ENABLED': ''}])
def test_recording_is_on_by_default_and_when_enabled(config):
    assert record_enabled(config) is True


@pytest.mark.parametrize('value', ['false', 'False', '0', 'no', 'off'])
def test_each_off_value_turns_recording_off(value):
    assert record_enabled({'LIVE_RECORD_ENABLED': value}) is False


def test_when_off_nothing_is_created_nothing_is_written_and_alerts_still_go_out(tmp_path, caplog):
    install = tmp_path / '수정본500'
    recorder = LiveAlertRecorder({'LIVE_RECORD_ENABLED': 'false'}, program=program_in(install))
    calls = []
    output = SignalOutput({}, transport=transport(calls), result_observer=recorder.record)
    with caplog.at_level(logging.WARNING):
        output.accept(event())
        output.accept(event('two'))
    assert len(calls) == 2
    assert not (install / RECORD_FOLDER).exists()
    assert len([text for text in caplog.messages if '사용 안 함' in text]) == 1


def test_a_folder_that_cannot_be_made_never_blocks_delivery(tmp_path, caplog):
    install = tmp_path / '수정본500'
    program = program_in(install)
    (install / RECORD_FOLDER).write_text('a file is in the way')
    calls = []
    with caplog.at_level(logging.WARNING):
        recorder = LiveAlertRecorder({}, program=program)
        output = SignalOutput({}, transport=transport(calls), result_observer=recorder.record)
        output.accept(event())
        output.accept(event('two'))
    assert len(calls) == 2
    assert len([text for text in caplog.messages if '설정을 읽지' in text]) == 1


def test_switching_it_on_later_starts_recording(tmp_path):
    install = tmp_path / '수정본500'
    program = program_in(install)
    LiveAlertRecorder({'LIVE_RECORD_ENABLED': 'false'}, program=program).record(event(), 'TEST', '전송됨')
    assert not (install / RECORD_FOLDER).exists()
    LiveAlertRecorder({'LIVE_RECORD_ENABLED': 'true'}, program=program).record(event('two'), 'TEST', '전송됨')
    assert len(rows(install / RECORD_FOLDER / 'alerts' / '2026-09-29.csv')) == 1


def test_the_daily_summary_uses_the_same_automatic_folder(tmp_path):
    install = tmp_path / '수정본500'
    config = {'LIVE_DAILY_SUMMARY_ENABLED': 'true', 'TELEGRAM_CHAT_ID': 'TEST'}
    recorder = LiveAlertRecorder(config, program=program_in(install))
    recorder.record(event(), 'TEST', '전송됨')
    notice = recorder.summary_due(dt.datetime(2026, 9, 30, 7, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))))
    assert '알림 신호 1건' in notice.payload['content']['message']
    recorder.mark_summary_sent(notice)
    assert (install / RECORD_FOLDER / 'daily_summary.json').is_file()


def test_the_shipped_config_turns_it_on():
    values = {}
    for line in (ROOT / 'Part1/program/config.txt').read_text('utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values.setdefault(key.strip(), value.strip())
    assert values['LIVE_RECORD_ENABLED'] == 'true'
