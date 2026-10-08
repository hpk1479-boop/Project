function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function ConvertTo-PortableText {
    param([AllowEmptyString()][string]$Text, [string]$ProjectRoot)
    foreach ($item in @(@($ProjectRoot, '<project>'), @($env:USERPROFILE, '<user>'), @([IO.Path]::GetTempPath(), '<temp>'))) {
        if ($item[0]) { $Text = [regex]::Replace($Text, [regex]::Escape([string]$item[0]), [string]$item[1], 'IgnoreCase') }
    }
    $Text = [regex]::Replace($Text, '[A-Za-z]:[\\/][^\r\n\s"''<>|]*', '<path>')
    $Text = [regex]::Replace($Text, '\\\\[^\r\n\s"''<>|]+', '<path>')
    if ($env:USERNAME) { $Text = [regex]::Replace($Text, [regex]::Escape($env:USERNAME), '<user>', 'IgnoreCase') }
    return $Text
}

function Write-SetupLog {
    param([string]$Text)
    $clean = ConvertTo-PortableText $Text $script:SetupRoot
    Write-Host $clean
    if ($script:SetupLog) {
        $directory = Split-Path -Parent $script:SetupLog
        if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        [IO.File]::AppendAllText($script:SetupLog, (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $clean + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
    }
}

function Invoke-Process {
    param([string]$Executable, [string[]]$Arguments = @(), [string]$WorkingDirectory = $PWD.Path, [int]$TimeoutSeconds = 1800)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $start.EnvironmentVariables['PYTHONUTF8'] = '1'
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $start.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $start.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    $start.EnvironmentVariables.Remove('PYLAUNCHER_ALLOW_INSTALL')
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw '프로세스를 시작하지 못했습니다.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)) {
            $killer = New-Object Diagnostics.Process
            $killer.StartInfo = New-Object Diagnostics.ProcessStartInfo
            $killer.StartInfo.FileName = 'taskkill.exe'
            $killer.StartInfo.Arguments = '/PID ' + $process.Id + ' /T /F'
            $killer.StartInfo.UseShellExecute = $false
            $killer.StartInfo.CreateNoWindow = $true
            $killer.StartInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
            try { if ($killer.Start() -and -not $killer.WaitForExit(5000)) { $killer.Kill() } }
            catch { if (-not $process.HasExited) { $process.Kill() } }
            finally { $killer.Dispose() }
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
            throw '프로세스 제한시간이 지났습니다. 로그를 확인하고 다시 실행하세요.'
        }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; StdOut = $stdout.GetAwaiter().GetResult(); StdErr = $stderr.GetAwaiter().GetResult() }
    } finally { $process.Dispose() }
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory, [string]$Stage)
    Write-SetupLog ($Stage + ' 시작')
    $result = Invoke-Process $Executable $Arguments $WorkingDirectory
    if ($result.StdOut) { Write-SetupLog $result.StdOut.Trim() }
    if ($result.StdErr) { Write-SetupLog $result.StdErr.Trim() }
    if ($result.ExitCode -eq 3010) {
        $failure = New-Object InvalidOperationException ($Stage + ': 재부팅이 필요합니다. 재부팅 후 통합설치를 다시 실행하세요. 후속 설치는 중지했습니다.')
        $failure.Data['RebootRequired'] = $true
        throw $failure
    }
    if ($result.ExitCode -ne 0) { throw ($Stage + ' 실패 (종료 코드 ' + $result.ExitCode + '). 설치 권한 또는 로그를 확인하세요.') }
    Write-SetupLog ($Stage + ' 완료')
}

function Get-PythonInfo {
    param([string]$Executable, [string[]]$Arguments = @())
    $code = 'import json,sys,struct;print(json.dumps(dict(version=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,executable=sys.executable,base_executable=getattr(sys,"_base_executable",sys.executable),prefix=sys.prefix,base_prefix=sys.base_prefix)))'
    try {
        $result = Invoke-Process $Executable (@($Arguments) + @('-I', '-B', '-c', $code)) -TimeoutSeconds 15
        if ($result.ExitCode -ne 0) { return $null }
        return ($result.StdOut.Trim() | ConvertFrom-Json -ErrorAction Stop)
    } catch { return $null }
}

