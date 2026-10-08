"""Single release pipeline. Reads original app code; writes only new artifacts."""
from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from .license_runtime import append_policy, canonical, sign_policy
from .manuals import MANUALS, manual_records, verify_archive_manual, verify_manual

TREES = ('common_ai', 'moses_language', 'Part1/program', 'Part2/calculations',
         'Part2/data_warehouse', 'Part2/event_backtest', 'Part2/generic_backtest',
         'Part2/live_replay', 'Part2/part1_host', 'Part2/pit', 'Part3/lab',
         'Part3/web', 'Part3/ai_context', 'Part3/docs')
FILES = ('START_MOSES.pyw', 'Part1/__main__.py', 'Part1/engine_processes.py',
         'Part1/live_control.py', 'Part2/bootstrap.py', 'Part2/ea_build_compatibility.json',
         'Part2/scenarios/xau_selected.json', 'Part3/run.py', 'Part3/cli.py',
         'Part3/backtest.py', 'Part3/watch_check.py', 'settings/strategy_registry.json',
         *MANUALS)
SKIP = {'__pycache__', '.pytest_cache', 'logs', 'tests', 'reference_sources',
        'python setup', 'runtime', 'generated', 'projects', 'build', 'verification',
        'event_state', 'checkpoints', 'captures', 'build_runs', 'test_special'}
DATA_SUFFIXES = {'.json', '.sql', '.js', '.css', '.html', '.svg', '.png', '.ico', '.txt', '.md'}
SECRETS = re.compile(r'^([ \t]*(?:TELEGRAM_TOKEN|TELEGRAM_CHAT_ID|TELEGRAM_COMMAND_CHAT_IDS|GEMINI_API_KEY)[ \t]*=)[^\r\n]*', re.M)
SPECIAL_SOURCE = 'Part1/program/SPECIAL'
INSTALLED_SPECIAL = '스페셜'


def _distribution_excluded(relative):
    return (any(part.casefold() in SKIP or part.startswith('.') for part in Path(relative).parts)
            or Path(relative).name.casefold() == 'strategy_visibility.json')


def _special_source(relative):
    """A file of the development SPECIAL folder; a release carries it in 스페셜/기본 instead (166)."""
    return tuple(part.casefold() for part in Path(relative).parts[:3]) == ('part1', 'program', 'special')


@contextmanager
def _special_reader(root):
    """The application's own literal-only SPECIAL reader, with its program folder importable."""
    program = root / 'Part1/program'
    if not (program / 'strategy_recipe/special_files.py').is_file():
        # Synthetic release fixtures use this version's trusted reader too.
        program = Path(__file__).resolve().parents[2] / 'Part1/program'
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(program))
        from strategy_recipe import special_files
        yield special_files
    finally:
        sys.path[:] = old_path


