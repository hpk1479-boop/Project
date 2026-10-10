"""Run selected release regression files through the existing offline worker."""
from pathlib import Path
import json
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_current import child, portable_value, read_child_result, relative


def main():
    label, *targets = sys.argv[1:]
    if not targets or not label.replace('_', '').isalnum():
        raise ValueError('A simple label and explicit test files are required')
    destination = relative(ROOT, '검증결과/fixes95/' + label)
    destination.mkdir(parents=True, exist_ok=True)
    temporary = relative(ROOT, '.v/r95_' + uuid.uuid4().hex[:7])
    temporary.mkdir(parents=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               PYTHONIOENCODING='utf-8', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTEST_ADDOPTS='', TMP=str(temporary), TEMP=str(temporary))
    env.pop('MOSES_PROCESS_SMOKE', None)
    details = []
    for index, target in enumerate(targets):
        relative(ROOT, target.split('::', 1)[0])
        result_path = destination / f'{index:02}.json'
        log = destination / f'{index:02}.log'
        command = [sys.executable, '-B', 'verification/current_worker.py', 'pytest', target,
                   result_path.relative_to(ROOT).as_posix(), temporary.relative_to(ROOT).as_posix()]
        code, seconds = child(command, ROOT, env, 300, log)
        result = read_child_result(result_path, code, target)
        result.update(target=target, seconds=seconds, log=log.relative_to(ROOT).as_posix())
        details.append(result)
        summary = dict(passed=sum(r['passed'] for r in details), failed=sum(r['failed'] for r in details), details=details)
        (destination / 'summary.json').write_text(json.dumps(portable_value(summary), ensure_ascii=False, indent=2), encoding='utf-8')
        print(('FAIL' if result['failed'] else 'PASS') + f" {target} ({result['passed']}/{result['failed']})", flush=True)
    return int(any(r['failed'] for r in details))


if __name__ == '__main__':
    raise SystemExit(main())
