"""Native C# fresh install/update, MT5 replacement and transaction rollback.

All files and throwaway keys are isolated. No desktop or registry is changed.
"""
import json
import subprocess
import unittest
import zipfile
from test_installer import InstallerTests as _InstallerTests


class Update99Tests(_InstallerTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = r'''
using System;
using System.IO;
using System.Security.Cryptography;
using MosesInstaller;
public static class UpdateProbe {
  public static int Main(string[] args) {
    try {
      if(args[0] == "unprotect") {
        Console.Write(System.Text.Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(args[1]), null, DataProtectionScope.LocalMachine))); return 0;
      }
      using(Stream payload = File.OpenRead(args[1])) {
        FileStream locked = args[0] == "locked" ? new FileStream(args[5],FileMode.Open,FileAccess.Read,FileShare.None) : null;
        try { InstallCore.InstallArchive(payload,args[2],File.ReadAllBytes(args[3]),args[4],null,false,InstallCore.IsInstallation(args[2])); }
        finally { if(locked != null) locked.Dispose(); }
      }
      return 0;
    } catch(Exception e) { Console.Write(e.ToString()); return 7; }
  }
}'''
        (cls.work / 'update_probe.cs').write_text(source, encoding='utf-8')
        compiler = __import__('pathlib').Path(__import__('os').environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        command = [str(compiler), '/nologo', '/codepage:65001', '/platform:x64',
                   '/target:exe', '/main:UpdateProbe', '/out:update_probe.exe']
        command += ['/reference:' + name + '.dll' for name in (
            'System.Windows.Forms', 'System.Drawing', 'System.Web.Extensions',
            'System.Security', 'System.IO.Compression', 'System.IO.Compression.FileSystem')]
        cls._compile(command + ['installer.cs', 'update_probe.cs'])

    def package(self, count, version, obsolete=False):
        package = self.work / ('package-' + version + '.zip')
        data = {'MOSES.exe': version.encode(), 'python.exe': version.encode(),
                'runtime/code.bundle': version.encode(),
                'settings/strategy_registry.json': json.dumps({'schema_version': 2,
                    'presets': [{'id': 'SPECIAL' + str(n)} for n in range(1, count + 1)]}).encode(),
                'Part1/program/config.txt': b'new default', 'Part2/event_backtest.json': b'{}',
                'settings/ai_settings.json': b'{}'}
        if obsolete:
            data['_internal/obsolete.dll'] = b'old'
        for name in ('THE_STAFF_OF_MOSES', 'PRICE_of_Moses', 'RSI_of_Moses', 'STO_of_Moses', 'DI_of_Moses'):
            data['Part1/program/MT5/' + name + '.ex5'] = version.encode()
        with zipfile.ZipFile(package, 'w') as archive:
            for path, value in data.items(): archive.writestr(path, value)
            archive.writestr('Part3/generated/' if version == 'v1.0' else 'Part3/TEST_SPECIAL/', b'')
        return package

    def apply(self, package, target, mt5, version, mode='apply', lock=None):
        record = self.work / ('policy-' + version + '.json')
        record.write_bytes(self.envelope(version=version, expires_at=2100000000 if version == 'v2.0' else 2000000000))
        args = [str(self.work / 'update_probe.exe'), mode, str(package), str(target), str(record), str(mt5)]
        if lock: args.append(str(lock))
        result = subprocess.run(args, capture_output=True, timeout=30)
        return result.returncode, result.stdout.decode('utf-8', errors='replace')

    def test_install_then_update_preserves_users_replaces_license_and_mt5(self):
        target, mt5 = self.work / 'installed', self.work / 'terminal'
        (mt5 / 'MQL5').mkdir(parents=True)
        code, message = self.apply(self.package(7, 'v1.0', True), target, mt5, 'v1.0')
        self.assertEqual(0, code, message)
        (target / 'Part1/program/config.txt').write_text('user config')
        (target / 'Part2/event_backtest.json').write_text('{"warehouse":"user-data"}')
        (target / 'settings/ai_settings.json').write_text('{"provider":"none"}')
        (target / 'Part1/special_settings.json').write_text(json.dumps({'specials': {
            'SPECIAL1': {'enabled': True}, 'SPECIAL7': {'enabled': True}, 'TEST_SPECIAL010': {'enabled': False}}}))
        (target / 'Part3/generated/Test_SPECIAL010.py').write_text('# user strategy')
        (target / 'Part3/generated/Test_SPECIAL010.recipe.json').write_text('{}')
        (target / 'Part2/user-result.csv').write_text('preserve')
        code, message = self.apply(self.package(4, 'v2.0'), target, mt5, 'v2.0')
        self.assertEqual(0, code, message)
        self.assertEqual('user config', (target / 'Part1/program/config.txt').read_text())
        self.assertEqual('preserve', (target / 'Part2/user-result.csv').read_text())
        self.assertEqual('# user strategy', (target / 'Part3/TEST_SPECIAL/Test_SPECIAL010.py').read_text())
        self.assertFalse((target / 'Part3/generated/Test_SPECIAL010.py').exists())
        self.assertEqual(4, len(json.loads((target / 'settings/strategy_registry.json').read_text())['presets']))
        self.assertEqual({'SPECIAL1', 'TEST_SPECIAL010'}, set(json.loads((target / 'Part1/special_settings.json').read_text())['specials']))
        self.assertFalse((target / '_internal/obsolete.dll').exists())
        self.assertEqual('v2.0', (mt5 / 'MQL5/Experts/THE_STAFF_OF_MOSES.ex5').read_text())
        self.assertEqual('v2.0', (mt5 / 'MQL5/Indicators/DI_of_Moses.ex5').read_text())
        record = json.loads((target / 'runtime/install.json').read_text(encoding='utf-8'))
        self.assertEqual(str(mt5), record['mt5_data_folder'])
        result = subprocess.run([str(self.work / 'update_probe.exe'), 'unprotect', str(target / 'runtime/license.bin')], capture_output=True, check=True)
        envelope = json.loads(result.stdout)
        import base64
        policy = json.loads(base64.b64decode(envelope['payload']))
        self.assertEqual('v2.0', policy['version'])
        self.assertEqual(2100000000, policy['expires_at'])

    def test_locked_mt5_rolls_back_all_program_and_mt5_files(self):
        target, mt5 = self.work / 'rollback-app', self.work / 'rollback-terminal'
        (mt5 / 'MQL5').mkdir(parents=True)
        code, message = self.apply(self.package(7, 'v1.0', True), target, mt5, 'v1.0')
        self.assertEqual(0, code, message)
        (target / 'Part3/generated/Test_SPECIAL010.py').write_text('# user strategy')
        (target / 'Part3/generated/Test_SPECIAL010.recipe.json').write_text('{}')
        before = {str(p): p.read_bytes() for tree in (target, mt5) for p in tree.rglob('*') if p.is_file()}
        code, message = self.apply(self.package(4, 'v2.0'), target, mt5, 'v2.0', mode='locked', lock=mt5 / 'MQL5/Indicators/DI_of_Moses.ex5')
        self.assertEqual(7, code, message)
        after = {str(p): p.read_bytes() for tree in (target, mt5) for p in tree.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_library_name_collision_rolls_back_without_overwriting_user_files(self):
        target, mt5 = self.work / 'collision-app', self.work / 'collision-terminal'
        (mt5 / 'MQL5').mkdir(parents=True)
        code, message = self.apply(self.package(7, 'v1.0'), target, mt5, 'v1.0')
        self.assertEqual(0, code, message)
        (target / 'Part3/TEST_SPECIAL').mkdir()
        (target / 'Part3/generated/Test_SPECIAL010.py').write_bytes(b'old strategy')
        (target / 'Part3/TEST_SPECIAL/Test_SPECIAL010.py').write_bytes(b'different strategy')
        before = {str(p): p.read_bytes() for tree in (target, mt5) for p in tree.rglob('*') if p.is_file()}
        code, message = self.apply(self.package(4, 'v2.0'), target, mt5, 'v2.0')
        self.assertEqual(7, code, message)
        after = {str(p): p.read_bytes() for tree in (target, mt5) for p in tree.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_library_merge_preserves_metadata_membership_and_newer_user_files(self):
        target, mt5 = self.work / 'merge-app', self.work / 'merge-terminal'
        (mt5 / 'MQL5').mkdir(parents=True)
        code, message = self.apply(self.package(7, 'v1.0'), target, mt5, 'v1.0')
        self.assertEqual(0, code, message)
        (target / 'Part3/TEST_SPECIAL').mkdir()
        for library in ('generated', 'TEST_SPECIAL'):
            (target / 'Part3' / library / 'Test_SPECIAL010.py').write_bytes(b'same strategy')
            (target / 'Part3' / library / 'Test_SPECIAL010.recipe.json').write_bytes(b'{"user":"metadata"}')
        (target / 'Part3/TEST_SPECIAL/Test_SPECIAL011.py').write_bytes(b'newer strategy')
        membership = b'{"schema_version":2,"registrations":{"TEST_SPECIAL010":["Part2"]}}'
        (target / 'settings/strategy_visibility.json').write_bytes(membership)
        code, message = self.apply(self.package(4, 'v2.0'), target, mt5, 'v2.0')
        self.assertEqual(0, code, message)
        self.assertEqual(membership, (target / 'settings/strategy_visibility.json').read_bytes())
        self.assertEqual(b'newer strategy', (target / 'Part3/TEST_SPECIAL/Test_SPECIAL011.py').read_bytes())
        self.assertEqual(b'same strategy', (target / 'Part3/TEST_SPECIAL/Test_SPECIAL010.py').read_bytes())
        self.assertEqual([], list((target / 'Part3/generated').iterdir()))
