"""Wait for the selected MT5 installation, retry only tester-start failure once."""
import time

def wait_closed(profile,*,emit=lambda *a:None,cancel=lambda:None,timeout=30):
    from generic_backtest.history.live_status import running_processes
    from generic_backtest.history.selection import same_path
    deadline=time.monotonic()+timeout;announced=False
    while True:
        cancel()
        rows=[r for r in running_processes() if same_path(r.get('executable',''),profile['executable'])]
        if not rows:return
        if not announced:emit('PROGRESS',{'message_code':'NATIVE_TERMINAL_RESTART_PREP'});announced=True
        if time.monotonic()>=deadline:
            raise RuntimeError('MT5 터미널이 아직 실행 중입니다. 같은 설치의 MT5 창을 닫고 다시 실행하세요. 완료된 조각은 보존됩니다.')
        time.sleep(.25)

def launch_once_retry(launch,profile,*,emit=lambda *a:None,cancel=lambda:None,wait=wait_closed):
    for attempt in range(2):
        wait(profile,emit=emit,cancel=cancel)
        try:
            value=launch()
            wait(profile,emit=emit,cancel=cancel)
            return value
        except Exception as exc:
            if 'NATIVE_TESTER_DID_NOT_START' not in str(exc):raise
            wait(profile,emit=emit,cancel=cancel)
            if attempt:
                raise RuntimeError('NATIVE_TESTER_DID_NOT_START: 1회 재시도도 실패했습니다. 이미 켜진 MT5가 있는지 확인하세요. 완료된 조각은 보존됩니다.') from exc
            emit('CAPTURE_RETRY',{'message':'테스터 시작 실패 — 터미널 종료 확인 후 1회 재시도'})
