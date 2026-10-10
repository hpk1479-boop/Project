"""174: an installation carries the rules for an AI working in it, and the commands they name work there.

The installed MOSES has no sources. Claude Code reads CLAUDE.md and Codex reads AGENTS.md at the
installation root; both come from one source (agent_rules.md). The rules send the AI to the Part3
CLI to make and test strategies, and to the Part2 CLI for the existing ones: those commands are run
here from the real project's source-free installed copy (code bundle only), as python.exe runs them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from releasekit import builder, manuals
from releasekit.runtime_bundle import build_code_bundle

KIT = Path(builder.__file__).resolve().parents[1]
RULES = Path(builder.__file__).with_name('agent_rules.md')

# Runs CLI steps in one fresh interpreter with only the installed copy's bundle: as python.exe, without license gate.
DRIVER = r'''
import contextlib, io, json, os, sys
from pathlib import Path
from releasekit.runtime_bundle import RuntimeBundle
from releasekit import runtime_entry
root = Path(sys.argv[1])
bundle = RuntimeBundle(root).install()
results = []
for folder, arguments in json.loads(Path(sys.argv[2]).read_text('utf-8')):
    os.chdir(root / folder)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = runtime_entry.dispatch(arguments, bundle)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    results.append({'code': code, 'out': out.getvalue(), 'err': err.getvalue()})
modules = sorted({str(Path(m.__file__).relative_to(root)) for m in list(sys.modules.values())
                  if getattr(m, '__file__', None) and Path(m.__file__).is_relative_to(root)})
print(json.dumps({'results': results, 'modules': modules}, ensure_ascii=False))
'''


def write(root, relative, content):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return target


def test_the_release_places_one_rules_source_at_the_root_and_never_the_developers(tmp_path):
    project = tmp_path / 'synthetic174'
    for directory in builder.TREES:
        (project / directory).mkdir(parents=True, exist_ok=True)
    for relative in builder.FILES:
        write(project, relative, '%PDF-1.7\n% fixture\n%%EOF\n' if relative in manuals.MANUALS else
              'VALUE = 1\n' if Path(relative).suffix in ('.py', '.pyw') else '{}')
    write(project, 'settings/strategy_registry.json', json.dumps({'schema_version': 2, 'presets': []}))
    write(project, 'CLAUDE.md', '# development rules')                  # the developer's own files
    write(project, 'AGENTS.md', '# development rules')
    write(project, 'Part3/AGENTS.md', '# Part3 development rules')
    payload = tmp_path / 'payload'
    builder.prepare_data(project, payload, list(builder.source_paths(project)))
    shipped = sorted(path.relative_to(payload).as_posix() for path in payload.rglob('*')
                     if path.name in builder.AGENT_RULES)
    assert shipped == ['AGENTS.md', 'CLAUDE.md']
    for name in builder.AGENT_RULES:
        assert (payload / name).read_bytes() == RULES.read_bytes()


def test_the_rules_keep_the_deployed_boundaries():
    text = RULES.read_text('utf-8')
    for rule in ('`--yes`, `job confirm`을 쓰지 않는다', '`Part3\\cli.py generate`로만 만든다',
                 '`--available-only`', '지우거나 옮기거나 고치지 않는다', '실시간 시작·정지도 하지 않는다',
                 '상대 경로만 쓴다', '한국어로 쓴다', '`스페셜/내 전략/SPECIAL105.py`로 **복사**',
                 '브로커 서버 날짜', '끝 날짜는 포함하지 않는다'):
        assert rule in text
    assert '기간은 UTC 날짜' not in text
    assert not re.search(r'[A-Za-z]:\\|Users\\|hpk', text)             # no machine paths in the shipped rules


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    """The real project's installed copy: data files, empty program folders and the code bundle only."""
    project = KIT.parent
    selected = list(builder.source_paths(project))
    root = tmp_path_factory.mktemp('i174') / 'MOSES'
    paths = builder.prepare_data(project, root, selected)
    build_code_bundle(project, root / 'runtime/code.bundle', paths=paths)
    assert 'Part3/cli.py' in paths and not (root / 'Part3/cli.py').exists()   # the CLI itself is bundled code
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
    data = json.loads(done.stdout.strip().splitlines()[-1])
    assert all(not name.endswith('.py') or not (root / name).is_file() for name in data['modules'])
    return data['results']


