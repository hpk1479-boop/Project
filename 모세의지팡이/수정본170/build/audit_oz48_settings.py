"""Read-only local audit of profile-bearing JSON settings. Does not start services.

Usage: python build/audit_oz48_settings.py --root . --output oz48_settings_audit.json
Only profile fields, file/JSON paths, statuses and errors are reported; credentials
and source_text/code strings are not dumped. Historical reference dirs are tagged.
"""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
import oz_profiles
FIELDS={'trigger','trigger_mode','oz_mode','trigger_type','FINAL_TRIGGER_MODE','SOURCE_TRIGGER_MODE','BREAKER_REGIME_SOURCE_TRIGGER_MODE','special5_source_trigger_mode','special5_source_gate','SOURCE_BAND_ENABLED','upper_regime_band','strategy_condition'}

def rows(value,path=''):
    if isinstance(value,dict):
        for key,item in value.items():
            location=path+'.'+key if path else key
            if key in ('source_text','code','source'):continue
            if key in FIELDS and not isinstance(item,(dict,list)) and item is not None:
                yield location,item,key
            elif key=='triggers' and isinstance(item,dict):
                for name,text in item.items():
                    if isinstance(text,str):yield location+'.'+name,text,'trigger'
            elif key in ('breaker','breaker_regime'):yield location,item,key
            elif key=='profile' and isinstance(item,list) and len(item)==2:
                yield location,item[1],'trigger_mode'
            else:yield from rows(item,location)
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from rows(item,f'{path}[{i}]')

def audit(root:Path)->dict:
    result=[];errors=[]
    for file in sorted(root.rglob('*.json')):
        relative=file.relative_to(root)
        if any(part in ('검증결과','__pycache__','.pytest_cache','.git') for part in relative.parts):continue
        try:value=json.loads(file.read_text('utf-8-sig'))
        except (OSError,ValueError) as exc:errors.append({'file':str(relative),'error':type(exc).__name__});continue
        for path,raw,key in rows(value):
            if key in ('breaker','breaker_regime','BREAKER_REGIME_SOURCE_TRIGGER_MODE','SOURCE_BAND_ENABLED','upper_regime_band'):
                status='REMOVED_SPECIAL5_SOURCE_SLOT';reason='현재 SPECIAL5를 명시적으로 다시 불러오세요. 자동 변경하지 않습니다.'
            elif key=='special5_source_gate' and raw=='EMA':
                status='SUPPORTED_SOURCE_METADATA';reason='과거 EMA 출처 메타데이터만 보존. 판정 우회에 사용하지 않음'
            else:
                try:
                    vm,tm=oz_profiles.parse_profile_text(raw)
                    status='LEGACY_ALIAS' if re.search('브레이커|BREAKER',str(raw),re.I) else 'SUPPORTED'
                    reason=f'{vm}/{tm}'
                except ValueError as exc:status='REQUIRES_EXPLICIT_RESELECTION';reason=str(exc)
            result.append({'file':relative.as_posix(),'json_path':path,'value':raw,'status':status,'detail':reason,
                'historical_reference':'reference' in relative.parts})
    return {'root':'.','read_only':True,'settings':result,'unreadable_json':errors}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--output',type=Path)
    args=parser.parse_args();text=json.dumps(audit(args.root),ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text,encoding='utf-8')
    else:print(text,end='')
if __name__=='__main__':main()