function Test-CompatiblePython {
    param($Info)
    return ($null -ne $Info -and $Info.bits -eq 64 -and $Info.version.Count -ge 3 -and $Info.version[0] -eq 3 -and $Info.version[1] -in @(12, 13))
}

function Get-RegistryValues {
    param([string]$Subkey, [string]$Name)
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
            $root = $null; $key = $null
            try {
                $root = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
                $key = $root.OpenSubKey($Subkey)
                if ($key) { $value = $key.GetValue($Name); if ($value) { $value } }
            } catch { } finally { if ($key) { $key.Dispose() }; if ($root) { $root.Dispose() } }
        }
    }
}

function Find-CompatiblePython {
    $candidates = @()
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) { foreach ($minor in @(13, 12)) { $candidates += [pscustomobject]@{ Exe = $launcher.Source; Args = @('-3.' + $minor) } } }
    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($python) { $candidates += [pscustomobject]@{ Exe = $python.Source; Args = @() } }
    foreach ($minor in @(13, 12)) {
        $key = 'SOFTWARE\Python\PythonCore\3.' + $minor + '\InstallPath'
        foreach ($path in @(Get-RegistryValues $key 'ExecutablePath')) { $candidates += [pscustomobject]@{ Exe = [string]$path; Args = @() } }
        foreach ($path in @(Get-RegistryValues $key '')) { $candidates += [pscustomobject]@{ Exe = (Join-Path $path 'python.exe'); Args = @() } }
        if ($env:LOCALAPPDATA) { $candidates += [pscustomobject]@{ Exe = (Join-Path $env:LOCALAPPDATA ('Programs\Python\Python3' + $minor + '\python.exe')); Args = @() } }
    }
    foreach ($candidate in $candidates) {
        if ($candidate.Exe -match '[\\/]WindowsApps[\\/]') { continue }
        if (-not (Test-Path -LiteralPath $candidate.Exe -PathType Leaf)) { continue }
        $info = Get-PythonInfo $candidate.Exe $candidate.Args
        if (-not (Test-CompatiblePython $info)) { continue }
        $base = Get-PythonInfo $info.base_executable
        if ((Test-CompatiblePython $base) -and $base.prefix -eq $base.base_prefix -and (Test-Path -LiteralPath $base.executable -PathType Leaf)) { return $base }
    }
    return $null
}

function Assert-ManagedPath {
    param([string]$Path, [string]$Parent)
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $boundary = [IO.Path]::GetFullPath($Parent).TrimEnd('\', '/')
    if (-not $full.StartsWith($boundary + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw '관리 범위를 벗어난 파일 이동을 차단했습니다.' }
    $cursor = $full
    while ($cursor -and $cursor.Length -ge $boundary.Length) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '연결된 폴더의 파일 이동을 차단했습니다. 일반 폴더로 옮겨 실행하세요.' }
        }
        if ($cursor -eq $boundary) { break }
        $cursor = Split-Path -Parent $cursor
    }
    return $full
}

