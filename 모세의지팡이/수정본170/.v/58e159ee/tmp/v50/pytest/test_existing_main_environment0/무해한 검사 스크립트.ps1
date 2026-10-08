$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\통합설치\common.ps1'
function Get-PythonInfo { return ('{"version": [3, 12, 10], "bits": 64, "executable": "C:\\Users\\hpk14\\Desktop\\모세의지팡이 프로젝트\\수정본170\\.v\\58e159ee\\tmp\\v50\\pytest\\test_existing_main_environment0\\env\\Scripts\\python.exe", "base_executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "prefix": "C:\\Users\\hpk14\\Desktop\\모세의지팡이 프로젝트\\수정본170\\.v\\58e159ee\\tmp\\v50\\pytest\\test_existing_main_environment0\\env", "base_prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313"}' | ConvertFrom-Json) }
function Invoke-Process {
        param($Executable, $Arguments, $WorkingDirectory, $TimeoutSeconds)
        $script:probe = $Arguments[-1]
        return [pscustomobject]@{ ExitCode=0; StdOut=''; StdErr='' }
    }
$report = Get-EnvironmentReport 'C:\Users\hpk14\Desktop\모세의지팡이 프로젝트\수정본170\.v\58e159ee\tmp\v50\pytest\test_existing_main_environment0\env' ('{"version": [3, 12, 10], "bits": 64, "executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "base_executable": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313", "base_prefix": "C:\\Users\\hpk14\\AppData\\Local\\Programs\\Python\\Python313"}' | ConvertFrom-Json) 'main'
$result = @{ probe=$script:probe; accepted=$report.OK }
Write-Output ('__INSTALLER_TEST_JSON__' + ($result | ConvertTo-Json -Depth 20 -Compress))
