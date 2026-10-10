"""WATCH MA syntax, unchanged formulas, exact arrays, boundaries and cache tests.

Moved from Part1/watch_ma_validation in 수정본180. The recorded-hash comparison went to
비교측정시험/test_ma_baseline_hashes.py.
"""
from pathlib import Path
import math
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"Part1/program"))
import watch_ma as ma
import indicator_facts as math_source  # 이평 계산식의 정식 위치 (구 strategy_TREND)


def sample_frame(size=600):
    x = np.arange(size, dtype='float64')
    return pd.DataFrame({'open': 100. + np.sin(x * .37) * 9. + x * .013,
                         'close': 99. + np.cos(x * .23) * 6. + x * .017})


def functions():
    return {family: getattr(math_source, family.lower()) for family in ('SMA','WMA','EMA','HMA')}


@pytest.mark.parametrize('name', ['SMA5','SMA20','SMA200','WMA9','WMA30','EMA12','EMA50','EMA200',
                                 'HMA12','HMA17','HMA55','SMA17','WMA23','EMA37','HMA150','EMA300','SMA1000'])
def test_dynamic_name(name):
    family, period = ma.parse_ma_name(name)
    assert period > 0 and ma.canonical_ma(name.lower()) == name
    assert family in functions()


BAD_NAMES = ['SMA','SMA0','SMA-20','SMA_20','SMA20.5','SMAABC','SMMA20','HMA 17',
             'HMAABC','HMA0','EMA01','200EMA','WMA+2','SMA２０']
@pytest.mark.parametrize('name', BAD_NAMES)
def test_invalid_name_exact_error(name):
    with pytest.raises(ma.WatchMAError, match='WATCH MA') as result:
        ma.parse_ma_name(name)
    assert name in str(result.value)


BAD_EXPRESSIONS = [
    '기울기()', '기울기(SMA)', '기울기(HMA0)', '기울기(HMA17, 3)', '기울기(SMA20,EMA50)',
    '골든크로스(SMA20)', '골든크로스(SMA20,EMA50,SMA200)', '골든크로스(SMA20,ABC50)',
    '데드크로스()', '데드크로스(SMA20)', '데드크로스(SMA20,EMA50,SMA200)', '데드크로스(SMA20,ABC50)',
    '정배열()', '정배열(SMA20)', '정배열(SMA20,ABC50)',
    '역배열()', '역배열(SMA20)', '역배열(HMAABC,EMA50)',
    '기울기(골든크로스(SMA20,EMA50)) > 0', '정배열(기울기(SMA20),EMA50)',
    '골든크로스(기울기(HMA17),EMA50)', '기울기(SMA20)', 'SMA20 > 1e999',
    'SMA20 > 2 > 1', '정배열(SMA20,,EMA50)', '정배열(SMA20,EMA50) AND',
    '기울기(SMA20[1]) > 0', 'CROSS(SMA20,EMA50)', '(SMA20 > 0)',
    '정배열(SMA20,EMA50))', '정배열(SMA20,EMA50', '1 > 0', 'SMA20 > NaN',
] + BAD_NAMES
@pytest.mark.parametrize('text', BAD_EXPRESSIONS)
def test_invalid_expression(text):
    with pytest.raises(ma.WatchMAError) as result:
        ma.parse_ma_expression(text)
    assert text in str(result.value)


def test_shared_identity_and_dependencies():
    expr = ma.parse_ma_expression('정배열(hma17, ema50, SMA200) AND 기울기(HMA17)>0 AND 골든크로스(HMA17,EMA50)')
    assert expr.dependencies == {'EMA50':1,'HMA17':2,'SMA200':0}
    assert expr.default_evaluation_mode == 'CLOSE'
    assert expr.history_rows('CLOSE') == 201
    assert ma.parse_ma_expression(expr.canonical).canonical == expr.canonical
    assert ma.parse_ma_expression('SMA20 > 0').dependencies != ma.parse_ma_expression('SMA21 > 0').dependencies