function Move-EnvironmentAside {
    param([string]$Path, [string]$ProjectRoot, [string]$Suffix = 'backup')
    $source = Assert-ManagedPath $Path $ProjectRoot
    if (-not (Test-Path -LiteralPath $source)) { return $null }
    $name = (Split-Path -Leaf $source) + '.' + $Suffix + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $target = Assert-ManagedPath (Join-Path (Split-Path -Parent $source) $name) $ProjectRoot
    Rename-Item -LiteralPath $source -NewName $name -ErrorAction Stop
    Write-SetupLog ('환경 보존: ' + $target.Substring([IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\').Length + 1))
    return $target
}

function Restore-Environment {
    param([string]$Path, [string]$Backup, [string]$ProjectRoot)
    if (-not $Backup) { return }
    Assert-ManagedPath $Backup $ProjectRoot | Out-Null
    Assert-ManagedPath $Path $ProjectRoot | Out-Null
    if (Test-Path -LiteralPath $Path) { Move-EnvironmentAside $Path $ProjectRoot 'failed' | Out-Null }
    Rename-Item -LiteralPath $Backup -NewName (Split-Path -Leaf $Path) -ErrorAction Stop
    Write-SetupLog '이전 실행 환경을 원래 위치로 복구했습니다.'
}

function Get-EnvironmentReport {
    param([string]$Env, $BaseInfo, [ValidateSet('main', 'generic', 'history')][string]$Kind = 'main')
    if (-not (Test-CompatiblePython $BaseInfo)) { return [pscustomobject]@{ OK = $false; Reason = '선택한 기본 Python을 확인하지 못했습니다.' } }
    $python = Join-Path $Env 'Scripts\python.exe'
    if ($Kind -eq 'main' -and -not (Test-Path -LiteralPath (Join-Path $Env 'Scripts\pythonw.exe') -PathType Leaf)) { return [pscustomobject]@{ OK = $false; Reason = 'MOSES 창 실행용 Python이 없습니다.' } }
    $info = Get-PythonInfo $python
    if (-not (Test-CompatiblePython $info)) { return [pscustomobject]@{ OK = $false; Reason = 'Python 3.12/3.13 64비트 실행 환경을 확인하지 못했습니다.' } }
    if ($info.prefix -ne [IO.Path]::GetFullPath($Env) -or $info.base_executable -ne $BaseInfo.executable -or $info.base_prefix -ne $BaseInfo.prefix -or ($info.version -join '.') -ne ($BaseInfo.version -join '.')) {
        return [pscustomobject]@{ OK = $false; Reason = '복사된 환경 또는 다른 기본 Python 연결을 다시 구성해야 합니다.' }
    }
    $cfg = Join-Path $Env 'pyvenv.cfg'
    if (Test-Path -LiteralPath $cfg) {
        $command = Get-Content -LiteralPath $cfg -Encoding UTF8 | Where-Object { $_ -match '^command\s*=' } | Select-Object -First 1
        if ($command -and $command.IndexOf([IO.Path]::GetFullPath($Env), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return [pscustomobject]@{ OK = $false; Reason = '다른 위치에서 복사한 환경입니다. 통합설치를 다시 실행하세요.' }
        }
    }
    $checks = switch ($Kind) {
        'main' { 'mods=["numpy","pandas","duckdb","webview","clr","requests","tzdata","jsonschema"]; pins={"numpy":"2.3.5","pandas":"3.0.1","pywebview":"6.2.1"}' }
        'generic' { 'mods=["numpy","pandas","duckdb"]; pins={"numpy":"2.3.5","pandas":"3.0.1"}' }
        'history' { 'mods=["numpy","MetaTrader5"]; pins={"numpy":"2.3.5","MetaTrader5":"5.0.6180"}' }
    }
    $code = "import importlib,importlib.metadata as m`n$checks`nfor name in mods: importlib.import_module(name)`nfor name,version in pins.items(): assert m.version(name)==version,name+' version mismatch'`n"
    if ($Kind -ne 'history') { $code += "v=tuple(map(int,m.version('duckdb').split('.')[:2])); assert (1,4)<=v<(2,0),'duckdb version mismatch'`n" }
    if ($Kind -eq 'main') { $code += "v=tuple(map(int,m.version('requests').split('.')[:2])); assert (2,32)<=v<(3,0),'requests version mismatch'`nv=tuple(map(int,m.version('jsonschema').split('.')[:2])); assert (4,23)<=v<(5,0),'jsonschema version mismatch'`nfrom zoneinfo import ZoneInfo`nZoneInfo('America/New_York'); ZoneInfo('Asia/Seoul')`n" }
    $result = Invoke-Process $python @('-I', '-B', '-c', $code) -TimeoutSeconds 60
    return [pscustomobject]@{ OK = ($result.ExitCode -eq 0); Reason = $(if ($result.ExitCode -eq 0) { '필수 모듈과 연결 정상' } else { '필수 모듈 또는 요구 버전 확인 실패' }) }
}

function Install-MosesEnv {
    param($BaseInfo, [string]$ProjectRoot)
    $envPath = Join-Path $ProjectRoot '통합설치\.venv-moses'
    if ((Get-EnvironmentReport $envPath $BaseInfo 'main').OK) { Write-SetupLog 'MOSES 실행 환경이 정상입니다.'; return }
    $backup = Move-EnvironmentAside $envPath $ProjectRoot
    try {
        Invoke-Checked $BaseInfo.executable @('-m', 'venv', $envPath) $ProjectRoot 'MOSES 가상환경 구성'
        Invoke-Checked (Join-Path $envPath 'Scripts\python.exe') @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $ProjectRoot '통합설치\requirements-main.txt')) $ProjectRoot 'MOSES 필수 패키지 설치'
        if (-not (Get-EnvironmentReport $envPath $BaseInfo 'main').OK) { throw 'MOSES 필수 모듈 최종 확인에 실패했습니다.' }
    } catch {
        if ($backup) { Restore-Environment $envPath $backup $ProjectRoot }
        elseif (Test-Path -LiteralPath $envPath) { Move-EnvironmentAside $envPath $ProjectRoot 'failed' | Out-Null }
        throw
    }
}

function Install-BacktestEnv {
    param($BaseInfo, [string]$ProjectRoot)
    $backups = @{}
    $part2 = Join-Path $ProjectRoot 'Part2'
    try {
        foreach ($kind in @('generic', 'history')) {
            $path = Join-Path $part2 ('.venv-' + $kind)
            if (-not (Get-EnvironmentReport $path $BaseInfo $kind).OK) { $backups[$kind] = Move-EnvironmentAside $path $ProjectRoot }
        }
        try { Invoke-Checked $BaseInfo.executable @('-B', (Join-Path $part2 'bootstrap.py'), '--history') $part2 'Part2 독립 실행 환경 구성' }
        finally { Convert-BacktestLogs $ProjectRoot }
        foreach ($kind in @('generic', 'history')) {
            if (-not (Get-EnvironmentReport (Join-Path $part2 ('.venv-' + $kind)) $BaseInfo $kind).OK) { throw 'Part2 필수 모듈 최종 확인에 실패했습니다.' }
        }
    } catch {
        foreach ($kind in @('generic', 'history')) {
            if ($backups.ContainsKey($kind)) {
                $path = Join-Path $part2 ('.venv-' + $kind)
                if ($backups[$kind]) { Restore-Environment $path $backups[$kind] $ProjectRoot }
                elseif (Test-Path -LiteralPath $path) { Move-EnvironmentAside $path $ProjectRoot 'failed' | Out-Null }
            }
        }
        throw
    }
}

function Convert-BacktestLogs {
    param([string]$ProjectRoot)
    foreach ($name in @('BACKTEST_SETUP.log', 'BACKTEST_SETUP_RAW.log')) {
        $file = Assert-ManagedPath (Join-Path $ProjectRoot ('Part2\' + $name)) $ProjectRoot
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $clean = ConvertTo-PortableText ([IO.File]::ReadAllText($file)) $ProjectRoot
            [IO.File]::WriteAllText($file, $clean, (New-Object Text.UTF8Encoding($false)))
        }
    }
}

function Download-Installer {
    param([string]$Url, [string]$Filename)
    $tempRoot = [IO.Path]::GetTempPath()
    $folder = Join-Path $tempRoot ('moses-setup-' + [guid]::NewGuid().ToString('N'))
    Assert-ManagedPath $folder $tempRoot | Out-Null
    New-Item -ItemType Directory -Path $folder -ErrorAction Stop | Out-Null
    $file = Join-Path $folder ([IO.Path]::GetFileName($Filename))
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Url -OutFile $file -UseBasicParsing -ErrorAction Stop
        return $file
    } catch { Remove-InstallerCache $file; throw '공식 설치 파일을 다운로드하지 못했습니다. 네트워크 연결을 확인하세요.' }
}

function Remove-InstallerCache {
    param([string]$Path)
    $tempRoot = [IO.Path]::GetTempPath()
    $full = Assert-ManagedPath $Path $tempRoot
    $folder = Split-Path -Parent $full
    if ((Split-Path -Leaf $folder) -notmatch '^moses-setup-[0-9a-f]{32}$') { throw '설치 캐시 삭제 범위를 확인하지 못했습니다.' }
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Force -ErrorAction Stop }
    if ((Test-Path -LiteralPath $folder) -and -not (Get-ChildItem -LiteralPath $folder -Force)) { Remove-Item -LiteralPath $folder -Force -ErrorAction Stop }
}

function Assert-InstallerSignature {
    param([string]$Path, [string]$Publisher = '')
    $signature = Get-AuthenticodeSignature -LiteralPath $Path -ErrorAction Stop
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or ($Publisher -and $signature.SignerCertificate.Subject -notmatch $Publisher)) { throw '설치 파일의 유효한 발행자 서명을 확인하지 못했습니다. 설치를 중지했습니다.' }
}

