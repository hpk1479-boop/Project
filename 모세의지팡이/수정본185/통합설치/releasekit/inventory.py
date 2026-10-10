"""Evidence of unchanged original files, without recording absolute paths."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

EXCLUDED = {'검증결과', '배포', '.v', '__pycache__', '.pytest_cache'}
RELOCATIONS = {
    '통합설치.bat': '통합설치/이전방식/통합설치.bat',
    'build_release95.py': '통합설치/이전방식/build_release95.py',
    '통합설치/install.bat': '통합설치/이전방식/install.bat',
    '통합설치/install.ps1': '통합설치/이전방식/install.ps1',
    '통합설치/README.md': '통합설치/이전방식/README95.md',
}


def inventory(root):
    result = {}
    for item in Path(root).rglob('*'):
        rel = item.relative_to(root)
        if any(part in EXCLUDED for part in rel.parts) or not item.is_file():
            continue
        result[rel.as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def compare(source, target, expected):
    source_now = inventory(source)
    changed_source = [p for p, h in expected.items() if source_now.get(p) != h]
    changed_copy = [p for p, h in expected.items()
                    if not (Path(target) / RELOCATIONS.get(p, p)).is_file()
                    or hashlib.sha256((Path(target) / RELOCATIONS.get(p, p)).read_bytes()).hexdigest() != h]
    return {'source_changed': changed_source, 'copy_changed': changed_copy,
            'original_files': len(expected), 'unchanged_packaging_archives': RELOCATIONS}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    output = root / '검증결과' / 'license96' / 'original_inventory.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    source = root.parent / '수정본95'
    output.write_text(json.dumps(inventory(source), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'original_files': len(json.loads(output.read_text(encoding='utf-8')))}, ensure_ascii=False))
