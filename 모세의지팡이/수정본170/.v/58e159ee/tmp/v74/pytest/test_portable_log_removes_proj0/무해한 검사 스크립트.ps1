$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\통합설치\common.ps1'
$result = ConvertTo-PortableText -Text 'project=C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_portable_log_removes_proj0\사용자 프로젝트\Part2\.venv-generic; host=C:\Users\hpk14\호스트 개인 경로\python.exe' -ProjectRoot 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_portable_log_removes_proj0\사용자 프로젝트'
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
