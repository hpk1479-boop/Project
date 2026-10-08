"""Run only the selected offline regressions and keep portable fresh evidence."""
from pathlib import Path
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '검증결과/revision151'

if __name__ == '__main__':
    label, *tests = sys.argv[1:]
    if not label.replace('_','').isalnum() or not tests:
        raise ValueError('A simple label and explicit test paths are required')
    temporary = EVIDENCE / (label+'_tmp')
    if temporary.exists():
        raise ValueError('Fresh test directory already exists')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                       MOSES_EVIDENCE_ROOT=str(EVIDENCE))
    command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp='+temporary.relative_to(ROOT).as_posix(), *tests]
    result = subprocess.run(command, cwd=ROOT, env=environment,
                            capture_output=True, text=True, encoding='utf-8')
    output = result.stdout + result.stderr
    output = '\n'.join(line for line in output.splitlines()
                       if not line.startswith('Failed to find real location of '))+'\n'
    for path, replacement in ((ROOT, '.'), (ROOT.parent, '<workspace>'), (Path.home(), '<user>')):
        # Tracebacks sometimes print repr(path), with doubled backslashes.
        output = output.replace(str(path).replace('\\', '\\\\'), replacement)
        output = output.replace(str(path), replacement).replace(path.as_posix(), replacement)
    output = re.sub(r'(?:\.\.[\\/])+', '<external>/', output)
    (EVIDENCE/(label+'.log')).write_text(output, encoding='utf-8')
    print(output, end='', flush=True)
    raise SystemExit(result.returncode)
