$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
& 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_relocated_launcher_works_0\다시 이동한 도구 & 한글 공백''\통합설치\launch.ps1' -CheckOnly
exit $LASTEXITCODE