@pytest.mark.parametrize('family', ['SMA','WMA','EMA','HMA'])
@pytest.mark.parametrize('period', [1,2,3,17,23,37,150,200,300,1000])
def test_warmup_contract(family, period):
    name = f'{family}{period}'
    needed = period + max(1, int(math.sqrt(period))) - 1 if family == 'HMA' else period
    assert ma.ma_history(name) == needed
    lag = 2 if family == 'HMA' else 1
    expr = ma.parse_ma_expression(f'기울기({name}) > 0')
    assert expr.history_rows('LIVE') == needed + lag
    assert expr.history_rows('CLOSE') == needed + lag + 1
    values = functions()[family](pd.Series(np.arange(needed + lag + 3, dtype=float)), period)
    assert values.first_valid_index() == needed - 1
    assert expr.evaluate({name: values}, needed - 1) is None
    assert expr.evaluate({name: values}, needed + lag - 1) is True


@pytest.mark.parametrize('family', ['SMA','WMA','EMA','HMA'])
@pytest.mark.parametrize('direction', [-1,0,1])
def test_slope_fixed_lag_and_sign(family, direction):
    name = family + '20'
    lag = 2 if family == 'HMA' else 1
    # HMA's immediately preceding row deliberately disagrees with n-2.
    values = [10., 1000. if family == 'HMA' else 10., 10. + direction]
    if lag == 1:
        values[-2] = 10.
    for op, expected in [('>',direction > 0),('<',direction < 0),('==',direction == 0)]:
        expr = ma.parse_ma_expression(f'기울기({name}) {op} 0')
        assert expr.evaluate({name: values}, 2) is expected
    assert ma.parse_ma_expression(f'기울기({name}) != 0').evaluate({name:[np.nan,1.]},-1) is None
    assert ma.parse_ma_expression(f'기울기({name}) > 0').evaluate({name:[10.]},0) is None


@pytest.mark.parametrize('label,values,expected', [
    ('정배열',[120,110,100],True),('정배열',[120,120,100],False),('정배열',[100,110,120],False),
    ('역배열',[100,110,120],True),('역배열',[100,100,120],False),('역배열',[120,110,100],False),
])
def test_strict_mixed_alignment(label,values,expected):
    expr = ma.parse_ma_expression(f'{label}(HMA17,EMA50,SMA200)')
    assert expr.evaluate({name:[value] for name,value in zip(('HMA17','EMA50','SMA200'),values)},-1) is expected


@pytest.mark.parametrize('label', ['정배열','역배열','골든크로스','데드크로스'])
def test_same_ma_no_special_case(label):
    assert ma.parse_ma_expression(f'{label}(SMA20,SMA20)').evaluate({'SMA20':[1.,2.]},-1) is False


@pytest.mark.parametrize('label,before,current,expected',[
    ('골든크로스',99,101,True),('골든크로스',100,101,True),('골든크로스',101,102,False),
    ('골든크로스',99,100,False),('데드크로스',101,99,True),('데드크로스',100,99,True),
    ('데드크로스',99,98,False),('데드크로스',101,100,False),
])
def test_cross_boundaries(label,before,current,expected):
    expr=ma.parse_ma_expression(f'{label}(SMA20,EMA50)')
    assert expr.evaluate({'SMA20':[before,current],'EMA50':[100.,100.]},1) is expected
    assert expr.evaluate({'SMA20':[before,current],'EMA50':[np.nan,100.]},1) is None
    assert expr.evaluate({'SMA20':[current],'EMA50':[100.]},0) is None


def test_boolean_composition_and_numeric_comparison():
    expr=ma.parse_ma_expression('정배열(SMA20,EMA50) AND 기울기(HMA17) > 0 OR SMA20 == EMA50')
    assert expr.evaluate({'SMA20':[5,5,5],'EMA50':[4,4,4],'HMA17':[1,20,2]},-1) is True
    assert expr.evaluate({'SMA20':[5,5,5],'EMA50':[5,5,5]},-1) is True
    assert expr.evaluate({'SMA20':[5,5,5],'EMA50':[4,4,4]},-1) is None
    assert ma.parse_ma_expression('SMA20 != EMA50').evaluate({'SMA20':[np.nan],'EMA50':[1]},-1) is None


