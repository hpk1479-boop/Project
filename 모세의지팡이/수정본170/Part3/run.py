"""PART3 전략연구소를 시작합니다. Python 표준 라이브러리만 필요합니다."""
from __future__ import annotations
import argparse
import sys
import threading
import time
import webbrowser

# 읽기 전용 reference/ 아래에 __pycache__도 만들지 않습니다.
sys.dont_write_bytecode=True
from lab.server import LabServer

# A reload asks again within this time; another open tab pings every 20 s. Either keeps it running.
CLOSE_GRACE=25

def report_shutdown_warnings(result):
    for warning in (result.get('warnings', ()) if isinstance(result,dict) else ()):
        if sys.stderr:print('엔진 종료 안내: '+str(warning),file=sys.stderr,flush=True)

def shutdown_runtime(server):
    """Close the same engine session as the desktop web window."""
    from lab import runtime_lifecycle
    result=runtime_lifecycle.shutdown()
    report_shutdown_warnings(result)
    server.runtime_stopped=True
    server.shutdown()

def stop_when_idle(server,seconds,poll=1.0):
    """Without a console there is no Ctrl+C.
    Exit shortly after the page reports it was closed; as a fallback (browser killed),
    exit once the page has stopped pinging for `seconds`."""
    def watch():
        while True:
            time.sleep(poll)
            now=time.monotonic();seen=server.last_seen;closed=server.closed_at
            closing=closed is not None and now-closed>=CLOSE_GRACE and seen==getattr(server,'close_seen',None)
            if closing or (seen is not None and now-seen>seconds):
                try:shutdown_runtime(server)
                except Exception as exc:
                    if sys.stderr:print('엔진 종료 실패: '+str(exc),file=sys.stderr,flush=True)
                    time.sleep(5)
                else:return
    threading.Thread(target=watch,daemon=True).start()

def main(argv=None):
    p=argparse.ArgumentParser(description='PART 3 전략연구소')
    p.add_argument('--port',type=int,default=8763)
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--idle-exit',type=float,default=0,help='마지막 화면 요청 후 이 초가 지나면 종료(0=끄지 않음)')
    args=p.parse_args(argv)
    try:server=LabServer(args.port)
    except OSError:
        # 이미 실행 중인 세션이 있으면 그 포트를 빼앗지 않습니다.
        server=LabServer(0)
    url=f'http://127.0.0.1:{server.server_port}/#token={server.token}'
    if sys.stdout:print('PART 3 전략연구소\n'+url+'\n종료: 이 터미널에서 Ctrl+C',flush=True)
    if args.idle_exit>0:stop_when_idle(server,args.idle_exit)
    if not args.no_browser:
        threading.Timer(0.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        try:
            if not getattr(server,'runtime_stopped',False):
                from lab import runtime_lifecycle
                result=runtime_lifecycle.shutdown()
                report_shutdown_warnings(result)
        finally:server.server_close()

if __name__=='__main__':main()
