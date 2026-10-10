"""Launch the selected MT5 Strategy Tester to produce native STAFF snapshots.

The EA still has only LIVE/BACKTEST modes. Data build is requested externally by
placing a short-lived FILE_COMMON marker; without it BACKTEST performs no export.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import shutil
import subprocess
import time
import uuid
import re
from .history.tester_evidence import NativeHistoryUnavailable

REQUEST_MAGIC='MOSES_NATIVE_BUILD_V1'
EXPERT_STEM='THE_STAFF_OF_MOSES'


NATIVE_MQL_SOURCES = (
    'PRICE_of_Moses.mq5',
    'RSI_of_Moses.mq5',
    'STO_of_Moses.mq5',
    'DI_of_Moses.mq5',
    'THE_STAFF_OF_MOSES.mq5',
)


def _project_mt5_source_root():
    return Path(__file__).resolve().parents[2]/'Part1'/'program'/'MT5'


def _metaeditor64(profile):
    executable=Path(profile.get('executable','')).expanduser().resolve()
    for name in ('MetaEditor64.exe','metaeditor64.exe'):
        candidate=executable.parent/name
        if candidate.is_file():return candidate
    raise ValueError('NATIVE_METAEDITOR_REQUIRED: '+str(executable.parent/'MetaEditor64.exe'))


def _read_compile_log(path):
    path=Path(path)
    if not path.is_file():return ''
    raw=path.read_bytes()
    for encoding in ('utf-16','utf-8-sig','utf-8','cp949','latin1'):
        try:return raw.decode(encoding)
        except (UnicodeDecodeError,LookupError):pass
    return ''


def _compile_one(metaeditor,source,mql5_root,work_dir):
    source=Path(source);output=source.with_suffix('.ex5')
    work=Path(work_dir);work.mkdir(parents=True,exist_ok=True)
    backup=work/(source.stem+'.ex5.backup')
    log=source.with_suffix('.log')
    if backup.exists():backup.unlink()
    if output.is_file():shutil.copy2(output,backup)
    output.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    try:
        args=[str(metaeditor),'/compile:'+str(source),'/inc:'+str(mql5_root),'/log']
        try:
            completed=subprocess.run(args,cwd=str(Path(metaeditor).parent),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90,check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('NATIVE_MQL_COMPILE_TIMEOUT: '+source.name) from exc
        details=_read_compile_log(log)
        (work/(source.stem+'.compile.log')).write_text(details,encoding='utf-8')
        summary=re.search(r'(\d+) errors?, (\d+) warnings?',details)
        if summary is None or int(summary[1]) != 0:
            raise ValueError('NATIVE_MQL_COMPILE_ERRORS: '+source.name+' | '+details[-1200:])
        if not output.is_file() or output.stat().st_size<=0:
            tail=' | '.join(line.strip() for line in details.splitlines()[-4:] if line.strip())
            raise ValueError('NATIVE_MQL_COMPILE_FAILED: '+source.name+((' | '+tail) if tail else '')+' | exit='+str(completed.returncode))
        return output
    except Exception:
        if backup.is_file():shutil.copy2(backup,output)
        raise
    finally:
        backup.unlink(missing_ok=True)
        log.unlink(missing_ok=True)


def ensure_native_mql_current(profile,work_dir,*,emit=lambda *a:None,cancel=lambda:None):
    """Install and compile the package's current MT5 sources before testing.

    The Strategy Tester must not accidentally execute an older LIVE-only EX5.
    In BACKTEST the current STAFF bypasses Named Pipe entirely and exports the
    PRICE/RSI/STO/DI native snapshots through FILE_COMMON.
    """
    source_root=_project_mt5_source_root()
    missing=[name for name in NATIVE_MQL_SOURCES if not (source_root/name).is_file()]
    if missing:raise ValueError('NATIVE_MQL_SOURCE_REQUIRED: '+','.join(missing))
    identity=source_root/'STAFF_Identity_Status.mqh'
    if not identity.is_file():raise ValueError('NATIVE_MQL_SOURCE_REQUIRED: STAFF_Identity_Status.mqh')

    data_root=Path(profile.get('data_root','')).expanduser().resolve()
    mql5_root=data_root/'MQL5'
    experts=mql5_root/'Experts';indicators=mql5_root/'Indicators'
    experts.mkdir(parents=True,exist_ok=True);indicators.mkdir(parents=True,exist_ok=True)
    emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_MQL_SYNC_START'})
    cancel()

    staged=[]
    for name in NATIVE_MQL_SOURCES[:-1]:
        target=indicators/name
        shutil.copy2(source_root/name,target)
        staged.append(target)
    staff=experts/'THE_STAFF_OF_MOSES.mq5'
    shutil.copy2(source_root/'THE_STAFF_OF_MOSES.mq5',staff)
    shutil.copy2(identity,experts/'STAFF_Identity_Status.mqh')
    for name in ('STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh','STAFF_Symbol_Map.mqh'):
        header=source_root/name
        if not header.is_file():raise ValueError('NATIVE_MQL_SOURCE_REQUIRED: '+name)
        shutil.copy2(header,experts/name)
    staged.append(staff)

    editor=_metaeditor64(profile)
    compiled=[]
    for source in staged:
        cancel()
        compiled.append(_compile_one(editor,source,mql5_root,Path(work_dir)/'mql_compile'))
    expert=experts/(EXPERT_STEM+'.ex5')
    if not expert.is_file():raise ValueError('NATIVE_EXPERT_EX5_REQUIRED: '+str(expert))
    emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_MQL_SYNC_DONE','expert':str(expert)})
    return expert


def common_files_root(env=None):
    env=os.environ if env is None else env
    appdata=env.get('APPDATA','').strip()
    if not appdata:raise ValueError('MT5_COMMON_APPDATA_UNAVAILABLE')
    return Path(appdata).expanduser().resolve()/'MetaQuotes'/'Terminal'/'Common'/'Files'


def locate_compiled_expert(profile):
    root=Path(profile['data_root']).expanduser().resolve()/'MQL5'/'Experts'
    direct=root/(EXPERT_STEM+'.ex5')
    if direct.is_file():return direct
    matches=[p for p in root.rglob(EXPERT_STEM+'.ex5') if p.is_file()]
    if len(matches)!=1:
        raise ValueError('NATIVE_EXPERT_EX5_REQUIRED: '+str(direct))
    return matches[0]


def tester_expert_name(profile,expert_path):
    root=Path(profile['data_root']).expanduser().resolve()/'MQL5'/'Experts'
    rel=Path(expert_path).resolve().relative_to(root)
    return str(rel.with_suffix('')).replace('/','\\')


def _tester_dates(start_ns,end_ns):
    if type(start_ns) is not int or type(end_ns) is not int or not 0<=start_ns<end_ns:
        raise ValueError('NATIVE_TEST_RANGE')
    start=datetime.fromtimestamp(start_ns/1_000_000_000,timezone.utc).date()
    # Tester dates are whole UTC days with an exclusive ToDate. Round outward;
    # the EA request carries exact bounds and limits native writes to [start,end).
    day_ns=86400*1_000_000_000
    end=datetime.fromtimestamp(((end_ns+day_ns-1)//day_ns)*86400,timezone.utc).date()
    return start.strftime('%Y.%m.%d'),end.strftime('%Y.%m.%d')


def write_tester_config(path,profile,symbol,start_ns,end_ns,expert_name,*,shutdown_terminal=False,tester_inputs=None):
    start,end=_tester_dates(start_ns,end_ns)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text='\n'.join([
        '[Tester]',
        f'Expert={expert_name}',
        f'Symbol={symbol}',
        'Period=M1',
        'Model=4', # Every tick based on real ticks
        'ExecutionMode=0',
        'Optimization=0',
        f'FromDate={start}',
        f'ToDate={end}',
        'ForwardMode=0',
        'ReplaceReport=1',
        'ShutdownTerminal='+('1' if shutdown_terminal else '0'),
        'UseLocal=1',
        'UseRemote=0',
        'UseCloud=0',
        'Visual=0',
        '',
    ])
    if tester_inputs:
        for key,value in tester_inputs.items():
            if any(c in str(key)+str(value) for c in '\r\n'):raise ValueError('invalid tester input')
        text+='\n[TesterInputs]\n'+'\n'.join(f'{key}={value}' for key,value in tester_inputs.items())+'\n'
    path.write_text(text,encoding='ascii',newline='\r\n')
    return path


def _write_request(common,session,symbol,start_ns=None,end_ns=None):
    directory=common/'MosesDataBuild';directory.mkdir(parents=True,exist_ok=True)
    request=directory/'native_request.txt'
    if request.exists():raise ValueError('NATIVE_BUILD_REQUEST_BUSY')
    tmp=directory/('native_request.'+session+'.tmp')
    limits='' if start_ns is None else f'{(int(start_ns)+999999999)//10**9}\n{(int(end_ns)+999999999)//10**9}\n'
    tmp.write_text(f'{REQUEST_MAGIC}\n{session}\n{symbol}\n'+limits,encoding='ascii',newline='\r\n')
    os.replace(tmp,request)
    return request


def _remove_own_request(request,session):
    try:
        lines=request.read_text('ascii').splitlines()
        if len(lines)>=2 and lines[1].strip()==session:request.unlink(missing_ok=True)
    except OSError:pass



def _windows_process_wait(pid,timeout_seconds):
    """Wait for one Windows process to exit without adding a third-party dependency."""
    if os.name!='nt':
        raise ValueError('NATIVE_WINDOWS_REQUIRED')
    import ctypes
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    SYNCHRONIZE=0x00100000
    WAIT_OBJECT_0=0
    WAIT_TIMEOUT=258
    handle=kernel32.OpenProcess(SYNCHRONIZE,False,int(pid))
    if not handle:
        # Process can already be gone by the time we open it.
        return True
    try:
        result=kernel32.WaitForSingleObject(handle,max(0,int(float(timeout_seconds)*1000)))
        if result==WAIT_OBJECT_0:return True
        if result==WAIT_TIMEOUT:return False
        raise ValueError('NATIVE_TERMINAL_WAIT_FAILED')
    finally:
        kernel32.CloseHandle(handle)


def _request_terminal_close(pid):
    """Ask the selected MT5 GUI to close normally; never force-kill it."""
    if os.name!='nt':
        raise ValueError('NATIVE_WINDOWS_REQUIRED')
    import ctypes
    from ctypes import wintypes
    user32=ctypes.WinDLL('user32',use_last_error=True)
    WM_CLOSE=0x0010
    windows=[]
    CALLBACK=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @CALLBACK
    def visit(hwnd,lparam):
        owner=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if int(owner.value)==int(pid):windows.append(hwnd)
        return True
    if not user32.EnumWindows(visit,0):
        raise ValueError('NATIVE_TERMINAL_ENUM_FAILED')
    if not windows:
        raise ValueError('NATIVE_TERMINAL_CLOSE_WINDOW_NOT_FOUND')
    for hwnd in windows:
        user32.PostMessageW(hwnd,WM_CLOSE,0,0)
    return len(windows)


def _stop_selected_terminal(profile,*,emit=lambda *a:None,cancel=lambda:None,timeout=20):
    """Free this MT5 installation so /config can be applied on a real startup."""
    from .history.selection import verify_running
    verify_running(profile)
    pid=int(profile.get('pid') or 0)
    if pid<=0:raise ValueError('NATIVE_TERMINAL_PID_REQUIRED')
    emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_TERMINAL_RESTART_PREP','pid':pid})
    cancel()
    _request_terminal_close(pid)
    if not _windows_process_wait(pid,timeout):
        raise ValueError('NATIVE_TERMINAL_CLOSE_TIMEOUT')
    emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_TERMINAL_CLOSED','pid':pid})


def _restart_selected_terminal(profile,*,emit=lambda *a:None,cancel=lambda:None,timeout=30):
    """Restart the same installation and return a fresh process identity for history access."""
    from .history.live_status import running_processes
    from .history.selection import same_path
    executable=Path(profile.get('executable','')).expanduser().resolve()
    args=[str(executable)]
    if profile.get('portable'):args.append('/portable')
    emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_TERMINAL_REOPENING'})
    process=subprocess.Popen(args,cwd=str(executable.parent))
    deadline=time.monotonic()+float(timeout)
    last_rows=[]
    while time.monotonic()<deadline:
        cancel()
        rows=running_processes();last_rows=rows
        match=[r for r in rows if int(r.get('pid') or -1)==int(process.pid)
               and same_path(r.get('executable',''),executable)]
        if match:
            row=match[0]
            refreshed=dict(profile)
            refreshed.update({'pid':int(row['pid']),'created_at':int(row['created_at']),
                              'executable':str(executable),'portable':bool(row.get('portable',profile.get('portable',False)))})
            # Give the terminal time to restore the saved account/session before the
            # separate history worker reconnects through the MetaTrader5 Python API.
            time.sleep(3.0)
            emit('PROGRESS',{'phase':'MT5_STRATEGY_TESTER','message_code':'NATIVE_TERMINAL_REOPENED','pid':refreshed['pid']})
            return refreshed
        if process.poll() is not None:
            raise ValueError('NATIVE_TERMINAL_REOPEN_FAILED: exit='+str(process.returncode))
        time.sleep(.5)
    raise ValueError('NATIVE_TERMINAL_REOPEN_TIMEOUT')

def run_native_tester(profile,symbol,start_ns,end_ns,work_dir,*,emit=lambda *a:None,cancel=lambda:None,
                      phase='DATA_BUILD_NATIVE',shutdown_terminal=False,start_timeout=45,
                      tester_inputs=None,capture_only=False,logical_symbol=None):
    # symbol = this broker's Tester symbol; logical_symbol = Wire/native identity checked by the EA.
    from .history.tester_evidence import journal_positions, fresh_lines, history_unavailable
    profile=dict(profile);symbol=str(symbol).strip()
    if not symbol:raise ValueError('NATIVE_SYMBOL_REQUIRED')
    executable=Path(profile.get('executable','')).expanduser().resolve()
    if executable.name.lower()!='terminal64.exe' or not executable.is_file():
        raise ValueError('NATIVE_TERMINAL64_REQUIRED')
    expert=locate_compiled_expert(profile);expert_name=tester_expert_name(profile,expert)
    common=common_files_root();session=uuid.uuid4().hex
    output=common/'MosesDataBuild'/session
    if output.exists():raise ValueError('NATIVE_SESSION_COLLISION')
    work=Path(work_dir).expanduser().resolve();work.mkdir(parents=True,exist_ok=True)
    config=write_tester_config(work/('native_'+session+'.ini'),profile,symbol,start_ns,end_ns,expert_name,shutdown_terminal=shutdown_terminal,tester_inputs=tester_inputs)
    request=_write_request(common,session,str(logical_symbol or symbol).strip(),start_ns,end_ns)
    args=[str(executable),'/config:'+str(config)]
    if profile.get('portable'):args.append('/portable')
    emit('PROGRESS',{'phase':phase,'message_code':'NATIVE_TESTER_START','session':session,'symbol':symbol,'expert':expert_name})
    started_at=time.monotonic();process=None;exit_seen=None
    active_reported=False;last_progress=started_at
    journal_before=journal_positions(profile)
    try:
        process=subprocess.Popen(args,cwd=str(executable.parent))
        while True:
            cancel()
            if (output/'complete.txt').is_file():break
            active=(output/'started.txt').is_file()
            if active and not active_reported:
                emit('PROGRESS',{'phase':phase,'message_code':'NATIVE_EXPORT_ACTIVE','session':session})
                active_reported=True
            now=time.monotonic()
            if active and now-last_progress>=5:
                sizes=[]
                for feed in output.glob('pipe_*.bin' if capture_only else 'feed_*.bin'):
                    try:sizes.append(feed.stat().st_size)
                    except OSError:pass
                emit('PROGRESS',{'phase':phase,'message_code':'NATIVE_EXPORT_PROGRESS',
                    'session':session,'elapsed_seconds':int(now-started_at),
                    'export_bytes':sum(sizes),'output_feeds':sum(size>16 for size in sizes)})
                last_progress=now
            code=process.poll()
            # MT5 reports this before the EA starts when the broker does not
            # offer the selected Tester symbol. Restarting the same account
            # cannot fix it; keep it separate from transient startup failures.
            if not active and code is not None and (int(code)&0xffffffff)==0xC46505BA:
                server=str(profile.get('account_server') or '확인되지 않은 서버')
                raise ValueError('NATIVE_TESTER_SYMBOL_NOT_FOUND: 현재 MT5 서버('+server+')에 '
                    +symbol+' 종목이 없습니다. 해당 종목을 제공하는 계정·서버로 로그인하거나 '
                    '기존 브로커 종목 설정을 확인한 뒤 다시 실행하세요. 완료된 조각은 보존됩니다.')
            if code is not None and exit_seen is None:exit_seen=time.monotonic()
            if not (output/'started.txt').is_file() and time.monotonic()-started_at>float(start_timeout):
                unavailable=history_unavailable(fresh_lines(profile,journal_before),symbol,start_ns,end_ns)
                if unavailable is not None:raise unavailable
                raise ValueError('NATIVE_TESTER_DID_NOT_START: terminal opened but tester did not start')
            # If the bootstrap process exits without the EA consuming the request,
            # allow a short hand-off window only for non-exclusive legacy callers.
            if exit_seen is not None and time.monotonic()-exit_seen>5:
                if active:
                    raise ValueError('NATIVE_TESTER_INCOMPLETE: terminal exited before complete.txt; exit='+str(code))
                unavailable=history_unavailable(fresh_lines(profile,journal_before),symbol,start_ns,end_ns)
                if unavailable is not None:raise unavailable
                raise ValueError('NATIVE_TESTER_DID_NOT_START: exit='+str(code))
            time.sleep(.25)
        if shutdown_terminal and process is not None and process.poll() is None:
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:raise ValueError('NATIVE_TESTER_TERMINAL_DID_NOT_CLOSE') from exc
        if capture_only:
            rows=[line.split('\t') for line in (output/'manifest.tsv').read_text('ascii').splitlines()]
            feeds=[row for row in rows if row[0]=='pipe_feed']
            if not feeds or any(len(row)!=5 or int(row[4])<0 for row in feeds):
                raise ValueError('MSP3_CAPTURE_INVALID_MANIFEST')
            return {'session':session,'export':str(output),'symbol':symbol,
                    'elapsed_seconds':time.monotonic()-started_at,'feeds':len(feeds),
                    'records':sum(int(row[4]) for row in feeds),'tester_model':4,
                    'empty':not any(int(row[4]) for row in feeds)}
        from data_warehouse.native import parse_export
        parsed=parse_export(output)
        if parsed['symbol']!=symbol:raise ValueError('NATIVE_EXPORT_SYMBOL_MISMATCH')
        if not parsed['feeds'] or any(int(feed['count'])<=0 for feed in parsed['feeds']):
            raise ValueError('NATIVE_EXPORT_EMPTY: one or more timeframes produced no snapshots; export='+str(output))
        return {'session':session,'export':str(output),'symbol':symbol,'elapsed_seconds':time.monotonic()-started_at,
                'feeds':len(parsed['feeds']),'rows':sum(int(feed['count']) for feed in parsed['feeds']),
                'payload_sha256':parsed['payload_sha256']}
    finally:
        if shutdown_terminal and process is not None and process.poll() is None:
            try:
                _request_terminal_close(process.pid)
                _windows_process_wait(process.pid,10)
            except Exception:
                pass
        _remove_own_request(request,session)