def _special_release(root):
    """(registry, {release path: bytes} of 스페셜/기본), read with the same reader as application startup.

    A present empty SPECIAL folder deliberately means zero builtins. The old
    registry remains a read-only fallback for projects predating that folder (None).
    Recipe files go to 스페셜/기본 as they are; a SPECIAL*.py goes as the recipe
    its PART3_RECIPE declares, so the installed folder holds data only. Numbers
    above the shipped range belong to the user's 내 전략 folder.
    """
    root = Path(root)
    folder = root / SPECIAL_SOURCE
    if not folder.exists():
        return None
    if (root / INSTALLED_SPECIAL).exists():
        raise ValueError('개발본에는 스페셜 폴더를 두지 않습니다. 기본 스페셜 원본은 ' + SPECIAL_SOURCE + '입니다.')
    with _special_reader(root) as reader:
        entries = reader.read_special_entries(root)
        notices = {row['file']: row['notice'] for row in reader.skipped_special_files(root)}
        identifier, (label, low, high) = reader.ID, reader.INSTALLED_FOLDERS[0]
    files = {}
    for path in sorted([*folder.glob('SPECIAL*.recipe.json'), *folder.glob('SPECIAL*.py')],
                       key=lambda path: path.name.casefold()):
        stem = path.name.removesuffix('.recipe.json').removesuffix('.py').upper()
        if identifier.fullmatch(stem) and int(stem[7:]) > high:
            raise ValueError(f'배포하는 기본 스페셜은 SPECIAL{low}~SPECIAL{high}입니다: {path.name}')
        if path.suffix.casefold() == '.py':
            if stem not in entries or path.name in notices:
                raise ValueError('기본 스페셜을 읽지 못했습니다: ' + notices.get(path.name, path.name))
            target = stem + '.recipe.json'
            data = (json.dumps(entries[stem], ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        else:
            target, data = path.name, path.read_bytes()
        files[INSTALLED_SPECIAL + '/' + label + '/' + target] = data
    return {'schema_version': 2, 'presets': list(entries.values())}, files


@dataclass(frozen=True)
class BuildSettings:
    version: str
    expiry_date: str
    install_minutes: int

    def validate(self):
        if len(self.version) > 64:
            raise ValueError('버전은 64자 이내로 입력하세요.')
        if not re.fullmatch(r'v?[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9_-]+)?', self.version):
            raise ValueError('버전은 v1.0 같은 형식으로 입력하세요.')
        if any(int(v) > 65535 for v in self.version.lstrip('v').split('-')[0].split('.')):
            raise ValueError('버전의 각 숫자는 65535 이하여야 합니다.')
        expires = expiry_epoch(self.expiry_date)
        if expires <= time.time():
            raise ValueError('프로그램 만료일은 오늘 이후여야 합니다.')
        if not 1 <= self.install_minutes <= 10080:
            raise ValueError('설치파일 유효시간은 1~10080분으로 입력하세요.')
        return expires


def expiry_epoch(value):
    # Encode the selected date deterministically. Runtime restores the calendar
    # fields and compares them to the execution PC's local midnight.
    day = date.fromisoformat(value) + timedelta(days=1)
    return int(datetime.combine(day, datetime.min.time(), timezone(timedelta(hours=9))).timestamp())


def source_paths(root):
    root = Path(root).resolve()
    manual_records(root)
    selected = {Path(p) for p in FILES}
    for tree in TREES:
        directory = root / tree
        if not directory.is_dir():
            raise ValueError('필수 폴더 없음: ' + tree)
        for path in directory.rglob('*'):
            rel = path.relative_to(root)
            if _distribution_excluded(rel) or _special_source(rel):
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in DATA_SUFFIXES | {'.py', '.pyw'}:
                selected.add(rel)
    # Check required entry files before parsing their imports.
    for rel in selected:
        if not (root / rel).is_file():
            raise ValueError('필수 파일 없음: ' + rel.as_posix())
    from .source_discovery import discover_project_code
    selected = discover_project_code(root, selected, excluded_parts=SKIP)
    from .resources import discover_resources
    resources = discover_resources(root, [(rel, root / rel) for rel in selected])
    selected.update(Path(rel) for rel in resources.files)
    for rel in sorted(selected):
        if _distribution_excluded(rel):
            raise ValueError('배포 제외 전략·개인 설정이 실행 코드에 연결되어 있습니다: ' + rel.as_posix())
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('프로젝트 밖 파일은 배포할 수 없습니다.')
        if not path.is_file():
            raise ValueError('필수 파일 없음: ' + rel.as_posix())
        yield rel, path


def prepare_data(root, destination, sources):
    destination = Path(destination)
    sources = list(sources)
    code_paths = []
    for rel, source in sources:
        if _distribution_excluded(rel):
            raise ValueError('테스트 전략·승급 상태는 배포할 수 없습니다: ' + Path(rel).as_posix())
        if source.suffix.lower() in {'.py', '.pyw'}:
            code_paths.append(rel.as_posix())
            continue
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if rel.as_posix() == 'Part1/program/config.txt':
            data = SECRETS.sub(r'\1', data.decode('utf-8-sig')).encode('utf-8')
        target.write_bytes(data)
    found = _special_release(root)
    if found is not None:
        normalized, specials = found
        registry = destination / 'settings/strategy_registry.json'
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_bytes(canonical(normalized))
        specials[INSTALLED_SPECIAL + '/안내.md'] = Path(__file__).with_name('special_guide.md').read_bytes()
        for rel, data in specials.items():
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    for rel, value in {'settings/ai_settings.json': {},
                       'Part2/event_backtest.json': {'warehouse': None, 'cores': None,
                                                   'overlap_trading_days': 3}}.items():
        path = destination / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(value))
    # The installation folder may be renamed/moved. Persist the source revision,
    # independently of the distributor's manually chosen v1.0-style release label.
    revision = re.fullmatch(r'수정본([1-9][0-9]*)', Path(root).name)
    metadata = destination / 'runtime/app_version.json'
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_bytes(canonical({'schema': 1, 'revision': int(revision[1]) if revision else None}))
    # Existing root-relative discovery uses directories even when code is virtual.
    for rel in code_paths:
        (destination / rel).parent.mkdir(parents=True, exist_ok=True)
    from .resources import discover_resources
    for rel in discover_resources(root, sources).directories:
        (destination / rel).mkdir(parents=True, exist_ok=True)
    return code_paths


def _portable(text, root):
    text = str(text).replace(str(root), '<project>')
    for value in (str(Path.home()), os.environ.get('TEMP', '')):
        if value:
            text = text.replace(value, '<local>')
    return re.sub(r'[A-Za-z]:[\\/][^\r\n\s\"\'<>|]*', '<path>', text)


def _run(args, root, log, timeout=1800):
    result = subprocess.run([str(a) for a in args], cwd=root, capture_output=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    Path(log).write_text(_portable(result.stdout + result.stderr, root), encoding='utf-8')
    if result.returncode:
        raise RuntimeError('빌드 단계 실패: ' + Path(log).name)


def signing_key(root):
    target = Path(root) / '통합설치' / '.keys' / 'release_rsa.json'
    if target.is_file():
        return json.loads(target.read_text(encoding='utf-8'))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Use Windows/.NET's RSA implementation; no private key leaves this tool.
    script = "$r=New-Object Security.Cryptography.RSACryptoServiceProvider 2048; $r.PersistKeyInCsp=$false; $p=$r.ExportParameters($true); @{modulus=[Convert]::ToBase64String($p.Modulus);exponent=[Convert]::ToBase64String($p.Exponent);d=[Convert]::ToBase64String($p.D)}|ConvertTo-Json -Compress; $r.Dispose()"
    result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-Command', script],
                            capture_output=True, encoding='utf-8', check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    key = json.loads(result.stdout.strip())
    target.write_bytes(canonical(key))
    return key


def _external_imports(root, paths):
    from .dependencies import build_hidden_imports
    application = build_hidden_imports(root, paths)
    # The probe runs with the same frozen standard library. Collect its own
    # imports from current code too, instead of hardcoding html.parser, etc.
    verification = build_hidden_imports(Path(__file__).parent, ['validation_probe.py'])
    return sorted(set(application) | set(verification))


def build_python(root, work, public_key, code_paths, version='v1.0'):
    tools = Path(__file__).resolve().parent
    generated = Path(work) / 'launcher'
    package = generated / 'releasekit'
    package.mkdir(parents=True)
    for name in ('__init__.py', 'license_runtime.py', 'runtime_bundle.py', 'runtime_entry.py', 'install_location.py'):
        shutil.copy2(tools / name, package / name)
    (package / 'runtime_public.py').write_text('PUBLIC_KEY = ' + repr(public_key) + '\n', encoding='utf-8')
    entry = generated / 'entry.py'
    entry.write_text('from releasekit.runtime_entry import main\nraise SystemExit(main())\n', encoding='utf-8')
    numeric = version.lstrip('v').split('-')[0].split('.')
    numbers = tuple(int(v) for v in (numeric + ['0'] * 4)[:4])
    if any(v > 65535 for v in numbers):
        raise ValueError('버전의 각 숫자는 65535 이하여야 합니다.')
    version_file = generated / 'version_info.txt'
    version_file.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(numbers) +
        ", prodvers=" + repr(numbers) + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('041204b0', [StringStruct('ProductName','MOSES'), StringStruct('FileDescription','MOSES'), StringStruct('ProductVersion'," + repr(version) + "), StringStruct('FileVersion'," + repr(version) + ")])]), VarFileInfo([VarStruct('Translation',[1042,1200])])])", encoding='utf-8')
    dist = Path(work) / 'frozen'
    hidden_imports = _external_imports(root, code_paths)
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
            '--name', 'python', '--console', '--contents-directory', '_internal',
            '--python-option', 'X utf8', '--python-option', 'u',
            '--distpath', str(dist), '--workpath', str(Path(work) / 'pyinstaller'),
            '--specpath', str(generated), '--paths', str(generated),
            '--version-file', str(version_file),
            '--icon', str(Path(root) / 'Part3/web/moses.ico'),
            '--exclude-module', 'tkinter', '--exclude-module', 'pytest']
    for name in ('torch', 'transformers'):
        if not any(item == name or item.startswith(name + '.') for item in hidden_imports):
            args.extend(['--exclude-module', name])
    for name in hidden_imports:
        args.extend(['--hidden-import', name])
    for name in ('webview', 'pythonnet', 'clr_loader', 'tzdata', 'jsonschema_specifications'):
        args.extend(['--collect-all', name])
    args.append(str(entry))
    _run(args, root, Path(work) / 'python_compile.log')
    output = dist / 'python'
    if not (output / 'python.exe').is_file():
        raise RuntimeError('배포 실행파일이 생성되지 않았습니다.')
    # UI entry has the Windows subsystem; child worker keeps console pipes working.
    data = bytearray((output / 'python.exe').read_bytes())
    pe_offset = int.from_bytes(data[0x3c:0x40], 'little')
    subsystem = pe_offset + 24 + 68
    data[subsystem:subsystem + 2] = (2).to_bytes(2, 'little')
    (output / 'MOSES.exe').write_bytes(data)
    # pywebview data collection contains a few support scripts: executable code
    # stays in PYZ; distribute DLL/HTML/data only, never original .py files.
    for path in output.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.py', '.pyw'}:
            path.unlink()
    return output


