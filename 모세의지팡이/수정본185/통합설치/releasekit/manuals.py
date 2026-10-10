"""Read the current revision's published manuals, never their editing sources."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

MANUAL_RELATIVE = '매뉴얼/모세_사용자_매뉴얼.pdf'
QUICK_RELATIVE = '매뉴얼/모세_간단사용서.pdf'
MANUALS = (MANUAL_RELATIVE, QUICK_RELATIVE)


def manual_record(root, relative=MANUAL_RELATIVE):
    root = Path(root).resolve()
    path = root / relative
    if not path.is_file():
        raise ValueError('매뉴얼이 없습니다: ' + relative)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('매뉴얼은 현재 수정본 폴더 안에 보관하세요.')
    data = path.read_bytes()
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
        raise ValueError('매뉴얼 PDF 파일을 확인하세요: ' + relative)
    return {'path': relative, 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}


def manual_records(root):
    return [manual_record(root, relative) for relative in MANUALS]


def verify_manual(root, expected):
    actual = [manual_record(root, record['path']) for record in expected]
    if actual != expected:
        raise RuntimeError('매뉴얼 변경이 감지되어 배포를 중단했습니다. 수정 후 다시 만드세요.')
    return actual


def verify_archive_manual(archive, expected):
    with zipfile.ZipFile(archive) as stream:
        for record in expected:
            data = stream.read(record['path'])
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise RuntimeError('통합설치에 포함된 매뉴얼이 현재 매뉴얼과 다릅니다.')
