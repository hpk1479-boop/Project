"""Withhold a release until its real frozen runtime passes isolated checks."""
from __future__ import annotations

import calendar
from importlib.machinery import EXTENSION_SUFFIXES
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import quote
import zipfile

from .license_runtime import canonical, protect_token, sign_policy
from .manuals import MANUAL_RELATIVE, manual_record


def portable(value, root):
    if isinstance(value, dict):
        return {key: portable(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [portable(item, root) for item in value]
    if isinstance(value, str):
        for prefix, label in ((str(root), '<project>'), (str(Path.home()), '<local>')):
            value = value.replace(prefix.replace('\\', '\\\\'), label).replace(prefix, label)
    return value


def archive_inventory(payload):
    """Include bootstrap/PYZ/base-library/native entries, not just PYZ."""
    from PyInstaller.archive.readers import CArchiveReader
    payload = Path(payload)
    outer = CArchiveReader(str(payload / 'python.exe'))
    modules = {name for name, entry in outer.toc.items() if entry[-1] in {'m', 'M'}}
    for name in outer.toc:
        if name.endswith('.pyz'):
            modules.update(outer.open_embedded_archive(name).toc)
    base = payload / '_internal/base_library.zip'
    with zipfile.ZipFile(base) as library:
        for name in library.namelist():
            if name.endswith('.pyc'):
                name = name[:-4].replace('/', '.')
                modules.add(name.removesuffix('.__init__'))
    native = []
    for path in (payload / '_internal').rglob('*.pyd'):
        rel = path.relative_to(payload / '_internal')
        suffix = next((value for value in sorted(EXTENSION_SUFFIXES, key=len, reverse=True)
                       if rel.name.endswith(value)), '.pyd')
        stem = rel.name[:-len(suffix)]
        name = rel.with_name(stem).as_posix().replace('/', '.')
        modules.add(name)
        native.append(rel.as_posix())
    return {'module_count': len(modules), 'modules': sorted(modules), 'native': sorted(native)}


def verify_mt5_files(payload):
    """The visible manual-copy files must be the freshly licensed runtime files."""
    from .mt5 import MT5_PROGRAMS
    payload = Path(payload)
    expected = {name + '.ex5' for name in MT5_PROGRAMS}
    manual = payload / 'MT5'
    if not manual.is_dir() or {p.name for p in manual.iterdir()} != expected:
        raise ValueError('설치 폴더의 MT5 파일 5개가 누락되었거나 잘못 포함되었습니다.')
    for name in sorted(expected):
        runtime = payload / 'Part1/program/MT5' / name
        if not runtime.is_file() or not (manual / name).is_file() or not runtime.stat().st_size or runtime.read_bytes() != (manual / name).read_bytes():
            raise ValueError('수동 복사용 MT5 파일이 라이선스 적용 파일과 다릅니다: ' + name)


def validate_structure(payload, resources, code_paths):
    from .resources import validate_payload
    payload = Path(payload)
    validate_payload(payload, resources, code_paths=code_paths)
    required = [
        'MOSES.exe', 'python.exe', 'runtime/code.bundle', '_internal/base_library.zip',
        '_internal/python' + str(sys.version_info.major) + str(sys.version_info.minor) + '.dll',
        '_internal/certifi/cacert.pem', '_internal/pythonnet/runtime/Python.Runtime.dll',
        '_internal/webview/lib/Microsoft.Web.WebView2.Core.dll',
        '_internal/webview/lib/Microsoft.Web.WebView2.WinForms.dll',
        '_internal/webview/lib/runtimes/win-x64/native/WebView2Loader.dll',
        '_internal/clr_loader/ffi/dlls/amd64/ClrLoader.dll',
        'prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe',
        MANUAL_RELATIVE,
    ]
    from .mt5 import MT5_PROGRAMS
    required += ['Part1/program/MT5/' + name + '.ex5' for name in MT5_PROGRAMS]
    required += ['MT5/' + name + '.ex5' for name in MT5_PROGRAMS]
    missing = [rel for rel in required if not (payload / rel).is_file()]
    for pattern in ('_internal/webview/js/*.js',
                    '_internal/tzdata/zoneinfo/Asia/Seoul'):
        if not any(path.is_file() for path in payload.glob(pattern)):
            missing.append(pattern)
    forbidden = []
    for path in payload.rglob('*'):
        if path.is_file() and (path.suffix.lower() in {'.py', '.pyw', '.mq5', '.mqh'} or
                               any(part.casefold() in {'.keys', 'test_special', 'generated'}
                                   for part in path.relative_to(payload).parts) or
                               path.name.casefold() in {'release_rsa.json', 'license_build_history.json', 'license.bin', 'strategy_visibility.json'}):
            forbidden.append(path.relative_to(payload).as_posix())
    forbidden += [str(path) for path in code_paths if any(
        part.casefold() in {'test_special', 'generated'} for part in Path(path).parts)]
    if missing or forbidden:
        raise ValueError('배포 파일 검사 실패: ' + json.dumps(
            {'missing': missing, 'forbidden': forbidden}, ensure_ascii=False))
    verify_mt5_files(payload)
    return {'passed': True, 'required_files': required, 'source_files_exposed': False,
            'manual': manual_record(payload)}


def _run_probe(worker, probe, fixture, mode, environment, *, expected_labels, timeout=45):
    expected_labels = set(expected_labels)
    if not expected_labels:
        raise ValueError('필수 배포 검사 목록이 비어 있습니다: ' + mode)
    proof = fixture / 'runtime' / ('validation_' + mode + '.json')
    proof.unlink(missing_ok=True)
    process = subprocess.Popen([str(worker), str(probe), str(fixture), mode],
                               cwd=fixture, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            process.kill()
        process.communicate(timeout=15)
        raise RuntimeError('실제 배포 EXE 검사 시간 초과: ' + mode)
    record = json.loads(proof.read_text('utf-8')) if proof.is_file() else None
    actual_labels = ({check.get('label') for check in record.get('checks', [])}
                     if isinstance(record, dict) else set())
    if (process.returncode != 0 or not isinstance(record, dict) or
            record.get('schema') != 1 or record.get('mode') != mode or
            record.get('nonce') != environment['MOSES_VALIDATION_NONCE'] or
            record.get('passed') is not True or not record.get('checks') or
            any(check.get('passed') is not True for check in record['checks']) or
            not expected_labels.issubset(actual_labels)):
        error = {'mode': mode, 'exitcode': process.returncode, 'proof': record,
                 'missing_checks': sorted(expected_labels - actual_labels),
                 'stderr': stderr[-6000:], 'stdout': stdout[-2000:]}
        raise RuntimeError('실제 배포 EXE 검사 실패: ' + json.dumps(error, ensure_ascii=False))
    return record


def expected_http_labels(root, resources):
    from .resources import required_documents
    from .validation_probe import Assets, GET_ENDPOINTS
    labels = {*GET_ENDPOINTS, '/', '/api/folder', 'generated_strategy_lifecycle101'}
    labels.update('/api/doc?name=' + quote(rel.removeprefix('Part3/'), safe='/')
                  for rel in required_documents(root))
    assets = Assets()
    assets.feed((Path(root) / 'Part3/web/index.html').read_text('utf-8-sig'))
    labels.update(assets.paths)
    for reference in resources.references:
        if reference.target.startswith('Part3/web/'):
            labels.add('/' + quote(reference.target.removeprefix('Part3/web/'), safe='/'))
    return sorted(labels)


def validate_release(root, payload, private_key, dependencies, resources, code_paths,
                     evidence, *, emit=print):
    root, payload, evidence = Path(root).resolve(), Path(payload).resolve(), Path(evidence).resolve()
    if not payload.is_relative_to(root) or not evidence.is_relative_to(root):
        raise ValueError('프로젝트 내부의 새 배포물만 검사할 수 있습니다.')
    evidence.mkdir(parents=True, exist_ok=True)
    fixture = payload.parent / ('validation_' + uuid.uuid4().hex[:8])
    report = {'schema': 1, 'passed': False, 'checks': [],
              'dependencies': dependencies, 'resources': list(resources.files),
              'payload': payload.relative_to(root).as_posix()}
    try:
        emit('배포 파일·문서·웹 자산·MT5 구성 검사')
        report['structure'] = validate_structure(payload, resources, code_paths)
        report['archive'] = archive_inventory(payload)
        # Separate from payload: validation scripts and temporary license tokens
        # can never enter the recipient installer.
        shutil.copytree(payload, fixture, ignore=shutil.ignore_patterns('prerequisites', 'THIRD_PARTY_LICENSES'))
        now = int(time.time())
        policy = {'schema': 1, 'version': 'build-validation', 'expires_at': now + 3600,
                  'expires_local': calendar.timegm(time.localtime(now + 3600)),
                  'issued_at': now, 'install_expires_at': now + 3600}
        (fixture / 'runtime/license.bin').write_bytes(protect_token(sign_policy(policy, private_key)))
        labels = {'imports': dependencies['probe_imports'],
                  'http': expected_http_labels(root, resources),
                  'gui': ['live', 'backtest', 'settings', 'strategy']}
        (fixture / 'runtime/validation_candidates.json').write_bytes(canonical(
            {'modules': dependencies['probe_imports'],
             'http_endpoints': [label for label in labels['http'] if label.startswith('/')]}))
        probe = fixture / 'runtime/validation_probe.py'
        shutil.copy2(Path(__file__).with_name('validation_probe.py'), probe)
        environment = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                           MOSES_VALIDATION_NONCE=uuid.uuid4().hex)
        local_settings = fixture / 'runtime/validation_localappdata'
        local_settings.mkdir()
        environment['LOCALAPPDATA'] = str(local_settings)
        environment.pop('PYTHONPATH', None)
        worker = fixture / 'python.exe'
        for mode, label in (('imports', '실제 EXE 라이브러리 로딩 검사'),
                            ('http', '실제 EXE 화면·도움말 요청 검사'),
                            ('gui', '실제 WebView 라이브·백테스트·설정 화면 검사')):
            emit(label)
            report['checks'].append(_run_probe(worker, probe, fixture, mode, environment,
                                              expected_labels=labels[mode]))
        report['passed'] = True
        emit('실제 배포 EXE 검사 통과')
        return portable(report, root)
    except Exception as error:
        report['error'] = portable(str(error), root)
        raise RuntimeError(portable(str(error), root)) from error
    finally:
        (evidence / 'release_validation.json').write_bytes(canonical(portable(report, root)))
        if fixture.exists():
            # This path was freshly generated beside payload and checked above.
            shutil.rmtree(fixture)


def write_payload_archive(payload, archive):
    """Preserve empty runtime folders as real ZIP directory entries."""
    payload = Path(payload)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for path in sorted(payload.rglob('*')):
            rel = path.relative_to(payload).as_posix()
            if path.is_file():
                stream.write(path, rel)
            elif path.is_dir():
                stream.write(path, rel + '/')
