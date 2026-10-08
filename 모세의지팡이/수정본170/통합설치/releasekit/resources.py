"""Discover current UI resources without executing original application code.

Documentation is owned by the server's DOCS declaration. Web resource folders
are selected independently of filename extensions, and local HTML/CSS/JS
references are followed. Mutable user state and developer secrets are never
selected by this helper. Non-static Python file lookups are reported, rather
than guessed to be mandatory distribution assets.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit


BLOCKED_PARTS = frozenset({
    '__pycache__', '.pytest_cache', 'logs', 'tests', 'verification', '검증결과',
    'projects', 'generated', 'runtime', 'event_state', 'checkpoints', 'captures',
    'build_runs', 'models', 'reference_sources', 'audit', 'validation_suite', 'test_special',
})
BLOCKED_SUFFIXES = frozenset({
    '.py', '.pyw', '.pyc', '.pyo', '.mq5', '.mqh', '.ex5', '.exe', '.dll',
    '.pyd', '.gguf', '.safetensors', '.pt', '.pth', '.pem', '.key', '.pfx',
})
ASSET_TREES = ('Part3/web', 'Part3/docs')


@dataclass(frozen=True)
class ResourceReference:
    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class ResourceManifest:
    files: tuple[str, ...]
    directories: tuple[str, ...]
    references: tuple[ResourceReference, ...]
    unresolved_python_reads: tuple[dict, ...] = ()

    def to_dict(self):
        return asdict(self)


def _relative(value):
    text = str(value).replace('\\', '/')
    path = PurePosixPath(text)
    if (not text or path.is_absolute() or ':' in text or
            any(part in ('', '.', '..') for part in text.split('/'))):
        raise ValueError('배포 리소스는 프로젝트 내부 상대 경로여야 합니다: ' + text)
    return path.as_posix()


def _allowed(relative):
    path = PurePosixPath(relative)
    return (not any(part.casefold() in BLOCKED_PARTS or part.startswith('.') for part in path.parts)
            and path.suffix.lower() not in BLOCKED_SUFFIXES
            and path.name.lower() not in {'ai_settings.json', 'connections.json', 'release_rsa.json', 'license.bin', 'strategy_visibility.json'})


def _checked_file(root, relative):
    relative = _relative(relative)
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('프로젝트 밖 리소스는 배포할 수 없습니다: ' + relative)
    if not _allowed(relative):
        raise ValueError('원본 코드·개인 설정·모델은 UI 리소스로 배포할 수 없습니다: ' + relative)
    if not path.is_file():
        raise ValueError('필수 배포 리소스 없음: ' + relative)
    return path


def required_documents(root):
    """Read the server's current literal DOCS list and verify every document."""
    root = Path(root).resolve()
    source = root / 'Part3/lab/server.py'
    if not source.is_file():
        return ()
    declarations = []
    for node in ast.parse(source.read_text('utf-8-sig')).body:
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == 'DOCS' for n in node.targets):
            declarations.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'DOCS':
            declarations.append(node.value)
    if not declarations:
        return ()
    if len(declarations) != 1:
        raise ValueError('Part3/lab/server.py의 DOCS 목록을 하나의 리터럴로 선언해야 합니다.')
    try:
        docs = ast.literal_eval(declarations[0])
    except (ValueError, TypeError):
        raise ValueError('DOCS는 정적인 문서 경로 목록이어야 합니다.') from None
    if not isinstance(docs, (list, tuple)) or any(not isinstance(name, str) for name in docs):
        raise ValueError('DOCS는 문서 경로 문자열 목록이어야 합니다.')
    result = []
    for name in docs:
        relative = 'Part3/' + _relative(name)
        _checked_file(root, relative)
        if relative not in result:
            result.append(relative)
    return tuple(result)


class _HTMLReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('src'):
            self.values.append(attrs['src'])
        if tag in ('link', 'a') and attrs.get('href'):
            self.values.append(attrs['href'])
        if tag in ('video',) and attrs.get('poster'):
            self.values.append(attrs['poster'])
        if attrs.get('srcset'):
            self.values.extend(item.strip().split()[0] for item in attrs['srcset'].split(',') if item.strip())


