"""Running the tests never writes into a revision's 검증결과 unless MOSES_EVIDENCE_ROOT asks for it.

Older browser tests used to rewrite their screenshots in 검증결과 on every run, which overwrote the
evidence of the revision they ran in (수정본113). Evidence now goes where MOSES_EVIDENCE_ROOT points;
under pytest that is a temporary folder unless the caller chose one.
"""
import os
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_SOURCES = sorted([*ROOT.glob('tests/test_*.py'), *ROOT.glob('tests/*.cjs'),
                       *ROOT.glob('Part3/tests/test_*.py'), *ROOT.glob('Part3/tests/*.cjs')])


def test_pytest_gives_the_tests_a_temporary_evidence_folder_outside_the_project():
    if os.environ.get('MOSES_EVIDENCE_ROOT_AUTO') != '1':
        pytest.skip('MOSES_EVIDENCE_ROOT was chosen by the caller for this run')
    folder = Path(os.environ['MOSES_EVIDENCE_ROOT']).resolve()
    assert folder.is_dir()
    assert not folder.is_relative_to(ROOT.resolve())


@pytest.mark.parametrize('source', TEST_SOURCES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_evidence_path_goes_through_moses_evidence_root(source):
    for number, line in enumerate(source.read_text('utf-8').splitlines(), 1):
        if re.search(r"""['"]검증결과[/'"]""", line) and 'MOSES_EVIDENCE_ROOT' not in line:
            # Reading or naming a path inside 검증결과 is allowed only through the evidence root.
            pytest.fail(f'{source.relative_to(ROOT).as_posix()}:{number} writes to 검증결과 directly: {line.strip()}')
