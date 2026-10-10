"""Part3 test library and independent Part1/Part2 strategy registrations.

Recipe definitions stay in the test library. Promotion changes registration
only; removing a host registration never deletes its definition.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
PARTS = ('Part1', 'Part2')
_lock = threading.RLock()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def test_directory(root=ROOT, *, for_write=False, part3_root=None):
    """Current location, with read compatibility for a pre-101 installation."""
    root = Path(root).resolve()
    owner = Path(part3_root).resolve() if part3_root is not None else root / 'Part3'
    current, legacy = owner / 'TEST_SPECIAL', owner / 'generated'
    for folder in (current, legacy):
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            raise ValueError('테스트 전략 폴더는 프로그램 내부의 일반 폴더여야 합니다.')
    if current.exists() and not current.is_dir():
        raise ValueError('테스트 전략 폴더를 확인하세요.')
    if for_write:
        # Upgrade old installed libraries once; IDs, timestamps and bytes stay intact.
        if not current.exists() and legacy.is_dir():
            legacy.replace(current)
        current.mkdir(parents=True, exist_ok=True)
        return current
    return current if current.is_dir() or not legacy.is_dir() else legacy


def _part(part):
    if part not in PARTS:
        raise ValueError('Part1 또는 Part2를 선택하세요.')
    return part


@contextmanager
def _transaction(paths):
    """Restore every affected user file if a multi-file operation fails."""
    originals = {Path(path): Path(path).read_bytes() if Path(path).exists() else None
                 for path in paths}
    try:
        yield
    except Exception:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.rollback')
                temporary.write_bytes(data)
                temporary.replace(path)
        _generated.cache_clear()
        raise


@lru_cache(maxsize=256)
def _generated(meta_path, signature):
    path = Path(meta_path)
    data = json.loads(path.read_text('utf-8-sig'))
    code = path.with_suffix('').with_suffix('.py')
    recipe = data.get('recipe', {})
    if recipe.get('schema_version') != 2:
        return None
    if (data.get('filename') != code.name or recipe.get('base') != 'AI'
            or not isinstance(recipe.get('strategy_intent'), dict)
            or hashlib.sha256(code.read_bytes()).hexdigest() != data.get('sha256')):
        raise ValueError('테스트 전략 파일과 설정이 일치하지 않습니다: ' + code.name)
    return {'id': code.stem.upper(), 'name': recipe.get('name', code.stem),
            'recipe': recipe, 'generated': True, 'filename': code.name,
            'created_at': data.get('created_at'), 'catalog_scope': data.get('catalog_scope'),
            'symbol_source': 'RECIPE' if recipe['strategy_intent'].get('symbols') else 'CONFIG',
            'time_filters': recipe['strategy_intent'].get('time_filters', []),
            'final_time_filters': recipe['strategy_intent'].get('final_time_filters', 0)}


def generated_entries(root=ROOT, errors=None):
    output = {}
    for path in sorted(test_directory(root).glob('Test_SPECIAL[0-9][0-9][0-9].recipe.json')):
        code = path.with_suffix('').with_suffix('.py')
        if not code.is_file():
            continue
        try:
            if path.is_symlink() or code.is_symlink():
                raise ValueError('테스트 전략은 일반 파일이어야 합니다: ' + code.name)
            a, b = path.stat(), code.stat()
            entry = _generated(str(path), (a.st_mtime_ns, a.st_size, b.st_mtime_ns, b.st_size))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            if errors is not None:
                errors.append(str(exc))
            continue
        if entry is not None:
            output[entry['id']] = deepcopy(entry)
    return output


def generated_errors(root=ROOT):
    errors = []
    generated_entries(root, errors)
    return errors


def visibility(root=ROOT):
    """Read-only migration: old shared visibility and registrations stay intact."""
    path = Path(root) / 'settings/strategy_visibility.json'
    data = json.loads(path.read_text('utf-8-sig')) if path.exists() else None
    if data is not None and not isinstance(data, dict):
        raise ValueError('전략 목록 설정을 확인하세요.')
    if data is None or data.get('schema_version') == 1:
        old_hidden = [] if data is None else data.get('hidden_builtins')
        if not isinstance(old_hidden, list) or any(not isinstance(x, str) for x in old_hidden):
            raise ValueError('전략 목록 설정을 확인하세요.')
        old = {key: list(PARTS) for key, entry in generated_entries(root).items()
               if entry.get('catalog_scope') != 'library'}
        return {'schema_version': 2, 'hidden_builtins': {part: list(old_hidden) for part in PARTS},
                'registrations': old}
    if data.get('schema_version') != 2 or not isinstance(data.get('hidden_builtins'), dict) \
            or not isinstance(data.get('registrations'), dict):
        raise ValueError('전략 목록 설정을 확인하세요.')
    if set(data['hidden_builtins']) != set(PARTS):
        raise ValueError('파트별 전략 목록 설정을 확인하세요.')
    for names in data['hidden_builtins'].values():
        if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
            raise ValueError('숨김 전략 목록을 확인하세요.')
    for name, parts in data['registrations'].items():
        if not isinstance(name, str) or not isinstance(parts, list) or set(parts) - set(PARTS):
            raise ValueError('승급 전략 등록을 확인하세요.')
    return deepcopy(data)


def hidden(root=ROOT, part=None):
    rows = visibility(root)['hidden_builtins']
    # None is the union of visible host lists: hidden in both hosts only.
    return set(rows[_part(part)]) if part is not None else set(rows['Part1']) & set(rows['Part2'])


def registrations(root=ROOT):
    return visibility(root)['registrations']


def _settings_path(part, root):
    return Path(root) / ('Part1/special_settings.json' if part == 'Part1' else 'Part2/backtest_ui.json')


def prune_settings(names, root=ROOT, part=None):
    for chosen_part in PARTS if part is None else (_part(part),):
        path = _settings_path(chosen_part, root)
        if not path.exists():
            continue
        data = json.loads(path.read_text('utf-8-sig'))
        if not isinstance(data.get('specials'), dict):
            raise ValueError('전략 설정 파일을 확인하세요: ' + str(path.name))
        data['specials'] = {name: row for name, row in data['specials'].items() if name.upper() not in names}
        write_json(path, data)


def _selection(names, available):
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
        raise ValueError('전략을 선택하세요.')
    chosen = set(names)
    if chosen - set(available):
        raise ValueError('현재 전략 목록을 새로고침하세요.')
    return chosen


def promote_selected(names, builtins, root=ROOT):
    with _lock:
        generated = generated_entries(root)
        chosen = _selection(names, generated)
        if chosen & set(builtins):
            raise ValueError('테스트 전략 ID가 기본 스페셜과 중복됩니다.')
        data = visibility(root)
        paths = [Path(root) / 'settings/strategy_visibility.json', *(_settings_path(p, root) for p in PARTS)]
        with _transaction(paths):
            for part in PARTS:
                added = {name for name in chosen if part not in data['registrations'].get(name, [])}
                if not added:
                    continue
                path = _settings_path(part, root)
                if path.exists():
                    saved = json.loads(path.read_text('utf-8-sig'))
                elif part == 'Part1':
                    # The installation's own strategies stay off, as when no file is saved (수정본167).
                    from .registry import user_strategies
                    own = user_strategies()
                    saved = {'version': 1, 'specials': {
                        key: {'enabled': key not in own, 'trigger': None, 'time_filters': None}
                        for key in builtins if key not in hidden(root, part)}}
                else:
                    saved = {'target_mode': 'SPECIAL', 'specials': {}, 'watch': {'text': '', 'chat_id': 'BACKTEST'}}
                if not isinstance(saved.get('specials'), dict):
                    raise ValueError('전략 설정 파일을 확인하세요: ' + path.name)
                for name in added:
                    # Stale settings cannot implicitly enable a freshly promoted strategy.
                    saved['specials'][name] = {'enabled': False, 'trigger': None, 'time_filters': None}
                    data['registrations'].setdefault(name, []).append(part)
                write_json(path, saved)
            write_json(paths[0], data)


def delete_selected(names, builtins, root=ROOT, *, part=None):
    """Remove host registrations only; Part3 test files always remain."""
    parts = PARTS if part is None else (_part(part),)
    with _lock:
        data = visibility(root)
        generated = generated_entries(root)
        available = {name for name in builtins if any(name not in data['hidden_builtins'][p] for p in parts)}
        available |= {name for name in generated if any(p in data['registrations'].get(name, []) for p in parts)}
        chosen = _selection(names, available)
        paths = [Path(root) / 'settings/strategy_visibility.json', *(_settings_path(p, root) for p in parts)]
        with _transaction(paths):
            for chosen_part in parts:
                prune_settings(chosen, root, chosen_part)
                data['hidden_builtins'][chosen_part] = sorted(set(data['hidden_builtins'][chosen_part]) | (chosen & set(builtins)))
                for name in chosen & set(generated):
                    data['registrations'][name] = [p for p in data['registrations'].get(name, []) if p != chosen_part]
            write_json(paths[0], data)


def reset_builtins(root=ROOT, *, part=None):
    """Restore this release's built-ins, retaining every library file."""
    parts = PARTS if part is None else (_part(part),)
    with _lock:
        data = visibility(root)
        paths = [Path(root) / 'settings/strategy_visibility.json', *(_settings_path(p, root) for p in parts)]
        with _transaction(paths):
            for chosen_part in parts:
                prune_settings(set(data['registrations']), root, chosen_part)
                data['hidden_builtins'][chosen_part] = []
                for name, registered in data['registrations'].items():
                    data['registrations'][name] = [p for p in registered if p != chosen_part]
            write_json(paths[0], data)


