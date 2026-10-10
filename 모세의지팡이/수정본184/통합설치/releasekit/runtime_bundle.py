"""Run an unchanged project from compiled code without distributing its sources.

The archive records relative project paths.  Filesystem shims apply only to
those paths; settings, market data and user-generated Python remain real files.
Code is executed with its original virtual filename and a fresh namespace when
the original application asks for a fresh path-based module.
"""
from __future__ import annotations

import ast
import builtins
import fnmatch
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import marshal
import operator
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import stat
import sys
import threading
import types
import zipfile

FORMAT_VERSION = 1
DEFAULT_EXCLUDE_PARTS = frozenset({
    '__pycache__', '.git', '.pytest_cache', 'tests', 'verification', '검증결과',
    '배포', 'reference', 'logs', 'runtime', 'event_state', 'generated',
    'audit', 'validation_suite', 'conditional_validation',
    'cadence_input_validation', 'watch_ma_validation', 'staff_golden',
})
_MARKER = '# MOSES-COMPILED-PATH: '
_ACTIVE = None
_INSTALL_LOCK = threading.RLock()
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv}


def _relative(value):
    text = str(value).replace('\\', '/')
    path = PurePosixPath(text)
    if (not text or path.is_absolute() or ':' in text or
            any(piece in ('', '.', '..') for piece in text.split('/'))):
        raise ValueError('Bundle paths must remain relative to the project root.')
    return path.as_posix()


def _path_key(value):
    return os.path.normcase(str(value)).replace('\\', '/')


def _literal(node, names):
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_literal(node.left, names), _literal(node.right, names))
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_literal(item, names) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {_literal(key, names): _literal(value, names)
                for key, value in zip(node.keys, node.values)}
    return ast.literal_eval(node)


def literal_contract(source):
    """Match the existing source_edit.constants contract without importing it."""
    names = {}
    for node in ast.parse(source).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and
                isinstance(node.targets[0], ast.Name)):
            try:
                names[node.targets[0].id] = _literal(node.value, names)
            except (ValueError, TypeError, KeyError, ZeroDivisionError):
                pass
    return names


def _reference_metadata(root):
    """Verify immutable MQL references now; distribute hashes, never MQL text."""
    actual = {}
    for folder in ('Part2/generic_backtest/reference_sources',
                   'Part2/calculations/reference_sources'):
        for path in sorted((root / folder).glob('*.mq5')):
            if path.is_file():
                actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    profiles = root / 'Part2/calculations/percentile/profiles.py'
    if profiles.is_file():
        expected = literal_contract(profiles.read_text('utf-8-sig')).get('SOURCE_HASHES', {})
        for family, digest in expected.items():
            relative = 'Part2/generic_backtest/reference_sources/' + family + '_of_Moses.mq5'
            if actual.get(relative) != digest:
                raise ValueError('E_SOURCE_DRIFT: ' + relative)
    # Existing HMA contract/reference drift is diagnostic when that legacy
    # verifier is called. Keep actual hashes, so its original comparison still
    # rejects the same input; do not turn it into a new application build gate.
    return actual


def build_code_bundle(source_root, output, *, paths=None,
                      exclude_parts=DEFAULT_EXCLUDE_PARTS):
    """Read sources and write compiled code plus literal-only contract metadata.

    ``paths`` is an optional explicit iterable of relative .py/.pyw filenames.
    Neither source files nor their directories are written by this function.
    """
    root = Path(source_root).resolve()
    output = Path(output)
    if paths is None:
        paths = [path.relative_to(root).as_posix()
                 for path in root.rglob('*')
                 if path.suffix.lower() in ('.py', '.pyw') and path.is_file()
                 and not any(piece in exclude_parts or piece.startswith('.venv')
                             for piece in path.relative_to(root).parts)]
    paths = sorted({_relative(path) for path in paths})
    references = _reference_metadata(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = {}
    temporary = output.with_name(output.name + '.partial')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for relative in paths:
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or path.suffix.lower() not in ('.py', '.pyw'):
                    raise ValueError('Only in-project Python sources may be compiled.')
                raw = path.read_bytes()
                # compile(bytes) respects UTF-8 BOM and Python encoding cookies.
                code = compile(raw, relative, 'exec', dont_inherit=True)
                encoding, _ = __import__('tokenize').detect_encoding(io.BytesIO(raw).readline)
                source = raw.decode(encoding)
                literal = literal_contract(source)
                facade = _MARKER + relative + '\n' + ''.join(
                    name + ' = ' + repr(value) + '\n' for name, value in literal.items())
                blob = marshal.dumps(code)
                member = 'code/' + relative + '.bin'
                archive.writestr(member, blob)
                entries[relative] = {'member': member, 'sha256': hashlib.sha256(blob).hexdigest(),
                                     'size': len(blob), 'facade': facade}
            manifest = {'format': FORMAT_VERSION, 'python': list(sys.version_info[:2]),
                        'magic': importlib.util.MAGIC_NUMBER.hex(), 'entries': entries,
                        'reference_sha256': references}
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False,
                                                        separators=(',', ':')))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def _glob_match(parts, pattern):
    if not pattern:
        return not parts
    if pattern[0] == '**':
        return (_glob_match(parts, pattern[1:]) or
                bool(parts) and _glob_match(parts[1:], pattern))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], pattern[0]) and _glob_match(parts[1:], pattern[1:])


