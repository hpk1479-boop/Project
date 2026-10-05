// Developer build template. Original application sources are never changed.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace MosesInstaller
{
    public static class InstallCore
    {
        public const string PublicModulus = "@@MODULUS@@";
        public const string PublicExponent = "@@EXPONENT@@";
        private const string RuntimeKey = @"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
        private static readonly byte[] TrailerMagic = Encoding.ASCII.GetBytes("MOSES96!");

        private static long Integer(Dictionary<string, object> data, string name)
        {
            object value;
            if (!data.TryGetValue(name, out value) || !(value is int || value is long))
                throw new InvalidDataException("Invalid policy.");
            return Convert.ToInt64(value);
        }

        public static byte[] ReadEnvelope(string executable)
        {
            using (FileStream input = new FileStream(executable, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (input.Length < 12) throw new InvalidDataException("Invalid package.");
                input.Seek(-12, SeekOrigin.End);
                using (BinaryReader reader = new BinaryReader(input, Encoding.UTF8, true))
                {
                    uint length = reader.ReadUInt32();
                    byte[] magic = reader.ReadBytes(8);
                    for (int i = 0; i < TrailerMagic.Length; i++)
                        if (magic[i] != TrailerMagic[i]) throw new InvalidDataException("Invalid package.");
                    if (length == 0 || length > 1048576 || length > input.Length - 12)
                        throw new InvalidDataException("Invalid package.");
                    input.Seek(-12L - length, SeekOrigin.End);
                    byte[] result = reader.ReadBytes((int)length);
                    if (result.Length != length) throw new InvalidDataException("Invalid package.");
                    return result;
                }
            }
        }

        public static Dictionary<string, object> ValidateEnvelope(byte[] envelope, long now)
        {
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = 1048576;
            serializer.RecursionLimit = 8;
            UTF8Encoding utf8 = new UTF8Encoding(false, true);
            Dictionary<string, object> record = serializer.Deserialize<Dictionary<string, object>>(utf8.GetString(envelope));
            byte[] payload = Convert.FromBase64String((string)record["payload"]);
            byte[] signature = Convert.FromBase64String((string)record["signature"]);
            if (payload.Length > 65536) throw new InvalidDataException("Invalid policy.");
            CspParameters parameters = new CspParameters();
            parameters.ProviderType = 24;
            using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider(parameters))
            {
                rsa.PersistKeyInCsp = false;
                RSAParameters publicKey = new RSAParameters();
                publicKey.Modulus = Convert.FromBase64String(PublicModulus);
                publicKey.Exponent = Convert.FromBase64String(PublicExponent);
                rsa.ImportParameters(publicKey);
                if (!rsa.VerifyData(payload, CryptoConfig.MapNameToOID("SHA256"), signature))
                    throw new InvalidDataException("Invalid package.");
            }
            Dictionary<string, object> policy = serializer.Deserialize<Dictionary<string, object>>(utf8.GetString(payload));
            long programNow = now;
            long programExpiry = Integer(policy, "expires_at");
            if (policy.ContainsKey("expires_local"))
            {
                DateTime local = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(now).ToLocalTime();
                programNow = (long)(DateTime.SpecifyKind(local, DateTimeKind.Unspecified) - new DateTime(1970, 1, 1)).TotalSeconds;
                programExpiry = Integer(policy, "expires_local");
            }
            if (Integer(policy, "schema") != 1 || now >= Integer(policy, "install_expires_at") || programNow >= programExpiry)
                throw new InvalidDataException("Unavailable package.");
            object version;
            if (!policy.TryGetValue("version", out version) || !(version is string) || ((string)version).Length == 0 || ((string)version).Length > 64)
                throw new InvalidDataException("Invalid policy.");
            foreach (char character in (string)version)
                if (Char.IsControl(character)) throw new InvalidDataException("Invalid policy.");
            return policy;
        }

        public static long UtcNowSeconds()
        {
            return (long)(DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
        }

        public static bool HasWebView2()
        {
            foreach (RegistryHive hive in new RegistryHive[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
            {
                foreach (RegistryView view in new RegistryView[] { RegistryView.Registry32, RegistryView.Registry64 })
                {
                    try
                    {
                        using (RegistryKey root = RegistryKey.OpenBaseKey(hive, view))
                        using (RegistryKey key = root.OpenSubKey(RuntimeKey))
                        {
                            if (key == null) continue;
                            Version version;
                            if (Version.TryParse(Convert.ToString(key.GetValue("pv")), out version) && version.CompareTo(new Version(0, 0, 0, 0)) > 0)
                                return true;
                        }
                    }
                    catch (System.Security.SecurityException) { }
                    catch (UnauthorizedAccessException) { }
                    catch (ArgumentException) { }
                }
            }
            return false;
        }

        public static string SafeEntryPath(string root, ZipArchiveEntry entry)
        {
            string name = entry.FullName.Replace('\\', '/');
            int unixType = (entry.ExternalAttributes >> 16) & 61440;
            if (unixType == 40960 || (entry.ExternalAttributes & 1024) != 0 || name.Length == 0 || name[0] == '/' || name.IndexOf(':') >= 0)
                throw new InvalidDataException("Invalid package path.");
            string trimmed = name.TrimEnd('/');
            if (trimmed.Length == 0) throw new InvalidDataException("Invalid package path.");
            foreach (string part in trimmed.Split('/'))
            {
                if (part.Length == 0 || part == "." || part == ".." || part.TrimEnd(' ', '.') != part || part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                    throw new InvalidDataException("Invalid package path.");
                string device = part.Split('.')[0].ToUpperInvariant();
                if (device == "CON" || device == "PRN" || device == "AUX" || device == "NUL" ||
                    (device.Length == 4 && (device.StartsWith("COM") || device.StartsWith("LPT")) && device[3] >= '1' && device[3] <= '9'))
                    throw new InvalidDataException("Invalid package path.");
            }
            string canonicalRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(canonicalRoot, trimmed.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(canonicalRoot, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Invalid package path.");
            return path;
        }

        private static void CheckDirectoryChain(string directory)
        {
            for (DirectoryInfo current = new DirectoryInfo(directory); current != null; current = current.Parent)
                if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("설치 폴더는 일반 폴더를 선택해 주세요.");
        }

        private static void CreateDirectory(string directory, List<string> createdDirectories)
        {
            CheckDirectoryChain(directory);
            if (Directory.Exists(directory)) return;
            string parent = Path.GetDirectoryName(directory);
            if (!String.IsNullOrEmpty(parent)) CreateDirectory(parent, createdDirectories);
            Directory.CreateDirectory(directory);
            createdDirectories.Add(directory);
        }

        public static void ExtractArchive(Stream input, string target, List<string> createdFiles, List<string> createdDirectories, Action<int> progress)
        {
            string root = Path.GetFullPath(target);
            CheckDirectoryChain(root);
            if (File.Exists(root) || (Directory.Exists(root) && Directory.EnumerateFileSystemEntries(root).GetEnumerator().MoveNext()))
                throw new IOException("기존 파일이 없는 새 폴더를 선택해 주세요.");
            using (ZipArchive archive = new ZipArchive(input, ZipArchiveMode.Read, true))
            {
                HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string destination = SafeEntryPath(root, entry);
                    if (!seen.Add(destination)) throw new InvalidDataException("Duplicate package path.");
                }
                CreateDirectory(root, createdDirectories);
                int count = 0;
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string destination = SafeEntryPath(root, entry);
                    bool directory = entry.FullName.EndsWith("/") || entry.FullName.EndsWith("\\");
                    CreateDirectory(directory ? destination : Path.GetDirectoryName(destination), createdDirectories);
                    if (!directory)
                    {
                        using (Stream source = entry.Open())
                        using (FileStream output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        {
                            createdFiles.Add(destination);
                            source.CopyTo(output);
                        }
                    }
                    count++;
                    if (progress != null) progress(archive.Entries.Count == 0 ? 80 : count * 80 / archive.Entries.Count);
                }
            }
        }

        public static void RollBack(List<string> files, List<string> directories)
        {
            // Delete only files created by this attempt. Never recursively remove a target.
            for (int i = files.Count - 1; i >= 0; i--)
                try { File.Delete(files[i]); } catch { }
            for (int i = directories.Count - 1; i >= 0; i--)
                try { Directory.Delete(directories[i], false); } catch { }
        }

        private const string ProductKey = @"Software\MOSES\TradingSystem";
        private static readonly string[] Mt5Files = { "THE_STAFF_OF_MOSES", "PRICE_of_Moses", "RSI_of_Moses", "STO_of_Moses", "DI_of_Moses" };
        public const string MissingInstallation = "모세 설치 위치를 찾을 수 없습니다. 모세가 설치된 폴더를 지정해 주세요. 모세가 설치되어 있지 않다면 ‘설치’를 클릭해 주세요.";
        public const string MultipleInstallations = "모세 설치본이 여러 개 발견되었습니다. 업데이트할 모세가 설치된 폴더를 지정해 주세요.";

        private static bool HasProgramFiles(string root)
        {
            return !String.IsNullOrWhiteSpace(root) && File.Exists(Path.Combine(root, "MOSES.exe")) &&
                File.Exists(Path.Combine(root, "python.exe")) && File.Exists(Path.Combine(root, "runtime", "code.bundle"));
        }

        public static bool IsInstallation(string root)
        {
            try { return HasProgramFiles(root) && File.Exists(Path.Combine(root, "runtime", "install.json")); }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
        }

        public static string DefaultInstallationFolder(string executable)
        {
            return Path.Combine(Path.GetDirectoryName(Path.GetFullPath(executable)), "모세");
        }

        private static string NormalizeFolder(string path)
        {
            string full = Path.GetFullPath(path);
            string volume = Path.GetPathRoot(full);
            return full.Length == volume.Length ? full : full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        public static List<string> FilterInstallations(IEnumerable<string> paths)
        {
            HashSet<string> result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string path in paths)
            {
                if (String.IsNullOrWhiteSpace(path)) continue;
                try { if (!Path.IsPathRooted(path)) continue; string root = NormalizeFolder(path); if (IsInstallation(root)) result.Add(root); }
                catch (ArgumentException) { }
                catch (IOException) { }
                catch (NotSupportedException) { }
                catch (UnauthorizedAccessException) { }
            }
            return result.OrderBy(value => value, StringComparer.OrdinalIgnoreCase).ToList();
        }

        public static List<string> FindInstallations()
        {
            List<string> paths = new List<string>();
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(ProductKey))
                    if (key != null) paths.Add(Convert.ToString(key.GetValue("InstallPath")));
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(ProductKey + @"\Installations"))
                    if (key != null)
                        foreach (string name in key.GetValueNames())
                            if (key.GetValueKind(name) == RegistryValueKind.String) paths.Add(Convert.ToString(key.GetValue(name)));
            }
            catch (System.Security.SecurityException) { }
            catch (UnauthorizedAccessException) { }
            catch (IOException) { }
            paths.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "MOSES"));
            paths.Add(DefaultInstallationFolder(Assembly.GetExecutingAssembly().Location));
            return FilterInstallations(paths);
        }

        private static void RegisterInstallation(string root)
        {
            try
            {
                string identity;
                using (SHA256 hash = SHA256.Create())
                    identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(root.ToLowerInvariant()))).Replace("-", "").ToLowerInvariant();
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(ProductKey + @"\Installations"))
                    key.SetValue(identity, root, RegistryValueKind.String);
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(ProductKey))
                    key.SetValue("InstallPath", root, RegistryValueKind.String);
            }
            catch (System.Security.SecurityException) { }
            catch (UnauthorizedAccessException) { }
            catch (IOException) { }
        }

        private static Dictionary<string, object> InstallationRecord(string root)
        {
            string path = Path.Combine(root, "runtime", "install.json");
            return File.Exists(path) ? new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path)) : new Dictionary<string, object>();
        }

        public static string SavedMt5(string root)
        {
            if (String.IsNullOrEmpty(root)) return "";
            try
            {
                object value;
                Dictionary<string, object> record = InstallationRecord(root);
                return record != null && record.TryGetValue("mt5_data_folder", out value) ? Convert.ToString(value) : "";
            }
            catch (ArgumentException) { return ""; }
            catch (InvalidOperationException) { return ""; }
            catch (IOException) { return ""; }
            catch (UnauthorizedAccessException) { return ""; }
        }

        public static bool IsMt5DataFolder(string path)
        {
            try { return !String.IsNullOrWhiteSpace(path) && Directory.Exists(Path.Combine(path, "MQL5")); }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
        }

        private static Mt5Location Mt5DataLocation(string path)
        {
            string data = NormalizeFolder(path), terminal = "";
            string origin = Path.Combine(data, "origin.txt");
            try
            {
                if (File.Exists(origin))
                {
                    string value = File.ReadAllText(origin).Trim().Trim('\0');
                    if (!String.IsNullOrWhiteSpace(value) && Path.IsPathRooted(value)) terminal = NormalizeFolder(value);
                }
            }
            catch (ArgumentException) { }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            if (terminal.Length == 0 && (File.Exists(Path.Combine(data, "terminal64.exe")) || File.Exists(Path.Combine(data, "terminal.exe")))) terminal = data;
            return new Mt5Location(data, terminal);
        }

        public static List<Mt5Location> FindMt5Locations(string terminalDataRoot)
        {
            List<Mt5Location> result = new List<Mt5Location>();
            if (Directory.Exists(terminalDataRoot))
                foreach (string path in Directory.GetDirectories(terminalDataRoot))
                    if (IsMt5DataFolder(path)) result.Add(Mt5DataLocation(path));
            return result.OrderBy(value => value.ToString(), StringComparer.OrdinalIgnoreCase).ToList();
        }

        public static List<Mt5Location> FindMt5Locations()
        {
            List<Mt5Location> result;
            try { result = FindMt5Locations(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "MetaQuotes", "Terminal")); }
            catch (IOException) { result = new List<Mt5Location>(); }
            catch (UnauthorizedAccessException) { result = new List<Mt5Location>(); }
            foreach (Process process in Process.GetProcesses())
            {
                try
                {
                    if (!process.ProcessName.StartsWith("terminal", StringComparison.OrdinalIgnoreCase)) continue;
                    string path = Path.GetDirectoryName(process.MainModule.FileName);
                    if (IsMt5DataFolder(path) && !result.Any(value => String.Equals(value.DataFolder, path, StringComparison.OrdinalIgnoreCase))) result.Add(Mt5DataLocation(path));
                }
                catch (Win32Exception) { }
                catch (InvalidOperationException) { }
                finally { process.Dispose(); }
            }
            return result.OrderBy(value => value.ToString(), StringComparer.OrdinalIgnoreCase).ToList();
        }

        public static List<Mt5Location> ResolveMt5Folder(string selected, IEnumerable<Mt5Location> candidates)
        {
            if (String.IsNullOrWhiteSpace(selected)) return new List<Mt5Location>();
            selected = NormalizeFolder(File.Exists(selected) ? Path.GetDirectoryName(selected) : selected);
            string name = Path.GetFileName(selected);
            if ((name.Equals("Experts", StringComparison.OrdinalIgnoreCase) || name.Equals("Indicators", StringComparison.OrdinalIgnoreCase)) &&
                Path.GetFileName(Path.GetDirectoryName(selected)).Equals("MQL5", StringComparison.OrdinalIgnoreCase)) selected = Path.GetDirectoryName(selected);
            if (Path.GetFileName(selected).Equals("MQL5", StringComparison.OrdinalIgnoreCase)) selected = Path.GetDirectoryName(selected);
            if (IsMt5DataFolder(selected)) return new List<Mt5Location> { Mt5DataLocation(selected) };
            string parent = selected.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            return candidates.Where(value => String.Equals(value.TerminalFolder, selected, StringComparison.OrdinalIgnoreCase) ||
                value.DataFolder.StartsWith(parent, StringComparison.OrdinalIgnoreCase)).GroupBy(value => value.DataFolder, StringComparer.OrdinalIgnoreCase).Select(group => group.First()).ToList();
        }

        public static void ScheduleInstallerRemoval(string executable)
        {
            // A temporary hidden helper removes only this completed installer after it exits.
            try
            {
                string path = Path.GetFullPath(executable);
                if (!String.Equals(path, Assembly.GetExecutingAssembly().Location, StringComparison.OrdinalIgnoreCase)) return;
                string digest;
                long length;
                using (FileStream input = File.OpenRead(path))
                using (SHA256 hash = SHA256.Create())
                {
                    length = input.Length;
                    digest = BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "");
                }
                const string script = @"
$ErrorActionPreference = 'Stop'
try {
    $target = $env:MOSES_SETUP_REMOVE_PATH
    $ownerId = [int]$env:MOSES_SETUP_REMOVE_PID
    $ownerStart = [long]$env:MOSES_SETUP_REMOVE_START
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $owner = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
        if ($null -eq $owner -or $owner.StartTime.ToUniversalTime().Ticks -ne $ownerStart) { break }
        if ($timer.Elapsed.TotalSeconds -ge 45) { exit }
        Start-Sleep -Milliseconds 100
    }
    $timer.Restart()
    while ($timer.Elapsed.TotalSeconds -lt 60) {
        try {
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { exit }
            $file = Get-Item -LiteralPath $target -Force
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $file.Length -ne [long]$env:MOSES_SETUP_REMOVE_LENGTH) { exit }
            $setupStream = [IO.File]::OpenRead($target)
            $setupHasher = [Security.Cryptography.SHA256]::Create()
            try { $actualHash = [BitConverter]::ToString($setupHasher.ComputeHash($setupStream)).Replace('-', '') }
            finally { $setupHasher.Dispose(); $setupStream.Dispose() }
            if ($actualHash -ne $env:MOSES_SETUP_REMOVE_SHA256) { exit }
            Remove-Item -LiteralPath $target -ErrorAction Stop
            exit
        } catch { Start-Sleep -Milliseconds 200 }
    }
} catch { exit }
";
                ProcessStartInfo start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"));
                start.Arguments = "-NoProfile -NonInteractive -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
                start.UseShellExecute = false; start.CreateNoWindow = true; start.WindowStyle = ProcessWindowStyle.Hidden;
                using (Process current = Process.GetCurrentProcess())
                {
                    start.EnvironmentVariables["MOSES_SETUP_REMOVE_PATH"] = path;
                    start.EnvironmentVariables["MOSES_SETUP_REMOVE_PID"] = current.Id.ToString();
                    start.EnvironmentVariables["MOSES_SETUP_REMOVE_START"] = current.StartTime.ToUniversalTime().Ticks.ToString();
                }
                start.EnvironmentVariables["MOSES_SETUP_REMOVE_LENGTH"] = length.ToString();
                start.EnvironmentVariables["MOSES_SETUP_REMOVE_SHA256"] = digest;
                using (Process helper = Process.Start(start)) { }
            }
            catch { } // Removal failure never changes a completed installation.
        }

        private static bool PreserveUserFile(string relative)
        {
            string name = relative.Replace('\\', '/');
            return (name.StartsWith("settings/", StringComparison.OrdinalIgnoreCase) &&
                    !name.Equals("settings/strategy_registry.json", StringComparison.OrdinalIgnoreCase)) ||
                name.Equals("Part1/program/config.txt", StringComparison.OrdinalIgnoreCase) ||
                name.Equals("Part1/special_settings.json", StringComparison.OrdinalIgnoreCase) ||
                name.Equals("Part2/backtest_ui.json", StringComparison.OrdinalIgnoreCase) ||
                name.Equals("Part2/event_backtest.json", StringComparison.OrdinalIgnoreCase) ||
                name.StartsWith("Part3/projects/", StringComparison.OrdinalIgnoreCase) ||
                name.StartsWith("Part3/generated/", StringComparison.OrdinalIgnoreCase) ||
                name.StartsWith("Part3/TEST_SPECIAL/", StringComparison.OrdinalIgnoreCase);
        }

        private static string ManagedPath(string root, string relative)
        {
            // Apply the exact archive path rules to persistent ownership metadata too.
            using (MemoryStream buffer = new MemoryStream())
            {
                using (ZipArchive archive = new ZipArchive(buffer, ZipArchiveMode.Create, true)) archive.CreateEntry(relative);
                buffer.Position = 0;
                using (ZipArchive archive = new ZipArchive(buffer, ZipArchiveMode.Read)) return SafeEntryPath(root, archive.Entries[0]);
            }
        }

        private static void CheckNotRunning(string root)
        {
            string prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            foreach (Process process in Process.GetProcesses())
            {
                try
                {
                    if (process.Id == Process.GetCurrentProcess().Id) continue;
                    string name = process.ProcessName;
                    if (!name.Equals("MOSES", StringComparison.OrdinalIgnoreCase) && !name.Equals("python", StringComparison.OrdinalIgnoreCase)) continue;
                    if (process.MainModule.FileName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("MOSES와 실행 중인 백테스트를 종료한 뒤 다시 설치해 주세요.");
                }
                catch (Win32Exception) { }
                catch (InvalidOperationException) { }
                finally { process.Dispose(); }
            }
        }

        public static void Install(string target, byte[] envelope, Action<int> progress, string mt5, bool update)
        {
            using (Stream payload = typeof(InstallCore).Assembly.GetManifestResourceStream("MosesPayload"))
            {
                if (payload == null) throw new InvalidDataException("Invalid package.");
                InstallArchive(payload, target, envelope, mt5, progress, true, update);
            }
        }

        public static void InstallArchive(Stream payload, string target, byte[] envelope, string mt5, Action<int> progress, bool integrateSystem, bool update)
        {
            Dictionary<string, object> policy = ValidateEnvelope(envelope, UtcNowSeconds());
            string root = NormalizeFolder(target);
            CheckDirectoryChain(root);
            if (update && !IsInstallation(root)) throw new IOException(MissingInstallation);
            if (!update && IsInstallation(root)) throw new IOException("선택한 폴더에 모세가 이미 설치되어 있습니다. ‘업데이트’를 클릭하거나 다른 설치 폴더를 지정해 주세요.");
            if (!update && Directory.Exists(root) && Directory.EnumerateFileSystemEntries(root).GetEnumerator().MoveNext())
                throw new IOException("MOSES 설치 폴더 또는 비어 있는 새 폴더를 선택해 주세요.");
            CheckNotRunning(root);
            if (!String.IsNullOrWhiteSpace(mt5) && !IsMt5DataFolder(mt5))
                throw new IOException("MT5 데이터 폴더를 다시 선택해 주세요.");
            Dictionary<string, object> oldRecord = update ? InstallationRecord(root) : new Dictionary<string, object>();
            string temporaryRoot = Path.GetFullPath(Path.GetTempPath());
            string work = Path.Combine(temporaryRoot, "moses-install-" + Guid.NewGuid().ToString("N"));
            string stage = Path.Combine(work, "payload");
            string backup = Path.Combine(work, "backup");
            List<string> stageFiles = new List<string>(), stageDirs = new List<string>(), createdDirs = new List<string>();
            Dictionary<string, string> originals = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            List<string> changed = new List<string>();
            bool keepRecovery = false;
            JavaScriptSerializer json = new JavaScriptSerializer();
            Action<string> remember = delegate(string path)
            {
                if (originals.ContainsKey(path)) return;
                CheckDirectoryChain(Path.GetDirectoryName(path));
                if (Directory.Exists(path) || (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0))
                    throw new IOException("설치 경로의 파일 상태를 확인해 주세요.");
                string copy = null;
                if (File.Exists(path))
                {
                    Directory.CreateDirectory(backup);
                    copy = Path.Combine(backup, changed.Count.ToString());
                    File.Copy(path, copy, false);
                }
                originals[path] = copy;
                changed.Add(path);
            };
            Action<string, byte[]> write = delegate(string path, byte[] data)
            {
                remember(path);
                CreateDirectory(Path.GetDirectoryName(path), createdDirs);
                File.WriteAllBytes(path, data);
            };
            Action<string, string> copyFile = delegate(string source, string path)
            {
                remember(path);
                CreateDirectory(Path.GetDirectoryName(path), createdDirs);
                File.Copy(source, path, true);
            };
            try
            {
                ExtractArchive(payload, stage, stageFiles, stageDirs, progress);
                if (!HasProgramFiles(stage)) throw new InvalidDataException("필수 프로그램 파일이 없습니다.");
                if (integrateSystem && !HasWebView2())
                {
                    string prerequisite = Path.Combine(stage, "prerequisites", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe");
                    ProcessStartInfo start = new ProcessStartInfo(prerequisite, "/silent /install");
                    start.UseShellExecute = false; start.CreateNoWindow = true;
                    using (Process installer = Process.Start(start))
                        if (installer == null || !installer.WaitForExit(600000)) throw new IOException("필수 실행 구성요소 설치를 확인하지 못했습니다.");
                    if (!HasWebView2()) throw new IOException("필수 실행 구성요소 설치를 확인하지 못했습니다.");
                }
                List<string> managed = new List<string>();
                foreach (string source in stageFiles)
                {
                    string relative = source.Substring(stage.Length + 1).Replace('\\', '/');
                    managed.Add(relative);
                    string destination = ManagedPath(root, relative);
                    if (update && PreserveUserFile(relative) && File.Exists(destination)) continue;
                    copyFile(source, destination);
                }
                foreach (string directory in stageDirs)
                    if (directory.StartsWith(stage + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        CreateDirectory(Path.Combine(root, directory.Substring(stage.Length + 1)), createdDirs);
                // Migrate recipient-owned strategies even when the new payload
                // has already created its empty library directory. Every file
                // change joins the same rollback transaction as the update.
                string legacyLibrary = ManagedPath(root, "Part3/generated/");
                string testLibrary = ManagedPath(root, "Part3/TEST_SPECIAL/");
                CheckDirectoryChain(legacyLibrary);
                CheckDirectoryChain(testLibrary);
                if (Directory.Exists(legacyLibrary))
                    foreach (string source in Directory.GetFiles(legacyLibrary))
                    {
                        if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0)
                            throw new IOException("테스트 전략 파일의 저장 위치를 확인해 주세요.");
                        string destination = ManagedPath(root, "Part3/TEST_SPECIAL/" + Path.GetFileName(source));
                        if (File.Exists(destination))
                        {
                            if (!File.ReadAllBytes(source).SequenceEqual(File.ReadAllBytes(destination)))
                                throw new IOException("이전 전략과 테스트 전략 폴더에 같은 이름의 다른 파일이 있습니다: " + Path.GetFileName(source));
                        }
                        else copyFile(source, destination);
                        remember(source); File.Delete(source);
                    }
                object oldManaged;
                if (oldRecord.TryGetValue("managed_files", out oldManaged))
                    foreach (object item in (System.Collections.IEnumerable)oldManaged)
                    {
                        string relative = Convert.ToString(item);
                        if (managed.Contains(relative) || PreserveUserFile(relative)) continue;
                        string path = ManagedPath(root, relative);
                        if (File.Exists(path)) { remember(path); File.Delete(path); }
                    }
                // New release presets replace old ones; preserve only still registered settings.
                string registryPath = Path.Combine(root, "settings", "strategy_registry.json");
                Dictionary<string, object> registry = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(registryPath));
                HashSet<string> available = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (object item in (System.Collections.IEnumerable)registry["presets"])
                    available.Add(Convert.ToString(((Dictionary<string, object>)item)["id"]));
                string generated = Path.Combine(root, "Part3", "TEST_SPECIAL");
                if (Directory.Exists(generated))
                    foreach (string path in Directory.GetFiles(generated, "Test_SPECIAL???.recipe.json"))
                        if (File.Exists(Path.ChangeExtension(Path.ChangeExtension(path, null), ".py")))
                            available.Add(Path.GetFileName(path).Split('.')[0].ToUpperInvariant());
                foreach (string relative in new string[] { "Part1/special_settings.json", "Part2/backtest_ui.json" })
                {
                    string path = ManagedPath(root, relative);
                    if (!File.Exists(path)) continue;
                    Dictionary<string, object> data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                    object value;
                    if (!data.TryGetValue("specials", out value) || !(value is Dictionary<string, object>)) throw new InvalidDataException("전략 설정 파일을 확인해 주세요.");
                    Dictionary<string, object> rows = (Dictionary<string, object>)value;
                    foreach (string name in new List<string>(rows.Keys)) if (!available.Contains(name)) rows.Remove(name);
                    write(path, Encoding.UTF8.GetBytes(json.Serialize(data)));
                }
                byte[] protectedEnvelope = ProtectedData.Protect(envelope, null, DataProtectionScope.LocalMachine);
                write(Path.Combine(root, "runtime", "license.bin"), protectedEnvelope);
                if (!String.IsNullOrWhiteSpace(mt5))
                {
                    foreach (string name in Mt5Files)
                    {
                        string source = Path.Combine(stage, "Part1", "program", "MT5", name + ".ex5");
                        string destination = Path.Combine(mt5, "MQL5", name == Mt5Files[0] ? "Experts" : "Indicators", name + ".ex5");
                        copyFile(source, destination);
                    }
                }
                Dictionary<string, object> record = new Dictionary<string, object>();
                record["schema"] = 1; record["version"] = policy["version"];
                record["managed_files"] = managed.ToArray(); record["mt5_data_folder"] = mt5 ?? "";
                write(Path.Combine(root, "runtime", "install.json"), Encoding.UTF8.GetBytes(json.Serialize(record)));
                if (progress != null) progress(100);
                if (integrateSystem) RegisterInstallation(root);
            }
            catch (Exception original)
            {
                List<string> failures = new List<string>();
                for (int i = changed.Count - 1; i >= 0; i--)
                {
                    string path = changed[i];
                    try
                    {
                        if (originals[path] == null) { if (File.Exists(path)) File.Delete(path); }
                        else File.Copy(originals[path], path, true);
                    }
                    catch { failures.Add(path); }
                }
                for (int i = createdDirs.Count - 1; i >= 0; i--) try { Directory.Delete(createdDirs[i], false); } catch { }
                if (failures.Count != 0) { keepRecovery = true; throw new IOException("일부 파일의 복구를 확인하지 못했습니다. 복구 파일 위치: " + backup, original); }
                throw;
            }
            finally
            {
                // All recursively removed files were created under this random staging folder.
                // Keep backup evidence when rollback did not restore every old file.
                if (!keepRecovery && Path.GetDirectoryName(Path.GetFullPath(work)).TrimEnd(Path.DirectorySeparatorChar).Equals(temporaryRoot.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                    try { if (Directory.Exists(work)) Directory.Delete(work, true); } catch { }
            }
        }

    }

    public sealed class Mt5Location
    {
        public string DataFolder { get; private set; }
        public string TerminalFolder { get; private set; }
        public Mt5Location(string dataFolder, string terminalFolder) { DataFolder = dataFolder; TerminalFolder = terminalFolder; }
        public override string ToString()
        {
            if (String.IsNullOrEmpty(TerminalFolder)) return "MT5 데이터 폴더 · " + DataFolder;
            return Path.GetFileName(TerminalFolder) + " · " + TerminalFolder;
        }
    }

    internal sealed class InstallWindow : Form
    {
        private readonly byte[] envelope;
        private readonly TextBox folder;
        private readonly Button browse;
        private readonly Button install;
        private readonly Button update;
        private readonly Button mt5Browse;
        private readonly ComboBox mt5Location;
        private readonly Label status;
        private readonly ProgressBar progress;
        private bool busy;
        private bool folderWasChosen;
        private string mt5RootLoaded;
        internal bool CompletedSuccessfully { get; private set; }

        public InstallWindow(byte[] record, string version)
        {
            envelope = record;
            Text = "MOSES " + version + " 설치 / 업데이트";
            ClientSize = new Size(670, 360);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("맑은 고딕", 10);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            Label label = new Label(); label.Text = "모세 설치 위치 · 업데이트할 폴더";
            label.SetBounds(22, 18, 610, 26); Controls.Add(label);
            folder = new TextBox();
            folder.Text = InstallCore.DefaultInstallationFolder(Assembly.GetExecutingAssembly().Location);
            folder.SetBounds(22, 48, 525, 28); Controls.Add(folder);
            folder.TextChanged += delegate { folderWasChosen = true; };
            browse = new Button(); browse.Text = "찾아보기";
            browse.SetBounds(554, 46, 92, 32);
            browse.Click += delegate
            {
                using (FolderBrowserDialog dialog = new FolderBrowserDialog())
                {
                    dialog.Description = "모세를 설치할 폴더 또는 업데이트할 기존 모세 폴더를 선택해 주세요.";
                    dialog.SelectedPath = folder.Text;
                    if (dialog.ShowDialog(this) == DialogResult.OK)
                    {
                        folderWasChosen = true; folder.Text = dialog.SelectedPath;
                        LoadMt5(folder.Text);
                    }
                }
            };
            Controls.Add(browse);
            Label mt5Label = new Label(); mt5Label.Text = "EA·지표를 복사할 MT5 · 설치 위치";
            mt5Label.SetBounds(22, 88, 610, 26); Controls.Add(mt5Label);
            mt5Location = new ComboBox(); mt5Location.DropDownStyle = ComboBoxStyle.DropDownList;
            mt5Location.DropDownWidth = 900;
            mt5Location.SetBounds(22, 119, 525, 28); Controls.Add(mt5Location);
            mt5Browse = new Button(); mt5Browse.Text = "경로 선택";
            mt5Browse.SetBounds(554, 117, 92, 32); Controls.Add(mt5Browse);
            mt5Browse.Click += delegate
            {
                using (FolderBrowserDialog dialog = new FolderBrowserDialog())
                {
                    dialog.Description = "MT5 설치 폴더 또는 파일 → 데이터 폴더 열기에서 확인한 폴더를 선택해 주세요.";
                    if (dialog.ShowDialog(this) != DialogResult.OK) return;
                    List<Mt5Location> found;
                    try { found = InstallCore.ResolveMt5Folder(dialog.SelectedPath, InstallCore.FindMt5Locations()); }
                    catch { found = new List<Mt5Location>(); }
                    if (found.Count != 1)
                    {
                        MessageBox.Show(this, found.Count > 1 ? "MT5 데이터 폴더가 여러 개 연결되어 있습니다. 사용할 MT5에서 파일 → 데이터 폴더 열기로 확인한 폴더를 지정해 주세요." :
                            "연결된 MT5 데이터 폴더를 찾지 못했습니다. MT5에서 파일 → 데이터 폴더 열기로 확인한 폴더를 지정해 주세요.", "MOSES"); return;
                    }
                    Mt5Location selected = found[0];
                    foreach (object item in mt5Location.Items)
                    {
                        Mt5Location candidate = item as Mt5Location;
                        if (candidate != null && String.Equals(candidate.DataFolder, selected.DataFolder, StringComparison.OrdinalIgnoreCase)) { selected = candidate; break; }
                    }
                    if (!mt5Location.Items.Contains(selected)) mt5Location.Items.Add(selected);
                    mt5Location.SelectedItem = selected;
                }
            };
            Label note = new Label();
            note.Text = "MOSES와 MT5를 종료한 뒤 진행하세요. EX5는 설치 폴더의 MT5 폴더에도 보관됩니다.";
            note.SetBounds(22, 157, 624, 44); Controls.Add(note);
            status = new Label(); status.SetBounds(22, 205, 624, 50); Controls.Add(status);
            progress = new ProgressBar(); progress.SetBounds(22, 265, 624, 20); Controls.Add(progress);
            update = new Button(); update.Text = "업데이트";
            update.SetBounds(22, 304, 112, 36); update.Click += BeginUpdate; Controls.Add(update);
            install = new Button(); install.Text = "설치";
            install.SetBounds(145, 304, 112, 36); install.Click += BeginInstall; Controls.Add(install);
            LoadMt5(folder.Text);
            folder.Leave += delegate { LoadMt5(folder.Text); };
            FormClosing += delegate(object sender, FormClosingEventArgs args) { if (busy) args.Cancel = true; };
        }

        private void LoadMt5(string root)
        {
            if (String.Equals(mt5RootLoaded, root, StringComparison.OrdinalIgnoreCase)) return;
            mt5RootLoaded = root;
            List<Mt5Location> candidates = InstallCore.FindMt5Locations();
            bool existing = InstallCore.IsInstallation(root);
            string saved = existing ? InstallCore.SavedMt5(root) : "";
            bool missingSaved = !String.IsNullOrEmpty(saved) && !InstallCore.IsMt5DataFolder(saved);
            if (InstallCore.IsMt5DataFolder(saved) && !candidates.Any(value => String.Equals(value.DataFolder, saved, StringComparison.OrdinalIgnoreCase)))
                candidates.Insert(0, InstallCore.ResolveMt5Folder(saved, candidates)[0]);
            mt5Location.Items.Clear();
            mt5Location.Items.Add("MT5 경로 지정 없이 설치 · EX5 직접 복사");
            foreach (Mt5Location candidate in candidates) mt5Location.Items.Add(candidate);
            if (InstallCore.IsMt5DataFolder(saved))
                mt5Location.SelectedItem = candidates.First(value => String.Equals(value.DataFolder, saved, StringComparison.OrdinalIgnoreCase));
            else if (existing && String.IsNullOrEmpty(saved)) mt5Location.SelectedIndex = 0;
            else if (candidates.Count == 1 && !missingSaved) mt5Location.SelectedIndex = 1;
            else if (candidates.Count == 0 && !missingSaved) mt5Location.SelectedIndex = 0;
            status.Text = missingSaved ? "기존 MT5 위치를 찾지 못했습니다. 경로를 다시 선택하거나 EX5 직접 복사를 선택해 주세요." :
                mt5Location.SelectedIndex < 0 ? "사용할 MT5를 선택해 주세요." : "설치 폴더의 MOSES.exe로 실행할 수 있습니다. 설치가 완료되면 창이 닫히고 이 설치 파일이 삭제됩니다.";
        }

        private void BeginInstall(object sender, EventArgs args) { StartInstallation(false); }
        private void BeginUpdate(object sender, EventArgs args) { StartInstallation(true); }

        private void StartInstallation(bool updating)
        {
            try { InstallCore.ValidateEnvelope(envelope, InstallCore.UtcNowSeconds()); }
            catch { Close(); return; }
            if (updating && !folderWasChosen)
            {
                List<string> existing = InstallCore.FindInstallations();
                if (existing.Count != 1) { status.Text = existing.Count == 0 ? InstallCore.MissingInstallation : InstallCore.MultipleInstallations; return; }
                folder.Text = existing[0];
            }
            if (String.IsNullOrWhiteSpace(folder.Text)) { status.Text = "모세 폴더를 지정해 주세요."; return; }
            if (updating && !InstallCore.IsInstallation(folder.Text)) { status.Text = InstallCore.MissingInstallation; return; }
            if (!updating && InstallCore.IsInstallation(folder.Text)) { status.Text = "선택한 폴더에 모세가 이미 설치되어 있습니다. ‘업데이트’를 클릭하거나 다른 설치 폴더를 지정해 주세요."; return; }
            LoadMt5(folder.Text);
            if (mt5Location.SelectedIndex < 0) { status.Text = "MT5 또는 직접 복사 방식을 선택해 주세요."; return; }
            string destination = folder.Text;
            Mt5Location selected = mt5Location.SelectedItem as Mt5Location;
            string mt5 = selected == null ? "" : selected.DataFolder;
            busy = true;
            install.Enabled = update.Enabled = browse.Enabled = folder.Enabled = mt5Location.Enabled = mt5Browse.Enabled = false;
            status.Text = updating ? "기존 프로그램을 업데이트하고 있습니다." : "프로그램을 설치하고 있습니다.";
            BackgroundWorker worker = new BackgroundWorker(); worker.WorkerReportsProgress = true;
            worker.DoWork += delegate { InstallCore.Install(destination, envelope, worker.ReportProgress, mt5, updating); };
            worker.ProgressChanged += delegate(object unused, ProgressChangedEventArgs change) { progress.Value = Math.Min(100, Math.Max(0, change.ProgressPercentage)); };
            worker.RunWorkerCompleted += delegate(object unused, RunWorkerCompletedEventArgs result)
            {
                try { FinishInstallation(result); }
                finally { worker.Dispose(); }
            };
            worker.RunWorkerAsync();
        }

        private void FinishInstallation(RunWorkerCompletedEventArgs result)
        {
            busy = false;
            if (result.Error == null && !result.Cancelled)
            {
                progress.Value = 100;
                CompletedSuccessfully = true;
                Close();
                return;
            }
            install.Enabled = update.Enabled = browse.Enabled = folder.Enabled = mt5Location.Enabled = mt5Browse.Enabled = true;
            progress.Value = 0;
            status.Text = result.Cancelled ? "설치가 취소되었습니다." : result.Error.Message;
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            byte[] envelope;
            Dictionary<string, object> policy;
            try
            {
                envelope = InstallCore.ReadEnvelope(Assembly.GetExecutingAssembly().Location);
                policy = InstallCore.ValidateEnvelope(envelope, InstallCore.UtcNowSeconds());
            }
            catch { return; } // Expired, altered, or unstamped installers deliberately remain silent.
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (InstallWindow window = new InstallWindow(envelope, (string)policy["version"]))
            {
                Application.Run(window);
                if (window.CompletedSuccessfully) InstallCore.ScheduleInstallerRemoval(Assembly.GetExecutingAssembly().Location);
            }
        }
    }
}
