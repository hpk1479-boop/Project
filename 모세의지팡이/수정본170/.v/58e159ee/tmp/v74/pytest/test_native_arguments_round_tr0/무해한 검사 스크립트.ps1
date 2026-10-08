$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\통합설치\common.ps1'
$r = Invoke-Process -Executable 'C:\Users\hpk14\AppData\Local\Programs\Python\Python313\python.exe' -Arguments @('-X','utf8','-c','import json,sys;print(json.dumps(sys.argv[1:],ensure_ascii=False))','','한글 공백','a&b','작은''따옴표','큰"따옴표','끝\','공백 경로\','\\서버\공유 폴더\') -WorkingDirectory 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v74\pytest\test_native_arguments_round_tr0'
$result = $r
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