def _relocate_code(code, filename):
    constants = tuple(_relocate_code(item, filename) if isinstance(item, types.CodeType)
                      else item for item in code.co_consts)
    return code.replace(co_filename=str(filename), co_consts=constants)


class _BundleLoader(importlib.abc.SourceLoader):
    def __init__(self, bundle, filename):
        self.bundle, self.filename = bundle, str(filename)

    def get_filename(self, fullname):
        return self.filename

    def get_data(self, path):
        relative = self.bundle.relative(path)
        if relative is None:
            return self.bundle.originals['read_bytes'](Path(path))
        return self.bundle.facade(relative).encode('utf-8')

    def get_code(self, fullname):
        return self.bundle.code(self.bundle.relative(self.filename))

    def exec_module(self, module):
        exec(self.get_code(module.__name__), module.__dict__)
        self.bundle.adapt(module)


class _BundleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, bundle):
        self.bundle = bundle

    def find_spec(self, fullname, path=None, target=None):
        leaf = fullname.rsplit('.', 1)[-1]
        for base in (sys.path if path is None else path):
            if not isinstance(base, (str, bytes, os.PathLike)):
                continue
            base = Path(os.fsdecode(base) or os.getcwd())
            for candidate, package in ((base / leaf / '__init__.py', True),
                                       (base / (leaf + '.py'), False),
                                       (base / (leaf + '.pyw'), False)):
                if self.bundle.relative(candidate) is not None:
                    loader = _BundleLoader(self.bundle, candidate.resolve())
                    return importlib.util.spec_from_file_location(fullname, candidate.resolve(),
                                loader=loader, submodule_search_locations=[str(candidate.parent.resolve())]
                                if package else None)
            directory = base / leaf
            if self.bundle.directory(directory):
                spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
                spec.submodule_search_locations = [str(directory.resolve())]
                return spec
        return None


