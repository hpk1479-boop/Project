"""Verified market-piece publication. Results never share these files."""
from pathlib import Path
import json
import shutil
import time
import uuid
from .delta import write_delta,source_bundles,read_delta,verify_delta
from .settings import file_hash
from .keyframes import write_indexed,verify_indexed

def bundles(root,*,start_ms=None,bootstrap=None,staff_validation=False):
    root=Path(root)
    if (root/'capture.delta2').is_file():
        from .keyframes import read_indexed
        yield from read_indexed(root/'capture.delta2',start_ms=start_ms,bootstrap=bootstrap,verify_crc=not staff_validation)
        return
    if (root/'capture.delta.gz').is_file():yield from read_delta(root/'capture.delta.gz',verify_crc=not staff_validation)
    else:
        # Canonical engine adapter enforces the current schema for MSP3 too.
        from event_engine.capture_io import capture_bundles
        yield from capture_bundles(root)

def _convert(source,parent,key,*,cancel=lambda:None,emit=lambda *a:None,start=None,end=None,destination=None):
    """Unique generation path keeps every previous piece valid until publication."""
    source=Path(source);dest=Path(destination) if destination is not None else Path(parent)/(key+'_'+uuid.uuid4().hex)
    dest.mkdir(parents=True,exist_ok=False);began=time.perf_counter()
    from event_host import load_staff
    def rows():
        iterator=bundles(source) if (source/'storage.json').exists() else source_bundles(source)
        for item in iterator:cancel();yield item
    clock=[0.];staff=load_staff()
    cache=staff.StaffPipeCache('',health_session='BACKTEST',monotonic=lambda:clock[0],gap_journal=dest/'seq_gaps.jsonl')
    raw_total=sum(p.stat().st_size for p in source.glob('pipe_*.bin'))
    from datetime import date
    calendar_days=max(1,(date.fromisoformat(end[:10])-date.fromisoformat(start[:10])).days) if start and end else None
    def conversion_progress(processed,days):
        emit('CONVERSION_PROGRESS',{'processed_bytes':processed,'total_bytes':raw_total or None,
            'processed_days':days,'total_days':calendar_days})
    index=write_indexed(rows(),dest/'capture.delta2',staff_cache=cache,receive_clock=clock,
        progress=conversion_progress)
    expected={k:index[k] for k in ('bundles','bundle_sha256')}
    converted=time.perf_counter()-began;t=time.perf_counter()
    emit('VERIFY_START',{'total_days':len(index['days'])})
    verify_indexed(dest/'capture.delta2',expected,cancel=cancel,
        progress=lambda done,total:emit('VERIFY_PROGRESS',{'verified_days':done,'total_days':total}))
    verification=time.perf_counter()-t
    (dest/'storage.json').write_text(json.dumps({'format':'MSD2',**expected,'keyframes':len(index['days']),
        'days':index['days']}),encoding='utf-8')
    # No absent original-file manifest: storage.json is the complete restore contract.
    (dest/'complete.txt').write_text('VERIFIED\n',encoding='ascii')
    raw=sum(p.stat().st_size for p in source.glob('pipe_*.bin*'))
    return dest,{'storage':'MSD2','reconstruction_verified':True,**expected,'keyframes':len(index['days']),
        'conversion_seconds':converted,'verification_seconds':verification,'raw_bytes':raw}

def files(root):return {p.name:file_hash(p) for p in Path(root).iterdir() if p.is_file()}


def convert(source,parent,key,*,cancel=lambda:None,**kwargs):
    parent=Path(parent).resolve();parent.mkdir(parents=True,exist_ok=True)
    staging=parent/('.partial_'+uuid.uuid4().hex)
    try:
        dest,metadata=_convert(source,parent,key,destination=staging,cancel=cancel,**kwargs)
        cancel()
        final=parent/(key+'_'+staging.name.removeprefix('.partial_'))
        dest.rename(final)
        return final,metadata
    finally:
        resolved=staging.resolve()
        if resolved.parent!=parent or not resolved.name.startswith('.partial_'):
            raise ValueError('unsafe capture staging path')
        if resolved.exists():shutil.rmtree(resolved)
