"""Read-only Windows dependency discovery for opaque compiled project code."""
from __future__ import annotations

import ast
from collections import defaultdict
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder
from pathlib import Path
import sys


INTERNAL_NAMES = frozenset({
    'lab', 'ai_context', 'event_backtest', 'generic_backtest', 'data_warehouse',
    'part1_host', 'live_replay', 'pit', 'replay', 'calculations', 'event_engine',
    'oz_engine', 'strategy_recipe', 'moses_language',
})
AI_NAMES = frozenset({
    'torch', 'transformers', 'peft', 'accelerate', 'safetensors', 'llama_cpp',
    'ollama', 'lmformatenforcer',
})
TOOL_NAMES = frozenset({'pytest', 'pip', 'venv', 'setuptools'})
OPTIONAL_NAMES = AI_NAMES | TOOL_NAMES
AI_SOURCE_PREFIXES = ('common_ai/', 'Part3/lab/ai/')
AI_SOURCE_FILES = frozenset({'Part3/lab/ai_compiler.py'})
RUNTIME_IMPORTS = frozenset({
    'webview', 'webview.platforms.edgechromium', 'webview.platforms.winforms',
    'numpy', 'pandas', 'zmq', 'duckdb', 'MetaTrader5', 'jsonschema',
    'jsonschema_specifications', 'tzdata', 'requests', 'clr', 'pythonnet',
    'clr_loader',
})
SEARCH_BASES = ((), ('Part1',), ('Part1', 'program'), ('Part2',), ('Part3',))
PROBE_EXCLUDE_ROOTS = frozenset({'webview', 'clr', 'pythonnet', 'clr_loader'})
_UNKNOWN = object()


def _excluded_optional(name, reference):
    first = name.split('.')[0]
    if first in TOOL_NAMES:
        return True
    relative = reference.split(':', 1)[0]
    return first in AI_NAMES and (relative.startswith(AI_SOURCE_PREFIXES) or relative in AI_SOURCE_FILES)


def _find_spec(name, search_paths=None):
    """Resolve real modules without importing/executing their parent package."""
    loaded = sys.modules.get(name)
    if loaded is not None and getattr(loaded, '__spec__', None) is not None:
        return loaded.__spec__
    direct = BuiltinImporter.find_spec(name) or FrozenImporter.find_spec(name)
    if direct is not None:
        return direct
    paths = list(sys.path) if search_paths is None else search_paths
    for index in range(1, len(name.split('.')) + 1):
        prefix = '.'.join(name.split('.')[:index])
        spec = PathFinder.find_spec(prefix, paths)
        if spec is None:
            return None
        paths = spec.submodule_search_locations or []
    return spec


def _internal_names(paths):
    result = set(INTERNAL_NAMES)
    for path in paths:
        result.update((path.stem, path.parts[0]))
        if path.name == '__init__.py':
            result.add(path.parent.name)
        for base in SEARCH_BASES:
            if path.parts[:len(base)] == base and len(path.parts) > len(base) + 1:
                result.add(path.parts[len(base)])
    return result


