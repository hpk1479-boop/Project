"""Local license check speed, run only on request (moved out of 통합설치/releasekit/tests/test_license.py in 수정본180).

The behaviour (a DPAPI-protected token is verified locally) stays in test_license.py.
"""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '통합설치'))
from releasekit import license_runtime as licensing
from releasekit.builder import signing_key


def test_local_license_check_stays_under_100ms():
    if os.name != 'nt':
        pytest.skip('Windows only')
    with tempfile.TemporaryDirectory(prefix='license-key-') as folder:
        key = signing_key(Path(folder))
    public = {k: key[k] for k in ('modulus', 'exponent')}
    policy = {'schema': 1, 'version': 'v1.0', 'expires_at': 2000000000, 'install_expires_at': 1999999900}
    token = licensing.protect_token(licensing.sign_policy(policy, key))
    times = []
    with patch('pathlib.Path.read_bytes', return_value=token):
        for _ in range(20):
            start = time.perf_counter()
            assert licensing.check_license(Path('.'), now=1999999999, public_key=public)
            times.append((time.perf_counter() - start) * 1000)
    # The requirement is actual local verification, not zero processing time.
    assert max(times) < 100, times
