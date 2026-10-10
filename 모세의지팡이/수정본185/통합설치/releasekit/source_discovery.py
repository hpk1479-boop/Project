"""Follow current project imports without executing application code."""
from __future__ import annotations

import ast
from pathlib import Path
import sys


SEARCH_ROOTS = ('', 'Part1', 'Part1/program', 'Part2', 'Part3')


def discover_project_code(root, selected, *, excluded_parts=()):
    root = Path(root).resolve()
    selected = {Path(value) for value in selected}
    pending = [p for p in selected if p.suffix.lower() in {'.py', '.pyw'}]
    visited = set()

    def add(path):
        if not path.is_file():
            return
        relative = path.relative_to(root)
        if any(part in excluded_parts or part.startswith('.') for part in relative.parts):
            raise ValueError('실행 코드가 배포 제외 폴더를 참조합니다: ' + relative.as_posix())
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('프로젝트 밖 코드 참조: ' + relative.as_posix())
        if relative not in selected:
            selected.add(relative)
            pending.append(relative)
        parent = path.parent
        while parent != root:
            init = parent / '__init__.py'
            if init.is_file() and init != path:
                init_rel = init.relative_to(root)
                if init_rel not in selected:
                    add(init)
            parent = parent.parent

    def resolve(name, bases):
        if not name or any(not piece.isidentifier() for piece in name.split('.')):
            return
        # A nested application calendar.py is not an absolute import of the
        # standard library calendar. Respect the interpreter's module names.
        if name.split('.')[0] in sys.stdlib_module_names and bases == absolute_bases:
            return
        for base in bases:
            stem = base.joinpath(*name.split('.'))
            package = stem / '__init__.py'
            if package.is_file():
                add(package)
                return
            for suffix in ('.py', '.pyw'):
                candidate = stem.with_suffix(suffix)
                if candidate.is_file():
                    add(candidate)
                    return
            # Namespace packages have no __init__.py. Resolving their imported
            # children below still finds the actual module files.

    absolute_bases = tuple(root / name for name in SEARCH_ROOTS)
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        tree = ast.parse((root / relative).read_text('utf-8-sig'), filename=relative.as_posix())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolve(alias.name, absolute_bases)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = (root / relative).parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    if not base.is_relative_to(root):
                        raise ValueError('프로젝트 밖 상대 import: ' + relative.as_posix())
                    bases = (base,)
                else:
                    bases = absolute_bases
                resolve(node.module or '', bases)
                for alias in node.names:
                    if alias.name != '*':
                        child = '.'.join(filter(None, (node.module, alias.name)))
                        resolve(child, bases)
            elif isinstance(node, ast.Call) and node.args:
                name = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else '')
                if name in {'__import__', 'import_module'} and isinstance(node.args[0], ast.Constant):
                    value = node.args[0].value
                    if isinstance(value, str) and not value.startswith('.'):
                        resolve(value, absolute_bases)
    return selected