function Install-BasePython {
    $file = Download-Installer 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' 'python-3.13.16-amd64.exe'
    try {
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b') { throw 'Python 설치 파일의 SHA256이 일치하지 않습니다.' }
        Assert-InstallerSignature $file 'Python Software Foundation'
        Invoke-Checked $file @('/quiet', 'InstallAllUsers=0', 'InstallLauncherAllUsers=0', 'Include_launcher=1', 'Include_pip=1', 'Include_test=0', 'PrependPath=0') (Split-Path -Parent $file) 'Python 사용자 설치'
        $base = Find-CompatiblePython
        if (-not $base) { throw '설치 후 Python 3.12/3.13 64비트를 확인하지 못했습니다.' }
        return $base
    } finally { Remove-InstallerCache $file }
}

function Get-WebViewVersion {
    $key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($value in @(Get-RegistryValues $key 'pv')) {
        $version = $null
        if ([version]::TryParse([string]$value, [ref]$version) -and $version -gt [version]'0.0.0.0') { return [string]$version }
    }
    return $null
}

function Ensure-WebView {
    if (Get-WebViewVersion) { Write-SetupLog 'WebView2 Runtime이 정상입니다.'; return }
    $file = Download-Installer 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' 'MicrosoftEdgeWebview2Setup.exe'
    try {
        Assert-InstallerSignature $file 'Microsoft'
        Invoke-Checked $file @('/silent', '/install') (Split-Path -Parent $file) 'WebView2 사용자 설치'
        if (-not (Get-WebViewVersion)) { throw 'WebView2 설치 후 등록을 확인하지 못했습니다. 조직의 설치 정책이나 관리자 권한 필요 여부를 확인하세요.' }
    } finally { Remove-InstallerCache $file }
}

