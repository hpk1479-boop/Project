"""State-file I/O tests use isolated files and actual Windows child processes.

Moved from Part1/audit in 수정본180.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]/'Part1/program/durable_protocol.py'


class StateIO(unittest.TestCase):
    def setUp(self):
        old_path=list(sys.path)
        sys.path.insert(0,str(SOURCE.parent))
        self.addCleanup(setattr,sys,'path',old_path)
        spec = importlib.util.spec_from_file_location('isolated_state_io', SOURCE)
        self.io = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.io)
        self.tmp = tempfile.TemporaryDirectory(prefix='moses-state-io-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'state.json'
        self.path.write_text('{"old":true}',encoding='utf-8')

    def child(self, body):
        setup = ('import sys,importlib.util; from pathlib import Path; '
                 'sys.path.insert(0,str(Path(sys.argv[1]).parent)); '
                 's=importlib.util.spec_from_file_location("io_test",sys.argv[1]); '
                 'io=importlib.util.module_from_spec(s); s.loader.exec_module(io); '
                 'p=Path(sys.argv[2]); ')
        proc = subprocess.Popen([sys.executable,'-B','-c',setup+body,str(SOURCE),str(self.path)],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,encoding='utf-8')
        def cleanup():
            if proc.poll() is None: proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(cleanup)
        return proc

    def writer(self, operation):
        done, failures = threading.Event(), []
        def run():
            try: operation()
            except Exception as exc: failures.append(exc)
            finally: done.set()
        thread = threading.Thread(target=run)
        thread.start()
        return thread,done,failures

    @unittest.skipUnless(os.name == 'nt','Windows sharing semantics')
    def test_external_reader_release_allows_atomic_replace(self):
        proc = self.child('f=p.open(); print("ready",flush=True); sys.stdin.readline(); f.close()')
        self.assertEqual(proc.stdout.readline().strip(),'ready')
        thread,done,failures = self.writer(lambda: self.io.atomic_json(self.path,{'new':True}))
        early = done.wait(.15)
        proc.communicate('\n',timeout=5)
        thread.join(5)
        self.assertFalse(early, failures)
        self.assertFalse(failures)
        self.assertTrue(done.is_set())
        self.assertEqual(json.loads(self.path.read_text()),{'new':True})

    def test_cooperating_reader_and_writer_share_process_lock(self):
        proc = self.child('\nwith io.state_lock(p):\n print("ready",flush=True)\n sys.stdin.readline()\n')
        ready = proc.stdout.readline().strip()
        if ready != 'ready':
            _,err=proc.communicate(timeout=5)
            self.fail(err)
        thread,done,failures = self.writer(lambda: self.io.atomic_json(self.path,{'new':True}))
        early = done.wait(.15)
        proc.communicate('\n',timeout=5)
        thread.join(5)
        self.assertFalse(early)
        self.assertFalse(failures)
        self.assertTrue(done.is_set())

    def test_terminated_lock_owner_does_not_block_recovery(self):
        proc = self.child('\nwith io.state_lock(p):\n print("ready",flush=True)\n sys.stdin.readline()\n')
        self.assertEqual(proc.stdout.readline().strip(), 'ready')
        thread, done, failures = self.writer(lambda: self.io.atomic_json(self.path, {'recovered': True}))
        early = done.wait(.15)
        proc.kill()
        proc.communicate(timeout=5)
        thread.join(5)
        self.assertFalse(early)
        self.assertTrue(done.is_set())
        self.assertFalse(failures)
        self.assertEqual(self.io.read_json(self.path), {'recovered': True})

    def test_permanent_denial_preserves_original_and_cleans_unique_temp(self):
        error = PermissionError('injected sharing denial')
        error.winerror = 5
        before = self.path.read_bytes()
        with patch.object(self.io.os,'replace',side_effect=error) as replace:
            with self.assertRaises(PermissionError):
                self.io.atomic_json(self.path,{'new':True},retry_timeout=.06)
            self.assertGreaterEqual(replace.call_count,2)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.path.parent.glob('*.tmp')))

    def test_non_sharing_error_is_not_retried(self):
        with patch.object(self.io.os,'replace',side_effect=OSError(28,'disk full')) as replace:
            with self.assertRaises(OSError): self.io.atomic_json(self.path,{'new':True})
            self.assertEqual(replace.call_count,1)
        self.assertEqual(json.loads(self.path.read_text()),{'old':True})

    def test_records_read_modify_write_is_cross_process_transaction(self):
        self.path.unlink()
        processes = [self.child('r=io.Records(p); print("ready",flush=True); sys.stdin.readline(); '
                               f'[r.put("{i}:"+str(n),n) for n in range(20)]') for i in range(3)]
        for proc in processes: self.assertEqual(proc.stdout.readline().strip(),'ready')
        for proc in processes: proc.stdin.write('\n'); proc.stdin.flush()
        for proc in processes:
            _,err=proc.communicate(timeout=15)
            self.assertEqual(proc.returncode,0,err)
        self.assertEqual(len(self.io.Records(self.path).all()),60)

    def test_corrupt_record_is_not_reset(self):
        self.path.write_text('not-json',encoding='utf-8')
        with self.assertRaises(ValueError): self.io.Records(self.path).put('key',1)
        self.assertEqual(self.path.read_text(),'not-json')


if __name__ == '__main__': unittest.main()
