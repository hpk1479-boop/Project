"""Execute production path resolvers from copied files without starting services."""
import ast
import datetime as dt
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Optional
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

class PortablePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="portable-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / "복사한 프로젝트 공백" / "깊은 하위" / "새 이름"
        self.program = self.project / "임의 하위폴더" / "실행 코드"
        self.program.mkdir(parents=True)
        # E2's path resolver delegates to the context-local memory boundary.
        # Include that production dependency in this relocated program too.
        shutil.copyfile(ROOT/'program/domain_memory.py',self.program/'domain_memory.py')
        old_path=list(sys.path)
        sys.path.insert(0,str(self.program))
        self.addCleanup(setattr,sys,'path',old_path)
        self.other = self.base / "unrelated"
        self.other.mkdir()
        old = Path.cwd()
        os.chdir(self.other)
        self.addCleanup(os.chdir, old)

    def functions(self, source, names, dest=None, extra=None):
        dest = dest or self.program / Path(source).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, dest)
        tree = ast.parse(dest.read_text(encoding="utf-8-sig"))
        nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
        for n in nodes:
            if isinstance(n, ast.FunctionDef): n.decorator_list = []
        env = dict(Path=Path, os=os, dt=dt, logging=logging, __file__=str(dest), threading=threading,
                   Optional=Optional, NotificationService=object)
        env.update(extra or {})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(dest), 'exec'), env)
        return env

    def test_config_never_falls_back_to_unrelated_working_directory(self):
        (self.other / 'config.txt').write_text('LOCATION=wrong', encoding='utf-8')
        for source in ('program/manager_KIM.py', 'program/THE STAFF OF MOSES.py'):
            with self.subTest(source=source):
                env = self.functions(source, {'load_config'})
                self.assertEqual(env['load_config'](), {})
                local = self.program / 'config.txt'
                local.write_text('LOCATION=copied', encoding='utf-8')
                self.assertEqual(env['load_config'](), {'LOCATION':'copied'})
                local.unlink()
        env = self.functions('program/manager_KIM.py', {'_resolve_config_path'})
        (self.other / 'command_aliases.json').write_text('{}', encoding='utf-8')
        self.assertEqual(env['_resolve_config_path']('command_aliases.json'), self.program/'command_aliases.json')

    def test_special2_uses_oz_state_from_same_copy_even_when_missing(self):
        wrong = self.other/'logs'/'oz_external_liquidity_state.json'
        wrong.parent.mkdir()
        wrong.write_text('{}', encoding='utf-8')
        env = self.functions('program/SPECIAL/SPECIAL2.py', {'_external_liquidity_state_path'},
                             self.program/'SPECIAL'/'SPECIAL2.py')
        self.assertEqual(env['_external_liquidity_state_path'](), self.program/'logs'/wrong.name)

    def test_economy_relative_state_path_is_independent_of_cwd(self):
        log = self.program/'logs'
        log.mkdir()
        env = self.functions('program/manager_KIM.py', {'EconomyWorker'}, extra={'LOG_DIR':log})
        worker = env['EconomyWorker']({}, threading.Event(), notifier=object())
        self.assertEqual(worker.alerted_file, self.program/'alerted_events.txt')
        self.assertEqual(worker.briefing_file, log/'last_briefing.txt')

    def test_common_files_relative_override_and_fallback_are_local(self):
        env = self.functions('program/manager_KIM.py', {'resolve_common_files'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env['resolve_common_files']({}), self.program/'StaffOfMoses')
            self.assertEqual(env['resolve_common_files']({'MT5_COMMON_FILES':'feed'}),
                             self.program/'feed'/'StaffOfMoses')
        with patch.dict(os.environ, {'APPDATA':str(self.other)}):
            self.assertEqual(env['resolve_common_files']({}),
                             self.other/'MetaQuotes'/'Terminal'/'Common'/'Files'/'StaffOfMoses')

    def test_launcher_search_is_downward_after_copy_and_rename(self):
        names = {'SearchError','_inside_base','_dedupe_paths','_variant_matches_in_dir',
                 '_descendant_matches','resolve_file_downward'}
        for i in range(2):
            base = self.project if i == 0 else self.base/'다른 복사본'
            base.mkdir(parents=True, exist_ok=True)
            env = self.functions('OZ_SYSTEM CONTROL.pyw', names, base/'OZ_SYSTEM CONTROL.pyw', {'BASE_DIR':base})
            outside = base.parent/'manager_KIM.py'
            outside.write_text('# outside', encoding='utf-8')
            self.assertIsNone(env['resolve_file_downward']('manager_KIM.py'))
            local = base/'a'/'b'/'manager_KIM.py'
            local.parent.mkdir(parents=True)
            local.write_text('# local', encoding='utf-8')
            self.assertEqual(env['resolve_file_downward']('manager_KIM.py'), local)
            duplicate = base/'second'/'manager_KIM.py'
            duplicate.parent.mkdir()
            duplicate.write_text('# duplicate', encoding='utf-8')
            with self.assertRaises(env['SearchError']): env['resolve_file_downward']('manager_KIM.py')

if __name__ == '__main__': unittest.main()
