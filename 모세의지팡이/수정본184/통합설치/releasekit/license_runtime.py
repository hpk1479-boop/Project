"""Local, startup-only verification. No network and no trading-loop hooks."""
from __future__ import annotations

import base64
import calendar
import ctypes
import hashlib
import json
import os
import struct
import time
from pathlib import Path

TRAILER = b'MOSES96!'
SHA256_DER = bytes.fromhex('3031300d060960864801650304020105000420')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sign_policy(policy, private_key):
    data = canonical(policy)
    modulus = int.from_bytes(base64.b64decode(private_key['modulus']), 'big')
    exponent = int.from_bytes(base64.b64decode(private_key['d']), 'big')
    size = (modulus.bit_length() + 7) // 8
    digest = SHA256_DER + hashlib.sha256(data).digest()
    encoded = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    signature = pow(int.from_bytes(encoded, 'big'), exponent, modulus).to_bytes(size, 'big')
    return canonical({'payload': base64.b64encode(data).decode('ascii'),
                      'signature': base64.b64encode(signature).decode('ascii')})


def verify_policy(envelope, public_key):
    outer = json.loads(envelope)
    data = base64.b64decode(outer['payload'], validate=True)
    signature = base64.b64decode(outer['signature'], validate=True)
    modulus = int.from_bytes(base64.b64decode(public_key['modulus'], validate=True), 'big')
    exponent = int.from_bytes(base64.b64decode(public_key['exponent'], validate=True), 'big')
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size or int.from_bytes(signature, 'big') >= modulus:
        raise ValueError('Invalid signature')
    digest = SHA256_DER + hashlib.sha256(data).digest()
    expected = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    actual = pow(int.from_bytes(signature, 'big'), exponent, modulus).to_bytes(size, 'big')
    import hmac
    if not hmac.compare_digest(expected, actual):
        raise ValueError('Invalid signature')
    policy = json.loads(data)
    if (policy.get('schema') != 1 or not isinstance(policy.get('version'), str)
            or type(policy.get('expires_at')) is not int
            or type(policy.get('install_expires_at')) is not int):
        raise ValueError('Invalid policy')
    if 'expires_local' in policy and type(policy['expires_local']) is not int:
        raise ValueError('Invalid calendar deadline')
    return policy


def append_policy(executable, envelope):
    with Path(executable).open('ab') as stream:
        stream.write(envelope)
        stream.write(struct.pack('<I', len(envelope)))
        stream.write(TRAILER)


def read_policy(executable):
    with Path(executable).open('rb') as stream:
        stream.seek(-12, 2)
        size, magic = struct.unpack('<I8s', stream.read(12))
        if magic != TRAILER or size > 65536:
            raise ValueError('Invalid installer')
        stream.seek(-12 - size, 2)
        return stream.read(size)


class _Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value, protect):
    if os.name != 'nt':
        raise OSError('Windows is required')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    func = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    func.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Blob)]
    func.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    storage = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = _Blob(len(value), storage)
    target = _Blob()
    # LOCAL_MACHINE: Windows binds the token to this machine. No UI allowed.
    flags = 5 if protect else 1
    if not func(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def protect_token(envelope):
    return _dpapi(envelope, True)


def unprotect_token(token):
    return _dpapi(token, False)


def check_license(root, *, now=None, public_key=None):
    """One local check per process, invoked before any application imports."""
    try:
        root = Path(root)
        if public_key is None:
            from releasekit.runtime_public import PUBLIC_KEY
            public_key = PUBLIC_KEY
        envelope = unprotect_token((root / 'runtime' / 'license.bin').read_bytes())
        policy = verify_policy(envelope, public_key)
        current = time.time() if now is None else now
        if 'expires_local' in policy:
            local_wall = calendar.timegm(time.localtime(current))
            return 0 < current and local_wall < policy['expires_local']
        return 0 < current < policy['expires_at']
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return False