def library_items(root=ROOT):
    errors = []
    valid = generated_entries(root, errors)
    state = registrations(root)
    items = []
    for code in sorted(test_directory(root).glob('Test_SPECIAL[0-9][0-9][0-9].py')):
        if code.is_symlink():
            errors.append('테스트 전략은 일반 파일이어야 합니다: ' + code.name)
            continue
        name = code.stem.upper()
        entry = valid.get(name)
        item = {key: value for key, value in (entry or {}).items() if key in ('id', 'filename', 'name', 'created_at')}
        item.update(id=name, filename=code.name, valid=entry is not None,
                    registrations=[p for p in PARTS if p in state.get(name, [])])
        if entry is None:
            item['name'] = code.stem
            item['warning'] = '이전 형식 또는 손상된 전략입니다. 보기와 삭제만 가능합니다.'
        items.append(item)
    return {'items': items, 'errors': errors}


def rename_generated(identifier, title, root=ROOT):
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120 \
            or any(ord(c) < 32 for c in title):
        raise ValueError('전략 이름은 1~120자의 일반 문자를 입력하세요.')
    with _lock:
        entry = generated_entries(root).get(identifier)
        if entry is None:
            raise ValueError('이름을 변경할 테스트 전략을 확인하세요.')
        code = test_directory(root) / entry['filename']
        sidecar = code.with_suffix('.recipe.json')
        source = code.read_text('utf-8')
        tree = ast.parse(source)
        nodes = [node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'PART3_RECIPE' for t in node.targets)]
        if len(nodes) != 1 or ast.literal_eval(nodes[0]) != entry['recipe']:
            raise ValueError('전략 이름을 변경할 생성 파일 형식을 확인하세요.')
        recipe = deepcopy(entry['recipe'])
        recipe['name'] = title.strip()
        # Change the data literal only; the existing lowered plan/calculations stay byte-identical.
        node = nodes[0]
        lines = source.splitlines(keepends=True)
        begin = sum(len(s.encode('utf-8')) for s in lines[:node.lineno - 1]) + node.col_offset
        end = sum(len(s.encode('utf-8')) for s in lines[:node.end_lineno - 1]) + node.end_col_offset
        changed = (source.encode('utf-8')[:begin] + repr(recipe).encode('utf-8') + source.encode('utf-8')[end:]).decode('utf-8')
        compile(changed, code.name, 'exec')
        meta = json.loads(sidecar.read_text('utf-8-sig'))
        meta.update(recipe=recipe, sha256=hashlib.sha256(changed.encode('utf-8')).hexdigest())
        with _transaction((code, sidecar)):
            temporary = code.with_name(code.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(changed, encoding='utf-8', newline='\n')
                temporary.replace(code)
                write_json(sidecar, meta)
            finally:
                temporary.unlink(missing_ok=True)
        _generated.cache_clear()


def delete_library(names, root=ROOT, *, confirmed=False):
    with _lock:
        available = {item['id']: item for item in library_items(root)['items']}
        chosen = _selection(names, available)
        state = visibility(root)
        if any(available[name]['registrations'] for name in chosen) and confirmed is not True:
            raise ValueError('등록된 전략을 삭제하면 Part1·Part2·Part3에서 모두 제거되며 복구할 수 없습니다. 삭제를 확인하세요.')
        paths = [Path(root) / 'settings/strategy_visibility.json', *(_settings_path(p, root) for p in PARTS)]
        for name in chosen:
            code = test_directory(root) / available[name]['filename']
            paths.extend((code, code.with_suffix('.recipe.json')))
        with _transaction(paths):
            prune_settings(chosen, root)
            for name in chosen:
                state['registrations'].pop(name, None)
                code = test_directory(root) / available[name]['filename']
                code.unlink()
                code.with_suffix('.recipe.json').unlink(missing_ok=True)
            write_json(paths[0], state)
        _generated.cache_clear()
