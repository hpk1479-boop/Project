param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
try {
    $taskTemp = Join-Path $PSScriptRoot '.buildtmp'
    New-Item -ItemType Directory -Path $taskTemp -Force | Out-Null
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    $env:PYTHONPATH = $PSScriptRoot
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONUTF8 = '1'
    # The UI uses base Python; downloads begin only after the build button.
    $taskCandidates = @()
    $taskPyLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($taskPyLauncher) {
        $taskCandidates += [pscustomobject]@{Exe=$taskPyLauncher.Source;Args=@('-3.13')}
        $taskCandidates += [pscustomobject]@{Exe=$taskPyLauncher.Source;Args=@('-3.12')}
    }
    $taskDefault = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($taskDefault) { $taskCandidates += [pscustomobject]@{Exe=$taskDefault.Source;Args=@()} }
    $taskPython = $null
    foreach ($taskCandidate in $taskCandidates) {
        $taskArguments = @($taskCandidate.Args) + @('-B','-m','releasekit.check_launcher')
        $taskSavedPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $taskOutput = @(& $taskCandidate.Exe @taskArguments 2>&1)
        } catch { continue } finally { $ErrorActionPreference = $taskSavedPreference }
        if ($LASTEXITCODE -ne 0) { continue }
        try {
            $taskState = $taskOutput[-1].ToString() | ConvertFrom-Json
            if ($taskState.ready -and (Test-Path -LiteralPath $taskState.python -PathType Leaf)) {
                $taskPython = $taskState.python
                break
            }
        } catch { continue }
    }
    if (-not $taskPython) { throw '배포 설정창에 필요한 Python 3.12 또는 3.13을 확인하지 못했습니다.' }
    if ($CheckOnly) { Write-Output 'CHECK_ONLY_OK'; exit 0 }
    $taskToken = [Guid]::NewGuid().ToString('N')
    $env:MOSES_RELEASE_LAUNCH_TOKEN = $taskToken
    $taskReady = Join-Path $taskTemp ('launcher_' + $taskToken + '.ready.json')
    $taskStdout = Join-Path $taskTemp ('launcher_' + $taskToken + '.stdout.log')
    $taskStderr = Join-Path $taskTemp ('launcher_' + $taskToken + '.stderr.log')
    # Use the verified interpreter with a hidden console.
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList '-B','-m','releasekit.ui' `
        -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $taskDeadline) {
        if (Test-Path -LiteralPath $taskReady) {
            $taskState = Get-Content -LiteralPath $taskReady -Raw -Encoding UTF8 | ConvertFrom-Json
            # The venv executable can relay to a different Python process ID.
            # This fresh nonce file is written only after that GUI is visible.
            if ($taskState.ready -and [int]$taskState.pid -gt 0) {
                Write-Output '모세의지팡이 배포 설정창이 실행되었습니다.'
                exit 0
            }
        }
        $taskProcess.Refresh()
        if ($taskProcess.HasExited) {
            $taskDetail = Get-Content -LiteralPath $taskStderr -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            throw ('설정창 실행에 실패했습니다. ' + $taskDetail)
        }
        Start-Sleep -Milliseconds 100
    }
    throw '설정창 준비를 확인하지 못했습니다. 통합설치/.buildtmp의 실행 로그를 확인하세요.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    if (-not $CheckOnly) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '모세의지팡이 배포') | Out-Null
    }
    exit 1
}
