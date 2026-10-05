# Windows-native smoke checks. Does NOT start any trading terminal or submit orders.
param([string]$BuildConfig='')
$ErrorActionPreference='Stop'
if(-not [Environment]::Is64BitOperatingSystem){throw 'Windows x64 is required.'}
if($PSVersionTable.PSVersion.Major -lt 5){throw 'Windows PowerShell 5.1 or newer is required.'}
$root=Split-Path -Parent $PSScriptRoot
$work=Join-Path ([IO.Path]::GetTempPath()) ('DS5-verify-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$utf8=New-Object Text.UTF8Encoding($false)
function Run-Worker([string]$exe,[string[]]$workerArgs) {
    $psi=New-Object Diagnostics.ProcessStartInfo
    $psi.FileName=$exe;$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
    $psi.Arguments=(($workerArgs|ForEach-Object { '"'+([regex]::Replace(([regex]::Replace($_,'(\\*)"','$1$1\"')),'(\\+)$','$1$1'))+'"' }) -join ' ')
    $p=[Diagnostics.Process]::Start($psi)
    try {$p.WaitForExit();if($p.ExitCode -ne 0){throw ('Worker failed: '+$p.ExitCode)}} finally {$p.Dispose()}
}
try {
    $deploy=Join-Path $PSScriptRoot 'releasekit\deploy'
    $common=[IO.File]::ReadAllText((Join-Path $deploy 'ui_common.ps1'),$utf8)
    foreach($name in @('ui_builder.ps1','ui_install.ps1')) {
        $source=[IO.File]::ReadAllText((Join-Path $deploy $name),$utf8).Replace('# DS5_COMMON',$common)
        $tokens=$null;$parseErrors=$null
        [void][Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$parseErrors)
        if($parseErrors.Count -gt 0){throw ($name+': '+($parseErrors.Message -join '; '))}
        Write-Host ('PASS PowerShell parser: '+$name)
    }
    $builder=Join-Path $root '배포만들기.exe'
    $stub=Join-Path $PSScriptRoot 'bin\DivineShield.Setup.stub.exe'
    foreach($path in @($builder,$stub)){
        $b=[IO.File]::ReadAllBytes($path)
        if($b.Length -lt 1024 -or $b[0] -ne 77 -or $b[1] -ne 90){throw ('Invalid Windows executable: '+$path)}
        Write-Host ('PASS Windows PE header: '+[IO.Path]::GetFileName($path))
    }
    $result=Join-Path $work 'defaults.json'
    Run-Worker $builder @('--defaults','--project',$root,'--result',$result)
    $settings=[IO.File]::ReadAllText($result,$utf8)|ConvertFrom-Json
    if($settings.project_root -ne $root){throw 'Project discovery mismatch.'}
    Write-Host 'PASS native builder launch / MT5 discovery'
    Write-Host ('MetaEditor: '+$settings.metaeditor)
    Write-Host ('MQL5: '+$settings.mql5_root)
    if($BuildConfig){
        $result=Join-Path $work 'build.json'
        try {Run-Worker $builder @('--build-config',$BuildConfig,'--result',$result,'--progress',(Join-Path $work 'build.log'))}
        catch {if(Test-Path -LiteralPath $result){Get-Content -LiteralPath $result -Raw -Encoding UTF8|Write-Host};throw}
        $built=[IO.File]::ReadAllText($result,$utf8)|ConvertFrom-Json
        if(-not $built.success){throw $built.error}
        Run-Worker $built.installer @('--verify-only','--result',(Join-Path $work 'package-check.json'))
        Write-Host 'PASS real compiler build / issued native installer self-verification'
        Write-Host $built.installer
    }
    Write-Host 'PASS requested smoke checks. GUI interaction, installation, expiry in MT5, and demo operation are separate checks.'
} finally {Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue}
