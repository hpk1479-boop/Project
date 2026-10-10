"""184: the installed rules' chapter on testing many combinations at once (lanes), and its commands.

The chapter's plan fields are the plan's own, its example plan is a valid plan of 2 × 4 runs, it says how the
lanes compute (what is shared, what is each run's own), and `cli.py lanes` works in a source-free installation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from releasekit import builder
from releasekit.runtime_bundle import build_code_bundle

KIT = Path(builder.__file__).resolve().parents[1]
RULES = Path(builder.__file__).with_name('agent_rules.md')
sys.path[:0] = [str(KIT.parent / 'Part3'), str(KIT.parent / 'Part2'), str(KIT.parent / 'Part1/program')]

from test_agent_rules174 import DRIVER, write  # noqa: E402


def chapter():
    return RULES.read_text('utf-8').split('## 6. 여러 조합 한 번에 시험하기', 1)[1].split('\n## 7.', 1)[0]


def test_the_chapter_names_the_plan_s_own_fields_and_its_example_is_a_plan_of_eight_runs():
    from lab import lanes
    text = chapter()
    table = text.split('### 계획 파일 칸', 1)[1].split('###', 1)[0]
    named = {name for row in table.splitlines() if row.startswith('| `') for name in re.findall(r'`([a-z_]+)`', row.split('|')[1])}
    assert named == set(lanes.KEYS)
    example = json.loads(text.split('```json', 1)[1].split('```', 1)[0])
    plan = lanes.normalize(example)
    assert plan['available_only'] is True and plan['spread_points'] > 0 and len(lanes.every_lane(plan)) == 8
    for action in ('plan', 'run', 'status', 'stop', 'report'):
        assert f'cli.py lanes {action} AI작업' in text
    for rule in ('녹화 읽기, STAFF 스냅샷', '엔진 판단', '결과 기록', '하나만 돌린 결과와 같다',
                 '트리거만 레인으로 묶고 전략은 하나씩 따로 돌렸다면 그렇게 적는다', '묶음 2/4'):
        assert rule in text
    rules = RULES.read_text('utf-8')
    assert '6장의 레인 명령' in rules and '8장의 복사만 예외' in rules and '## 9. 보고' in rules


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    """The real project's installed copy: data files, empty program folders and the code bundle only."""
    project = KIT.parent
    selected = list(builder.source_paths(project))
    root = tmp_path_factory.mktemp('i184') / 'MOSES'
    paths = builder.prepare_data(project, root, selected)
    build_code_bundle(project, root / 'runtime/code.bundle', paths=paths)
    assert 'Part3/lab/lanes.py' in paths and not (root / 'Part3/lab/lanes.py').exists()   # bundled code
    # The installer adds the MT5 programs; a data plan reads the EA's identity.
    for source in (project / 'Part1/program/MT5').glob('*.ex5'):
        target = root / 'Part1/program/MT5' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (root / 'AI작업').mkdir()
    return root


def run(root, steps):
    plan = root / 'AI작업' / 'steps.json'
    plan.write_text(json.dumps(steps, ensure_ascii=False), encoding='utf-8')
    environment = {**os.environ, 'PYTHONPATH': str(KIT), 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    environment.pop('PYTHONHOME', None)
    done = subprocess.run([sys.executable, '-B', '-c', DRIVER, str(root), str(plan)], cwd=root, env=environment,
                          capture_output=True, text=True, encoding='utf-8', timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])['results']


def test_the_lanes_commands_work_in_a_source_free_installation(installed):
    plan = {'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR', 'result_mode': 'ALERT_ONLY',
            'strategies': ['SPECIAL2'], 'triggers': ['올존', '무지성 올존'], 'spread_points': 20}
    write(installed, 'AI작업/레인.json', json.dumps(plan, ensure_ascii=False))
    write(installed, 'AI작업/틀린레인.json', json.dumps({**plan, 'strategy': 'SPECIAL2'}, ensure_ascii=False))
    cli = str(installed / 'Part3/cli.py')
    usage, preview, report, wrong = run(installed, [
        ['.', [cli, 'lanes', '--help']],
        ['.', [cli, 'lanes', 'plan', r'AI작업\레인.json', '--warehouse', '데이터창고']],
        ['.', [cli, 'lanes', 'report', r'AI작업\레인.json', '--warehouse', '데이터창고']],
        ['.', [cli, 'lanes', 'plan', r'AI작업\틀린레인.json', '--warehouse', '데이터창고']]])
    assert usage['code'] == 0 and all(action in usage['out'] for action in ('plan', 'run', 'status', 'stop', 'report'))
    assert preview['code'] == 0, preview['err']
    shown = json.loads(preview['out'])
    assert shown['runs'] == 2 and shown['to_run'] == 2 and shown['groups'] == 1 and shown['ready'] is False
    assert shown['lanes'] == [{'strategy': 'SPECIAL2', 'trigger': '올존'}, {'strategy': 'SPECIAL2', 'trigger': '무지성 올존'}]
    assert report['code'] == 0 and '끝난 실행 0/2' in report['out']
    assert (installed / 'AI작업/레인.report.md').read_text('utf-8') == report['out']
    assert wrong['code'] == 1 and '모르는 칸' in wrong['err'] and 'strategy' in wrong['err']
    assert not (installed / 'AI작업/레인.lanes.json').exists()             # nothing started
