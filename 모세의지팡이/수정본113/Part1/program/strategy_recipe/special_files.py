"""Developer-maintained SPECIAL definitions, separate from the test library.

Recipe JSON or a literal PART3_RECIPE in a SPECIAL*.py file is accepted. The
saved source's imports and callbacks are never executed to read its definition.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

SPECIAL_DIRECTORY = 'Part1/program/SPECIAL'
ID = re.compile(r'SPECIAL[0-9]{1,3}')


def _read(path):
    if path.suffix.lower() == '.py':
        values = []
        for node in ast.parse(path.read_text('utf-8-sig'), filename=path.name).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(target, ast.Name) and target.id == 'PART3_RECIPE' for target in targets):
                try:
                    values.append(ast.literal_eval(node.value))
                except (TypeError, ValueError):
                    raise ValueError('스페셜 파일의 Recipe는 literal 데이터여야 합니다: ' + path.name) from None
        if len(values) != 1:
            raise ValueError('스페셜 파일에 PART3_RECIPE 선언이 하나 필요합니다: ' + path.name)
        recipe = values[0]
        name = path.stem.upper()
        if not isinstance(recipe, dict):
            raise ValueError('스페셜 Recipe 형식을 확인하세요: ' + path.name)
        return {'id': name, 'name': recipe.get('name', name), 'recipe': recipe,
                'symbol_source': 'RECIPE' if recipe.get('strategy_intent', {}).get('symbols') else 'CONFIG',
                'time_filters': recipe.get('strategy_intent', {}).get('time_filters', []),
                'final_time_filters': recipe.get('strategy_intent', {}).get('final_time_filters', 0)}
    return json.loads(path.read_text('utf-8-sig'))


@lru_cache(maxsize=4)
def _entries(folder_name, signature):
    folder = Path(folder_name)
    result = {}
    for filename, _, _ in signature:
        path = folder / filename
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('스페셜 파일은 일반 파일이어야 합니다: ' + path.name)
        entry = _read(path)
        stem = path.name.removesuffix('.recipe.json').removesuffix('.py').upper()
        if not ID.fullmatch(stem) or int(stem[7:]) == 0 or not isinstance(entry, dict):
            raise ValueError('스페셜 파일명은 SPECIAL1~SPECIAL999 형식이어야 합니다: ' + path.name)
        name = entry.get('id')
        recipe = entry.get('recipe', {})
        if (name != stem or name in result or not isinstance(recipe, dict) or recipe.get('schema_version') != 2
                or recipe.get('base') != 'AI' or not isinstance(recipe.get('strategy_intent'), dict)
                or not isinstance(entry.get('name'), str) or not entry['name'].strip()):
            raise ValueError('스페셜 파일의 이름·중복·Recipe 형식을 확인하세요: ' + path.name)
        from .contract import execution_plan
        meaning = deepcopy(recipe['strategy_intent'])
        # CONFIG presets acquire the real symbol list in load_plugins at startup.
        # Validate their structure without changing their saved definition.
        if entry.get('symbol_source') == 'CONFIG':
            meaning['symbols'] = ['EURUSD']
        execution_plan(meaning)
        result[name] = deepcopy(entry)
    return result


def read_special_entries(root):
    root = Path(root).resolve()
    folder = root / SPECIAL_DIRECTORY
    if not folder.is_dir():
        return None
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError('스페셜 폴더는 현재 수정본 안에 보관하세요.')
    paths = sorted([*folder.glob('SPECIAL*.recipe.json'), *folder.glob('SPECIAL*.py')],
                   key=lambda path: path.name.casefold())
    signature = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
    return deepcopy(_entries(str(folder), signature))
