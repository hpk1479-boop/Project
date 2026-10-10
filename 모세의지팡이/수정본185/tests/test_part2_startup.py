"""Part2 bootstrap probe, failed-repair restore and START_BACKTEST entry (moved from Part2/validation_suite in 수정본180)."""
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]/'Part2'
sys.path.insert(0,str(ROOT))
import bootstrap


def test_missing_environment(tmp_path):
    ok,detail=bootstrap.probe(tmp_path/'missing')
    assert not ok and 'Python 실행 파일 없음' in detail


def test_existing_executable_is_not_sufficient(tmp_path,monkeypatch):
    env=tmp_path/'env';exe=bootstrap.executable(env)
    exe.parent.mkdir(parents=True);exe.write_text('broken')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=103,stdout='',stderr='No Python at old path'))
    ok,detail=bootstrap.probe(env)
    assert not ok and detail=='No Python at old path'


def test_healthy_prefix_probe(tmp_path,monkeypatch):
    env=tmp_path/'env';exe=bootstrap.executable(env)
    exe.parent.mkdir(parents=True);exe.write_text('stub')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps({'prefix':str(env)}),stderr=''))
    assert bootstrap.probe(env)[0]


def test_failed_repair_restores_original(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap,'ROOT',tmp_path)
    monkeypatch.setattr(bootstrap,'LOG',tmp_path/'log')
    env=tmp_path/'.venv-generic';env.mkdir();(env/'evidence').write_text('original')
    monkeypatch.setattr(bootstrap,'probe',lambda *a,**k:(False,'broken'))
    def fail(cmd):
        env.mkdir();(env/'new').write_text('partial')
        raise RuntimeError('controlled installation failure')
    monkeypatch.setattr(bootstrap,'run',fail)
    import pytest
    with pytest.raises(RuntimeError):bootstrap.ensure_environment()
    assert (env/'evidence').read_text()=='original'
    assert len(list(tmp_path.glob('.venv-generic.failed-*')))==1


def test_windows_entry_avoids_file_association():
    s=(ROOT/'START_BACKTEST.cmd').read_text()
    assert 'bootstrap.py' in s and 'py -3.12' in s and 'BACKTEST_PYTHON' in s
    assert 'codex-runtimes' not in s
