$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_backtest_failure_restores0\옮긴 프로젝트 & 작은''따옴표\통합설치\common.ps1'

function Start-Process { throw 'unexpected external process' }
function Invoke-WebRequest { throw 'unexpected network request' }
function Invoke-RestMethod { throw 'unexpected network request' }

function Get-EnvironmentReport { return [pscustomobject]@{ OK = $false; Reason = 'fixture missing module' } }
function Invoke-Process {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    foreach ($kind in @('generic', 'history')) {
        $envPath = Join-Path $WorkingDirectory ('.venv-' + $kind)
        New-Item -ItemType Directory -Path $envPath -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $envPath 'partial.txt'), 'failed ' + $kind)
    }
    return [pscustomobject]@{ ExitCode = 9; StdOut = ''; StdErr = 'fixture bootstrap failure' }
}
$failed = $false
try { Install-BacktestEnv ('{"version": [3, 12, 10], "bits": 64, "executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "base_executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313", "base_prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313"}' | ConvertFrom-Json) 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_backtest_failure_restores0\옮긴 프로젝트 & 작은''따옴표' | Out-Null } catch { $failed = $true; $reason = $_.Exception.Message }
$result = @{ failed = $failed; reason = $reason }
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
