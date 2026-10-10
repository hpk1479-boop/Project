"""Developer-maintained SPECIAL definitions, separate from the test library.

Recipe JSON or a literal PART3_RECIPE in a SPECIAL*.py file is accepted. The
saved source's imports and callbacks are never executed to read its definition.
A file that cannot be read or checked is left out with a one-line reason; the
other files still load (158). A problem with the folder itself still raises.

An installed MOSES keeps its strategies in the top folder 스페셜 (166): 기본 holds
the shipped ones, which an update replaces, and 내 전략 the ones made or received
after installation, which an update never touches. Each folder has its own
numbers, so the two never name the same strategy. The development project has
no 스페셜 folder and keeps reading SPECIAL_DIRECTORY.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

SPECIAL_DIRECTORY = 'Part1/program/SPECIAL'
INSTALLED_DIRECTORY = '스페셜'
INSTALLED_FOLDERS = (('기본', 1, 99), ('내 전략', 100, 999))
USER_FOLDER = INSTALLED_DIRECTORY + '/' + INSTALLED_FOLDERS[1][0]
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


def _stem(filename):
    return filename.removesuffix('.recipe.json').removesuffix('.py').upper()


def _entry(folder, path, execution_plan, numbers=None):
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('스페셜 파일은 일반 파일이어야 합니다: ' + path.name)
    stem = _stem(path.name)
    if numbers is not None and ID.fullmatch(stem) and not numbers[1] <= int(stem[7:]) <= numbers[2]:
        raise ValueError(f'{numbers[0]}은 SPECIAL{numbers[1]}~SPECIAL{numbers[2]}만 씁니다')
    entry = _read(path)
    if not ID.fullmatch(stem) or int(stem[7:]) == 0 or not isinstance(entry, dict):
        raise ValueError('스페셜 파일명은 SPECIAL1~SPECIAL999 형식이어야 합니다: ' + path.name)
    name = entry.get('id')
    recipe = entry.get('recipe', {})
    if (name != stem or not isinstance(recipe, dict) or recipe.get('schema_version') != 2
            or recipe.get('base') != 'AI' or not isinstance(recipe.get('strategy_intent'), dict)
            or not isinstance(entry.get('name'), str) or not entry['name'].strip()):
        raise ValueError('스페셜 파일의 이름·중복·Recipe 형식을 확인하세요: ' + path.name)
    meaning = deepcopy(recipe['strategy_intent'])
    # CONFIG presets acquire the real symbol list in load_plugins at startup.
    # Validate their structure without changing their saved definition.
    if entry.get('symbol_source') == 'CONFIG':
        meaning['symbols'] = ['EURUSD']
    execution_plan(meaning)
    return name, deepcopy(entry)


def _reason(exc, filename):
    """One line for the screen and Telegram; never a local path."""
    if isinstance(exc, json.JSONDecodeError):
        return f'JSON 형식 오류 {exc.lineno}행 {exc.colno}열'
    if isinstance(exc, SyntaxError):
        return f'파이썬 문법 오류 {exc.lineno}행'
    if isinstance(exc, UnicodeDecodeError):
        return 'UTF-8 파일이 아닙니다'
    if isinstance(exc, OSError):
        return '파일을 읽지 못했습니다'
    text = ' '.join(str(exc).split()).removesuffix(': ' + filename)
    if isinstance(exc, KeyError):
        text = 'Recipe 항목 누락 ' + text
    return (text or type(exc).__name__)[:200]


@lru_cache(maxsize=4)
def _entries(folder_name, signature, numbers=None):
    """(entries, skipped). A defective file is left out with its reason; the other files still load."""
    from .contract import execution_plan
    folder = Path(folder_name)
    result, skipped, claims = {}, [], {}
    for filename, _, _ in signature:
        claims.setdefault(_stem(filename), []).append(filename)
    for filename, _, _ in signature:
        twins = claims[_stem(filename)]
        if len(twins) > 1:
            # Which definition was meant is unknown, so none of them runs.
            skipped.append((filename, '같은 전략 번호 파일 중복 ' + ', '.join(twins)))
            continue
        try:
            name, entry = _entry(folder, folder / filename, execution_plan, numbers)
        except Exception as exc:
            skipped.append((filename, _reason(exc, filename)))
            continue
        result[name] = entry
    return result, tuple(skipped)


def _folders(root):
    """(folder, label, numbers) to read: the installed 스페셜 folders when present, else the development folder."""
    installed = root / INSTALLED_DIRECTORY
    if installed.is_dir():
        if installed.is_symlink() or not installed.resolve().is_relative_to(root):
            raise ValueError('스페셜 폴더는 MOSES 폴더 안에 두세요.')
        return [(installed / label, label, (label, low, high)) for label, low, high in INSTALLED_FOLDERS]
    folder = root / SPECIAL_DIRECTORY
    if not folder.is_dir():
        return None
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError('스페셜 폴더는 현재 수정본 안에 보관하세요.')
    return [(folder, None, None)]


def _folder_entries(root):
    """(entries, skipped rows (shown name, file name, reason), {ID: folder relative to root}), or None."""
    root = Path(root).resolve()
    folders = _folders(root)
    if folders is None:
        return None
    entries, skipped, sources = {}, [], {}
    for folder, label, numbers in folders:
        # An installed folder that was removed reads as empty; the other one still loads.
        if not folder.is_dir():
            continue
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            raise ValueError('스페셜 폴더는 MOSES 폴더 안에 두세요.')
        paths = sorted([*folder.glob('SPECIAL*.recipe.json'), *folder.glob('SPECIAL*.py')],
                       key=lambda path: path.name.casefold())
        signature = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
        found, left = _entries(str(folder), signature, numbers)
        entries.update(found)
        sources.update(dict.fromkeys(found, folder.relative_to(root).as_posix()))
        skipped.extend(((label + '/' if label else '') + filename, filename, reason) for filename, reason in left)
    return entries, tuple(skipped), sources


def read_special_entries(root):
    found = _folder_entries(root)
    return None if found is None else deepcopy(found[0])


def special_sources(root):
    """{strategy ID: the folder it was read from, relative to root}; None without a SPECIAL folder."""
    found = _folder_entries(root)
    return None if found is None else dict(found[2])


def skipped_special_files(root):
    """Files left out of read_special_entries: file, strategy ID (None for a bad file name), one-line notice."""
    found = _folder_entries(root)
    return [{'file': shown, 'id': _stem(filename) if ID.fullmatch(_stem(filename)) else None,
             'notice': shown + ' 제외 · ' + reason}
            for shown, filename, reason in (() if found is None else found[1])]
