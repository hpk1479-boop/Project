"""Transport-facing operations for the Part3 test strategy library."""
from __future__ import annotations
import sys
from . import storage


def _catalog():
    program = storage.ROOT.parent / 'Part1/program'
    if str(program) not in sys.path:
        sys.path.insert(0, str(program))
    from strategy_recipe import registry, user_catalog
    return registry, user_catalog


def listing():
    _, owner = _catalog()
    return owner.library_items(storage.ROOT.parent)


def _idle():
    from . import unified_live, backtest_jobs
    if unified_live.engines()['engines']:
        raise ValueError('라이브 감시를 종료한 뒤 전략 목록을 변경하세요.')
    if backtest_jobs.active_jobs():
        raise ValueError('백테스트를 종료한 뒤 전략 목록을 변경하세요.')


def change(data):
    if not isinstance(data, dict) or data.get('action') not in ('rename', 'promote', 'delete'):
        raise ValueError('테스트 전략 목록 작업을 확인하세요.')
    from . import unified_live
    registry, owner = _catalog()
    root = storage.ROOT.parent
    with unified_live._lock:
        _idle()
        action = data['action']
        if action == 'rename':
            owner.rename_generated(data.get('id'), data.get('name'), root)
            message = '전략 이름을 변경했습니다.'
        elif action == 'promote':
            owner.promote_selected(data.get('ids'), registry.builtin_entries(), root)
            message = 'Part1·Part2 전략 설정 목록에 등록했습니다. 라이브 실행은 전략 설정에서 직접 켜세요.'
        else:
            owner.delete_library(data.get('ids'), root, confirmed=data.get('confirmed', False))
            message = '선택한 테스트 전략을 모든 파트에서 삭제했습니다.'
        return {'ok': True, 'message': message, **listing()}


def change_registration(data):
    if not isinstance(data, dict) or data.get('action') not in ('delete', 'reset'):
        raise ValueError('전략 목록 작업을 확인하세요.')
    part = data.get('part')
    if part not in ('Part1', 'Part2'):
        raise ValueError('목록을 변경할 Part1 또는 Part2를 선택하세요.')
    from . import unified_live
    registry, owner = _catalog()
    root = storage.ROOT.parent
    with unified_live._lock:
        _idle()
        if data['action'] == 'delete':
            owner.delete_selected(data.get('ids'), registry.builtin_entries(), root, part=part)
            message = part + ' 전략설정에서 선택한 전략을 삭제했습니다. 전략 원본은 Part3에 보존됩니다.'
        else:
            owner.reset_builtins(root, part=part)
            message = part + ' 전략설정을 초기화했습니다. 기본 스페셜을 복구하고 새로 만든 전략 등록을 해제했습니다.'
        return {'ok': True, 'message': message, **listing()}
