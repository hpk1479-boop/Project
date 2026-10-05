# Maintainer-only rebuild. End users do NOT need Go, Python, or a compiler for the installer.
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$kit=Join-Path $PSScriptRoot 'releasekit'
$sharedTools=Join-Path (Split-Path -Parent $root) '개발도구'
$go=Get-Command go -ErrorAction SilentlyContinue
$goExe=if($go){$go.Source}else{Join-Path $sharedTools 'go\bin\go.exe'}
if(-not (Test-Path -LiteralPath $goExe -PathType Leaf)){throw 'Go compiler not found.'}
$oldOS=$env:GOOS;$oldArch=$env:GOARCH;$oldCGO=$env:CGO_ENABLED
$oldCache=$env:GOCACHE;$oldModCache=$env:GOMODCACHE
Push-Location $kit
try {
    $env:GOCACHE=Join-Path $sharedTools 'go-cache'
    $env:GOMODCACHE=Join-Path $sharedTools 'go-mod-cache'
    & $goExe run ./tools/manifest
    if($LASTEXITCODE -ne 0){throw 'Manifest generation failed.'}
    $env:GOOS='windows';$env:GOARCH='amd64';$env:CGO_ENABLED='0'
    & $goExe vet ./...
    if($LASTEXITCODE -ne 0){throw 'Windows go vet failed.'}
    $stub=Join-Path $PSScriptRoot 'bin\DivineShield.Setup.stub.exe'
    $builder=Join-Path $root '배포만들기.exe'
    & $goExe build -trimpath '-ldflags=-s -w -H windowsgui' -o $stub ./cmd/setup
    if($LASTEXITCODE -ne 0){throw 'Setup engine build failed.'}
    & $goExe build -trimpath '-ldflags=-s -w -H windowsgui' -o $builder ./cmd/builder
    if($LASTEXITCODE -ne 0){throw 'Release builder build failed.'}
    Get-FileHash -Algorithm SHA256 -LiteralPath $stub,$builder | Format-List
    Write-Host 'Rebuild complete. No customer package was issued by this script.'
} finally {
    $env:GOOS=$oldOS;$env:GOARCH=$oldArch;$env:CGO_ENABLED=$oldCGO
    $env:GOCACHE=$oldCache;$env:GOMODCACHE=$oldModCache
    Pop-Location
}
