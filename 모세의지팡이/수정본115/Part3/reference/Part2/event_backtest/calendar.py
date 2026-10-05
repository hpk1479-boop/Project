"""Actual observed trading days from the M1 MSP3 stream, not weekday guesses."""
from pathlib import Path
import datetime as dt
import gzip
import struct
from .settings import warehouse_path

def capture_calendar(directory):
    root=Path(directory);meta={};feed=None
    for line in (root/'manifest.tsv').read_text('ascii').splitlines()[1:]:
        parts=line.split('\t')
        if len(parts)==2:meta[parts[0]]=parts[1]
        if len(parts)==5 and parts[0]=='pipe_feed' and parts[2]=='1m':feed=parts
    if feed is None:raise ValueError('M1 녹화가 없어 거래일을 확인할 수 없습니다.')
    path=warehouse_path(root,feed[3]);opener=gzip.open if path.suffix=='.gz' else open
    scale=1 if meta.get('pipe_observation_unit')=='milliseconds' else 1000
    days={};first=None;last=None;count=0
    with opener(path,'rb') as f:
        header=f.read(16)
        if len(header)!=16 or struct.unpack('<IIII',header)[:2]!=(0x4D535033,2):raise ValueError('MSP3 required')
        while h:=f.read(16):
            if len(h)!=16:raise ValueError('truncated MSP3')
            observed,flags,length=struct.unpack('<qiI',h)
            if not 4<=length<=16*1024*1024:raise ValueError('MSP3 length')
            if len(f.read(length))!=length:raise ValueError('truncated MSP3 record')
            stamp=observed*scale
            if last is not None and stamp<=last:raise ValueError('MSP3 time order')
            if first is None:first=stamp
            last=stamp;count+=1
            day=dt.datetime.fromtimestamp(stamp/1000,dt.timezone.utc).date().isoformat()
            days[day]=days.get(day,0)+1
    if count!=int(feed[4]):raise ValueError('MSP3 count')
    return {'observed_days':sorted(days),'observations_by_day':days,'first_observation_ms':first,'last_observation_ms':last}

def warm_start(start,days,captures):
    if not days:return start
    available=sorted({day for c in captures for day in c.get('observed_days',[]) if day<start})
    if len(available)<days:raise ValueError(f'겹침에 필요한 실제 거래일 부족: {len(available)}/{days}. 이전 녹화 조각을 추가하세요.')
    return available[-days]
