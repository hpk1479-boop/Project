import json
from pathlib import Path
import zipfile

import pytest

from releasekit import validation


@pytest.mark.parametrize('problem', ['silent-exit', 'failed-check', 'wrong-nonce', 'nonzero-exit'])
def test_real_worker_failure_or_missing_proof_never_passes(tmp_path, monkeypatch, problem):
    (tmp_path/'runtime').mkdir()
    proof=tmp_path/'runtime/validation_http.json'
    proof.write_text('{"stale":true}')
    class Process:
        returncode=1 if problem=='nonzero-exit' else 0
        def __init__(self,*args,**kwargs):
            assert not proof.exists()
        def communicate(self,**kwargs):
            if problem!='silent-exit':
                record={'schema':1,'mode':'http','nonce':'bad' if problem=='wrong-nonce' else 'nonce',
                        'passed':True,'checks':[{'passed':problem!='failed-check'}]}
                proof.write_text(json.dumps(record))
            return '', ''
    monkeypatch.setattr(validation.subprocess,'Popen',Process)
    with pytest.raises(RuntimeError,match='실제 배포 EXE 검사 실패'):
        validation._run_probe(tmp_path/'python.exe',tmp_path/'probe.py',tmp_path,'http',
                              {'MOSES_VALIDATION_NONCE':'nonce'},expected_labels=['fixture'])


def test_real_worker_requires_current_success_proof(tmp_path, monkeypatch):
    (tmp_path/'runtime').mkdir()
    class Process:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):
            record={'schema':1,'mode':'http','nonce':'current', 'passed':True,
                    'checks':[{'label':'/api/mo/live/status','passed':True}]}
            (tmp_path/'runtime/validation_http.json').write_text(json.dumps(record))
            return '', ''
    monkeypatch.setattr(validation.subprocess,'Popen',Process)
    record=validation._run_probe(tmp_path/'python.exe',tmp_path/'probe.py',tmp_path,'http',
                                 {'MOSES_VALIDATION_NONCE':'current'},expected_labels=['/api/mo/live/status'])
    assert record['checks'][0]['label']=='/api/mo/live/status'


def test_import_candidates_survive_proof_cleanup(tmp_path, monkeypatch):
    (tmp_path/'runtime').mkdir()
    inputs=tmp_path/'runtime/validation_candidates.json'
    inputs.write_text('{"modules":["json"]}')
    class Process:
        returncode=0
        def __init__(self,*args,**kwargs):
            assert json.loads(inputs.read_text())['modules']==['json']
        def communicate(self,**kwargs):
            record={'schema':1,'mode':'imports','nonce':'current','passed':True,
                    'checks':[{'label':'json','passed':True}]}
            (tmp_path/'runtime/validation_imports.json').write_text(json.dumps(record))
            return '', ''
    monkeypatch.setattr(validation.subprocess,'Popen',Process)
    validation._run_probe(tmp_path/'python.exe',tmp_path/'probe.py',tmp_path,'imports',
                          {'MOSES_VALIDATION_NONCE':'current'},expected_labels=['json'])
    assert inputs.is_file()


def test_archive_preserves_empty_folder_after_extraction(tmp_path):
    payload=tmp_path/'payload'
    (payload/'Part3/generated').mkdir(parents=True)
    (payload/'data').mkdir()
    (payload/'data/value.txt').write_text('value')
    archive=tmp_path/'payload.zip'
    validation.write_payload_archive(payload,archive)
    with zipfile.ZipFile(archive) as stream:
        assert stream.getinfo('Part3/generated/').is_dir()
        stream.extractall(tmp_path/'installed')
    assert (tmp_path/'installed/Part3/generated').is_dir()
    assert (tmp_path/'installed/data/value.txt').read_text()=='value'


def test_runtime_validation_failure_report_is_saved_and_fixture_removed(tmp_path, monkeypatch):
    payload=tmp_path/'work/payload'
    (payload/'runtime').mkdir(parents=True)
    (payload/'python.exe').write_bytes(b'fixture')
    monkeypatch.setattr(validation,'validate_structure',lambda *args: {'passed':True})
    monkeypatch.setattr(validation,'archive_inventory',lambda *args: {'modules':[]})
    monkeypatch.setattr(validation,'sign_policy',lambda *args:b'fixture-envelope')
    monkeypatch.setattr(validation,'protect_token',lambda *args:b'fixture-token')
    def fail(*args,**kwargs):raise RuntimeError('missing frozen dependency')
    monkeypatch.setattr(validation,'_run_probe',fail)
    from releasekit.resources import ResourceManifest
    resources=ResourceManifest((),(),())
    (tmp_path/'Part3/web').mkdir(parents=True)
    (tmp_path/'Part3/web/index.html').write_text('<p>fixture</p>')
    evidence=tmp_path/'evidence'
    with pytest.raises(RuntimeError,match='missing frozen dependency'):
        validation.validate_release(tmp_path,payload,{}, {'probe_imports':['json']},
                                     resources,[],evidence,emit=lambda value:None)
    assert json.loads((evidence/'release_validation.json').read_bytes())['passed'] is False
    assert not list((tmp_path/'work').glob('validation_*'))
    assert not (payload/'runtime/license.bin').exists()


def test_partial_gui_success_proof_cannot_skip_required_views(tmp_path, monkeypatch):
    (tmp_path/'runtime').mkdir()
    class Process:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):
            record={'schema':1,'mode':'gui','nonce':'current','passed':True,
                    'checks':[{'label':'live','passed':True}]}
            (tmp_path/'runtime/validation_gui.json').write_text(json.dumps(record))
            return '', ''
    monkeypatch.setattr(validation.subprocess,'Popen',Process)
    with pytest.raises(RuntimeError,match='missing_checks'):
        validation._run_probe(tmp_path/'python.exe',tmp_path/'probe.py',tmp_path,'gui',
                              {'MOSES_VALIDATION_NONCE':'current'},
                              expected_labels=['live','backtest','settings'])


def test_native_archive_inventory_removes_python_abi_suffix(tmp_path, monkeypatch):
    import PyInstaller.archive.readers
    from importlib.machinery import EXTENSION_SUFFIXES
    class Archive:
        toc={}
        def __init__(self,*args):pass
    monkeypatch.setattr(PyInstaller.archive.readers,'CArchiveReader',Archive)
    internal=tmp_path/'_internal'
    (internal/'pkg').mkdir(parents=True)
    (internal/'pkg'/('native'+EXTENSION_SUFFIXES[0])).write_bytes(b'fixture')
    with zipfile.ZipFile(internal/'base_library.zip','w') as archive:
        archive.writestr('encodings/__init__.pyc',b'fixture')
    report=validation.archive_inventory(tmp_path)
    assert 'pkg.native' in report['modules']
    assert 'encodings' in report['modules']


def test_x86_clr_dll_cannot_replace_required_amd64_runtime(tmp_path):
    from releasekit.resources import ResourceManifest
    runtime=tmp_path/'_internal/clr_loader/ffi/dlls/x86'
    runtime.mkdir(parents=True)
    (runtime/'ClrLoader.dll').write_bytes(b'wrong architecture fixture')
    with pytest.raises(ValueError,match='amd64/ClrLoader.dll'):
        validation.validate_structure(tmp_path,ResourceManifest((),(),()),[])
