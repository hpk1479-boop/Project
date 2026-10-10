"""Offline regression fixtures; no installer execution or actual network I/O."""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request

import pytest

from releasekit import prerequisites as p


SIGNED = {'Status': 'Valid', 'Subject': 'CN=Microsoft Corporation, O=Microsoft Corporation, C=US',
          'Issuer': 'CN=Microsoft Code Signing PCA 2024, O=Microsoft Corporation, C=US'}
DATA = b'MZ' + b'\0' * 30
OFFICIAL_FILE = 'https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/uuid/' + p.WEBVIEW2_FILENAME


class Response(io.BytesIO):
    def __init__(self, content=DATA, length=None, url=OFFICIAL_FILE):
        super().__init__(content)
        self.headers = {} if length is False else {'Content-Length': str(len(content) if length is None else length)}
        self.url = url

    def geturl(self):
        return self.url


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'MIN_INSTALLER_BYTES', 16)
    monkeypatch.setattr(p, 'MAX_INSTALLER_BYTES', 128)
    monkeypatch.setattr(p, 'DOWNLOAD_CHUNK_BYTES', 4)
    monkeypatch.setattr(p, '_signature_record', lambda path: SIGNED.copy())
    monkeypatch.setattr(p.time, 'sleep', lambda seconds: None)
    return tmp_path / 'new revision'


def setup(project):
    project.mkdir(exist_ok=True)
    directory = project / '통합설치/prerequisites'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / p.WEBVIEW2_FILENAME, directory / (p.WEBVIEW2_FILENAME + '.part')


def responses(monkeypatch, *outcomes):
    calls = []
    iterator = iter(outcomes)

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            outcome = next(iterator)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    monkeypatch.setattr(p, 'build_opener', lambda *handlers: Opener())
    return calls


def test_downloads_verified_offline_installer_atomically_with_progress(project, monkeypatch):
    target, partial = setup(project)
    calls = responses(monkeypatch, Response())
    logs = []
    assert p.ensure_webview2(project, logs.append) == target
    assert target.read_bytes() == DATA
    assert not partial.exists()
    assert len(calls) == 1
    assert calls[0][0].full_url == 'https://go.microsoft.com/fwlink/p/?LinkId=2124701'
    assert calls[0][0].headers['Accept-encoding'] == 'identity'
    assert any('다운로드 중' in line for line in logs)
    assert any('%' in line for line in logs)
    assert 'Microsoft 서명 검증 완료' in logs[-1]
    assert all(str(project) not in line for line in logs)


def test_reuses_only_verified_cache_and_cleans_old_partial(project, monkeypatch):
    target, partial = setup(project)
    target.write_bytes(DATA)
    partial.write_bytes(b'aborted download')
    calls = responses(monkeypatch)
    assert p.ensure_webview2(project, lambda line: None) == target
    assert not calls
    assert not partial.exists()


def test_invalid_cached_file_is_removed_and_replaced(project, monkeypatch):
    target, _ = setup(project)
    target.write_bytes(b'bad')
    responses(monkeypatch, Response())
    logs = []
    p.ensure_webview2(project, logs.append)
    assert target.read_bytes() == DATA
    assert any('다시 다운로드' in line for line in logs)


def test_transient_network_failure_gets_one_retry(project, monkeypatch):
    target, partial = setup(project)
    calls = responses(monkeypatch, URLError('connection unavailable'), Response())
    logs = []
    assert p.ensure_webview2(project, logs.append) == target
    assert len(calls) == 2
    assert not partial.exists()
    assert any('재시도' in line for line in logs)


def test_network_failure_is_bounded_and_does_not_expose_local_paths(project, monkeypatch):
    target, partial = setup(project)
    calls = responses(monkeypatch, URLError(str(project)), URLError(str(project)))
    with pytest.raises(p.PrerequisiteError, match='인터넷 연결') as caught:
        p.ensure_webview2(project, lambda line: None)
    assert len(calls) == 2
    assert str(project) not in str(caught.value)
    assert not target.exists() and not partial.exists()


@pytest.mark.parametrize('url', [
    'http://go.microsoft.com/fwlink/p/?LinkId=2124701',
    'https://go.microsoft.com.evil.test/setup.exe',
    'https://microsoft.com/setup.exe',
    'https://evil.test/download.microsoft.com/setup.exe',
    'https://user:password@go.microsoft.com/setup.exe',
    'https://download.microsoft.com:444/setup.exe',
    'file:///private.exe',
])
def test_untrusted_or_insecure_download_urls_are_blocked(url):
    with pytest.raises(p.PrerequisiteError, match='공식 HTTPS'):
        p._trusted_url(url)


@pytest.mark.parametrize('url', [p.WEBVIEW2_URL, OFFICIAL_FILE,
    'https://download.microsoft.com/file.exe',
    'https://msedge.sf.tlu.dl.delivery.mp.microsoft.com/file.exe'])
def test_official_hosts_are_permitted(url):
    assert p._trusted_url(url) == url


def test_redirect_is_checked_before_urllib_can_follow_it():
    handler = p._MicrosoftRedirectHandler()
    with pytest.raises(p.PrerequisiteError, match='공식 HTTPS'):
        handler.redirect_request(Request(p.WEBVIEW2_URL), None, 302, 'Found', {}, 'https://evil.test/file.exe')


