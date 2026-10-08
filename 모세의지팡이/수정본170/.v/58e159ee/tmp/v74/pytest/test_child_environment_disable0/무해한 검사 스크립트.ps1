$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\통합설치\common.ps1'
$result = Invoke-Process -Executable 'C:\Users\hpk14\AppData\Local\Programs\Python\Python313\python.exe' -Arguments @('-c','import json,os;print(json.dumps({k:os.environ.get(k) for k in [''PYLAUNCHER_ALLOW_INSTALL'', ''PYTHON_MANAGER_AUTOMATIC_INSTALL'', ''PYTHONUTF8'', ''PYTHONIOENCODING'', ''PYTHONDONTWRITEBYTECODE'']}))') -WorkingDirectory 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_child_environment_disable0'
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
