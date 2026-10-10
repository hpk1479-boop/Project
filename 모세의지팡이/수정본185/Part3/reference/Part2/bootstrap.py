"""Repair relocated virtual environments without trusting a copied launcher.

Run through START_BACKTEST.cmd, or an explicitly selected base Python.
Existing environments are renamed, never recursively deleted. No market data,
strategy, terminal or account configuration is changed by this module.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
LOG = ROOT / 'BACKTEST_SETUP.log'
RAW_LOG = ROOT / 'BACKTEST_SETUP_RAW.log'


def executable(env: Path, windowed: bool = False) -> Path:
    return env / ('Scripts' if os.name == 'nt' else 'bin') / (
        'pythonw.exe' if windowed and os.name == 'nt' else 'python.exe' if os.name == 'nt' else 'python')


def probe(env: Path, *, history: bool = False) -> tuple[bool, str]:
    python = executable(env)
    if not python.is_file():
        return False, f'Python 실행 파일 없음: {python}'
    modules = ('numpy', 'MetaTrader5') if history and os.name == 'nt' else (
        ('numpy',) if history else ('numpy', 'pandas', 'zmq', 'tkinter', 'duckdb'))
    code = ('import sys,struct,json,importlib; '
            'mods=' + repr(modules) + '; '
            'loaded=[importlib.import_module(m) for m in mods]; '
            'assert struct.calcsize("P")==8,"64-bit Python required"; '
            'print(json.dumps({"prefix":sys.prefix,"version":sys.version,'
            '"modules":{m:getattr(v,"__version__","builtin") for m,v in zip(mods,loaded)}}))')
    try:
        r = subprocess.run([str(python), '-I', '-c', code], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode:
            return False, (r.stderr or r.stdout).strip()
        receipt = json.loads(r.stdout)
        if Path(receipt['prefix']).resolve() != env.resolve():
            return False, '선택한 가상환경과 실제 Python 실행 환경이 일치하지 않음'
        return True, r.stdout.strip()
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)



def setup_detail_ko(detail: str) -> str:
    text = str(detail or '').strip()
    import re
    if text.startswith('{'):
        try:
            receipt = json.loads(text)
            if isinstance(receipt, dict) and isinstance(receipt.get('modules'), dict):
                names = ', '.join(receipt['modules'])
                return f'64비트 Python 및 필수 모듈 확인 완료 ({names})'
        except json.JSONDecodeError:
            pass
    missing = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
    if missing:
        return f"필수 Python 모듈 '{missing.group(1)}' 없음"
    if '64-bit Python required' in text:
        return '64비트 Python이 필요함'
    if 'prefix does not match' in text.lower() or '가상환경과 실제 Python' in text:
        return '선택한 가상환경과 실제 Python 실행 환경이 일치하지 않음'
    if 'Python 실행 파일 없음:' in text:
        return text
    if not text:
        return '원인 정보 없음'
    return 'Python 실행 환경 점검 실패 · 기술 원본은 BACKTEST_SETUP_RAW.log에 보존됨'

def log(message: str) -> None:
    line = f'{datetime.now().isoformat(timespec="seconds")} {message}'
    print(line, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def run(command: list[str]) -> None:
    log('실행 환경 구성 명령 시작')
    with RAW_LOG.open('a', encoding='utf-8') as f:
        f.write('\n=== '+datetime.now().isoformat(timespec='seconds')+' ===\n')
        f.write(subprocess.list2cmdline(command)+'\n')
        result = subprocess.run(command, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
    if result.returncode:
        raise RuntimeError(f'환경 구성 명령 실패(종료코드 {result.returncode})')
    log('실행 환경 구성 명령 완료')


def ensure_environment(*, history: bool = False) -> Path:
    env = ROOT / ('.venv-history' if history else '.venv-generic')
    ok, detail = probe(env, history=history)
    log(f'환경 확인 {env.name}: {"정상" if ok else "재구성 필요"} · {setup_detail_ko(detail)}')
    if ok:
        return executable(env)
    # A venv needs a functioning BASE installation. Do not edit pyvenv.cfg by
    # replacing a username: extension modules and script shebangs can differ.
    base = Path(getattr(sys, '_base_executable', sys.executable))
    if not base.is_file():
        raise RuntimeError('정상 동작하는 64비트 기본 Python이 필요합니다.')
    backup = None
    if env.exists():
        suffix = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = env.with_name(env.name + '.backup-' + suffix)
        env.rename(backup)
        log(f'기존 실행 환경 백업: {backup.name}')
    try:
        run([str(base), '-m', 'venv', str(env)])
        req = ROOT / ('requirements-history.txt' if history else 'requirements-backtest.txt')
        run([str(executable(env)), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(req)])
        ok, detail = probe(env, history=history)
        if not ok:
            raise RuntimeError('새 실행 환경 점검 실패: ' + setup_detail_ko(detail))
        log('실행 환경 준비 완료 · ' + setup_detail_ko(detail))
    except BaseException:
        # Preserve failed installation for diagnostics too; restore old path.
        if env.exists():
            env.rename(env.with_name(env.name + '.failed-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
        if backup is not None and backup.exists():
            backup.rename(env)
        raise
    return executable(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--history', action='store_true', help='also repair the independent history environment')
    parser.add_argument('--check', action='store_true', help='read-only diagnostics; no installation')
    args = parser.parse_args(argv)
    if args.check:
        details = {name: probe(ROOT / name, history=name.endswith('history'))
                   for name in ('.venv-generic', '.venv-history')}
        print(json.dumps(details, ensure_ascii=False, indent=2))
        return 0 if all(ok for ok, _ in details.values()) else 1
    import struct
    if sys.version_info[:2] not in ((3, 12), (3, 13)) or struct.calcsize('P') != 8:
        raise RuntimeError('Python 3.12 또는 3.13 64비트가 필요합니다. Tcl/Tk와 pip를 포함해 설치해 주세요.')
    log(f'백테스트 실행 환경 시작 · Python {sys.version.split()[0]} · {platform.platform()}')
    ensure_environment()
    if args.history:
        ensure_environment(history=True)
    if args.launch:
        # Use console python so launch exceptions are captured in the log.
        log('BACKTEST CONTROL 실행')
        with LOG.open('a', encoding='utf-8') as f:
            return subprocess.call([str(executable(ROOT / '.venv-generic')), str(ROOT / 'BACKTEST CONTROL.pyw')],
                cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        detail = traceback.format_exc()
        with RAW_LOG.open('a', encoding='utf-8') as f:
            f.write('\n=== 실행 환경 오류 원본 ===\n'+detail+'\n')
        log('실행 환경 오류 · '+setup_detail_ko(str(exc)))
        raise SystemExit(1)
