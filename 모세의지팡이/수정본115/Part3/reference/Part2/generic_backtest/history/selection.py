"""MT5 process inventory and pure selection policy. Never starts a terminal."""
import configparser
import os
from pathlib import Path
from .live_status import running_processes
from ..contracts import GenericError

LIVE_CONFIRM="LIVE가 사용 중인 MT5입니다.\n백테스트에서도 이 MT5를 사용하시겠습니까?"
NO_TERMINAL="실행 중인 MT5가 없습니다. MT5를 실행해 주세요."


def same_path(a,b):
    return os.path.normcase(os.path.normpath(str(a)))==os.path.normcase(os.path.normpath(str(b)))


def _text(path):
    raw=path.read_bytes()
    if len(raw)>1024*1024:raise ValueError('oversized identity config')
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')


def _saved_identity(executable,portable):
    install=Path(executable).parent
    roots=[install] if portable else []
    directory=Path(os.environ.get('APPDATA',''))/'MetaQuotes/Terminal'
    if not portable and directory.is_dir():
        for candidate in directory.iterdir():
            if not candidate.is_dir():continue
            try:
                if same_path(_text(candidate/'origin.txt').strip(),install):roots.append(candidate)
            except (OSError,ValueError):continue
    if len(roots)!=1:return {}
    try:
        config=configparser.ConfigParser(interpolation=None,strict=False)
        config.read_string(_text(roots[0]/'config/common.ini'))
        server=config.get('Common','Server',fallback='').strip()
        login=config.getint('Common','Login',fallback=0)
        return {'data_root':str(roots[0]),'account_server':server,'account_login':login,
                'identity_source':'SAVED_CONFIG_EXPECTATION'}
    except (OSError,ValueError,configparser.Error):return {}


def discover_terminals():
    processes=running_processes()
    rows=[]
    for p in processes:
        if not p.get('executable') or Path(p['executable']).name.lower()!='terminal64.exe':continue
        row={'pid':p['pid'],'created_at':p['created_at'],'executable':p['executable'],
             'portable':p.get('portable',False),'live':False,'live_unknown':True}
        row.update(_saved_identity(row['executable'],row['portable']))
        rows.append(row)
    rows.sort(key=lambda r:(r['executable'],r['pid']))
    return {'terminals':rows,'live_identity_status':'NOT_INSPECTED_INDEPENDENT_BACKTEST'}


def select_terminal(rows,choose,confirm):
    """Callbacks are GUI-only. Declining LIVE offers other running terminals."""
    candidates=sorted(rows,key=lambda r:not r.get('live',False))
    if not candidates:raise GenericError('E_MT5_NOT_RUNNING',NO_TERMINAL)
    force_picker=False
    while candidates:
        chosen=candidates[0] if len(candidates)==1 and not force_picker else choose(candidates)
        if chosen is None:return None
        if chosen not in candidates:raise GenericError('E_MT5_IDENTITY','invalid selection')
        if chosen.get('live_unknown'):
            if confirm('LIVE 사용 MT5를 식별하지 못했습니다. 선택한 MT5를 백테스트 조회에 사용하시겠습니까?'):
                return dict(chosen,unknown_live_confirmed=True)
        elif not chosen.get('live') or confirm(LIVE_CONFIRM):
            return dict(chosen,live_confirmed=bool(chosen.get('live')))
        candidates=[r for r in candidates if r!=chosen]
        force_picker=True
    return None


def verify_running(profile,processes=None):
    rows=running_processes() if processes is None else processes
    matches=[p for p in rows if p.get('pid')==profile.get('pid')
        and same_path(p.get('executable',''),profile.get('executable',''))
        and p.get('created_at')==profile.get('created_at')]
    if len(matches)!=1:raise GenericError('E_MT5_NOT_RUNNING',NO_TERMINAL)
    if sum(same_path(p.get('executable',''),profile['executable']) for p in rows)!=1:
        raise GenericError('E_MT5_IDENTITY','같은 실행파일의 여러 MT5를 구분할 수 없습니다.')
    # No application status files are inspected. Permission is obtained when
    # this process/profile is chosen; identity checks above remain mandatory.
    return matches[0]
