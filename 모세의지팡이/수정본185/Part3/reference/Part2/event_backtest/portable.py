"""Portable metadata validation; machine runtime paths never enter the catalog."""
import re

def validate(value):
    if isinstance(value,dict):
        for key,item in value.items():validate(str(key));validate(item)
    elif isinstance(value,(list,tuple)):
        for item in value:validate(item)
    elif isinstance(value,str):
        if re.search(r'(?i)(?:[a-z]:[\\/]|\\\\[^.]|(?:^|[\\/])\.\.(?:[\\/]|$))',value) or value.startswith('/'):
            raise ValueError('창고 메타데이터에 절대 경로를 저장할 수 없습니다.')
    return value
