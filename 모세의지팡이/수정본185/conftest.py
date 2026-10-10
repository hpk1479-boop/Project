"""Test runs never write into 검증결과 unless asked.

Browser tests save screenshots and reports. Under pytest they go to a temporary folder, so running
the suite (in this revision or a copy of an older one) cannot overwrite a revision's evidence.
To keep them as a revision's evidence, set MOSES_EVIDENCE_ROOT to that revision's 검증결과 folder.
"""
import os
import shutil
import tempfile

_CREATED = []


def pytest_configure(config):
    if os.environ.get('MOSES_EVIDENCE_ROOT'):
        return
    folder = tempfile.mkdtemp(prefix='moses-evidence-')
    os.environ['MOSES_EVIDENCE_ROOT'] = folder
    os.environ['MOSES_EVIDENCE_ROOT_AUTO'] = '1'
    _CREATED.append(folder)


def pytest_unconfigure(config):
    while _CREATED:
        folder = _CREATED.pop()
        if os.environ.get('MOSES_EVIDENCE_ROOT') == folder:
            del os.environ['MOSES_EVIDENCE_ROOT']
            os.environ.pop('MOSES_EVIDENCE_ROOT_AUTO', None)
        shutil.rmtree(folder, ignore_errors=True)
