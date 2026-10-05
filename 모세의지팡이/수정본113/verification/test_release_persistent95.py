"""Explicit continuing simple WATCH commands preserve their existing lifetime."""
import io
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from command_interpreter import CommandInterpreter
from domain_clock import event_scope
from kim_secretary import KimSecretary
import staff_schema as wire

BASE = 1790985600
CONFIG = {'SYMBOLS': 'XAUUSD+', 'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+',
          'TELEGRAM_CHAT_ID': 'TEST', 'WONBI_SIGMA': '3'}


def text(word):
    return ('골드 1분 상단 원비 터치 ' + word + ' 알려줘').replace('  ', ' ')


def parse(value):
    secretary = KimSecretary(CommandInterpreter(CONFIG), CONFIG)
    with event_scope((BASE - 121) * 1000, 'persistent95', {}):
        return secretary.parse(value, 'TEST')


@pytest.mark.parametrize('word,expected', [('', False), ('계속', True), ('지속', True), ('항상', True)])
def test_simple_watch_keeps_explicit_repeat_word_after_normalization(word, expected):
    result = parse(text(word))
    assert result.kind == 'WATCH'
    assert result.value['persistent'] is expected
    assert result.value['watch_type'] == 'WONBI_TOUCH'
    assert result.value['evaluation_mode'] == 'LIVE'
    assert result.value['timeframes'] == ['1m']
    assert result.value['level_side'] == 'HIGH'


@pytest.mark.parametrize('value', [
    '골드 1분 EMA50 > EMA200 계속 알려줘',
    '골드 계속 1분 EMA50 > EMA200 알려줘',
    '골드 1분 EMA50 > 알려줘',
    '골드 1분 EMA50 > EMA200; 알려줘',
])
def test_canonical_ma_invalid_or_new_repeat_grammar_stays_rejected(value):
    result = parse(value)
    assert result.kind == 'ERROR' and result.value is None


def test_canonical_ma_without_repeat_keeps_existing_single_shot_contract():
    result = parse('골드 1분 EMA50 > EMA200 알려줘')
    assert result.kind == 'WATCH'
    assert result.value['watch_type'] == 'MA_EXPRESSION'
    assert result.value['persistent'] is False


def capture_input(tmp_path, warm_touch):
    from event_engine.capture_io import FILE_HEADER, RECORD_HEADER
    folder = tmp_path / 'capture'
    folder.mkdir()
    samples = [(-120, -120, False), (-60, -60, warm_touch), (0, 0, False),
               (60, 60, True), (61, 60, True), (120, 120, False), (180, 180, True), (181, 180, True)]
    packets = []
    for seq, (observed, forming, hit) in enumerate(samples, 1):
        times = np.arange(650, dtype='<i8') * 60 + BASE + forming - 649 * 60
        values = np.full((650, len(wire.PIPE_VALUE_COLUMNS)), 100., dtype='<f8')
        values[:, :4] = [100., 101., 99., 100.]
        values[:, wire.PIPE_VALUE_COLUMNS.index('wonbi_upper')] = 103.
        values[:, wire.PIPE_VALUE_COLUMNS.index('wonbi_lower')] = 97.
        for index, column in enumerate(wire.PIPE_VALUE_COLUMNS):
            if column.endswith(('_lower_out', '_upper_out')): values[:, index] = np.nan
        if hit: values[-1, 1] = 104.
        raw = wire.pack_v2('XAUUSD+', '1m', times, np.ones(650, dtype='<i8'), values, seq=seq)
        packets.append(((BASE + observed) * 1000, raw))
    with (folder / 'feed.bin').open('wb') as handle:
        handle.write(FILE_HEADER.pack(0x4D535033, 2, len(wire.PIPE_VALUE_COLUMNS), 650))
        for observed, raw in packets:
            handle.write(RECORD_HEADER.pack(observed, 0, len(raw)))
            handle.write(raw)
    (folder / 'manifest.tsv').write_text('key\tvalue\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\n'
        'pipe_observation_unit\tmilliseconds\npipe_feed\t0\t1m\tfeed.bin\t8\n', encoding='ascii')
    (folder / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
    return folder, packets


@pytest.mark.parametrize('word,expected', [('', 1), ('계속', 2), ('지속', 2), ('항상', 2)])
@pytest.mark.parametrize('warm_touch', [False, True])
def test_parser_to_live_wire_and_capture_replay_obeys_repeat_and_warmup_lifetime(tmp_path, word, expected, warm_touch):
    from event_application import create_event_engine
    from event_engine import Kind
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_backtest.bridge import CaptureInputs
    from event_host import load_staff

    folder, packets = capture_input(tmp_path, warm_touch)
    staff = load_staff()
    outputs = []
    for replay in (False, True):
        engine = create_event_engine(CONFIG, symbols=('XAUUSD+',), selection=['WATCH'], backtest=replay)
        engine.ingress.post(Kind.COMMAND, source='command', source_seq=1, source_time=(BASE - 121) * 1000,
                            payload={'symbol': 'XAUUSD+', 'strategy': 'WATCH', 'chat_id': 'TEST', 'text': text(word)})
        engine.run()
        registered = engine.strategy_state['WATCH_CONDITIONS']['runtime'].controller._watches
        assert len(registered) == 1 and next(iter(registered.values())).persistent is bool(word)
        cache = staff.StaffPipeCache('', health_session='persistent95', monotonic=lambda: 0., gap_journal=tmp_path / 'unused.jsonl')
        if replay:
            for item in CaptureInputs(staff, cache, [folder], capture_start='beginning'):
                engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                                    source_time=item.source_time, payload=item.payload)
                engine.run()
        else:
            adapter = StaffIngressAdapter(cache, engine.ingress)
            for seq, (observed, child) in enumerate(packets, 1):
                raw = wire.pack_bundle('XAUUSD+', [child], seq=seq, sent_at_ms=observed)
                adapter.receive_one(io.BytesIO(raw).read, allowed_symbols=('XAUUSD+',))
                engine.run()
        assert not engine.error_log, engine.error_log
        notices = [(signal.source_time, signal.payload['signal_id'], signal.payload['content']['message'])
            for signal in engine.signals if signal.payload['content'].get('type') == 'NOTIFICATION'
            and '상단 원비' in signal.payload['content'].get('message', '')
            and '감시' not in signal.payload['content'].get('message', '')]
        observed = [item for item in notices if item[0] >= BASE * 1000]
        # A successful warmup alert consumes a single-shot watch. Continuing
        # watches survive and fire only on new false->true touch transitions.
        assert len(observed) == (0 if warm_touch and not word else expected)
        assert len(notices) == (1 if not word else 2 + int(warm_touch))
        assert len(engine.strategy_state['WATCH_CONDITIONS']['runtime'].controller._watches) == int(bool(word))
        outputs.append(notices)
    assert outputs[0] == outputs[1]
