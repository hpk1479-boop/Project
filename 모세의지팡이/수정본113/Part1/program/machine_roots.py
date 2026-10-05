"""Computer-local root locations. Never copied into project/run settings."""
import json
import os
from pathlib import Path
import uuid

def location_file():
    return Path(os.environ['LOCALAPPDATA'])/'THE_STAFF_OF_MOSES'/'roots.json'

def load_live_root(path=None):
    p=Path(path) if path is not None else location_file()
    if not p.exists():return None
    data=json.loads(p.read_text('utf-8'))
    return data.get('live_records')

def save_live_root(folder,*,path=None,program=None):
    root=Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():raise ValueError('기록 폴더를 선택하세요.')
    # A root may be anywhere on this computer, but not inside a code package.
    package=Path(program or Path(__file__).parent).resolve().parents[1]
    if root.is_relative_to(package):
        raise ValueError('수정본 코드 폴더 밖의 기록 폴더를 선택하세요.')
    p=Path(path) if path is not None else location_file()
    data=json.loads(p.read_text('utf-8')) if p.exists() else {}
    data['live_records']={'path':str(root),'selection_id':uuid.uuid4().hex}
    p.parent.mkdir(parents=True,exist_ok=True)
    temporary=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(p)
    finally:temporary.unlink(missing_ok=True)
    return data['live_records']

def recommended_root(program=None):
    return Path(program or Path(__file__).parent).resolve().parents[2]/'LIVE_기록'