def collect_notices(destination):
    target = Path(destination) / 'THIRD_PARTY_LICENSES'
    target.mkdir()
    for dist in importlib.metadata.distributions():
        name = re.sub('[^A-Za-z0-9_.-]', '_', dist.metadata.get('Name', 'dependency'))
        if name.lower().startswith(('pytest', 'pip', 'setuptools')):
            continue
        for rel in dist.files or []:
            if any(token in rel.name.lower() for token in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(rel))
                if source.is_file():
                    parts = [p for p in rel.parts if p not in ('.', '..') and ':' not in p]
                    out = target / name / Path(*parts)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, out)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        python_target = target / 'Python'
        python_target.mkdir(exist_ok=True)
        shutil.copy2(python_license, python_target / 'LICENSE.txt')


def compiler_path():
    system = Path(os.environ.get('WINDIR', ''))
    candidate = system / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not candidate.is_file():
        raise RuntimeError('Windows .NET Framework 빌드 도구가 필요합니다.')
    return candidate


def build(root, settings, *, emit=print, metaeditor=None):
    root = Path(root).resolve()
    expires = settings.validate()
    sources = list(source_paths(root))
    checked_sources = dict(sources)
    for folder in ('Part1/program/MT5', 'Part2/generic_backtest/reference_sources',
                   'Part2/calculations/reference_sources'):
        for path in (root / folder).glob('*'):
            if path.is_file() and path.suffix.lower() in {'.mq5', '.mqh'}:
                checked_sources[path.relative_to(root)] = path
    before = {rel.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for rel, p in checked_sources.items()}
    manuals = manual_records(root)
    if any(record['sha256'] != before[record['path']] for record in manuals):
        raise RuntimeError('매뉴얼 변경이 감지되어 배포를 중단했습니다. 수정 후 다시 만드세요.')
    build_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    work = root / '통합설치' / '.work' / build_id
    payload = work / 'payload'
    payload.mkdir(parents=True)
    evidence = root / '검증결과' / 'license96' / build_id
    evidence.mkdir(parents=True)
    key = signing_key(root)
    public = {k: key[k] for k in ('modulus', 'exponent')}
    emit('현재 수정본의 원본 확인 및 배포 데이터 준비')
    code_paths = prepare_data(root, payload, sources)
    verify_manual(payload, manuals)
    emit('현재 수정본의 사용자 매뉴얼·간단사용서 PDF 포함 확인')
    from .dependencies import collect_dependencies
    from .resources import discover_resources
    dependencies = collect_dependencies(root, code_paths)
    verification_dependencies = collect_dependencies(Path(__file__).parent, ['validation_probe.py'])
    dependencies['required_imports'] = _external_imports(root, code_paths)
    dependencies['probe_imports'] = sorted(set(dependencies['probe_imports']) |
                                          set(verification_dependencies['probe_imports']))
    dependencies['verification_dependencies'] = verification_dependencies
    (evidence / 'dependencies.json').write_bytes(canonical(dependencies))
    resources = discover_resources(root, sources)
    (evidence / 'resources.json').write_bytes(canonical(resources.to_dict()))
    from .runtime_bundle import build_code_bundle
    manifest = build_code_bundle(root, payload / 'runtime' / 'code.bundle', paths=code_paths)
    emit('MT5 EA·인디케이터 유효기간 적용 및 새 컴파일')
    from .mt5 import build_mt5
    outputs = build_mt5(root, work / 'mt5', expires, metaeditor_path=metaeditor)
    mt5_target = payload / 'Part1' / 'program' / 'MT5'
    mt5_target.mkdir(parents=True, exist_ok=True)
    mt5_manual = payload / 'MT5'
    mt5_manual.mkdir()
    for path in outputs:
        shutil.copy2(path, mt5_target / path.name)
        shutil.copy2(path, mt5_manual / path.name)
    from .release_history import prepare_compatibility, commit_history
    pending_history = prepare_compatibility(
        root, payload, before,
        hashlib.sha256((mt5_target / 'THE_STAFF_OF_MOSES.ex5').read_bytes()).hexdigest())
    emit('Python 실행파일·하위 프로세스 배포 구성')
    frozen = build_python(root, work, public, code_paths, settings.version)
    shutil.copytree(frozen, payload, dirs_exist_ok=True)
    prereq = root / '통합설치' / 'prerequisites' / 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    if not prereq.is_file():
        raise RuntimeError('WebView2 공식 오프라인 설치파일이 필요합니다. 개발자 안내를 확인하세요.')
    (payload / 'prerequisites').mkdir()
    shutil.copy2(prereq, payload / 'prerequisites' / prereq.name)
    collect_notices(payload)
    from .validation import validate_release, write_payload_archive
    validation = validate_release(root, payload, key, dependencies, resources,
                                  code_paths, evidence, emit=emit)
    if validation.get('passed') is not True:
        raise RuntimeError('배포 EXE 검사를 통과하지 않아 설치파일 생성을 중단했습니다.')
    from .validation import verify_mt5_files
    verify_mt5_files(payload)
    verify_manual(payload, manuals)
    archive = work / 'payload.zip'
    write_payload_archive(payload, archive)
    verify_archive_manual(archive, manuals)
    emit('통합설치 EXE 생성')
    installer_source = Path(__file__).with_name('installer.cs').read_text(encoding='utf-8')
    installer_source = installer_source.replace('@@MODULUS@@', public['modulus']).replace('@@EXPONENT@@', public['exponent'])
    numeric = settings.version.lstrip('v').split('-')[0].split('.')
    file_version = '.'.join((numeric + ['0'] * 4)[:4])
    attributes = ('[assembly: System.Reflection.AssemblyTitle("모세의지팡이 통합설치")]\n'
                  '[assembly: System.Reflection.AssemblyProduct("MOSES")]\n'
                  '[assembly: System.Reflection.AssemblyVersion("' + file_version + '")]\n'
                  '[assembly: System.Reflection.AssemblyFileVersion("' + file_version + '")]\n'
                  '[assembly: System.Reflection.AssemblyInformationalVersion("' + settings.version + '")]\n\n')
    installer_source = installer_source.replace('namespace MosesInstaller', attributes + 'namespace MosesInstaller', 1)
    generated = work / 'installer.cs'
    generated.write_text(installer_source, encoding='utf-8-sig')
    raw_installer = work / '모세의지팡이 통합설치.exe'
    args = [compiler_path(), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
            '/win32icon:' + str(root / 'Part3/web/moses.ico'),
            '/out:' + str(raw_installer), '/resource:' + str(archive) + ',MosesPayload']
    for name in ('System.Windows.Forms', 'System.Drawing', 'System.Web.Extensions',
                 'System.Security', 'System.IO.Compression', 'System.IO.Compression.FileSystem'):
        args.append('/reference:' + name + '.dll')
    args.append(str(generated))
    _run(args, root, work / 'installer_compile.log')
    final_folder = root / '배포' / (settings.version + '_' + build_id)
    final_folder.mkdir(parents=True)
    final = final_folder / '모세의지팡이 통합설치.exe'
    try:
        shutil.copy2(raw_installer, final)
        after = {rel.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for rel, p in checked_sources.items()}
        if after != before:
            raise RuntimeError('원본 변경이 감지되어 배포를 중단했습니다.')
        verify_archive_manual(archive, manuals)
    except Exception:
        final.unlink(missing_ok=True)
        raise
    # Copy the large unsigned EXE and verify originals BEFORE issuing. Only the
    # small signed trailer is appended after the installation clock begins.
    issued = int(time.time())
    policy = {'schema': 1, 'version': settings.version, 'expires_at': expires,
              'expires_local': expires + 9 * 3600,
              'issued_at': issued, 'install_expires_at': issued + settings.install_minutes * 60}
    try:
        append_policy(final, sign_policy(policy, key))
        history_info = commit_history(root, pending_history)
    except Exception:
        final.unlink(missing_ok=True)
        raise
    report = {'version': settings.version, 'source_folder': root.name,
              'expiry_date': settings.expiry_date, 'expiry_timezone': 'execution PC local calendar',
              'install_minutes': settings.install_minutes, 'issued_at': issued,
              'install_expires_at': policy['install_expires_at'],
              'installer': final.relative_to(root).as_posix(),
              'installer_sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
              'source_unchanged': True, 'source_files': len(before),
              'python_version': sys.version.split()[0],
              'manual': manuals[0], 'quick_guide': manuals[1],
              'mql_provenance': history_info,
              'release_validation': {'passed': validation['passed'],
                                     'report': (evidence / 'release_validation.json').relative_to(root).as_posix(),
                                     'modes': [check['mode'] for check in validation['checks']]},
              'modules': len(manifest.get('entries', {})),
              'payload_files': [p.relative_to(payload).as_posix() for p in sorted(payload.rglob('*')) if p.is_file()],
              'limitations': ['Local PC clock can be changed', 'Python bytecode can be reverse engineered',
                              'MT5 binaries are not PC-bound', 'Fresh-PC integration needs a separate Windows PC']}
    (evidence / 'build_summary.json').write_bytes(canonical(report))
    for name in ('python_compile.log', 'installer_compile.log'):
        shutil.copy2(work / name, evidence / name)
    emit('완료: ' + final.relative_to(root).as_posix())
    return final, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--expires', required=True, help='YYYY-MM-DD, execution PC calendar date inclusive')
    parser.add_argument('--install-minutes', type=int, default=10)
    parser.add_argument('--metaeditor')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build(root, BuildSettings(args.version, args.expires, args.install_minutes), metaeditor=args.metaeditor)


if __name__ == '__main__':
    main()
