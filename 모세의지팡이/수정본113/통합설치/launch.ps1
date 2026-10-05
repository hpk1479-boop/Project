param([switch]$CheckOnly, [switch]$NoPrompt, [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
$script:SetupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:SetupLog = Join-Path $PSScriptRoot 'logs\launch.log'
. (Join-Path $PSScriptRoot 'common.ps1')
try {
    $envPath = Join-Path $PSScriptRoot '.venv-moses'
    $info = Get-PythonInfo (Join-Path $envPath 'Scripts\python.exe')
    if (Test-CompatiblePython $info) {
        $base = Get-PythonInfo $info.base_executable
    } else {
        $base = Find-CompatiblePython
        if (-not (Test-CompatiblePython $base)) { throw '개발자 실행에는 Python 3.12 또는 3.13 64비트가 필요합니다.' }
        $requirements = Join-Path $PSScriptRoot 'requirements-main.txt'
        $fingerprint = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
        $shared = Join-Path (Split-Path -Parent $script:SetupRoot) '.moses-runtime'
        $envPath = Join-Path $shared ('main-' + ($base.version -join '.') + '-' + $fingerprint)
        $mutex = New-Object Threading.Mutex($false, ('Local\MosesDeveloperEnvironment-' + $fingerprint))
        $held = $false
        try {
            try { $held = $mutex.WaitOne(60000) } catch [Threading.AbandonedMutexException] { $held = $true }
            if (-not $held) { throw '다른 창에서 개발자 실행 환경을 준비 중입니다. 잠시 후 다시 실행하세요.' }
            if (-not (Get-EnvironmentReport $envPath $base 'main').OK) {
                if ($CheckOnly) { throw '공용 개발자 실행 환경이 없습니다. MOSES_실행.bat를 실행하면 처음 한 번 자동 준비합니다.' }
                Write-SetupLog '공용 개발자 실행 환경을 준비합니다. 필요한 패키지를 다운로드 중입니다.'
                if (-not (Test-Path -LiteralPath $shared)) { New-Item -ItemType Directory -Path $shared | Out-Null }
                Invoke-Checked $base.executable @('-m', 'venv', $envPath) $script:SetupRoot '공용 Python 실행 환경 구성'
                Invoke-Checked (Join-Path $envPath 'Scripts\python.exe') @('-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check', '-r', $requirements) $script:SetupRoot '개발자 필수 패키지 설치'
            }
        } finally {
            if ($held) { $mutex.ReleaseMutex() }
            $mutex.Dispose()
        }
    }
    if (-not (Test-CompatiblePython $base)) { throw '기본 Python 연결을 확인하지 못했습니다.' }
    $report = Get-EnvironmentReport $envPath $base 'main'
    if (-not $report.OK) { throw $report.Reason }
    if (-not (Get-WebViewVersion)) { throw 'WebView2 Runtime을 확인하지 못했습니다.' }
    $pythonw = Join-Path $envPath 'Scripts\pythonw.exe'
    $entry = Join-Path $script:SetupRoot 'START_MOSES.pyw'
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf) -or -not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw 'MOSES 실행 파일을 찾지 못했습니다.' }
    Show-BacktestOverrideNotice $script:SetupRoot
    if ($CheckOnly -or $PrepareOnly) { Write-SetupLog 'MOSES 실행 전 검사 통과. 프로그램은 실행하지 않았습니다.'; exit 0 }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $pythonw
    $start.Arguments = ConvertTo-NativeArgument $entry
    $start.WorkingDirectory = $script:SetupRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($start)
    if (-not $process) { throw 'MOSES 프로세스를 시작하지 못했습니다.' }
    $process.Dispose()
    Write-SetupLog 'MOSES 전용 창 시작을 요청했습니다.'
    exit 0
} catch {
    Write-SetupLog ('MOSES 실행 차단: ' + $_.Exception.Message + ' 통합설치/logs/launch.log를 확인하세요.')
    if (-not $CheckOnly -and -not $NoPrompt) { Read-Host '안내를 확인했으면 Enter를 눌러 마치세요' | Out-Null }
    exit 1
}
