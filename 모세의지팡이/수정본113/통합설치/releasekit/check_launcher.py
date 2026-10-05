"""Cold launcher check: standard library only, no downloads or build caches."""
import sys
import json
import tkinter
from . import ui


def main():
    if sys.version_info[:2] not in {(3, 12), (3, 13)}:
        raise RuntimeError('Python 3.12 또는 3.13이 필요합니다.')
    if not tkinter.TkVersion:
        raise RuntimeError('Python 화면 도구를 확인할 수 없습니다.')
    print(json.dumps({'ready': True, 'python': getattr(sys, '_base_executable', sys.executable)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