@pytest.mark.parametrize('response, reason', [
    (lambda: Response(url='https://evil.test/installer.exe'), '공식 HTTPS'),
    (lambda: Response(url=OFFICIAL_FILE.replace(p.WEBVIEW2_FILENAME, 'MicrosoftEdgeWebview2Setup.exe')), 'x64 오프라인'),
    (lambda: Response(content=b'<html>not executable</html>'), '실행파일'),
    (lambda: Response(length=6), '다운로드 크기'),
    (lambda: Response(length=129), '다운로드 크기'),
    (lambda: Response(length='invalid'), '크기 정보'),
    (lambda: Response(length=60), '완료되지'),
    (lambda: Response(length=20), '허용 범위'),
])
def test_wrong_or_truncated_download_is_removed(project, monkeypatch, response, reason):
    target, partial = setup(project)
    responses(monkeypatch, response())
    with pytest.raises(p.PrerequisiteError, match=reason):
        p.ensure_webview2(project, lambda line: None)
    assert not target.exists() and not partial.exists()


def test_download_without_content_length_is_validated_by_final_size(project, monkeypatch):
    target, partial = setup(project)
    responses(monkeypatch, Response(length=False))
    p.ensure_webview2(project, lambda line: None)
    assert target.read_bytes() == DATA
    assert not partial.exists()


@pytest.mark.parametrize('record', [
    {'Status': 'NotSigned', 'Subject': SIGNED['Subject'], 'Issuer': SIGNED['Issuer']},
    {'Status': 'HashMismatch', 'Subject': SIGNED['Subject'], 'Issuer': SIGNED['Issuer']},
    {'Status': 'Valid', 'Subject': 'CN=Microsoft Corporation, O=Microsoft Corporation Evil', 'Issuer': SIGNED['Issuer']},
    {'Status': 'Valid', 'Subject': 'CN=Other company, O=Microsoft Corporation', 'Issuer': SIGNED['Issuer']},
    {'Status': 'Valid', 'Subject': SIGNED['Subject'], 'Issuer': 'CN=Untrusted CA, O=Other corporation'},
    {'Status': 'Valid', 'Subject': None, 'Issuer': None},
])
def test_unsigned_tampered_or_non_microsoft_signatures_are_rejected(project, monkeypatch, record):
    target, partial = setup(project)
    monkeypatch.setattr(p, '_signature_record', lambda path: record)
    responses(monkeypatch, Response())
    with pytest.raises(p.PrerequisiteError, match='유효한 Microsoft 서명'):
        p.ensure_webview2(project, lambda line: None)
    assert not target.exists() and not partial.exists()


def test_interrupt_cleans_partial_without_creating_final_file(project, monkeypatch):
    target, partial = setup(project)

    def interrupted(path, emit):
        path.write_bytes(DATA)
        raise KeyboardInterrupt

    monkeypatch.setattr(p, '_download', interrupted)
    with pytest.raises(KeyboardInterrupt):
        p.ensure_webview2(project, lambda line: None)
    assert not target.exists() and not partial.exists()


def test_cannot_delete_directory_named_like_partial_file(project, monkeypatch):
    target, partial = setup(project)
    partial.mkdir()
    keep = partial / 'keep.txt'
    keep.write_text('must preserve')
    with pytest.raises(p.PrerequisiteError, match='정리하지 못했습니다'):
        p.ensure_webview2(project, lambda line: None)
    assert keep.exists()
    assert not target.exists()


def test_reparse_folder_is_rejected_before_any_write(project, monkeypatch):
    target, _ = setup(project)
    monkeypatch.setattr(p, '_is_reparse', lambda path: path.name == 'prerequisites')
    with pytest.raises(p.PrerequisiteError, match='연결 폴더'):
        p.ensure_webview2(project, lambda line: None)
    assert not target.exists()


def test_signature_command_uses_literal_path_without_interpolating_filename(tmp_path, monkeypatch):
    path = tmp_path / "odd' [$] name.exe"
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(SIGNED), stderr='')

    monkeypatch.setattr(p.subprocess, 'run', run)
    monkeypatch.setenv('PSModulePath', 'PowerShell 7 incompatible modules')
    assert p._signature_record(path) == SIGNED
    args, kwargs = calls[0]
    assert str(path) not in ' '.join(args)
    assert kwargs['env']['MOSES_WEBVIEW2_FILE'] == str(path)
    assert 'PSModulePath' not in kwargs['env']
    assert kwargs['errors'] == 'replace'
    script = base64.b64decode(args[-1]).decode('utf-16-le')
    assert 'Get-AuthenticodeSignature -LiteralPath $env:MOSES_WEBVIEW2_FILE' in script
    assert "Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'" in script
    assert str(path) not in script
    assert not any('install' in arg.lower() for arg in args)


@pytest.mark.parametrize('result', [
    SimpleNamespace(returncode=1, stdout='', stderr='private user path'),
    SimpleNamespace(returncode=0, stdout='not json', stderr='private user path'),
    SimpleNamespace(returncode=0, stdout='[]', stderr=''),
])
def test_signature_subprocess_failures_are_safe(tmp_path, monkeypatch, result):
    monkeypatch.setattr(p.subprocess, 'run', lambda *args, **kwargs: result)
    with pytest.raises(p.PrerequisiteError, match='Windows 서명') as caught:
        p._signature_record(tmp_path / 'file.exe')
    assert 'private user path' not in str(caught.value)


def test_missing_revision_path_failure_has_no_user_path(tmp_path):
    absent = tmp_path / 'absent private revision'
    with pytest.raises(p.PrerequisiteError, match='준비 폴더') as caught:
        p.ensure_webview2(absent, lambda line: None)
    assert str(absent) not in str(caught.value)
