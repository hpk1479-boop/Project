"""Selected generated-file adapter for the common Part2 backtest job lifecycle.

Only portable relative references are persisted. Machine roots are passed to
the child in its environment and never become part of a strategy or scenario.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import sys

from . import storage


def _part2(project_root):
    for folder in (Path(project_root) / 'Part2', Path(project_root) / 'Part1' / 'program'):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    from event_backtest import settings
    return settings


def relative_reference(root, value):
    """Resolve a portable root-relative file reference without escaping it."""
    text = str(value).replace('\\', '/')
    if (not text or PureWindowsPath(text).drive or text.startswith('/') or
            '..' in Path(text).parts):
        raise ValueError('파일 참조는 루트 안의 상대 경로여야 합니다.')
    base = Path(root).resolve()
    path = (base / text).resolve()
    if not path.is_relative_to(base):
        raise ValueError('파일 참조가 루트 폴더를 벗어났습니다.')
    return path


def prepare_generated(request, project_root):
    if not isinstance(request, dict):
        raise ValueError('생성 전략 백테스트 입력을 확인하세요.')
    from .unified_backtest import _available_only
    available_only = _available_only(request)
    filename = request.get('filename')
    if not isinstance(filename, str):
        raise ValueError('백테스트할 생성 전략 파일을 선택하세요.')
    strategy = storage.generated_path(filename)
    storage.reopen(strategy.name)
    root = Path(project_root).resolve()
    from strategy_recipe.user_catalog import test_directory
    if strategy.parent != test_directory(root).resolve():
        raise ValueError('현재 Part3와 같은 프로젝트에 연결된 생성 전략만 실행할 수 있습니다.')
    settings = _part2(root)
    current = settings.settings()
    common = {key: str(request.get(key, '')).strip() for key in ('symbol', 'start', 'end', 'mode')}
    common['available_only'] = available_only
    result_mode = request.get('result_mode', 'ALERT_ONLY')
    try:
        spread = float(request.get('spread_points', 0))
    except (TypeError, ValueError):
        raise ValueError('스프레드는 0 이상의 숫자여야 합니다.') from None
    if not math.isfinite(spread) or spread < 0:
        raise ValueError('스프레드는 0 이상의 유한한 수여야 합니다.')
    if request.get('build_only'):
        raise ValueError('데이터 구축만 실행하려면 공통 데이터 구축 화면을 사용하세요.')
    cores = request.get('cores', current.get('cores'))
    if cores is not None and (type(cores) is not int or cores < 1):
        raise ValueError('작업 프로세스 수는 1 이상의 정수여야 합니다.')
    scenario = settings.scenario(
        defaults={key: current[key] for key in
                  ('cores', 'capture_start', 'overlap_trading_days')},
        **common, strategies=[strategy.stem], oz_evaluation='all', cores=cores,
        result_mode=result_mode, spread_points={common['symbol']: spread}, build_only=False,
        virtual_entry=request.get('virtual_entry'),
    )
    return scenario, {'filename': strategy.name,
                      'strategy_sha256': hashlib.sha256(strategy.read_bytes()).hexdigest(),
                      'cores': cores}


def command(job, action, approved=None):
    """Return the common supervisor's command/env/cwd for one selected file."""
    if action not in ('plan', 'run'):
        raise ValueError('백테스트 실행 단계를 확인하세요.')
    identifier = job['id']
    if (not isinstance(identifier, str) or len(identifier) != 32 or
            any(char not in '0123456789abcdef' for char in identifier)):
        raise ValueError('실행 ID를 확인하세요.')
    root = Path(job['project_root']).resolve()
    warehouse = Path(job['warehouse']).resolve()
    folder = Path(job['folder']).resolve()
    if folder != relative_reference(warehouse, 'runs/' + identifier):
        raise ValueError('생성 전략 실행 폴더가 공통 실행 ID와 일치하지 않습니다.')
    adapter = job['adapter']
    strategy = storage.generated_path(adapter['filename'])
    from strategy_recipe.user_catalog import test_directory
    if strategy.parent != test_directory(root).resolve():
        raise ValueError('생성 전략이 현재 프로젝트 안에 있어야 합니다.')
    digest = hashlib.sha256(strategy.read_bytes()).hexdigest()
    if digest != adapter.get('strategy_sha256'):
        raise ValueError('계획 확인 후 생성 전략 파일이 변경되었습니다. 새 백테스트를 시작하세요.')
    spec = {'version': 1, 'project_root': '.', 'warehouse': '.',
            'strategy': strategy.relative_to(root).as_posix(),
            'strategy_sha256': digest, 'job_dir': 'runs/' + identifier,
            'session_id': identifier, 'scenario': dict(job['scenario']),
            'action': action, 'approved_token': approved,
            'rebuild': bool(job.get('rebuild')), 'cores': adapter.get('cores')}
    path = folder / 'generated_request.json'
    storage.json_write(path, spec)
    environment = {'PART3_BT_REQUEST': str(path), 'PART3_BT_PROJECT_ROOT': str(root),
                   'PART3_BT_WAREHOUSE': str(warehouse),
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
    args = [job.get('python_executable') or sys.executable, '-B', '-X', 'utf8',
            str(root / 'Part3' / 'backtest.py')]
    return args, environment, root / 'Part2'
