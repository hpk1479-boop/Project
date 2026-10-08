"""Windows integration test; private pipe only, no live STAFF connection."""
import ast
import ctypes
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import unittest
import uuid


@unittest.skipUnless(os.name == "nt", "Windows named pipes required")
class PipeSecurityTest(unittest.TestCase):
    def test_descriptor_and_separate_process_transfer(self):
        source = Path(__file__).resolve().parents[1] / "program/THE STAFF OF MOSES.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "staff_pipe_security")
        namespace = {"ctypes": ctypes, "contextmanager": contextmanager}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source), "exec"), namespace)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        k.CreateNamedPipeW.argtypes = [ctypes.c_wchar_p] + [ctypes.c_uint32] * 6 + [ctypes.c_void_p]
        k.CreateNamedPipeW.restype = ctypes.c_void_p
        k.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                               ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        k.LocalFree.argtypes = [ctypes.c_void_p]
        a.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32] + [ctypes.c_void_p] * 5
        a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        name = r"\\.\pipe\StaffSecurityTest_" + uuid.uuid4().hex
        client = r'''
import ctypes, sys
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
k.CreateFileW.restype = ctypes.c_void_p
k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
k.CloseHandle.argtypes = [ctypes.c_void_p]
h = k.CreateFileW(sys.argv[1], 0xC0000000, 0, None, 3, 0, None)
if h == ctypes.c_void_p(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
try:
    b = ctypes.create_string_buffer(4)
    n = ctypes.c_uint32()
    assert k.ReadFile(h, b, 4, ctypes.byref(n), None)
    assert b.raw == b"SMOS" and n.value == 4
finally:
    k.CloseHandle(h)
'''
        for _ in range(2):  # Re-created pipes must retain the same permissions.
            with namespace["staff_pipe_security"]() as sa:
                h = k.CreateNamedPipeW(name, 3, 0, 1, 4096, 4096, 0, ctypes.byref(sa))
                self.assertNotEqual(h, ctypes.c_void_p(-1).value)
            try:
                sd, text = ctypes.c_void_p(), ctypes.c_void_p()
                try:
                    self.assertEqual(a.GetSecurityInfo(h, 6, 0x14, None, None, None,
                                                      None, ctypes.byref(sd)), 0)
                    self.assertTrue(a.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                        sd, 1, 0x14, ctypes.byref(text), None))
                    sddl = ctypes.wstring_at(text)
                    self.assertIn("(ML;;NW;;;ME)", sddl)
                    self.assertIn("S-1-5-21-", sddl)
                    self.assertNotIn(";;;WD)", sddl)
                    self.assertNotIn(";;;AU)", sddl)
                finally:
                    if text.value:
                        k.LocalFree(text)
                    if sd.value:
                        k.LocalFree(sd)
                # CreateFile connects without waiting for ConnectNamedPipe.
                # Send data before waiting for the child so test failures cannot
                # strand the test runner in a blocking ConnectNamedPipe call.
                with subprocess.Popen([sys.executable, "-c", client, name],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
                    import time
                    deadline = time.monotonic() + 10
                    sent = ctypes.c_uint32()
                    while not k.WriteFile(h, b"SMOS", 4, ctypes.byref(sent), None):
                        if p.poll() is not None or time.monotonic() >= deadline:
                            p.kill()
                            self.fail(p.communicate(timeout=5)[1].decode(errors="replace"))
                        time.sleep(0.02)
                    out, err = p.communicate(timeout=10)
                    self.assertEqual(p.returncode, 0, err.decode(errors="replace"))
                    self.assertEqual(sent.value, 4)
            finally:
                k.CloseHandle(h)


if __name__ == "__main__":
    unittest.main()
