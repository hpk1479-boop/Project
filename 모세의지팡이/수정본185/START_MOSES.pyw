"""Open the integrated localhost UI in a dedicated WebView2 window."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PART3 = ROOT / 'Part3'
os.chdir(PART3)
sys.path.insert(0, str(PART3))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

try:
    from lab.desktop_window import run
    run()
except Exception as exc:
    detail = traceback.format_exc()
    logs = PART3 / 'logs'
    logs.mkdir(exist_ok=True)
    (logs / 'moses_start_error.txt').write_text(detail, encoding='utf-8')
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            str(exc) if isinstance(exc, RuntimeError) else
            '전용 창을 열지 못했습니다. Part3/logs/moses_start_error.txt를 확인하세요.',
            'MOSES', 0x10,
        )
    except Exception:
        pass