function Find-Ollama {
    $command = Get-Command ollama.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $paths = @()
    if ($command) { $paths += $command.Source }
    if ($env:LOCALAPPDATA) { $paths += Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    foreach ($path in $paths) {
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and (Invoke-Process $path @('--version')).ExitCode -eq 0) { return $path }
    }
    return $null
}

function Install-Ollama {
    if (Find-Ollama) { Write-SetupLog '선택 항목 Ollama 실행 파일을 확인했습니다.'; return }
    $file = Download-Installer 'https://ollama.com/download/OllamaSetup.exe' 'OllamaSetup.exe'
    try {
        Assert-InstallerSignature $file
        Invoke-Checked $file @('/VERYSILENT', '/NORESTART') (Split-Path -Parent $file) '선택 항목 Ollama 설치'
        if (-not (Find-Ollama)) { throw 'Ollama 설치 후 실행 파일의 버전을 확인하지 못했습니다.' }
    } finally { Remove-InstallerCache $file }
}

function Show-BacktestOverrideNotice {
    param([string]$ProjectRoot)
    $file = Join-Path $ProjectRoot 'Part3\projects\connections.json'
    if (Test-Path -LiteralPath $file) {
        try {
            $data = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            if ($data.python_executable) { Write-SetupLog '백테스트 Python 연결 설정이 있습니다. 해당 설정을 유지했습니다. 연결 화면에서 현재 실행 환경을 확인하세요.' }
        } catch { Write-SetupLog '백테스트 연결 설정을 읽지 못했습니다. 기존 파일을 유지했습니다. 연결 화면에서 확인하세요.' }
    }
}


function Start-Process { throw 'unexpected external process' }
function Invoke-WebRequest { throw 'unexpected network request' }
function Invoke-RestMethod { throw 'unexpected network request' }

$script:FixtureBase = ('{"version": [3, 12, 10], "bits": 64, "executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "base_executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313", "base_prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313"}' | ConvertFrom-Json)

function Get-PythonInfo {
    param([string]$Executable, [string[]]$Arguments = @())
    if ($Executable -eq $script:FixtureBase.executable) { return $script:FixtureBase }
    $prefix = Split-Path -Parent (Split-Path -Parent $Executable)

    return [pscustomobject]@{
        version = $script:FixtureBase.version; bits = 64; executable = $Executable
        base_executable = $script:FixtureBase.executable; prefix = $prefix
        base_prefix = $script:FixtureBase.prefix
    }
}
function Get-WebViewVersion { return '120.0.0.0' }
function Invoke-Process {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    return [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
}
function Download-Installer { throw 'unexpected download' }
