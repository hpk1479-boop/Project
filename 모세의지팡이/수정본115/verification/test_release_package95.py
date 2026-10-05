"""Fresh-install isolation, manifest integrity and relocation behavior."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_release95 as release


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    for name in release.TREES:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in release.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    (root / 'Part1/program/config.txt').write_text(
        'TELEGRAM_TOKEN=private\nTELEGRAM_CHAT_ID=private\nTELEGRAM_COMMAND_CHAT_IDS=private\n'
        'GEMINI_API_KEY=private\nWONBI_SIGMA=2.0\n', encoding='utf-8')
    for name in ('settings/ai_settings.json', 'Part1/event_state/signal_receipts.json',
                 'Part1/program/logs/old.txt', 'Part3/generated/Test_SPECIAL001.py',
                 'Part3/projects/ai_settings.json', 'runtime/ai_service.json',
                 'Part2/scenarios/xau_continuous.json',
                 'Part2/.venv-generic/pyvenv.cfg', '검증결과/old.txt', 'models/gguf/private.gguf'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private', encoding='utf-8')
    return root


def test_new_install_excludes_private_state_and_preserves_original(source):
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    target, manifest = release.build(source)
    assert all((source / name).read_bytes() == data for name, data in before.items())
    assert not any(b'private' in p.read_bytes() for p in target.rglob('*') if p.is_file())
    assert (target / 'settings/ai_settings.json').read_text('utf-8').strip() == '{}'
    assert 'WONBI_SIGMA=2.0' in (target / 'Part1/program/config.txt').read_text('utf-8')
    assert len(manifest['files']) == len([p for p in target.rglob('*') if p.is_file()]) - 1


def test_manifest_and_files_work_after_relocation(source):
    target, manifest = release.build(source)
    moved = source.parent / '다른 위치' / 'MOSES95'
    moved.parent.mkdir()
    shutil.move(str(target), str(moved))
    saved = json.loads((moved / 'release_manifest.json').read_text('utf-8'))
    assert saved == manifest
    for name, row in saved['files'].items():
        data = (moved / name).read_bytes()
        assert not Path(name).is_absolute() and '..' not in Path(name).parts and ':' not in name
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']


@pytest.mark.parametrize('destination', ['../outside', 'Part1', '배포', '배포/../Part1'])
def test_builder_rejects_unsafe_target_before_mutation(source, destination):
    with pytest.raises(ValueError):
        release.build(source, destination)
    assert not (source / '배포').exists()


def test_existing_distribution_never_overwritten(source):
    target, _ = release.build(source)
    marker = target / 'user.txt'
    marker.write_text('keep', encoding='utf-8')
    with pytest.raises(FileExistsError):
        release.build(source)
    assert marker.read_text('utf-8') == 'keep'


def test_missing_required_file_is_reported_before_build(source):
    (source / 'START_MOSES.pyw').unlink()
    with pytest.raises(ValueError, match='필수 배포 파일'):
        release.build(source)
    assert not (source / '배포').exists()
