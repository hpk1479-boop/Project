"""Coverage planning, retaining existing daily pieces after the calendar turns."""
import datetime as dt

def coverage(candidates,start,end):
    selected=[];missing=[];cursor=start
    candidates=[c for c in candidates if c['end']>start and c['start']<end]
    while cursor<end:
        active=[c for c in candidates if c['start']<=cursor<c['end']]
        if active:
            # A monthly piece supersedes contained daily alternatives. No replay
            # contains overlapping recordings, and the source files stay untouched.
            c=max(active,key=lambda x:(x['end'],x.get('recorded_at','')))
            if selected and c['start']<selected[-1]['end']:
                raise ValueError('겹치는 부분 녹화 조각: 시작·종료 경계를 정리해야 합니다.')
            selected.append(c);cursor=c['end']
        else:
            after=min([c['start'] for c in candidates if c['start']>cursor]+[end])
            missing.append((cursor,after));cursor=after
    return selected,missing

def missing_pieces(piece,candidates):
    selected,missing=coverage(candidates,piece['start'],piece['end'])
    if missing==[(piece['start'],piece['end'])]:return selected,[piece]
    result=[]
    # Never re-record existing daily data simply because its month just ended.
    for start,end in missing:
        cursor=dt.date.fromisoformat(start);limit=dt.date.fromisoformat(end)
        while cursor<limit:
            after=cursor+dt.timedelta(days=1)
            result.append({'start':cursor.isoformat(),'end':after.isoformat(),'unit':'DAY'})
            cursor=after
    return selected,result
