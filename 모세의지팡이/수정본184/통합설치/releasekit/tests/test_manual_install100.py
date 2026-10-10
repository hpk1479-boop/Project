"""Native installer checks for current manuals on install and update.

All packages, installation folders and RSA keys are throwaway fixtures.
The harness explicitly disables desktop/registry integration and MT5 copying.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest
import zipfile

import test_installer as installer_fixture


MANUAL = '매뉴얼/모세_사용자_매뉴얼.pdf'
LEGACY_MANUAL = '매뉴얼/모세_사용자_매뉴얼_수정본99.pdf'


class ManualInstall100Tests(unittest.TestCase):
    # Reuse only the isolated fixture setup, not the unrelated inherited tests.
    @classmethod
    def _compile(cls, command):
        installer_fixture.InstallerTests._compile.__func__(cls, command)

    @classmethod
    def envelope(cls, **overrides):
        return installer_fixture.InstallerTests.envelope.__func__(cls, **overrides)

    @classmethod
    def tearDownClass(cls):
        installer_fixture.InstallerTests.tearDownClass.__func__(cls)

    @classmethod
    def setUpClass(cls):
        installer_fixture.InstallerTests.setUpClass.__func__(cls)
        source = r'''
using System;
using System.IO;
using MosesInstaller;
public static class ManualInstallProbe {
  public static int Main(string[] args) {
    try {
      FileStream locked = args.Length > 3 ? new FileStream(args[3], FileMode.Open,
          FileAccess.Read, FileShare.None) : null;
      try {
        using(Stream payload = File.OpenRead(args[0]))
          InstallCore.InstallArchive(payload, args[1], File.ReadAllBytes(args[2]),
              "", null, false, InstallCore.IsInstallation(args[1]));
      } finally { if(locked != null) locked.Dispose(); }
      return 0;
    } catch(Exception error) { Console.Write(error.ToString()); return 7; }
  }
}'''
        (cls.work / 'manual_probe.cs').write_text(source, encoding='utf-8')
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        command = [str(compiler), '/nologo', '/codepage:65001', '/platform:x64',
                   '/target:exe', '/main:ManualInstallProbe', '/out:manual_probe.exe']
        command += ['/reference:' + name + '.dll' for name in (
            'System.Windows.Forms', 'System.Drawing', 'System.Web.Extensions',
            'System.Security', 'System.IO.Compression', 'System.IO.Compression.FileSystem')]
        cls._compile(command + ['installer.cs', 'manual_probe.cs'])

    def package_with_manual(self, version, manuals):
        package = self.work / ('manual-' + version + '.zip')
        files = {
            'MOSES.exe': version.encode(),
            'python.exe': version.encode(),
            'runtime/code.bundle': version.encode(),
            'settings/strategy_registry.json': json.dumps({
                'schema_version': 2, 'presets': [{'id': 'SPECIAL1'}]}).encode(),
            'Part1/program/config.txt': b'new defaults',
            'settings/ai_settings.json': b'{}',
        }
        files.update(manuals)
        with zipfile.ZipFile(package, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, value)
            archive.writestr('Part3/generated/' if version == 'v1.0' else 'Part3/TEST_SPECIAL/', b'')
        return package

    def install_manual_package(self, package, target, version, lock=None):
        record = self.work / ('manual-policy-' + version + '.json')
        record.write_bytes(self.envelope(version=version))
        args = [str(self.work / 'manual_probe.exe'), str(package), str(target), str(record)]
        if lock is not None:
            args.append(str(lock))
        result = subprocess.run(args, cwd=self.work, capture_output=True, timeout=30)
        return result.returncode, result.stdout.decode('utf-8', errors='replace')

    def test_first_install_delivers_exact_manual_and_records_ownership(self):
        manual = b'%PDF-1.4\ncurrent manual for first install\n%%EOF\n'
        target = self.work / 'manual-first-install'
        code, message = self.install_manual_package(
            self.package_with_manual('v1.0', {MANUAL: manual}), target, 'v1.0')
        self.assertEqual(0, code, message)
        self.assertEqual(manual, (target / MANUAL).read_bytes())
        record = json.loads((target / 'runtime/install.json').read_text(encoding='utf-8'))
        self.assertIn(MANUAL, record['managed_files'])
        self.assertEqual('', record['mt5_data_folder'])
        self.assertFalse(any((target / '매뉴얼').glob('*.docx')))
        self.assertFalse(any((target / '매뉴얼').glob('*.md')))

    def test_update_replaces_latest_pdf_removes_owned_legacy_and_preserves_users(self):
        target = self.work / 'manual-update-install'
        old = b'%PDF-1.4\nold manual\n%%EOF\n'
        code, message = self.install_manual_package(self.package_with_manual(
            'v1.0', {MANUAL: old, LEGACY_MANUAL: old}), target, 'v1.0')
        self.assertEqual(0, code, message)
        (target / 'Part1/program/config.txt').write_bytes(b'user config')
        (target / 'settings/ai_settings.json').write_bytes(b'{"provider":"gemini"}')
        generated = target / 'Part3/generated/Test_SPECIAL010.py'
        generated.write_bytes(b'# user strategy')
        (target / 'Part3/generated/Test_SPECIAL010.recipe.json').write_bytes(b'{}')
        user_pdf = target / '매뉴얼/내 참고자료.pdf'
        user_pdf.write_bytes(b'user-owned reference')
        latest = b'%PDF-1.4\nnewly edited manual\n%%EOF\n'
        code, message = self.install_manual_package(self.package_with_manual(
            'v2.0', {MANUAL: latest}), target, 'v2.0')
        self.assertEqual(0, code, message)
        self.assertEqual(latest, (target / MANUAL).read_bytes())
        self.assertFalse((target / LEGACY_MANUAL).exists())
        self.assertEqual(b'user config', (target / 'Part1/program/config.txt').read_bytes())
        self.assertEqual(b'{"provider":"gemini"}', (target / 'settings/ai_settings.json').read_bytes())
        migrated = target / 'Part3/TEST_SPECIAL/Test_SPECIAL010.py'
        self.assertEqual(b'# user strategy', migrated.read_bytes())
        self.assertEqual(b'{}', (migrated.parent / 'Test_SPECIAL010.recipe.json').read_bytes())
        self.assertFalse(generated.exists())
        self.assertEqual(b'user-owned reference', user_pdf.read_bytes())
        record = json.loads((target / 'runtime/install.json').read_text(encoding='utf-8'))
        self.assertEqual('v2.0', record['version'])
        self.assertIn(MANUAL, record['managed_files'])
        self.assertNotIn(LEGACY_MANUAL, record['managed_files'])

    def test_locked_pdf_rolls_back_program_manual_license_and_install_record(self):
        target = self.work / 'manual-rollback-install'
        code, message = self.install_manual_package(self.package_with_manual(
            'v1.0', {MANUAL: b'%PDF-1.4\noriginal\n%%EOF\n'}), target, 'v1.0')
        self.assertEqual(0, code, message)
        before = {str(path.relative_to(target)): path.read_bytes()
                  for path in target.rglob('*') if path.is_file()}
        code, message = self.install_manual_package(self.package_with_manual(
            'v2.0', {MANUAL: b'%PDF-1.4\nreplacement\n%%EOF\n'}), target, 'v2.0',
            lock=target / MANUAL)
        self.assertEqual(7, code, message)
        after = {str(path.relative_to(target)): path.read_bytes()
                 for path in target.rglob('*') if path.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