@pytest.mark.parametrize('name', ['SMA17','WMA23','EMA37','HMA17','SMA20','WMA17','EMA20','EMA50','HMA12','HMA150','EMA300'])
def test_general_functions_exact_full_arrays(name):
    frame=sample_frame()
    family,period=ma.parse_ma_name(name)
    source='close' if family=='EMA' else 'open'
    expected=functions()[family](frame[source],period)
    actual=ma.MAFeatureCache(functions()).calculate(frame,[name])[name]
    np.testing.assert_array_equal(actual.to_numpy().view(np.uint64),expected.to_numpy().view(np.uint64))
    assert actual.first_valid_index()==expected.first_valid_index()
    assert actual.dtype==expected.dtype


@pytest.mark.parametrize('family', ['SMA','WMA','EMA','HMA'])
def test_nan_positions_unchanged(family):
    frame=sample_frame();frame.loc[[0,2,100,210,211],:]=np.nan
    source='close' if family=='EMA' else 'open'
    expected=functions()[family](frame[source],17)
    actual=ma.MAFeatureCache(functions()).calculate(frame,[family+'17'])[family+'17']
    np.testing.assert_array_equal(actual.to_numpy().view(np.uint64),expected.to_numpy().view(np.uint64))


def test_cache_reuses_one_canonical_feature_and_invalidates_edits():
    counts={family:0 for family in functions()}
    def wrap(family,fn):
        def compute(*args):
            counts[family]+=1
            return fn(*args)
        return compute
    cache=ma.MAFeatureCache({family:wrap(family,fn) for family,fn in functions().items()})
    frame=sample_frame()
    first=cache.calculate(frame,['hma17','HMA17','EMA50'])
    again=cache.calculate(frame,['HMA17','EMA50'])
    assert counts=={'SMA':0,'WMA':0,'EMA':1,'HMA':1}
    np.testing.assert_array_equal(first['HMA17'],again['HMA17'])
    cache.calculate(frame,['HMA18','EMA200'])
    assert counts['HMA']==2 and counts['EMA']==2
    frame.loc[len(frame)-1,'open']+=1
    changed=cache.calculate(frame,['HMA17'])
    assert counts['HMA']==3 and changed['HMA17'].iloc[-1]!=first['HMA17'].iloc[-1]


@pytest.mark.parametrize('name,column', [('EMA20','ema_20'),('EMA50','ema_50'),('HMA17','hma_17'),('HMA50','hma_50')])
def test_source_columns_and_nan_are_authoritative(name,column):
    frame=sample_frame(100)
    frame[column]=np.linspace(900,999,100)
    frame.loc[[0,20,99],column]=np.nan
    def forbidden(*a):pytest.fail('Source MA was silently replaced')
    cache=ma.MAFeatureCache({family:forbidden for family in functions()})
    actual=cache.calculate(frame,[name])[name]
    np.testing.assert_array_equal(actual.to_numpy().view(np.uint64),frame[column].to_numpy().view(np.uint64))


def test_ema_source_window_independent_of_other_period_requests():
    frame=sample_frame(1400)
    cache=ma.MAFeatureCache(functions())
    before=cache.calculate(frame,['EMA37'])['EMA37']
    after=cache.calculate(frame,['HMA1000','EMA37'])['EMA37']
    np.testing.assert_array_equal(before.to_numpy().view(np.uint64),after.to_numpy().view(np.uint64))
    expected=math_source.ema(frame.close.tail(ma.feature_source_rows('EMA37')),37)
    np.testing.assert_array_equal(after.tail(len(expected)),expected)


def test_unavailable_large_period_does_not_allocate_period_sized_weights():
    result=ma.MAFeatureCache(functions()).calculate(sample_frame(5),['WMA1000000000'])
    assert result['WMA1000000000'].isna().all()
