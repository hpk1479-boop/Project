"""Read-only discovery of portable GGUF files; never loads an AI model."""
from __future__ import annotations

import os
from pathlib import Path
import re
import struct

from .local_gguf import PROJECT_ROOT

DIRECTORY = 'models/gguf'
SPLIT = re.compile(r'-(\d{5})-of-(\d{5})\.gguf$', re.I)


def _check_header(path):
    # Listing reads no tensors or tokenizer metadata, even for a large model.
    with path.open('rb') as handle:
        header = handle.read(24)
    if (len(header) != 24 or header[:4] != b'GGUF' or
            struct.unpack('<I', header[4:8])[0] not in (2, 3)):
        raise ValueError('GGUF 파일 형식 확인 실패')


def models(root=PROJECT_ROOT):
    root = Path(root).resolve()
    directory = root / DIRECTORY
    result = {'directory': DIRECTORY, 'models': [], 'warnings': []}
    if not directory.exists():
        return result
    if not directory.resolve().is_relative_to(root):
        raise ValueError('GGUF 모델 폴더는 프로젝트 내부에 있어야 합니다.')
    if not directory.is_dir():
        raise ValueError('models/gguf 위치에 모델 폴더를 준비하세요.')

    def unreadable(_):
        raise ValueError('GGUF 모델 폴더를 읽지 못했습니다. 폴더 접근 권한을 확인하세요.')

    for base, folders, files in os.walk(directory, followlinks=False, onerror=unreadable):
        folders[:] = sorted(d for d in folders if (Path(base) / d).resolve().is_relative_to(root)
                             and not (Path(base) / d).is_symlink()
                             and not (hasattr(Path, 'is_junction') and (Path(base) / d).is_junction()))
        for name in sorted(files):
            if not name.lower().endswith('.gguf'):
                continue
            split = SPLIT.search(name)
            if split and int(split[1]) != 1:
                continue
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if not path.resolve().is_relative_to(root):
                result['warnings'].append(relative + ': 프로젝트 밖의 파일은 제외했습니다.')
                continue
            try:
                _check_header(path)
                result['models'].append({'name': name, 'path': relative, 'bytes': path.stat().st_size})
            except (OSError, ValueError, struct.error):
                result['warnings'].append(relative + ': GGUF 파일 형식을 확인하지 못해 제외했습니다.')
    result['models'].sort(key=lambda item: (item['name'].casefold(), item['path']))
    return result
