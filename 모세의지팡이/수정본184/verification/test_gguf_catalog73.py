"""Portable GGUF filename discovery never loads or classifies model weights."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'Part3'))
from lab.ai import gguf_catalog


def _write(root, name='model.gguf', *, tail=b'', version=3):
    path = root / 'models/gguf' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', version, 0, 0) + tail)
    return path


def _only(root):
    result = gguf_catalog.models(root)
    assert result['directory'] == 'models/gguf'
    assert result['warnings'] == [] and len(result['models']) == 1
    model = result['models'][0]
    assert set(model) == {'name', 'path', 'bytes'}
    return model


def test_auto_discovery_is_recursive_readonly_and_limited_to_dedicated_directory(tmp_path):
    root = tmp_path / 'project'
    first = _write(root, 'MOSES-4B-Q4_K_M.gguf')
    second = _write(root, '한국어 폴더/MOSES-8B.GGUF')
    (root / 'models/gguf/설명.txt').write_text('모델 목록 안내', 'utf-8')
    (root / 'models/outside.gguf').write_bytes(first.read_bytes())
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    result = gguf_catalog.models(root)
    assert {model['path'] for model in result['models']} == {
        'models/gguf/MOSES-4B-Q4_K_M.gguf', 'models/gguf/한국어 폴더/MOSES-8B.GGUF'}
    assert result['warnings'] == []
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    by_name = {model['name']: model for model in result['models']}
    assert set(by_name) == {first.name, second.name}
    assert all(set(model) == {'name', 'path', 'bytes'} for model in result['models'])
    assert by_name[first.name]['bytes'] == first.stat().st_size


@pytest.mark.parametrize('name', ['모세 최종.gguf', 'custom-0.5B-Q4.gguf', 'custom-6.01B.gguf',
                                 'custom-200B.gguf', 'custom-240m.GGUF', 'unknown_model.gguf',
                                 'qwen3.5-4B-병합-2026.gguf'])
def test_arbitrary_filename_is_returned_verbatim_without_scale_labels(tmp_path, name):
    _write(tmp_path, name)
    result = _only(tmp_path)
    assert result['name'] == name and result['path'] == 'models/gguf/' + name


def test_renaming_model_changes_only_display_name_and_relative_path(tmp_path):
    original = _write(tmp_path, 'custom-4B.gguf', tail=b'weight fixture')
    first = _only(tmp_path)
    renamed = original.with_name('사용자 지정 파일명.gguf')
    original.rename(renamed)
    second = _only(tmp_path)
    assert second['name'] == renamed.name and second['path'] == 'models/gguf/' + renamed.name
    assert second['bytes'] == first['bytes']


def test_only_first_shard_of_split_models_is_selectable(tmp_path):
    for name in ('MOSES-8B-00001-of-00003.gguf', 'MOSES-8B-00002-of-00003.gguf',
                 'MOSES-8B-00003-of-00003.gguf', 'MOSES-8B-00000-of-00003.gguf'):
        _write(tmp_path, name)
    assert _only(tmp_path)['path'] == 'models/gguf/MOSES-8B-00001-of-00003.gguf'


@pytest.mark.parametrize('raw', [b'', b'GGUF', b'GGUF' + struct.pack('<I', 3),
                               b'bad!' + struct.pack('<IQQ', 3, 0, 0),
                               b'GGUF' + struct.pack('<IQQ', 1, 0, 0),
                               b'GGUF' + struct.pack('<IQQ', 9, 0, 0)])
def test_invalid_header_is_excluded_with_relative_warning(tmp_path, raw):
    path = _write(tmp_path, 'bad.gguf')
    path.write_bytes(raw)
    result = gguf_catalog.models(tmp_path)
    assert result['models'] == [] and len(result['warnings']) == 1
    assert result['warnings'][0].startswith('models/gguf/bad.gguf:')
    assert str(tmp_path) not in result['warnings'][0]


@pytest.mark.parametrize('version', [2, 3])
def test_supported_header_versions_are_listed(tmp_path, version):
    _write(tmp_path, 'model.gguf', version=version)
    assert _only(tmp_path)['name'] == 'model.gguf'


def test_absent_or_empty_dedicated_folder_is_readable(tmp_path):
    assert gguf_catalog.models(tmp_path) == {'directory': 'models/gguf', 'models': [], 'warnings': []}
    (tmp_path / 'models/gguf').mkdir(parents=True)
    assert gguf_catalog.models(tmp_path)['models'] == []


def test_dedicated_path_is_a_file_instead_of_folder_is_explicit(tmp_path):
    (tmp_path / 'models').mkdir()
    (tmp_path / 'models/gguf').write_text('파일', 'utf-8')
    with pytest.raises(ValueError, match='폴더'):
        gguf_catalog.models(tmp_path)


def test_walk_access_failure_is_explicit_without_absolute_path(tmp_path, monkeypatch):
    (tmp_path / 'models/gguf').mkdir(parents=True)
    def denied(directory, **kwargs):
        handler = kwargs.get('onerror')
        assert handler is not None
        handler(PermissionError('실행 PC 절대 경로가 포함될 수 있는 원본 오류'))
        return iter(())
    monkeypatch.setattr(gguf_catalog.os, 'walk', denied)
    with pytest.raises(ValueError) as error:
        gguf_catalog.models(tmp_path)
    assert '폴더' in str(error.value) and str(tmp_path) not in str(error.value)


def test_huge_or_corrupt_optional_metadata_does_not_change_filename_listing(tmp_path):
    path = _write(tmp_path, '사용자가 정한 이름.gguf')
    # Listing validates the header. Native llama.cpp inspects architecture,
    # metadata and tensors only when this file is actually loaded.
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 2**63, 2**63) + b'corrupt metadata')
    assert _only(tmp_path)['name'] == path.name


def test_query_reads_at_most_24_bytes_and_never_reads_weights(tmp_path, monkeypatch):
    path = _write(tmp_path, 'model.gguf', tail=b'x' * (2 * 1024 * 1024))
    original = Path.open
    reads = []
    class Tracked:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *_): self.handle.close()
        def read(self, size):
            assert 0 <= size <= 24
            data = self.handle.read(size)
            reads.append((len(data), self.handle.tell()))
            return data
    def observe(candidate, *args, **kwargs):
        handle = original(candidate, *args, **kwargs)
        return Tracked(handle) if candidate == path else handle
    monkeypatch.setattr(Path, 'open', observe)
    assert _only(tmp_path)['name'] == path.name
    assert reads and sum(size for size, _ in reads) <= 24
    assert max(offset for _, offset in reads) <= 24


def test_folder_move_preserves_paths_and_original_filenames(tmp_path):
    original = tmp_path / 'original'
    _write(original, '한국어 모델/모세 최종.gguf')
    _write(original, 'MOSES-14B-Q4.gguf')
    before = gguf_catalog.models(original)
    moved = tmp_path / '다른 위치/portable'
    moved.parent.mkdir()
    shutil.move(str(original), str(moved))
    assert gguf_catalog.models(moved) == before
    assert all(not Path(model['path']).is_absolute() and '\\' not in model['path'] for model in before['models'])


def _symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip('현재 Windows 실행 권한에서 symlink 생성이 허용되지 않음')


def test_root_escape_model_symlink_is_excluded(tmp_path):
    root = tmp_path / 'project'
    outside = tmp_path / 'outside'
    target = _write(outside, 'external.gguf')
    (root / 'models/gguf').mkdir(parents=True)
    _symlink(root / 'models/gguf/model.gguf', target)
    result = gguf_catalog.models(root)
    assert result['models'] == [] and len(result['warnings']) == 1
    assert '프로젝트 밖' in result['warnings'][0] and str(outside) not in result['warnings'][0]


def test_root_escape_dedicated_directory_symlink_is_rejected(tmp_path):
    root = tmp_path / 'project'
    outside = tmp_path / 'outside'
    _write(outside, 'external.gguf')
    (root / 'models').mkdir(parents=True)
    _symlink(root / 'models/gguf', outside / 'models/gguf', directory=True)
    with pytest.raises(ValueError, match='프로젝트 내부'):
        gguf_catalog.models(root)


def test_nested_external_directory_symlink_is_not_walked(tmp_path):
    root = tmp_path / 'project'
    outside = tmp_path / 'outside'
    _write(root, 'local.gguf')
    _write(outside, 'external.gguf')
    _symlink(root / 'models/gguf/linked', outside / 'models/gguf', directory=True)
    assert _only(root)['name'] == 'local.gguf'


def test_catalog_query_does_not_import_ml_packages_or_start_model_runtime(tmp_path):
    _write(tmp_path, 'model.gguf')
    source = '''import json, pathlib, sys\n
sys.path.insert(0, sys.argv[1])
from lab.ai import gguf_catalog
result = gguf_catalog.models(pathlib.Path(sys.argv[2]))
blocked = {'torch','transformers','peft','lmformatenforcer','lab.ai.gguf_engine','lab.ai.model_runtime'}
print(json.dumps({'models':len(result['models']), 'loaded':sorted(blocked.intersection(sys.modules))}))
'''
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([sys.executable, '-c', source, str(PROJECT / 'Part3'), str(tmp_path)],
                            capture_output=True, text=True, timeout=10, **kwargs)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'models': 1, 'loaded': []}
