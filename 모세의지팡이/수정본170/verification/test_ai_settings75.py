"""Bundled GGUF runner selection, without loading models. (수정본138 removed the LoRA provider and its cases.)"""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Part3'))
from common_ai.local_gguf import DEFAULT_LLAMA_SERVER_PATH, LocalGGUF, check_files
from common_ai.provider import configured_settings
from lab import server, unified_settings


@pytest.mark.parametrize('runner', [None, ''])
def test_runner_defaults_without_choosing_a_model(runner):
    source = {'provider': 'local_gguf', 'llama_server_path': runner}
    value = configured_settings(source)
    assert value['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert not value.get('gguf_model_path')
    assert source == {'provider': 'local_gguf', 'llama_server_path': runner}
    with pytest.raises(ValueError, match='모델 파일'):
        LocalGGUF(value)


def test_model_only_settings_use_bundled_runner():
    value = LocalGGUF({'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'})
    assert value.settings['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert value.model == 'models/gguf/chosen.gguf'


@pytest.mark.parametrize('runner', ['runtime/custom/llama-server.exe', 'runtime\\custom\\llama-server.exe'])
def test_explicit_runner_override_is_preserved(runner):
    value = configured_settings({'provider': 'local_gguf', 'llama_server_path': runner})
    assert value['llama_server_path'] == 'runtime/custom/llama-server.exe'


def test_default_runner_follows_project_relocation(tmp_path):
    root = tmp_path / 'project'
    model = root / 'models/gguf/chosen.gguf'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    runner = root / DEFAULT_LLAMA_SERVER_PATH
    runner.parent.mkdir(parents=True)
    runner.write_bytes(b'test executable; never run')
    source = {'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'}
    before = check_files(source, root)
    moved = tmp_path / '다른 위치'
    root.rename(moved)
    after = check_files(source, moved)
    assert [p.relative_to(root).as_posix() if p else None for p in before] == [
        p.relative_to(moved).as_posix() if p else None for p in after]
    assert all(p is None or p.is_file() for p in after)
    assert 'llama_server_path' not in source
    (moved / DEFAULT_LLAMA_SERVER_PATH).unlink()
    with pytest.raises(ValueError, match='GGUF 실행기가 없습니다'):
        check_files(source, moved)


def test_save_model_only_and_reset_custom_runner(tmp_path, monkeypatch):
    settings = tmp_path / 'settings/ai_settings.json'
    monkeypatch.setattr(server, 'AI_SETTINGS', settings)
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {})
    from common_ai.client import Client
    from common_ai.model_runtime import RUNTIME
    monkeypatch.setattr(Client, 'invalidate', Mock())
    monkeypatch.setattr(RUNTIME, 'invalidate', Mock())
    unified_settings.save_ai({'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'})
    assert json.loads(settings.read_text('utf-8'))['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    unified_settings.save_ai({'llama_server_path': 'runtime/custom/llama-server.exe'})
    assert server.ai_settings()['llama_server_path'] == 'runtime/custom/llama-server.exe'
    server.AI_SESSIONS['dialogue'] = object()
    unified_settings.save_ai({'llama_server_path': ''})
    saved = json.loads(settings.read_text('utf-8'))
    assert saved['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert saved['gguf_model_path'] == 'models/gguf/chosen.gguf'
    assert not server.AI_SESSIONS


# The GGUF settings screen is checked in test_gguf_ui73.py (수정본162 merged the runner reset there).