def _asset_references(path):
    suffix = path.suffix.lower()
    if suffix not in {'.html', '.htm', '.css', '.js', '.mjs'}:
        return ()
    text = path.read_text('utf-8-sig')
    if suffix in {'.html', '.htm'}:
        parser = _HTMLReferences()
        parser.feed(text)
        return tuple(parser.values)
    if suffix == '.css':
        text = re.sub(r'/\*[\s\S]*?\*/', '', text)
        urls = re.findall(r'url\(\s*[\"\']?([^\)\"\']+)[\"\']?\s*\)', text, re.I)
        urls += re.findall(r'@import\s+[\"\']([^\"\']+)[\"\']', text, re.I)
        return tuple(urls)
    # Static ES imports and import.meta.url assets. Do not interpret API routes
    # or ordinary arbitrary strings as files.
    urls = re.findall(r'\bimport\s+(?:[^;\n]*?\s+from\s+)?[\"\']([^\"\']+)[\"\']', text)
    urls += re.findall(r'\bimport\(\s*[\"\']([^\"\']+)[\"\']\s*\)', text)
    urls += re.findall(r'\bnew\s+URL\(\s*[\"\']([^\"\']+)[\"\']\s*,\s*import\.meta\.url\s*\)', text)
    return tuple(urls)


def _local_asset(root, source, url):
    value = str(url).strip()
    if not value or value.startswith(('#', '//')):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    value = unquote(parsed.path).replace('\\', '/')
    if ':' in value or '\x00' in value:
        raise ValueError('잘못된 UI 리소스 경로: ' + source)
    if value.startswith('/api/'):
        return None
    # Root-relative web routes address Part3/web, just like the local UI server.
    target = root / 'Part3/web' / value.lstrip('/') if value.startswith('/') else (root / source).parent / value
    target = target.resolve()
    if not target.is_relative_to(root):
        raise ValueError('UI 리소스가 프로젝트 밖을 가리킵니다: ' + source)
    relative = target.relative_to(root).as_posix()
    # Navigational links to the root/index page do not imply a file called '/'.
    if target.is_dir() and value in ('/', './'):
        return None
    return relative


