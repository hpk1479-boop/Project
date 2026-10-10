"""PART3 소유 파일만 저장합니다. 원본 SPECIAL/Part1/Part2에는 쓰지 않습니다.

동일 파일 경쟁은 open(mode='x')로 처리합니다. 번호 검사 후 기존 파일을
덮는 방식이 아니므로 두 창에서 동시에 생성해도 기존 .py를 덮어쓰지 않습니다.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from . import catalog
from .compiler import compile_recipe

ROOT=catalog.ROOT
NUMBER=re.compile(r'^(?:Test_)?SPECIAL0*(\d+)(?:\.py|\.recipe\.json)$',re.I)

def json_write(path:Path,data)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def connections()->dict:
    path=ROOT/'projects/connections.json'
    data=json.loads(path.read_text('utf-8')) if path.exists() else {}
    # Part1·Part2·Part3 always sit side by side: the default is the relative '..', never a fixed path.
    default='..' if (ROOT.parent/'Part1/program').is_dir() else ''
    data.setdefault('project_root',default)
    data.setdefault('python_executable','')
    if data.get('warehouse'):data['warehouse']=str(resolve_warehouse(data['warehouse'],project_path(data)))
    else:data['warehouse']=part2_warehouse(project_path(data))
    return data

def resolve_warehouse(value,project_root=None)->Path:
    """Internal locations follow the project; external roots remain selectable."""
    path=Path(value).expanduser()
    if not path.is_absolute():
        if '..' in path.parts:raise ValueError('상위 폴더 경로 대신 외부 창고의 전체 위치를 지정하세요.')
        path=Path(project_root or ROOT.parent)/path
    return path.resolve()

def part2_warehouse(project_root:str)->str:
    """Part2's own warehouse setting (its event_backtest.json or its default rule), read-only.
    The rule stays in Part2; Part3 only asks it. Empty when Part2 is not beside Part3."""
    if not project_root:return ''
    source=Path(project_root)/'Part2/event_backtest/settings.py'
    if not source.is_file():return ''
    try:
        import importlib.util
        spec=importlib.util.spec_from_file_location('_part3_part2_settings',source)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return str(module.settings()['warehouse'])
    except Exception:return ''

def part1_gemini(project_root:str|None=None)->dict:
    """GEMINI_API_KEY / GEMINI_MODEL from Part1 config.txt, read-only. Never written or logged."""
    root=project_root if project_root is not None else project_path()
    path=Path(root)/'Part1/program/config.txt' if root else None
    found={}
    if path and path.is_file():
        for line in path.read_text('utf-8-sig',errors='replace').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=(x.strip() for x in line.split('=',1))
            if key in ('GEMINI_API_KEY','GEMINI_MODEL') and value:found[key]=value
    return found

def project_path(data:dict|None=None)->str:
    value=(data or connections()).get('project_root','')
    if not value:return ''
    p=Path(value).expanduser()
    return str((ROOT/p).resolve() if not p.is_absolute() else p.resolve())

def save_connections(data:dict):
    clean={k:str(data.get(k,'')).strip() for k in ('project_root','python_executable','warehouse')}
    if clean['project_root']:
        root=Path(project_path(clean))
        if not (root/'Part1/program').is_dir() or not (root/'Part2').is_dir():
            raise ValueError('Part1/program과 Part2가 들어 있는 공통 상위 폴더를 입력하세요.')
    # Defaults are not stored as fixed paths: they keep following the side-by-side layout.
    if clean['warehouse']:
        root=Path(project_path(clean) or ROOT.parent).resolve()
        warehouse=resolve_warehouse(clean['warehouse'],root)
        if str(warehouse)==part2_warehouse(str(root)):clean['warehouse']=''
        else:clean['warehouse']=warehouse.relative_to(root).as_posix() if warehouse.is_relative_to(root) else str(warehouse)
    if clean['project_root'] and ROOT.parent.resolve()==Path(project_path(clean)):clean['project_root']='..'
    json_write(ROOT/'projects/connections.json',clean)
    if 'warehouse' in data:
        from .unified_backtest import _part2
        _part2()
        from event_backtest.warehouse_cleanup import cleanup_warehouse
        connected=connections().get('warehouse')
        if connected:cleanup_warehouse(connected)
    return clean

def test_folder(*, for_write=False):
    from strategy_recipe.user_catalog import test_directory
    return test_directory(ROOT.parent, for_write=for_write, part3_root=ROOT)

def _number_paths():
    yield test_folder()

def next_number()->int:
    seen=[]
    for folder in _number_paths():
        if folder.is_dir():
            for p in folder.iterdir():
                m=NUMBER.fullmatch(p.name)
                if m:seen.append(int(m[1]))
    number=max(seen,default=0)+1
    if number>999:raise ValueError('Test_SPECIAL001~999 번호를 모두 사용했습니다. 기존 파일은 덮어쓰지 않습니다.')
    return number

def next_filename()->str:return f'Test_SPECIAL{next_number():03d}.py'

def preview(recipe:dict)->dict:
    recipe=load_saved_recipe(recipe)
    filename=next_filename()
    code=compile_recipe(recipe,filename)
    return {'filename':filename,'code':code,'summary':catalog.summary(recipe)}

def generate(recipe:dict)->dict:
    recipe=load_saved_recipe(recipe)
    folder=test_folder(for_write=True)
    for _ in range(1000):
        result=preview(recipe);filename=result['filename'];path=folder/filename
        try:
            # 계산/문법 검사 후에만 파일을 예약합니다. O_EXCL에 대응하는 'x' 모드입니다.
            with path.open('x',encoding='utf-8',newline='\n') as out:out.write(result['code'])
        except FileExistsError:continue
        stamp=dt.datetime.now().astimezone().isoformat(timespec='seconds')
        sidecar={'recipe':recipe,'created_at':stamp,'catalog_scope':'library',
                 'filename':filename,'sha256':hashlib.sha256(result['code'].encode()).hexdigest()}
        # 설정도 같은 새 번호로만 저장합니다. 실패하면 코드는 이미 보존되어 있습니다.
        try:
            with path.with_suffix('.recipe.json').open('x',encoding='utf-8') as out:
                json.dump(sidecar,out,ensure_ascii=False,indent=2)
        except OSError as exc:
            result['warning']=f'Python 파일은 생성되었습니다. 설정 저장 실패: {exc}'
        if 'warning' not in result:
            result['strategy_id'] = path.stem.upper()
        result.update(path=str(path),created_at=stamp)
        return result
    raise RuntimeError('새 번호를 예약하지 못했습니다.')

def recent()->list:
    folder=test_folder();items=[]
    for p in sorted(folder.glob('Test_SPECIAL[0-9][0-9][0-9].py'),key=lambda p:p.stat().st_mtime,reverse=True):
        meta={}
        try:meta=json.loads(p.with_suffix('.recipe.json').read_text('utf-8'))
        except (OSError,ValueError):pass
        r=meta.get('recipe',{})
        items.append({'filename':p.name,'name':r.get('name','직접 작성한 전략'),'base':r.get('base',''),
            'created_at':meta.get('created_at',dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat()),
            'manual':meta.get('manual',False)})
    return items[:100]

def generated_path(filename:str)->Path:
    from .compiler import FILE_RE
    if not FILE_RE.fullmatch(filename):raise ValueError('생성된 Test_SPECIALXXX.py 파일만 선택할 수 있습니다.')
    folder=test_folder()
    original=folder/filename
    if original.is_symlink():raise ValueError('테스트 전략은 일반 파일이어야 합니다.')
    p=original.resolve()
    if p.parent!=folder.resolve() or not p.is_file():raise FileNotFoundError(filename)
    return p


def load_saved_recipe(recipe: dict) -> dict:
    checked=catalog.canonical(recipe)
    catalog.validate(checked)
    return checked


def load_saved_draft(data: dict) -> dict:
    if set(data) != {'recipe'}:
        raise ValueError('초안에는 AI Recipe만 저장합니다.')
    return {'recipe':load_saved_recipe(data['recipe'])}


def save_draft(data: dict) -> None:
    json_write(ROOT/'projects/last_draft.json',load_saved_draft(data))


def reopen(filename:str)->dict:
    # Previously generated code remains view-only, including legacy files.
    p=generated_path(filename);code=p.read_text('utf-8')
    sidecar=p.with_suffix('.recipe.json')
    meta=json.loads(sidecar.read_text('utf-8')) if sidecar.is_file() else {}
    changed=hashlib.sha256(code.encode()).hexdigest()!=meta.get('sha256')
    return {'code':code,'filename':filename,'external_edit':changed}


def save_project(recipe:dict)->str:
    recipe=load_saved_recipe(recipe)
    stamp=dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    name=f'recipe_{stamp}_{uuid.uuid4().hex[:6]}.json'
    json_write(ROOT/'projects'/name,recipe)
    return name
