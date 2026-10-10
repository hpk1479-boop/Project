from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from releasekit import license_runtime as licensing
from releasekit.builder import signing_key, BuildSettings, expiry_epoch


class LicenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A throwaway project root: the real release key in 통합설치/.keys is never read or created.
        cls.temporary = tempfile.TemporaryDirectory(prefix='license-key-')
        cls.key = signing_key(Path(cls.temporary.name))
        cls.public = {k: cls.key[k] for k in ('modulus', 'exponent')}
        cls.policy = {'schema': 1, 'version': 'v1.0', 'expires_at': 2000000000,
                      'install_expires_at': 1999999900}
        cls.envelope = licensing.sign_policy(cls.policy, cls.key)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_signature_and_tampering(self):
        self.assertEqual(licensing.verify_policy(self.envelope, self.public), self.policy)
        outer = json.loads(self.envelope)
        bad = dict(self.policy, expires_at=2100000000)
        outer['payload'] = base64.b64encode(licensing.canonical(bad)).decode()
        with self.assertRaises(ValueError):
            licensing.verify_policy(licensing.canonical(outer), self.public)

    def test_dpapi_roundtrip_and_corruption(self):
        if os.name != 'nt':
            self.skipTest('Windows only')
        protected = licensing.protect_token(self.envelope)
        self.assertEqual(licensing.unprotect_token(protected), self.envelope)
        with self.assertRaises(OSError):
            licensing.unprotect_token(protected[:-16] + bytes(16))

    def test_exact_program_expiry_boundary(self):
        with patch('pathlib.Path.read_bytes', return_value=b'token'), patch.object(licensing, 'unprotect_token', return_value=self.envelope):
            self.assertTrue(licensing.check_license(Path('.'), now=1999999999, public_key=self.public))
            self.assertFalse(licensing.check_license(Path('.'), now=2000000000, public_key=self.public))
            # Installer expiration is not a runtime program expiration.
            self.assertTrue(licensing.check_license(Path('.'), now=1999999950, public_key=self.public))

    def test_bad_missing_token_fail_closed(self):
        with patch('pathlib.Path.read_bytes', side_effect=FileNotFoundError):
            self.assertFalse(licensing.check_license(Path('.'), public_key=self.public))

    def test_local_calendar_is_independent_of_timezone(self):
        import calendar
        deadline = calendar.timegm((2030, 1, 1, 0, 0, 0))
        policy = dict(self.policy, expires_local=deadline)
        envelope = licensing.sign_policy(policy, self.key)
        with patch('pathlib.Path.read_bytes', return_value=b'token'), patch.object(licensing, 'unprotect_token', return_value=envelope):
            for offset in (-8 * 3600, 0, 9 * 3600):
                actual_utc = deadline - offset
                def local_tuple(utc):
                    return time.gmtime(utc + offset)
                with patch.object(licensing.time, 'localtime', side_effect=local_tuple):
                    self.assertTrue(licensing.check_license(Path('.'), now=actual_utc - 1, public_key=self.public))
                    self.assertFalse(licensing.check_license(Path('.'), now=actual_utc, public_key=self.public))

    def test_korea_next_midnight(self):
        from datetime import datetime, timezone
        self.assertEqual(expiry_epoch('2026-12-31'), int(datetime(2026, 12, 31, 15, tzinfo=timezone.utc).timestamp()))

    def test_settings(self):
        BuildSettings('v1.0', '2099-12-31', 10).validate()
        for version, minutes in [('..', 10), ('v1.0', 0), ('v1.0', 10081)]:
            with self.assertRaises(ValueError):
                BuildSettings(version, '2099-12-31', minutes).validate()

    def test_protected_token_is_checked_locally(self):
        # Speed is measured on request in 비교측정시험/test_license_speed.py.
        if os.name != 'nt':
            self.skipTest('Windows only')
        token = licensing.protect_token(self.envelope)
        with patch('pathlib.Path.read_bytes', return_value=token):
            self.assertTrue(licensing.check_license(Path('.'), now=1999999999, public_key=self.public))


if __name__ == '__main__':
    unittest.main()
