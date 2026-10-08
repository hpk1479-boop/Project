$revisionRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Split-Path $revisionRoot -Parent
$warehouseRoot = Join-Path (Split-Path $workspaceRoot -Parent) ((Split-Path $workspaceRoot -Leaf) + '_warehouse')
$evidenceRoot = Join-Path $revisionRoot '검증결과\part2_connection'
function Read-SharedText([string] $path) {
    $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    $stream = [IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
    $reader = [IO.StreamReader]::new($stream,[Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}
$captureRows = @()
$recording = Join-Path $evidenceRoot 'year_recording_progress.jsonl'
if (Test-Path -LiteralPath $recording) {
    foreach ($line in (Read-SharedText $recording).Split("`n")) {
        try { $row = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
        if ($row.kind -eq 'CAPTURE_COMPLETE') { $captureRows += $row.start }
    }
}
$result = [ordered]@{time=(Get-Date -Format o);recorded=$captureRows}
$retry = Join-Path $evidenceRoot 'day_tick_retry.json'
if (Test-Path -LiteralPath $retry) {
    $run = (Read-SharedText $retry | ConvertFrom-Json).retry_run
    $progressPath = Join-Path $warehouseRoot "runs\$run\TIMER_tick\progress.json"
    if (Test-Path -LiteralPath $progressPath) {
        try { $result.day_tick = Read-SharedText $progressPath | ConvertFrom-Json } catch {}
    }
}
foreach ($label in @('year_parallel','quarter_sequential')) {
    $progressPath = Join-Path $evidenceRoot ($label+'_progress.json')
    if (Test-Path -LiteralPath $progressPath) {
        try { $result[$label] = Read-SharedText $progressPath | ConvertFrom-Json } catch {}
    }
    $result[$label+'_complete'] = Test-Path -LiteralPath (Join-Path $evidenceRoot ($label+'.json'))
}
$result.day_tick_complete = Test-Path -LiteralPath (Join-Path $evidenceRoot 'day_replay_comparison.json')
$result | ConvertTo-Json -Compress -Depth 6
