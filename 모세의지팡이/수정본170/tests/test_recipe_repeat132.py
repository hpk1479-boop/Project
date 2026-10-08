"""수정본132 1단계: 같은 묶음 안에서 반복되던 계산을 줄여도 값은 전체 계산과 같다.

- 이동평균 저장소의 봉 수정 확인: 같은 스냅샷이면 원소 비교를 건너뛰지만, 실제로 지난 봉이
  수정되면 지금처럼 다시 계산한다 (확정봉 수정, 진행봉 변화, 새 봉 포함).
- 이동평균 이름 해석: 같은 글자면 같은 답, 틀린 입력은 매번 같은 오류.
- 언어사전 경로: 절대 경로만 기억하고, 상대 경로는 작업 폴더를 따라간다.
"""
from pathlib import Path
import json
import shutil
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT)]

ROWS = 700
START = 1_790_000_000
NAMES = ('SMA20', 'WMA23', 'HMA18', 'EMA21', 'EMA37')   # EA가 보내지 않는 기간 = 저장소 계산


def _values(seed=132):
    from event_engine.market import COLUMNS
    from staff_schema import PIPE_VALUE_COLUMNS
    rng = np.random.default_rng(seed)
    close = 2000. + np.cumsum(rng.normal(0., 1., ROWS))
    values = np.full((ROWS, len(PIPE_VALUE_COLUMNS)), np.nan)
    values[:, COLUMNS['open']] = np.r_[close[0], close[:-1]]
    values[:, COLUMNS['close']] = close
    values[:, COLUMNS['high']] = np.maximum(values[:, COLUMNS['open']], close) + .5
    values[:, COLUMNS['low']] = np.minimum(values[:, COLUMNS['open']], close) - .5
    return values


def _snapshot(values, seq, shift=0):
    from event_engine import FeedSnapshot
    times = START + (np.arange(ROWS, dtype='<i8') + shift) * 60
    return FeedSnapshot(times, np.ones(ROWS, dtype='<i8'), values, seq, '132:1', {})


def _full(values, name):
    """전체 계산: 저장소 없이 마지막 창 전체로 공식을 그대로 계산 (EMA=종가, 나머지=시가)."""
    from event_engine.market import COLUMNS
    from indicator_facts import ma_array
    from watch_ma import feature_source_rows, parse_ma_name
    family, period = parse_ma_name(name)
    column = COLUMNS['close' if family == 'EMA' else 'open']
    # 저장소처럼 연속 배열로 계산 (열 보기 그대로면 가중합 반올림 경로가 달라짐)
    return ma_array(np.array(values[-feature_source_rows(name):, column]), family, period)


def _check(store, view, values, label):
    got = store.get('XAUUSD+', '1m', view, list(NAMES))
    for name in NAMES:
        assert got[name].tobytes() == _full(values, name).tobytes(), (label, name)
    return got


def test_shared_ma_store_matches_full_formula_through_repeats_corrections_and_new_bar():
    from event_engine.market import COLUMNS, MarketView
    from watch_array_facts import WatchMAStore
    store = WatchMAStore()
    base = _values()
    first = MarketView(_snapshot(base, 1))
    _check(store, first, base, 'first')
    # 같은 묶음 안 반복: 같은 보기, 같은 스냅샷의 다른 보기(원비 특징 포함)
    for _ in range(3):
        _check(store, first, base, 'same view')
    other = MarketView(first.snapshot, features={'open_band_4_mid': first.column('open_band_4_mid')})
    _check(store, other, base, 'same snapshot, other view')
    # 진행봉만 바뀜 (확정봉 그대로)
    forming = base.copy()
    forming[-1, COLUMNS['open']] += .25; forming[-1, COLUMNS['close']] += 1.5
    forming_view = MarketView(_snapshot(forming, 2))
    before = _check(store, forming_view, forming, 'forming')
    # 확정봉 수정 (시간·개수·epoch 같음 = close_key 같음): 반드시 다시 계산
    corrected = forming.copy()
    corrected[-3, COLUMNS['open']] += 7.; corrected[-30, COLUMNS['close']] -= 9.
    corrected_view = MarketView(_snapshot(corrected, 3))
    assert corrected_view.close_key() == forming_view.close_key()
    after = _check(store, corrected_view, corrected, 'corrected')
    assert all(not np.array_equal(before[n], after[n], equal_nan=True) for n in NAMES)
    _check(store, corrected_view, corrected, 'corrected repeat')
    # 새 봉 (한 봉 밀림)
    advanced = np.vstack([corrected[1:], corrected[-1:]])
    advanced[-1, COLUMNS['open']] = advanced[-2, COLUMNS['close']]
    _check(store, MarketView(_snapshot(advanced, 4, shift=1)), advanced, 'new bar')


