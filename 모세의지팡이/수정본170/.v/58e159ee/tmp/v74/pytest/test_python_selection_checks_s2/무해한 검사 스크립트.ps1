$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\통합설치\common.ps1'
$result = [bool](Test-CompatiblePython ('{"version": [3, 10, 11], "bits": 64, "executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "base_executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313", "base_prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313"}' | ConvertFrom-Json))
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
