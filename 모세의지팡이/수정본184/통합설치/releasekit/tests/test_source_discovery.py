from pathlib import Path

import pytest

from releasekit.source_discovery import discover_project_code


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_new_package_and_transitive_relative_module_follow_current_code(tmp_path):
    put(tmp_path, 'Part3/lab/view.py', 'from new_package import screen\n')
    put(tmp_path, 'Part2/new_package/__init__.py', 'from . import metadata\n')
    put(tmp_path, 'Part2/new_package/screen.py', 'from .child import VALUE\n')
    put(tmp_path, 'Part2/new_package/child.py', 'VALUE = 7\n')
    put(tmp_path, 'Part2/new_package/metadata.py', 'VALUE = 9\n')
    result = discover_project_code(tmp_path, {'Part3/lab/view.py'})
    assert {p.as_posix() for p in result} == {
        'Part3/lab/view.py','Part2/new_package/__init__.py','Part2/new_package/screen.py',
        'Part2/new_package/child.py','Part2/new_package/metadata.py'}


def test_namespace_and_literal_dynamic_import_are_included(tmp_path):
    put(tmp_path, 'START_MOSES.pyw', 'import newspace.part\nimportlib.import_module("new_feature")\n')
    put(tmp_path, 'Part2/newspace/part.py', 'VALUE=1\n')
    put(tmp_path, 'Part3/new_feature.py', 'VALUE=2\n')
    result = discover_project_code(tmp_path, {'START_MOSES.pyw'})
    assert Path('Part2/newspace/part.py') in result
    assert Path('Part3/new_feature.py') in result


def test_excluded_runtime_code_reference_stops_build(tmp_path):
    put(tmp_path, 'START_MOSES.pyw', 'import tests.helper\n')
    put(tmp_path, 'tests/helper.py', 'VALUE=1\n')
    with pytest.raises(ValueError, match='배포 제외'):
        discover_project_code(tmp_path, {'START_MOSES.pyw'}, excluded_parts={'tests'})


def test_stdlib_name_is_not_taken_from_nested_project_file(tmp_path):
    put(tmp_path, 'START_MOSES.pyw', 'import calendar\n')
    put(tmp_path, 'Part2/event_backtest/calendar.py', 'VALUE=1\n')
    assert discover_project_code(tmp_path, {'START_MOSES.pyw'}) == {Path('START_MOSES.pyw')}
