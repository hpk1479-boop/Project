$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
function global:Start-Process { throw 'unexpected process launch' }
function global:Invoke-WebRequest { throw 'unexpected network request' }
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_dot_source_has_no_install0\옮긴 프로젝트 & 작은''따옴표\통합설치\common.ps1'
$result = $true
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
