from pathlib import Path
from hashlib import sha256
import math
from .contracts import SourceProfile, PercentileError

SOURCE_HASHES = {
 'PRICE':'0ba9b7abece245e4f15cdb0a13f95112983c21b6dcf6dc445c7bc12aaf31381f',
 'RSI':'abc356a5ef87dcf4e097fc4030433d9b0f00544b09ceac88b0bf76bf5391e2d1',
 'STO':'a81a0d1877730b52b95a00b3182d4136c098f77f5bc5c5dfb59d3b2a6c90b579',
 'DI':'5bcf2dc131eec86fa32f2e1b18f6ff7fa9193f67967820b525d0b12925756787'}
PRICE_DEFAULTS = dict(InpDisplayMode=1,InpUseSmooth=True,InpDomCycle=20,InpLeveling=10.0,
 InpUseHighPrecision=False,InpShowHMA6=True,InpShowSignals=True,InpShowRegimeBand=True,
 InpWriteValidation=False,InpMa1Len=20,InpMa1Src=2,InpMa1Method=1,
 InpMa2Len=50,InpMa2Src=2,InpMa2Method=1,InpMa3Len=200,InpMa3Src=2,InpMa3Method=1)

def freeze_source_profile(family, params=None, seed_policy='SOURCE_DEFINED_ONLY', **metadata):
    family=family.upper()
    if family not in SOURCE_HASHES: raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',family)
    defaults = PRICE_DEFAULTS.copy() if family=='PRICE' else dict(UseHighPrecision=False,**{family+'Length':14},BandPeriod=20,Leveling=10.0,Vibration=10)
    if params:
        unknown=set(params)-set(defaults)
        if unknown: raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',str(sorted(unknown)))
        for name,value in params.items():
            original=defaults[name]
            if type(original) is bool and type(value) is not bool:
                raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',f'{name} requires bool')
            if type(original) is int and type(value) is not int:
                raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',f'{name} requires int')
            if type(original) is float:
                if type(value) not in (int,float) or not math.isfinite(value):
                    raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',f'{name} requires finite double')
                value=float(value)
            defaults[name]=value
    if seed_policy not in ('SOURCE_DEFINED_ONLY','NATIVE_STATE_CONDITIONED','NATIVE_RAW_DIAGNOSTIC'):
        raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE',seed_policy)
    if family=='PRICE':
        if defaults['InpDisplayMode'] not in (1,3):
            raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE','showMA requires independently validated native iMA dependency')
        if defaults['InpWriteValidation']:
            raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE','CSV side effects are outside provider')
        if defaults['InpDomCycle']!=20 or defaults['InpLeveling']!=10.0:
            raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE','only default numeric parameter profile is verified structurally')
    elif any(defaults[k]!=v for k,v in {family+'Length':14,'BandPeriod':20,'Leveling':10.0,'Vibration':10}.items()):
        raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE','nondefault oscillator numeric parameters')
    if family=='RSI':
        # Only RSI's new raw calculation ABI invalidates prior RSI bindings.
        metadata.setdefault('algorithm_version','PIT_PERCENTILE_RSI_SOURCE_V2')
    precision=defaults['InpUseHighPrecision' if family=='PRICE' else 'UseHighPrecision']
    return SourceProfile(family,SOURCE_HASHES[family],tuple(sorted(defaults.items())),
        source_file=f'program/MT5/{family}_of_Moses.mq5',precision_mode='exact' if precision else 'grid100',seed_policy=seed_policy,**metadata)

def verify_source_hashes(project_root):
    actual={}
    for family, expected in SOURCE_HASHES.items():
        path=Path(project_root)/'generic_backtest'/'reference_sources'/f'{family}_of_Moses.mq5'
        actual[family]=sha256(path.read_bytes()).hexdigest()
        if actual[family]!=expected: raise PercentileError('E_SOURCE_DRIFT',str(path))
    return actual