class RuntimeBundle:
    def __init__(self, root, archive=None):
        self.root = Path(root).resolve()
        self.archive_path = Path(archive or self.root / 'runtime/code.bundle')
        self.archive = zipfile.ZipFile(self.archive_path)
        manifest = json.loads(self.archive.read('manifest.json'))
        if (manifest.get('format') != FORMAT_VERSION or
                manifest.get('python') != list(sys.version_info[:2]) or
                manifest.get('magic') != importlib.util.MAGIC_NUMBER.hex()):
            self.archive.close()
            raise ValueError('Compiled runtime and code bundle versions do not match.')
        self.entries = manifest['entries']
        self.reference_sha256 = manifest.get('reference_sha256', {})
        for relative, digest in self.reference_sha256.items():
            if (_relative(relative) != relative or not relative.endswith('.mq5') or
                    not isinstance(digest, str) or len(digest) != 64 or
                    any(char not in '0123456789abcdef' for char in digest)):
                self.archive.close()
                raise ValueError('Invalid reference source hash metadata.')
        self.lookup = {}
        self.directories = {''}
        for relative, info in self.entries.items():
            if (_relative(relative) != relative or
                    info.get('member') != 'code/' + relative + '.bin' or
                    not isinstance(info.get('facade'), str) or
                    not info['facade'].startswith(_MARKER + relative + '\n')):
                self.archive.close()
                raise ValueError('Invalid compiled code bundle entry.')
            key = _path_key(relative)
            if key in self.lookup:
                raise ValueError('Duplicate compiled code path.')
            self.lookup[key] = relative
            current = PurePosixPath(relative).parent
            while current.as_posix() != '.':
                self.directories.add(current.as_posix())
                current = current.parent
        self.directory_keys = {_path_key(path) for path in self.directories}
        self.raw_cache = {}
        self.code_cache = {}
        self.originals = {}
        self.finder = _BundleFinder(self)
        self.lock = threading.RLock()
        self.installed = False

    def relative(self, path):
        try:
            path = Path(path).absolute()
            relative = path.relative_to(self.root).as_posix()
            if '..' in PurePosixPath(relative).parts:
                return None
            return self.lookup.get(_path_key(relative))
        except (ValueError, TypeError, OSError):
            return None

    def directory(self, path):
        try:
            relative = Path(path).absolute().relative_to(self.root).as_posix()
            return _path_key('' if relative == '.' else relative) in self.directory_keys
        except (ValueError, TypeError, OSError):
            return False

    def raw(self, relative):
        with self.lock:
            if relative not in self.raw_cache:
                info = self.entries[relative]
                raw = self.archive.read(info['member'])
                if len(raw) != info['size'] or hashlib.sha256(raw).hexdigest() != info['sha256']:
                    raise ValueError('Compiled code integrity check failed: ' + relative)
                self.raw_cache[relative] = raw
            return self.raw_cache[relative]

    def code(self, relative):
        with self.lock:
            if relative not in self.code_cache:
                code = marshal.loads(self.raw(relative))
                if not isinstance(code, types.CodeType):
                    raise ValueError('Invalid compiled module: ' + relative)
                self.code_cache[relative] = _relocate_code(code, self.root / relative)
            return self.code_cache[relative]

    def facade(self, relative):
        return self.entries[relative]['facade']

    def run_path(self, path, init_globals=None, run_name=None):
        relative = self.relative(path)
        if relative is None:
            return self.originals['run_path'](path, init_globals=init_globals, run_name=run_name)
        # The standard helper preserves temporary sys.modules and sys.argv[0]
        # behavior, including dataclass lookups during path-based execution.
        result = runpy._run_module_code(self.code(relative), init_globals,
                                       mod_name=run_name or '<run_path>',
                                       script_name=str(self.root / relative))
        return result

    def _virtual_paths(self, base, pattern, *, recursive=False):
        try:
            prefix = Path(base).absolute().relative_to(self.root).as_posix()
        except ValueError:
            return
        prefix = '' if prefix == '.' else prefix.rstrip('/') + '/'
        pieces = str(pattern).replace('\\', '/').split('/')
        if any(piece in ('', '.', '..') for piece in pieces):
            return
        if recursive:
            pieces.insert(0, '**')
        if os.name == 'nt':
            pieces = [piece.casefold() for piece in pieces]
        for relative in (*self.entries, *self.directories):
            if not relative or not _path_key(relative).startswith(_path_key(prefix)):
                continue
            suffix = relative[len(prefix):]
            candidate = suffix.casefold() if os.name == 'nt' else suffix
            if _glob_match(candidate.split('/'), pieces):
                yield self.root / relative

    def install(self):
        global _ACTIVE
        with _INSTALL_LOCK:
            if self.installed:
                return self
            if _ACTIVE is not None:
                raise RuntimeError('Only one compiled project bundle may be active per process.')
            names = ('exists', 'is_file', 'is_dir', 'read_bytes', 'read_text', 'open',
                     'stat', 'iterdir', 'glob', 'rglob')
            self.originals = {name: getattr(Path, name) for name in names}
            self.originals.update(compile=builtins.compile, builtin_open=builtins.open,
                                  run_path=runpy.run_path,
                                  source_get_code=importlib.machinery.SourceFileLoader.get_code,
                                  source_exec_module=importlib.machinery.SourceFileLoader.exec_module)
            originals, bundle = self.originals, self

            def exists(path, *args, **kwargs):
                return bool(bundle.relative(path) is not None or bundle.directory(path) or
                            originals['exists'](path, *args, **kwargs))

            def is_file(path, *args, **kwargs):
                return bool(bundle.relative(path) is not None or originals['is_file'](path, *args, **kwargs))

            def is_dir(path, *args, **kwargs):
                return bool(bundle.directory(path) or originals['is_dir'](path, *args, **kwargs))

            def read_bytes(path, *args, **kwargs):
                relative = bundle.relative(path)
                return bundle.raw(relative) if relative is not None else originals['read_bytes'](path, *args, **kwargs)

            def read_text(path, *args, **kwargs):
                relative = bundle.relative(path)
                return bundle.facade(relative) if relative is not None else originals['read_text'](path, *args, **kwargs)

            def virtual_open(file, mode='r', *args, **kwargs):
                relative = bundle.relative(file)
                if relative is None:
                    return originals['builtin_open'](file, mode, *args, **kwargs)
                if any(option in mode for option in ('w', 'a', 'x', '+')):
                    raise PermissionError('Compiled application sources are read-only.')
                return io.BytesIO(bundle.raw(relative)) if 'b' in mode else io.StringIO(bundle.facade(relative))

            def path_open(path, mode='r', *args, **kwargs):
                if bundle.relative(path) is not None:
                    return virtual_open(path, mode, *args, **kwargs)
                return originals['open'](path, mode, *args, **kwargs)

            def path_stat(path, *args, **kwargs):
                relative = bundle.relative(path)
                if relative is not None:
                    return os.stat_result((stat.S_IFREG | 0o444, 0, 0, 1, 0, 0,
                                           bundle.entries[relative]['size'], 0, 0, 0))
                if bundle.directory(path):
                    try:
                        return originals['stat'](path, *args, **kwargs)
                    except FileNotFoundError:
                        return os.stat_result((stat.S_IFDIR | 0o555, 0, 0, 1, 0, 0, 0, 0, 0, 0))
                return originals['stat'](path, *args, **kwargs)

            def merge_paths(actual, virtual):
                seen = set()
                for path in (*actual, *virtual):
                    key = os.path.normcase(str(path.absolute()))
                    if key not in seen:
                        seen.add(key)
                        yield path

            def iterdir(path):
                if not bundle.directory(path):
                    yield from originals['iterdir'](path)
                    return
                actual = list(originals['iterdir'](path)) if os.path.isdir(path) else []
                yield from merge_paths(actual, bundle._virtual_paths(path, '*'))

            def glob(path, pattern, *args, **kwargs):
                yield from merge_paths(originals['glob'](path, pattern, *args, **kwargs),
                                       bundle._virtual_paths(path, pattern))

            def rglob(path, pattern, *args, **kwargs):
                yield from merge_paths(originals['rglob'](path, pattern, *args, **kwargs),
                                       bundle._virtual_paths(path, pattern, recursive=True))

            def compiled(source, filename, mode, flags=0, dont_inherit=False, optimize=-1, **kwargs):
                if (mode == 'exec' and not flags & ast.PyCF_ONLY_AST and isinstance(source, str)
                        and source.startswith(_MARKER)):
                    relative = source.split('\n', 1)[0][len(_MARKER):]
                    if relative in bundle.entries and source == bundle.facade(relative):
                        return bundle.code(relative)
                return originals['compile'](source, filename, mode, flags, dont_inherit, optimize, **kwargs)

            def source_get_code(loader, fullname):
                relative = bundle.relative(loader.path)
                if relative is not None:
                    return bundle.code(relative)
                return originals['source_get_code'](loader, fullname)

            def source_exec_module(loader, module):
                result = originals['source_exec_module'](loader, module)
                if bundle.relative(loader.path) is not None:
                    bundle.adapt(module)
                return result

            replacements = dict(exists=exists, is_file=is_file, is_dir=is_dir,
                                read_bytes=read_bytes, read_text=read_text, open=path_open,
                                stat=path_stat, iterdir=iterdir, glob=glob, rglob=rglob)
            for name, function in replacements.items():
                setattr(Path, name, function)
            builtins.compile = compiled
            builtins.open = virtual_open
            runpy.run_path = self.run_path
            importlib.machinery.SourceFileLoader.get_code = source_get_code
            importlib.machinery.SourceFileLoader.exec_module = source_exec_module
            sys.meta_path.insert(0, self.finder)
            _ACTIVE, self.installed = self, True
            return self

    def uninstall(self):
        global _ACTIVE
        with _INSTALL_LOCK:
            if not self.installed:
                self.archive.close()
                return
            for name in ('exists', 'is_file', 'is_dir', 'read_bytes', 'read_text', 'open',
                         'stat', 'iterdir', 'glob', 'rglob'):
                setattr(Path, name, self.originals[name])
            builtins.compile = self.originals['compile']
            builtins.open = self.originals['builtin_open']
            runpy.run_path = self.originals['run_path']
            importlib.machinery.SourceFileLoader.get_code = self.originals['source_get_code']
            importlib.machinery.SourceFileLoader.exec_module = self.originals['source_exec_module']
            if self.finder in sys.meta_path:
                sys.meta_path.remove(self.finder)
            self.installed, _ACTIVE = False, None
            self.archive.close()

    def adapt(self, module):
        """Packaging-only adapters; no market or strategy implementation changes."""
        relative = self.relative(getattr(module, '__file__', ''))
        if relative == 'Part2/generic_backtest/native_mt5.py':
            module.ensure_native_mql_current = self._ensure_native_ex5
        elif relative == 'Part2/event_backtest/recording.py':
            def source_hash():
                folder = self.root / 'Part1/program/MT5'
                return module.digest({name: module.file_hash(folder / name)
                                      for name in self._ex5_names()})
            module.source_hash = source_hash
            def deployment_targets(profile):
                root = Path(profile['data_root']) / 'MQL5'
                return [root / ('Experts' if name == self._ex5_names()[-1] else 'Indicators') / name
                        for name in self._ex5_names()]
            # The source-free package installs only these files. Keep the
            # original durable backup/restore context manager, with its exact
            # target mapping now limited to the five files it actually writes.
            module.deployment_targets = deployment_targets
        elif relative == 'Part2/calculations/percentile/profiles.py':
            def verify_source_hashes(project_root):
                actual = {}
                for family, expected in module.SOURCE_HASHES.items():
                    path = Path(project_root) / 'generic_backtest/reference_sources' / (family + '_of_Moses.mq5')
                    observed = self.reference_hash(path)
                    if observed is None or observed != expected:
                        raise module.PercentileError('E_SOURCE_DRIFT', str(path))
                    actual[family] = observed
                return actual
            module.verify_source_hashes = verify_source_hashes
        elif relative in ('Part2/pit/contracts.py', 'Part2/calculations/support/pit_contracts.py',
                           'Part2/generic_backtest/canonical.py'):
            original = module.file_hash
            def file_hash(path):
                reference = self.reference_hash(path)
                return reference if reference is not None else original(path)
            module.file_hash = file_hash

    def reference_hash(self, path):
        try:
            relative = Path(path).absolute().relative_to(self.root).as_posix()
        except (ValueError, TypeError, OSError):
            return None
        # Metadata cannot silently bless a replaced physical reference file.
        # If a user has a real file at this path, use its actual hash.
        key = next((key for key in self.reference_sha256
                    if _path_key(key) == _path_key(relative)), None)
        if key is None:
            return None
        if os.path.isfile(path):
            with self.originals.get('builtin_open', builtins.open)(path, 'rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest()
        return self.reference_sha256[key]

    @staticmethod
    def _ex5_names():
        return ('PRICE_of_Moses.ex5', 'RSI_of_Moses.ex5', 'STO_of_Moses.ex5',
                'DI_of_Moses.ex5', 'THE_STAFF_OF_MOSES.ex5')

    def _ensure_native_ex5(self, profile, work_dir, *, emit=lambda *a: None, cancel=lambda: None):
        """Install the exact licensed builds; original recording owns restore."""
        source_root = self.root / 'Part1/program/MT5'
        missing = [name for name in self._ex5_names()
                   if not (source_root / name).is_file() or (source_root / name).stat().st_size <= 0]
        if missing:
            raise ValueError('NATIVE_EXPERT_EX5_REQUIRED: ' + ','.join(missing))
        mql5 = Path(profile.get('data_root', '')).expanduser().resolve() / 'MQL5'
        emit('PROGRESS', {'phase': 'MT5_STRATEGY_TESTER', 'message_code': 'NATIVE_MQL_SYNC_START'})
        cancel()
        for name in self._ex5_names():
            cancel()
            destination = mql5 / ('Experts' if name == self._ex5_names()[-1] else 'Indicators') / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.moses-installing')
            try:
                shutil.copy2(source_root / name, temporary)
                if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256((source_root / name).read_bytes()).digest():
                    raise ValueError('NATIVE_EXPERT_EX5_INSTALL_HASH: ' + name)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        expert = mql5 / 'Experts/THE_STAFF_OF_MOSES.ex5'
        emit('PROGRESS', {'phase': 'MT5_STRATEGY_TESTER', 'message_code': 'NATIVE_MQL_SYNC_DONE',
                          'expert': str(expert)})
        return expert