def _qualified(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _qualified(node.value, aliases)
        return base + '.' + node.attr if base else ''
    return ''


def _windows_value(node, aliases):
    if isinstance(node, ast.Constant):
        return node.value
    qualified = _qualified(node, aliases)
    if qualified in {'os.name', 'sys.platform', 'typing.TYPE_CHECKING'}:
        return {'os.name': 'nt', 'sys.platform': 'win32', 'typing.TYPE_CHECKING': False}[qualified]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_windows_value(value, aliases) for value in node.elts]
        return values if all(value is not _UNKNOWN for value in values) else _UNKNOWN
    if isinstance(node, ast.Call) and not node.args and not node.keywords and _qualified(node.func, aliases) == 'platform.system':
        return 'Windows'
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and len(node.args) == 1 and not node.keywords and node.func.attr in {'startswith', 'endswith'}:
        left, right = _windows_value(node.func.value, aliases), _windows_value(node.args[0], aliases)
        if isinstance(left, str) and isinstance(right, str):
            return getattr(left, node.func.attr)(right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _windows_value(node.operand, aliases)
        return not value if value is not _UNKNOWN else _UNKNOWN
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = _windows_value(node.left, aliases), _windows_value(node.comparators[0], aliases)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        operator = node.ops[0]
        if isinstance(operator, ast.Eq): return left == right
        if isinstance(operator, ast.NotEq): return left != right
        if isinstance(operator, ast.In) and isinstance(right, (list, tuple, set, str)): return left in right
        if isinstance(operator, ast.NotIn) and isinstance(right, (list, tuple, set, str)): return left not in right
    if isinstance(node, ast.BoolOp):
        values = [_windows_value(value, aliases) for value in node.values]
        if isinstance(node.op, ast.And):
            if any(value is not _UNKNOWN and not value for value in values): return False
            if all(value is not _UNKNOWN for value in values): return True
        elif isinstance(node.op, ast.Or):
            if any(value is not _UNKNOWN and value for value in values): return True
            if all(value is not _UNKNOWN for value in values): return False
    return _UNKNOWN


class _Imports(ast.NodeVisitor):
    def __init__(self, relative, register, lookup, internal):
        self.relative, self.register, self.lookup, self.internal = relative, register, lookup, internal
        self.aliases = {}
        self.active = True
        self.optional_guard = False
        self.dynamic = []

    def add(self, name, node):
        self.register(name, self.relative + ':' + str(node.lineno), self.active, self.optional_guard)

    def visit_Import(self, node):
        for item in node.names:
            if self.active:
                self.aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
            self.add(item.name, node)

    def visit_ImportFrom(self, node):
        if node.level:
            self.add('.' * node.level + (node.module or ''), node)
            return
        if not node.module:
            return
        self.add(node.module, node)
        for item in node.names:
            if item.name == '*':
                continue
            if self.active:
                self.aliases[item.asname or item.name] = node.module + '.' + item.name
            # Exported classes/functions are not additional modules. Checking a
            # child's spec also avoids executing package __init__.py for discovery.
            if (self.active and node.module.split('.')[0] not in self.internal
                    and not _excluded_optional(node.module, self.relative)):
                child = node.module + '.' + item.name
                if self.lookup(child)[0] is not None:
                    self.add(child, node)

    def visit_If(self, node):
        self.visit(node.test)
        value = _windows_value(node.test, self.aliases)
        if value is not _UNKNOWN:
            value = bool(value)
        active = self.active
        self.active = active and value is not False
        for item in node.body: self.visit(item)
        self.active = active and value is not True
        for item in node.orelse: self.visit(item)
        self.active = active

    def visit_Try(self, node):
        prior = self.optional_guard
        catches_import = any(isinstance(part, ast.Name) and part.id in {'ImportError', 'ModuleNotFoundError'}
                             for handler in node.handlers if handler.type is not None for part in ast.walk(handler.type))
        self.optional_guard = prior or catches_import
        for item in node.body: self.visit(item)
        self.optional_guard = prior
        for item in node.handlers + node.orelse + node.finalbody: self.visit(item)

    def visit_Call(self, node):
        function = _qualified(node.func, self.aliases)
        if function in {'importlib.import_module', '__import__'}:
            literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
            self.dynamic.append({'reference': self.relative + ':' + str(node.lineno), 'module': literal,
                                 'active_windows_branch': self.active})
            if literal:
                self.add(literal, node)
        self.generic_visit(node)


def collect_dependencies(root, code_paths):
    """Return required/probe imports and explicit exclusions; never alter code."""
    root = Path(root).resolve()
    paths = sorted({Path(value) for value in code_paths})
    for path in paths:
        source = root / path
        if path.is_absolute() or '..' in path.parts or not source.resolve().is_relative_to(root) or source.suffix.lower() not in {'.py', '.pyw'}:
            raise ValueError('의존성 검사는 수정본 안의 상대 Python 경로만 허용합니다.')
    internal = _internal_names(paths)
    # App source folders must not accidentally satisfy a missing external
    # package. Installed build-environment site-packages remain searchable.
    search = [str(Path(value or '.').resolve()) for value in sys.path
              if not Path(value or '.').resolve().is_relative_to(root) or 'site-packages' in Path(value).parts]
    specs = {}

    def lookup(name):
        if name not in specs:
            try:
                specs[name] = (_find_spec(name, search), None)
            except (ImportError, ValueError, AttributeError, OSError) as error:
                specs[name] = (None, type(error).__name__)
        return specs[name]

    classified = {key: defaultdict(set) for key in ('required_imports', 'unavailable_required',
                  'excluded_optional', 'excluded_platform', 'excluded_internal')}
    reasons = {}

    def register(name, reference, active=True, optional=False):
        first = name.split('.')[0]
        if not active:
            category, reason = 'excluded_platform', 'inactive Windows/constant branch'
        elif name.startswith('.') or first in internal and first not in sys.stdlib_module_names:
            category, reason = 'excluded_internal', 'project module or existing legacy namespace'
        elif _excluded_optional(name, reference):
            category, reason = 'excluded_optional', ('excluded build-time dependency' if first in TOOL_NAMES
                                                     else 'AI dependency excluded in current AI source scope')
        else:
            spec, error = lookup(name)
            if spec is not None:
                category, reason = 'required_imports', None
            elif optional:
                category, reason = 'excluded_optional', 'explicit ImportError fallback'
            else:
                category, reason = 'unavailable_required', 'not installed' if error is None else 'spec lookup failed: ' + error
        classified[category][name].add(reference)
        if reason:
            reasons[category, name] = reason

    dynamic = []
    for path in paths:
        source = root / path
        tree = ast.parse(source.read_text(encoding='utf-8-sig'), filename=path.as_posix())
        visitor = _Imports(path.as_posix(), register, lookup, internal)
        visitor.visit(tree)
        dynamic.extend(visitor.dynamic)
    for name in sorted(RUNTIME_IMPORTS):
        register(name, '<distribution runtime>')
    required = sorted(classified['required_imports'])
    probes = [name for name in required if name.split('.')[0] not in PROBE_EXCLUDE_ROOTS]
    result = {'schema': 1, 'target_platform': 'win32', 'required_imports': required,
              'probe_imports': probes,
              'probe_excluded': [{'module': name, 'reason': 'GUI/managed runtime checked by separate UI probe'}
                                 for name in required if name not in probes],
              'import_references': {name: sorted(classified['required_imports'][name]) for name in required},
              'dynamic_imports': sorted(dynamic, key=lambda item: (item['reference'], item['module'] or ''))}
    for category in ('unavailable_required', 'excluded_optional', 'excluded_platform', 'excluded_internal'):
        result[category] = [{'module': name, 'references': sorted(references), 'reason': reasons[category, name]}
                            for name, references in sorted(classified[category].items())]
    return result


def build_hidden_imports(root, code_paths):
    """Fail before freeze when a required import cannot be found."""
    result = collect_dependencies(root, code_paths)
    if result['unavailable_required']:
        entries = [item['module'] + ' (' + ', '.join(item['references']) + ')' for item in result['unavailable_required']]
        raise ValueError('필수 배포 의존성이 없습니다: ' + '; '.join(entries))
    return result['required_imports']
