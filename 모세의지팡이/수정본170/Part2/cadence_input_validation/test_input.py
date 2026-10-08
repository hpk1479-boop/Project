"""Exact raw input contract, current-index decoding and cadence regressions."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from generic_backtest.history import cache
from generic_backtest.history.cache import GenericArchiveReader
from generic_backtest.canonical import identity, plain, write_json, file_hash
from generic_backtest.contracts import InstrumentSpec
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.market import GenericMarketCore
from generic_backtest.live_parity import timeline
from generic_backtest.evaluation import TimeframeCloseGate, close_evaluation_view, evaluation_base_tf
from pit.archive.reader import TICK_DTYPE
from pit.models import RAW, TickRecord
from cadence_input_validation.reference_reader import LegacyArchiveReader

CAL = {'kind': 'UTC_GRID_RESEARCH_V1', 'explicit_research_choice': True}
INSTRUMENT = plain(InstrumentSpec('XAUUSD+', 'SYNTHETIC_INPUT_CONTRACT_ONLY'))


def native_rows(milliseconds):
    ms = np.asarray(milliseconds, dtype='<i8')
    a = np.zeros(len(ms), dtype=TICK_DTYPE)
    a['time_msc'] = ms; a['time'] = ms//1000
    a['bid'] = 100+np.arange(len(a), dtype='float64')*.25
    a['ask'] = a['bid']+.1; a['last'] = a['bid']
    a['volume'] = 1; a['volume_real'] = 1.; a['flags'] = 2
    return a


def archive_from_parts(root, parts, bounds, stream='GLOBAL_VALIDATION_STREAM'):
    """Explicitly synthetic fixtures; descriptors preserve their native raw bytes."""
    root = Path(root); (root/'chunks').mkdir(parents=True)
    descriptors = []; digest = hashlib.sha256()
    for i, rows in enumerate(parts):
        path = root/'chunks'/f'{i:08d}.npy'
        np.save(path, rows, allow_pickle=False)
        raw = rows.tobytes(); digest.update(raw)
        descriptors.append({'file': 'chunks/'+path.name, 'count': len(rows),
            'file_sha256': file_hash(path), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
            'coverage_start_ns': bounds[i], 'coverage_end_ns': bounds[i+1],
            'stream_namespace': 'CHUNK_LOCAL_'+str(i)})
    m = {'kind': 'GENERIC_RAW_ARCHIVE_V1', 'status': 'READY', 'instrument': INSTRUMENT,
         'stream_namespace': stream, 'coverage_start_ns': bounds[0], 'coverage_end_ns': bounds[-1],
         'count': sum(map(len, parts)), 'raw_sha256': digest.hexdigest(), 'chunks': descriptors,
         'gaps': [], 'coverage': {'completeness': 'UNVERIFIED', 'broker_history_completeness': 'NOT_APPLICABLE_SYNTHETIC'},
         'input_kind': 'SYNTHETIC_VALIDATION_ONLY', 'source_build': ['CURRENT_TEST_FIXTURE']}
    m['archive_identity'] = identity(m); write_json(root/'manifest.json', m)
    return root, m


def signature(tick):
    return (type(tick), tuple(tick.__dict__.items()), tick.to_bytes(), tick.tick_id)


@pytest.mark.parametrize('partitioned', [False, True])
def test_exact_bits_duplicates_empty_chunks_and_global_ids(tmp_path, partitioned):
    rows = native_rows([100,100,100,101,101,102])
    # Float fields must not be converted through float arrays/JSON. These include
    # distinct quiet/signalling NaN payloads, -0, +inf and the largest uint64.
    bits = rows['bid'].view('<u8')
    bits[:] = [0x8000000000000000, 0x7ff8000000000042, 0x7ff0000000000001,
               0x7ff0000000000000, 0x3ff0000000000000, 0x0000000000000000]
    rows['volume'][-1] = 2**64-1
    rows['ask'].view('<u8')[2] = 0xfff8000000000007
    rows['last'].view('<u8')[3] = 0xfff0000000000000
    rows['volume_real'].view('<u8')[4] = 0x8000000000000000
    empty = rows[:0]
    parts = [empty, rows[:3], rows[3:], empty] if partitioned else [empty, rows, empty]
    bounds = [0,100_000_000,101_000_000,103_000_000,104_000_000] if partitioned else [0,100_000_000,103_000_000,104_000_000]
    root, _ = archive_from_parts(tmp_path/'raw', parts, bounds)
    old = list(LegacyArchiveReader(root)); new = list(GenericArchiveReader(root))
    assert [signature(t) for t in new] == [signature(t) for t in old]
    assert b''.join(t.to_bytes() for t in new) == rows.tobytes()
    assert [t.source_ordinal for t in new] == list(range(1,7))
    assert all(t.stream_id == 'GLOBAL_VALIDATION_STREAM' for t in new)
    assert all(type(v) is int for t in new for k,v in t.__dict__.items() if k!='stream_id')
    with pytest.raises(FrozenInstanceError): new[0].time_msc = 999
    assert replace(new[0], source_ordinal=9).to_bytes() == new[0].to_bytes()


def test_millisecond_precision_above_float_exact_integer_limit(tmp_path):
    ms = 2**53+1
    rows = native_rows([ms,ms,ms+1,ms+2])
    root, _ = archive_from_parts(tmp_path/'raw', [rows], [ms*1_000_000,(ms+3)*1_000_000])
    old = list(LegacyArchiveReader(root)); new = list(GenericArchiveReader(root))
    assert [signature(t) for t in old] == [signature(t) for t in new]
    assert [t.time_msc for t in new] == [ms,ms,ms+1,ms+2]


def unchecked_reader(cls, root, manifest):
    # Isolate per-observation validation from the unchanged full-archive preflight.
    obj = cls.__new__(cls); obj.root = root; obj.manifest = manifest
    return obj


@pytest.mark.parametrize('milliseconds,bounds', [
    ([100,99], [0,200_000_000]), ([100,200], [0,200_000_000]),
    ([-2,100], [0,200_000_000]), ([100,99], [100_000_000,101_000_000]),
])
def test_error_code_ordinal_and_precedence_identical(tmp_path, milliseconds, bounds):
    root, m = archive_from_parts(tmp_path/'raw', [native_rows(milliseconds)], bounds)
    def outcome(cls):
        prefix=[]
        try:
            for t in unchecked_reader(cls,root,m):prefix.append(signature(t))
        except Exception as e:return prefix,type(e),getattr(e,'code',None),str(e)
        raise AssertionError('invalid fixture unexpectedly accepted')
    assert outcome(GenericArchiveReader) == outcome(LegacyArchiveReader)
    with pytest.raises(Exception) as a: GenericArchiveReader(root)
    with pytest.raises(type(a.value)) as b: LegacyArchiveReader(root)
    assert str(a.value) == str(b.value)


def test_only_current_record_decoded_and_no_row_buffer_exposed(monkeypatch, tmp_path):
    root, m = archive_from_parts(tmp_path/'raw', [native_rows([100,100,101,102])], [0,103_000_000])
    decoded=[]
    class TracedRaw:
        @staticmethod
        def iter_unpack(buffer):
            for fields in RAW.iter_unpack(buffer):
                decoded.append(fields[5])
                yield fields
    monkeypatch.setattr(cache, 'RAW', TracedRaw)
    iterator = iter(GenericArchiveReader(root))
    assert decoded == []
    first = next(iterator)
    assert decoded == [100]
    assert all(isinstance(value,(str,int)) for value in first.__dict__.values())
    second = next(iterator)
    assert decoded == [100,100]
    assert first.source_ordinal == 1 and second.source_ordinal == 2
    assert first.bid_bits != second.bid_bits
    iterator.close()
    assert decoded == [100,100]  # Neither remaining record was decoded/published.


def test_no_from_bytes_or_chunk_tick_replacement_in_new_iterator(monkeypatch,tmp_path):
    root, _ = archive_from_parts(tmp_path/'raw', [native_rows([0,0,1])], [0,2_000_000])
    def forbidden(*a,**k):raise AssertionError('row-object conversion path reached')
    monkeypatch.setattr(TickRecord,'from_bytes',forbidden)
    assert len(list(GenericArchiveReader(root))) == 3


def test_live_parity_same_timestamp_raw_publish_poll_order(tmp_path):
    rows = native_rows([0,500,500,1000,1000,1501,2000])
    root,_ = archive_from_parts(tmp_path/'raw',[rows],[0,2500_000_000])
    def events(cls):
        return [(kind,now,signature(t) if t else None) for kind,now,t in timeline(cls(root),0,2500_000_000)]
    expected=events(LegacyArchiveReader);assert events(GenericArchiveReader)==expected
    assert [kind for kind,now,_ in expected if now==1000_000_000] == ['RAW','RAW','PUBLISH','EVALUATE']


def test_1h_5m_close_observes_progressing_hour_and_not_future_spike():
    inst=InstrumentSpec('XAUUSD+','SYNTHETIC_CADENCE_ONLY');calendar=CalendarRegistry(CAL)
    start=9*3600
    core=GenericMarketCore(inst,calendar,start*10**9,{'1h':10,'5m':20},'CADENCE')
    assert evaluation_base_tf(('1h','5m')) == '5m'
    gate=TimeframeCloseGate(inst,calendar,'5m');observed={}
    for minute in range(61):
        price=80. if minute==32 else 90000. if minute==36 else 100.+minute
        bits=int(np.float64(price).view(np.uint64))
        sec=start+minute*60
        tick=TickRecord('CADENCE',minute+1,sec,bits,bits,bits,1,sec*1000,2,bits)
        view,_=core.step(tick);close=gate.on_tick(tick)
        if close is not None:observed[minute]=close_evaluation_view(view,'5m',close)
    assert list(observed)==list(range(5,61,5))
    for minute,view in observed.items():
        assert view.token.now_ns==(start+minute*60)*10**9
        assert view.bars('5m')[-1].state=='COMPLETED'
        if minute<60:
            hour=view.bars('1h')[-1]
            assert hour.state=='FORMING' and hour.last_ordinal==minute+1
            assert hour.open_ns==start*10**9
    assert observed[30].bars('1h')[-1].low==100.
    assert observed[35].bars('1h')[-1].low==80.
    assert observed[35].bars('1h')[-1].high==135.
    assert observed[40].bars('1h')[-1].high==90000.
    assert observed[35].token.now_ns < (start+3600)*10**9


