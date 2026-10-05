"""Read the current revision's published manual, never its editing sources."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

MANUAL_RELATIVE = '매뉴얼/모세_사용자_매뉴얼.pdf'


def manual_record(root):
    root = Path(root).resolve()
    path = root / MANUAL_RELATIVE
    if not path.is_file():
        raise ValueError('사용자 매뉴얼이 없습니다: ' + MANUAL_RELATIVE)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('매뉴얼은 현재 수정본 폴더 안에 보관하세요.')
    data = path.read_bytes()
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
        raise ValueError('사용자 매뉴얼 PDF 파일을 확인하세요: ' + MANUAL_RELATIVE)
    return {'path': MANUAL_RELATIVE, 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}


def verify_manual(root, expected):
    actual = manual_record(root)
    if actual != expected:
        raise RuntimeError('매뉴얼 변경이 감지되어 배포를 중단했습니다. 수정 후 다시 만드세요.')
    return actual


def verify_archive_manual(archive, expected):
    with zipfile.ZipFile(archive) as stream:
        data = stream.read(MANUAL_RELATIVE)
    if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise RuntimeError('통합설치에 포함된 매뉴얼이 현재 매뉴얼과 다릅니다.')
