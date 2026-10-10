"""Compile THE_STAFF_OF_MOSES.mq5 with a portable MetaEditor copy (the user's terminal is untouched).

The EX5 replaces Part1/program/MT5/THE_STAFF_OF_MOSES.ex5 only when it compiled with 0 errors.
Result: 검증결과/revision152/ea/compile_result.json (no absolute paths).
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Part1/program/MT5'
EDITOR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('C:/Program Files/MetaTrader 5/metaeditor64.exe')
NAME = 'THE_STAFF_OF_MOSES.mq5'

work = Path(tempfile.mkdtemp(prefix='mq152_'))
try:
    local = work / 'metaeditor64.exe'
    shutil.copy2(EDITOR, local)
    for header in SOURCE.glob('*.mqh'):
        shutil.copy2(header, work / header.name)
    source = work / NAME
    shutil.copy2(SOURCE / NAME, source)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    proc = subprocess.run([str(local), '/portable', '/compile:' + str(source), '/log'], cwd=work,
                          startupinfo=startup, timeout=180)
    log = source.with_suffix('.log').read_bytes().decode('utf-16', errors='replace')
    match = re.search(r'(\d+) errors?, (\d+) warnings?', log)
    errors = int(match[1]) if match else None
    warnings = int(match[2]) if match else None
    output = source.with_suffix('.ex5')
    record = {'file': NAME, 'errors': errors, 'warnings': warnings, 'exit': proc.returncode,
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    lines = [line.replace(str(work), '<work>') for line in log.splitlines()
             if re.search(r'error|warning', line, re.I)]
    if errors == 0 and output.is_file():
        shutil.copy2(output, SOURCE / output.name)
        record['ex5_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    out = ROOT / '검증결과/revision152/ea'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'compile_result.json').write_text(json.dumps({**record, 'messages': lines[-40:]},
                                                        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False))
    print('\n'.join(lines[-40:]))
finally:
    shutil.rmtree(work, ignore_errors=True)
