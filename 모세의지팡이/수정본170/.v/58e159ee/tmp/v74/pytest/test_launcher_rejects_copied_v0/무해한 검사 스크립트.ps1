$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
& 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_launcher_rejects_copied_v0\옮긴 프로젝트 & 작은''따옴표\통합설치\launch.ps1' -CheckOnly
exit $LASTEXITCODE