def test_the_part3_commands_of_the_rules_work_in_a_source_free_installation(installed):
    symbols = run(installed, [['.', ['-c', 'import sys, json; from pathlib import Path; '
                                     'sys.path[:0] = [str(Path.cwd() / "Part3")]; from lab import catalog; '
                                     'print(json.dumps(list(catalog.current_symbols())))']]])[0]
    assert symbols['code'] == 0, symbols['err']
    chosen = json.loads(symbols['out'].strip().splitlines()[-1])[:1]
    recipe = {'schema_version': 2, 'base': 'AI', 'name': '규칙 시험 전략', 'description': '174 설치본 CLI 시험',
              'symbols': chosen, 'strategy_intent': {'direction': 'LONG', 'symbols': chosen,
                  'order_mode': 'SIMULTANEOUS', 'steps': [{'kind': 'MA_PRICE_TOUCH', 'tfs': ['5m'],
                      'ma_family': 'EMA', 'slow_period': 50}],
                  'final': {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}}
    write(installed, 'AI작업/초안.json', json.dumps({'recipe': recipe}, ensure_ascii=False))
    broken = {**recipe, 'strategy_intent': {**recipe['strategy_intent'], 'steps': [{'kind': 'NO_SUCH_KIND', 'tfs': ['5m']}]}}
    write(installed, 'AI작업/틀린초안.json', json.dumps({'recipe': broken}, ensure_ascii=False))
    cli = str(installed / 'Part3/cli.py')
    preview, wrong = run(installed, [['.', [cli, 'preview', '--recipe', r'AI작업\초안.json']],
                                     ['.', [cli, 'preview', '--recipe', r'AI작업\틀린초안.json']]])
    assert preview['code'] == 0 and 'PART3_RECIPE' in preview['out']
    assert wrong['code'] == 1 and '오류' in wrong['err']
    assert not list((installed / 'Part3/TEST_SPECIAL').glob('*.py'))    # checking writes nothing
    generate, listed, usage, frame = run(installed, [
        ['.', [cli, 'generate', '--recipe', r'AI작업\초안.json']],
        ['.', [cli, 'list']],
        ['.', [cli, 'backtest', '--help']],
        ['.', [cli, 'backtest', 'Test_SPECIAL001.py', '--start', '2026-01-01', '--end', '2026-02-01',
               '--result-mode', 'VIRTUAL_ENTRY', '--base-frame', '7m']],
    ])
    assert generate['code'] == 0 and generate['out'].strip().endswith('Test_SPECIAL001.py')
    library = installed / 'Part3/TEST_SPECIAL'
    assert sorted(path.name for path in library.iterdir()) == ['Test_SPECIAL001.py', 'Test_SPECIAL001.recipe.json']
    assert [row['filename'] for row in json.loads(listed['out'])] == ['Test_SPECIAL001.py']
    # Every backtest option the rules name is the installed CLI's own.
    named = set(re.findall(r'`(--[a-z-]+)', RULES.read_text('utf-8').split('## 4.', 1)[1].split('## 5.', 1)[0]))
    assert named >= {'--available-only', '--result-mode', '--virtual-entry', '--base-frame', '--spread-points'}
    assert usage['code'] == 0 and named <= set(re.findall(r'--[a-z-]+', usage['out']))
    # The base frame reads the generated recipe through Part2's own rule, before any job or warehouse.
    assert frame['code'] == 1 and '기준 프레임은' in frame['err']
    default = installed.parent.parent / (installed.parent.name + '_warehouse')   # Part2's own fallback location
    assert not (installed / 'runs').exists() and not default.exists()


def test_the_part2_scenario_of_the_rules_is_accepted_in_a_source_free_installation(installed):
    example = RULES.read_text('utf-8').split('## 5.', 1)[1].split('```json', 1)[1].split('```', 1)[0]
    scenario = json.loads(example)
    assert scenario['available_only'] is True
    write(installed, 'AI작업/s1.json', json.dumps(scenario, ensure_ascii=False))
    warehouse = installed / '데이터창고'
    result, = run(installed, [['Part2', ['-m', 'event_backtest', 'strategies', '--scenario', r'..\AI작업\s1.json',
                                         '--warehouse', r'..\데이터창고']]])
    assert result['code'] == 0, result['out'][-2000:] + result['err'][-2000:]
    event = json.loads(result['out'].strip().splitlines()[-1])
    assert event['event'] == 'COMPLETE' and scenario['strategies'][0] in event['result']['strategies']
    assert not warehouse.exists()                                        # listing strategies writes nothing