def _static_value(node, symbols):
    """Evaluate only literal path construction; never invoke original functions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return node.value
    if isinstance(node, ast.Name) and node.id in symbols:
        return symbols[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _static_value(node.left, symbols), _static_value(node.right, symbols)
        if isinstance(left, Path) and isinstance(right, str):
            return left / right
    if isinstance(node, ast.Attribute):
        value = _static_value(node.value, symbols)
        if isinstance(value, Path) and node.attr in {'parent', 'parents', 'name', 'stem', 'suffix'}:
            return getattr(value, node.attr)
    if isinstance(node, ast.Subscript):
        value = _static_value(node.value, symbols)
        index = _static_value(node.slice, symbols)
        if isinstance(value, type(Path().parents)) and isinstance(index, int):
            return value[index]
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == 'Path' and len(node.args) == 1:
            value = _static_value(node.args[0], symbols)
            if isinstance(value, (Path, str)):
                return Path(value)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {'resolve', 'absolute'} and not node.args:
            value = _static_value(node.func.value, symbols)
            # Do not guess the original process's current directory.
            if isinstance(value, Path) and value.is_absolute():
                return value.resolve()
    raise ValueError('not a static resource path')


def _python_read_audit(root, selected_sources):
    """Report dynamic reads; only statically recognized resource trees add files.

    This does not execute Python or pretend that mutable state files are required
    assets. Known static path expressions below are deliberately conservative.
    """
    references, unresolved = [], []
    for selected in selected_sources or ():
        value = selected[0] if isinstance(selected, (tuple, list)) else selected
        relative = _relative(value)
        if PurePosixPath(relative).suffix.lower() not in {'.py', '.pyw'}:
            continue
        tree = ast.parse((root / relative).read_text('utf-8-sig'))
        symbols = {'__file__': str(root / relative)}
        for assignment in tree.body:
            if (isinstance(assignment, ast.Assign) and len(assignment.targets) == 1
                    and isinstance(assignment.targets[0], ast.Name)):
                try:
                    symbols[assignment.targets[0].id] = _static_value(assignment.value, symbols)
                except (ValueError, TypeError, IndexError):
                    pass
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {'read_text', 'read_bytes', 'open'}:
                continue
            try:
                target = _static_value(node.func.value, symbols)
                if not isinstance(target, Path) or not target.is_absolute():
                    raise ValueError('not a static absolute path')
                target = target.resolve()
                if not target.is_relative_to(root):
                    raise ValueError('not inside project')
                target_relative = target.relative_to(root).as_posix()
                # This helper selects only immutable UI/document assets. Known
                # code paths remain virtual code; settings/state stay builder-owned.
                if not any(target_relative.startswith(tree + '/') for tree in ASSET_TREES):
                    raise ValueError('outside immutable resource trees')
            except (ValueError, TypeError, IndexError):
                target_relative = None
            if target_relative is not None:
                if PurePosixPath(target_relative).suffix.lower() in {'.py', '.pyw'}:
                    references.append(ResourceReference(relative, target_relative, 'static_python_code_read'))
                else:
                    _checked_file(root, target_relative)
                    references.append(ResourceReference(relative, target_relative, 'static_python_resource_read'))
                continue
            # The path of dynamic config/data reads is unknown by static AST.
            unresolved.append({'source': relative, 'line': node.lineno,
                               'operation': node.func.attr,
                               'reason': 'runtime_or_optional_path_not_inferred_as_required_resource'})
    return references, unresolved


def discover_resources(root, selected_sources=None):
    """Return suffix-independent UI assets, current documents and references."""
    root = Path(root).resolve()
    files = set(required_documents(root))
    for tree in ASSET_TREES:
        folder = root / tree
        if not folder.is_dir():
            raise ValueError('필수 UI 리소스 폴더 없음: ' + tree)
        for path in folder.rglob('*'):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and _allowed(relative):
                _checked_file(root, relative)
                files.add(relative)
    references = [ResourceReference('Part3/lab/server.py', name, 'DOCS') for name in required_documents(root)]
    pending = sorted(files)
    visited = set()
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        for value in _asset_references(root / source):
            target = _local_asset(root, source, value)
            if target is None:
                continue
            _checked_file(root, target)
            references.append(ResourceReference(source, target, 'local_web_asset'))
            if target not in files:
                files.add(target)
                pending.append(target)
    python_references, unresolved = _python_read_audit(root, selected_sources)
    references.extend(python_references)
    files.update(reference.target for reference in python_references if reference.kind != 'static_python_code_read')
    # The application expects this writable output directory even before the
    # first generated strategy. Original generated files themselves stay out.
    directories = {'Part3/TEST_SPECIAL'}
    # An installation reads its SPECIAL files from 스페셜: the shipped ones and the user's own (166).
    if (root / 'Part1/program/SPECIAL').is_dir():
        directories.update(('스페셜/기본', '스페셜/내 전략'))
    return ResourceManifest(tuple(sorted(files)), tuple(sorted(directories)),
                            tuple(sorted(set(references), key=lambda value: (value.source, value.target, value.kind))),
                            tuple(unresolved))


def validate_payload(payload, manifest, code_paths=()):
    """Fail before issuing an EXE if a declared resource/code path is missing."""
    payload = Path(payload).resolve()
    code = {_relative(value) for value in code_paths}
    missing = []
    for relative in manifest.files:
        path = payload / _relative(relative)
        if (path.is_symlink() or not path.resolve().is_relative_to(payload)
                or not path.is_file()):
            missing.append(relative)
    for reference in manifest.references:
        relative = _relative(reference.target)
        if relative not in code and not (payload / relative).is_file():
            missing.append(relative)
    for relative in manifest.directories:
        path = payload / _relative(relative)
        if path.is_symlink() or not path.resolve().is_relative_to(payload) or not path.is_dir():
            missing.append(relative + '/')
    if missing:
        raise ValueError('배포 리소스 누락: ' + ', '.join(sorted(set(missing))))
    return {'resources': len(manifest.files), 'references': len(manifest.references),
            'required_directories': len(manifest.directories)}
