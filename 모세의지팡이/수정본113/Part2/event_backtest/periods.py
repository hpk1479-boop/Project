"""Requested periods and verified coverage, without guessing trading weekdays."""
import datetime as dt
from .catalog_plan import coverage
from .calendar import warm_start


def limit_end(scenario, today=None):
    today=today or dt.datetime.now(dt.timezone.utc).date()
    end=min(scenario['end'],today.isoformat())
    if scenario['start']>=end:
        raise ValueError('요청 기간에 완료된 날짜가 없습니다. 시작일을 확인하세요.')
    return end


def adjustment(scenario,start,end,reason):
    return {'requested_start':scenario['start'],'requested_end':scenario['end'],
            'start':start,'end':end,'reason':reason,
            'message':f"요청 {scenario['start']} ~ {scenario['end']} → 실제 {start} ~ {end} (UTC, 종료일 미포함)."}


def available_plan(scenario,catalog,prior,end):
    """Hash-check stored inputs, separate gaps, and retain complete warm-up only."""
    days=int(scenario['overlap_trading_days'])
    try:first=warm_start(scenario['start'],days,prior)
    except ValueError:first=scenario['start']
    valid=[];invalid=[]
    for row in prior:
        if row['end']<=first or row['start']>=end or not row.get('reconstruction_verified'):
            continue
        if not catalog.find_capture(row['capture_id']):invalid.append(row)
        else:valid.append(row)
    for row in invalid:
        _,absent=coverage(valid,max(first,row['start']),min(end,row['end']))
        if absent:raise ValueError('보유 데이터 무결성 오류: '+row['capture_id'])
    _,gaps=coverage(valid,scenario['start'],end)
    cursor=scenario['start'];ranges=[]
    for lo,hi in [*gaps,(end,end)]:
        if cursor<lo:ranges.append((cursor,lo))
        cursor=hi
    periods=[];excluded=[{'start':a,'end':b,'reason':'NOT_STORED',
                          'message':'보유 데이터 없음: 자동 구축하지 않습니다.'} for a,b in gaps]
    selected={}
    for lo,hi in ranges:
        observed=sorted({d for c in valid for d in c.get('observed_days',())
                         if lo<=d<hi and c['start']<=d<c['end']})
        if not observed:continue
        original_lo,original_hi=lo,hi;lo=max(lo,observed[0])
        observed_lo=lo
        if original_lo<lo:
            excluded.append({'start':original_lo,'end':lo,'reason':'NO_OBSERVATIONS',
                             'message':'보유 데이터에 시장 관측이 없는 날짜입니다.'})
        hi=min(hi,(dt.date.fromisoformat(observed[-1])+dt.timedelta(days=1)).isoformat())
        if hi<original_hi:
            excluded.append({'start':hi,'end':original_hi,'reason':'NO_OBSERVATIONS',
                             'message':'보유 데이터에 시장 관측이 없는 날짜입니다.'})
        try:
            warm=warm_start(lo,days,valid)
            _,absent=coverage(valid,warm,hi)
            if absent:raise ValueError('warm-up crosses an unrecorded gap')
        except ValueError:
            if len(observed)<=days:
                excluded.append({'start':original_lo,'end':hi,'reason':'WARMUP_MISSING',
                                 'message':'준비 거래일 부족: 이 구간은 실행하지 않습니다.'})
                continue
            lo=observed[days];warm=observed[0]
        if observed_lo<lo:
            excluded.append({'start':observed_lo,'end':lo,'reason':'WARMUP',
                             'message':'준비 데이터로 사용하며 결과 기간에서는 제외합니다.'})
        captures,absent=coverage(valid,warm,hi)
        if absent:raise ValueError('보유 데이터 구간 검증 중 누락이 확인됐습니다.')
        selected.update({c['capture_id']:c for c in captures})
        periods.append({'start':lo,'end':hi})
    if not periods:raise ValueError('요청 기간에 백테스트할 수 있는 검증된 보유 데이터가 없습니다. 데이터 구축을 먼저 실행하세요.')
    return list(selected.values()),periods,excluded


def apply_plan(scenario,plan):
    value=dict(scenario)
    if plan.get('period_adjustment'):
        value.update(start=plan['period_adjustment']['start'],end=plan['period_adjustment']['end'])
    if plan.get('available_periods'):value['_available_periods']=plan['available_periods']
    if plan.get('period_adjustment'):value['period_adjustment']=plan['period_adjustment']
    if plan.get('excluded_periods'):value['excluded_periods']=plan['excluded_periods']
    return value