def test_correction_reaches_an_ma_name_asked_after_another_one():
    # 수정본134 (외부 검토로 재현): 보정 묶음에서 SMA17만 먼저 읽고 WMA23을 따로 읽으면
    # WMA23이 보정 전 값으로 남았다. 어느 이름을 어떤 순서로 읽어도 전체 계산과 같아야 한다.
    from event_engine.market import COLUMNS, MarketView
    from watch_array_facts import WatchMAStore
    rng = np.random.default_rng(32)
    values = _values()
    opens = 2000. + np.cumsum(rng.normal(0., 1., ROWS))
    values[:, COLUMNS['open']] = opens; values[:, COLUMNS['close']] = opens + .25
    values[:, COLUMNS['high']] = opens + 2; values[:, COLUMNS['low']] = opens - 2
    corrected = values.copy(); corrected[-3, COLUMNS['open']] += 7; corrected[-3, COLUMNS['high']] += 7
    for same_view in (True, False):
        store = WatchMAStore()
        store.get('XAUUSD+', '1m', MarketView(_snapshot(values, 1)), ['SMA17', 'WMA23'])
        view = MarketView(_snapshot(corrected, 2))
        store.get('XAUUSD+', '1m', view, ['SMA17'])
        later = view if same_view else MarketView(view.snapshot)
        for name in ('WMA23', 'SMA17', 'HMA18', 'EMA21'):
            got = store.get('XAUUSD+', '1m', later, [name])[name]
            assert got.tobytes() == _full(corrected, name).tobytes(), (same_view, name)


def test_same_snapshot_skip_is_only_taken_for_identical_bytes():
    from event_engine.market import COLUMNS, MarketView
    from watch_array_facts import _same_bits
    base = _values()
    one = MarketView(_snapshot(base, 1))
    again = MarketView(one.snapshot)
    copy = MarketView(_snapshot(base.copy(), 1))
    changed = base.copy(); changed[5, COLUMNS['close']] += 1.
    moved = MarketView(_snapshot(changed, 1))
    close = lambda view: view.column('close')[:-1]
    assert _same_bits(close(one), close(again))          # 같은 바이트: 원소 비교 없이 같음
    assert _same_bits(close(one), close(copy))           # 다른 바이트, 같은 값: 원소 비교로 같음
    assert not _same_bits(close(one), close(moved))      # 값이 다르면 다름


def test_ma_name_answers_and_errors_are_unchanged():
    from watch_ma import WatchMAError, canonical_ma, parse_ma_name
    for _ in range(2):                                   # 두 번째는 기억된 답
        assert parse_ma_name('sma20') == ('SMA', 20)
        assert parse_ma_name('HMA17') == ('HMA', 17)
        assert canonical_ma('ema21') == 'EMA21'
        for bad in ('SMA0', 'SMA 20', 'XMA20', '', 'SMA2.5', 'EMA-3'):
            with pytest.raises(WatchMAError):
                parse_ma_name(bad)
        for bad in (20, None, ['SMA20'], {'SMA20': 1}):  # 글자가 아니면 (해시 불가 포함) 같은 오류
            with pytest.raises(WatchMAError):
                parse_ma_name(bad)


def test_relative_dictionary_path_follows_the_working_folder(tmp_path, monkeypatch):
    import moses_language as language
    raw = json.loads(language.language_path().read_text('utf-8'))
    name = next(iter(raw['syntax_literals']))
    for folder, text in (('a', 'A문법'), ('b', 'B문법')):
        (tmp_path / folder).mkdir()
        raw['syntax_literals'][name] = text
        (tmp_path / folder / 'language.json').write_text(json.dumps(raw, ensure_ascii=False), 'utf-8')
    try:
        monkeypatch.chdir(tmp_path / 'a')
        assert language.syntax_literal(name, 'language.json') == 'A문법'
        monkeypatch.chdir(tmp_path / 'b')
        assert language.syntax_literal(name, 'language.json') == 'B문법'
    finally:
        language.cache_clear()


def test_absolute_dictionary_path_keeps_one_dictionary_until_cache_clear(tmp_path):
    import moses_language as language
    target = tmp_path / 'language.json'
    shutil.copyfile(language.language_path(), target)
    try:
        first = language.load_dictionary(target)
        assert language.load_dictionary(target) is first
        assert language.load_dictionary(str(target)) is first
        language.cache_clear()
        assert language.load_dictionary(target) is not first
        assert language.load_dictionary(target) == first
    finally:
        language.cache_clear()
