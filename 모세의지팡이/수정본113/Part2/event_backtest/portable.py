"""Portable metadata validation; machine runtime paths never enter the catalog."""
import re

def validate(value):
    if isinstance(value,dict):
        for key,item in value.items():
            validate(str(key))
            if key in ('symbol','tester_symbol'):
                from .settings import SYMBOL_FORM
                if not isinstance(item,str) or not SYMBOL_FORM.fullmatch(item):
                    raise ValueError('논리 종목 ID 형식이 올바르지 않습니다.')
            else:validate(item)
    elif isinstance(value,(list,tuple)):
        for item in value:validate(item)
    elif isinstance(value,str):
        if re.search(r'(?i)(?:[a-z]:[\\/]|\\\\[^.]|(?:^|[\\/])\.\.(?:[\\/]|$))',value) or value.startswith('/'):
            raise ValueError('창고 메타데이터에 절대 경로를 저장할 수 없습니다.')
    return value
