"""Fetch the official offline WebView2 prerequisite; never execute it here."""
from __future__ import annotations

import base64
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


WEBVIEW2_FILENAME = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
# Microsoft Evergreen Standalone Installer, x64. 2124703 is a bootstrapper.
# https://developer.microsoft.com/en-us/microsoft-edge/webview2
WEBVIEW2_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124701'
MIN_INSTALLER_BYTES = 10 * 1024 * 1024
MAX_INSTALLER_BYTES = 600 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_ATTEMPTS = 2
DOWNLOAD_TIMEOUT = 60


class PrerequisiteError(RuntimeError):
    """Safe, user-facing failure without local paths or subprocess output."""


def _trusted_url(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        valid = (parts.scheme == 'https' and parts.username is None
                 and parts.password is None and parts.port in (None, 443)
                 and (host in {'go.microsoft.com', 'download.microsoft.com'}
                      or host.endswith('.dl.delivery.mp.microsoft.com')))
    except ValueError:
        valid = False
    if not valid:
        raise PrerequisiteError('WebView2 다운로드 주소가 Microsoft 공식 HTTPS 주소가 아닙니다.')
    return url


class _MicrosoftRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_reparse(path):
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _paths(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise PrerequisiteError('배포 대상 폴더를 확인할 수 없습니다.')
    setup = root / '통합설치'
    directory = setup / 'prerequisites'
    if _is_reparse(setup) or _is_reparse(directory):
        raise PrerequisiteError('WebView2 준비 폴더에 연결 폴더를 사용할 수 없습니다.')
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise PrerequisiteError('WebView2 준비 폴더가 배포 대상 폴더 밖에 있습니다.')
    target = directory / WEBVIEW2_FILENAME
    partial = directory / (WEBVIEW2_FILENAME + '.part')
    if _is_reparse(target) or _is_reparse(partial):
        raise PrerequisiteError('WebView2 설치파일에 연결 파일을 사용할 수 없습니다.')
    return target, partial


def _signature_record(path):
    # The filename is passed in an environment variable rather than inserted
    # into PowerShell source. -LiteralPath handles brackets and quotes safely.
    script = """$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Import-Module -Name (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -Force
$signature = Get-AuthenticodeSignature -LiteralPath $env:MOSES_WEBVIEW2_FILE
[ordered]@{Status=[string]$signature.Status; Subject=[string]$signature.SignerCertificate.Subject; Issuer=[string]$signature.SignerCertificate.Issuer} | ConvertTo-Json -Compress
"""
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    env = os.environ.copy()
    # PowerShell 7's inherited PSModulePath can make Windows PowerShell 5.1
    # auto-load incompatible modules when it is launched by Python.
    env.pop('PSModulePath', None)
    env['MOSES_WEBVIEW2_FILE'] = str(path)
    powershell = Path(env.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run(
            [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=90,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0:
            raise ValueError('signature command failed')
        record = json.loads(result.stdout.lstrip('\ufeff').strip())
        if not isinstance(record, dict):
            raise ValueError('invalid signature result')
        return record
    except (OSError, subprocess.SubprocessError, ValueError):
        raise PrerequisiteError('WebView2 설치파일의 Windows 서명을 확인하지 못했습니다.') from None


def _microsoft_certificate(value):
    # Match complete distinguished-name fields, not a loose substring.
    if not isinstance(value, str):
        return False
    return bool(re.search(r'(?:^|,)\s*O\s*=\s*Microsoft Corporation\s*(?:,|$)', value, re.I))


def _validate_file(path):
    try:
        size = path.stat().st_size
        with path.open('rb') as stream:
            magic = stream.read(2)
    except OSError:
        raise PrerequisiteError('WebView2 설치파일을 읽을 수 없습니다.') from None
    if magic != b'MZ' or not MIN_INSTALLER_BYTES <= size <= MAX_INSTALLER_BYTES:
        raise PrerequisiteError('WebView2 오프라인 설치파일의 형식 또는 크기가 올바르지 않습니다.')
    record = _signature_record(path)
    if (record.get('Status') != 'Valid'
            or not _microsoft_certificate(record.get('Subject'))
            or not _microsoft_certificate(record.get('Issuer'))
            or not re.search(r'(?:^|,)\s*CN\s*=\s*Microsoft Corporation\s*(?:,|$)',
                             str(record.get('Subject', '')), re.I)):
        raise PrerequisiteError('WebView2 설치파일이 유효한 Microsoft 서명 파일이 아닙니다.')


def _unlink(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise PrerequisiteError('WebView2 임시 설치파일을 정리하지 못했습니다.') from None


def _download(partial, emit):
    _trusted_url(WEBVIEW2_URL)
    opener = build_opener(_MicrosoftRedirectHandler(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(WEBVIEW2_URL, headers={'User-Agent': 'MOSES-ReleaseBuilder/1.0', 'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
        final_url = _trusted_url(response.geturl())
        if Path(urlsplit(final_url).path).name.lower() != WEBVIEW2_FILENAME.lower():
            raise PrerequisiteError('WebView2 공식 주소에서 x64 오프라인 설치파일을 받지 못했습니다.')
        raw_length = response.headers.get('Content-Length')
        try:
            expected = int(raw_length) if raw_length is not None else None
        except (TypeError, ValueError):
            raise PrerequisiteError('WebView2 다운로드 크기 정보를 확인하지 못했습니다.') from None
        if expected is not None and not MIN_INSTALLER_BYTES <= expected <= MAX_INSTALLER_BYTES:
            raise PrerequisiteError('WebView2 오프라인 설치파일의 다운로드 크기가 올바르지 않습니다.')
        downloaded = 0
        milestone = 0
        with partial.open('xb') as stream:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                if downloaded == 0 and chunk[:2] != b'MZ':
                    raise PrerequisiteError('WebView2 다운로드 응답이 실행파일이 아닙니다.')
                downloaded += len(chunk)
                if downloaded > MAX_INSTALLER_BYTES or (expected is not None and downloaded > expected):
                    raise PrerequisiteError('WebView2 다운로드 크기가 허용 범위를 초과했습니다.')
                stream.write(chunk)
                current = (downloaded * 100 // expected // 10) if expected else downloaded // (10 * 1024 * 1024)
                if current > milestone:
                    milestone = current
                    suffix = f'{min(100, downloaded * 100 // expected)}%' if expected else f'{downloaded // (1024 * 1024)}MB'
                    emit('WebView2 다운로드 중… ' + suffix)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is not None and downloaded != expected:
            raise PrerequisiteError('WebView2 다운로드가 완료되지 않았습니다. 다시 실행해 주세요.')


def ensure_webview2(root, emit=print):
    """Return a verified cached/downloaded x64 installer beneath revision root.

    Only prepares a file for packaging. The developer PC never installs it.
    Existing invalid cache files and interrupted .part files are discarded.
    Network failures get one fresh retry; invalid data fails immediately.
    """
    try:
        target, partial = _paths(root)
    except OSError:
        raise PrerequisiteError('WebView2 준비 폴더를 확인하거나 만들지 못했습니다.') from None
    _unlink(partial)
    if target.exists():
        try:
            _validate_file(target)
        except PrerequisiteError:
            _unlink(target)
            emit('기존 WebView2 설치파일 검증 실패. 다시 다운로드합니다.')
        else:
            emit('WebView2 설치파일 검증 완료')
            return target
    emit('WebView2 다운로드 중…')
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _download(partial, emit)
            _validate_file(partial)
            partial.replace(target)
            emit('WebView2 다운로드 및 Microsoft 서명 검증 완료')
            return target
        except (HTTPError, URLError, HTTPException, TimeoutError, ConnectionError, OSError):
            _unlink(partial)
            if attempt + 1 == DOWNLOAD_ATTEMPTS:
                raise PrerequisiteError('WebView2 다운로드에 실패했습니다. 인터넷 연결 후 다시 실행해 주세요.') from None
            emit('WebView2 다운로드 재시도 중…')
            time.sleep(1)
        except BaseException:
            _unlink(partial)
            raise
    raise PrerequisiteError('WebView2 설치파일을 준비하지 못했습니다.')
