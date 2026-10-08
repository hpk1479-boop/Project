"""PART3 전략연구소 — 명령 프롬프트 창 없이 시작합니다(더블클릭).

브라우저 탭을 닫으면 약 25초 뒤 서버가 스스로 종료됩니다(새로고침은 유지).
브라우저가 강제로 꺼져 알림을 못 보낸 경우에는 3분 뒤 종료됩니다.
시작 오류는 창으로 알리고 logs/start_error.txt에 남깁니다.
"""
import os
import sys
import traceback
from pathlib import Path

HERE=Path(__file__).resolve().parent
os.chdir(HERE);sys.path.insert(0,str(HERE))
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True

try:
    import run
    run.main(['--idle-exit','180'])
except Exception:
    detail=traceback.format_exc()
    log=HERE/'logs';log.mkdir(exist_ok=True)
    (log/'start_error.txt').write_text(detail,encoding='utf-8')
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            '시작하지 못했습니다.\n자세한 내용: logs/start_error.txt\n\n'+detail.strip().splitlines()[-1],
            'MOSES 통합 웹 UI',0x10)
    except Exception:pass
