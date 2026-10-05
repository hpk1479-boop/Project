$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$script:utf8 = New-Object System.Text.UTF8Encoding($false)
$script:ctx = [IO.File]::ReadAllText($ContextPath, $script:utf8) | ConvertFrom-Json
$script:worker = $null
$script:jobMode = ''
$script:busyControls = @()
$script:lastOutput = ''
trap {
    [void][System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '하나님의방패', 'OK', 'Error')
    exit 1
}
function Label($text, $x, $y, $w, $h=26) {
    $c = New-Object System.Windows.Forms.Label
    $c.Text=$text; $c.SetBounds($x,$y,$w,$h); $form.Controls.Add($c)
    return $c
}
function TextBox($text, $x, $y, $w) {
    $c=New-Object System.Windows.Forms.TextBox
    $c.Text=[string]$text; $c.SetBounds($x,$y,$w,28); $form.Controls.Add($c)
    return $c
}
function Button($text, $x, $y, $w=112, $h=32) {
    $c=New-Object System.Windows.Forms.Button
    $c.Text=$text; $c.SetBounds($x,$y,$w,$h); $form.Controls.Add($c)
    return $c
}
function CheckBox($text, $x, $y, $w, $checked=$false) {
    $c=New-Object System.Windows.Forms.CheckBox
    $c.Text=$text; $c.Checked=$checked; $c.SetBounds($x,$y,$w,28); $form.Controls.Add($c)
    return $c
}
function Pick-Folder($description, $initial) {
    $d=New-Object System.Windows.Forms.FolderBrowserDialog
    $d.Description=$description; $d.ShowNewFolderButton=$false
    if ([IO.Directory]::Exists($initial)) {$d.SelectedPath=$initial}
    try { if ($d.ShowDialog($form) -eq 'OK') { return $d.SelectedPath } } finally {$d.Dispose()}
    return $null
}
function Quote-Arg([string]$a) {
    # Windows argv quoting: double slashes before quotes and the closing quote.
    $a=[regex]::Replace($a, '(\\*)"', '$1$1\"')
    $a=[regex]::Replace($a, '(\\+)$', '$1$1')
    return '"'+$a+'"'
}
function Notify([string]$message, [bool]$isError=$false) {
    $icon=[System.Windows.Forms.MessageBoxIcon]::Information
    if($isError){$icon=[System.Windows.Forms.MessageBoxIcon]::Error}
    [void][System.Windows.Forms.MessageBox]::Show($form,$message,'하나님의방패',[System.Windows.Forms.MessageBoxButtons]::OK,$icon)
}
function Start-EngineWork([string]$mode, [string[]]$arguments) {
    if($null -ne $script:worker){return}
    $script:jobMode=$mode
    $script:resultPath=Join-Path $SessionDir ('result_'+[guid]::NewGuid().ToString('N')+'.json')
    $script:progressPath=Join-Path $SessionDir ('progress_'+[guid]::NewGuid().ToString('N')+'.log')
    $all=@($arguments)+@('--result',$script:resultPath)
    if($mode -in @('build','install')){$all+=@('--progress',$script:progressPath)}
    $psi=New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName=$EnginePath
    $psi.Arguments=(($all | ForEach-Object {Quote-Arg $_}) -join ' ')
    $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true
    $psi.WorkingDirectory=Split-Path -Parent $EnginePath
    $script:worker=New-Object System.Diagnostics.Process
    $script:worker.StartInfo=$psi
    try {
        if(-not $script:worker.Start()){throw '작업 프로세스를 시작하지 못했습니다.'}
        foreach($c in $script:busyControls){$c.Enabled=$false}
        $log.Text='검증 중...'; $timer.Start()
    } catch {
        $script:worker.Dispose(); $script:worker=$null
        Notify $_.Exception.Message $true
    }
}
$timer=New-Object System.Windows.Forms.Timer
$timer.Interval=250
$timer.Add_Tick({
    if($null -eq $script:worker){$timer.Stop();return}
    if([IO.File]::Exists($script:progressPath)) {
        try {$log.Text=[IO.File]::ReadAllText($script:progressPath,$script:utf8);$log.SelectionStart=$log.TextLength;$log.ScrollToCaret()} catch {}
    }
    if(-not $script:worker.HasExited){return}
    $timer.Stop(); $code=$script:worker.ExitCode; $script:worker.Dispose();$script:worker=$null
    foreach($c in $script:busyControls){$c.Enabled=$true}
    try {
        if(-not [IO.File]::Exists($script:resultPath)){throw ('결과를 받지 못했습니다. 종료 코드: '+$code)}
        $r=[IO.File]::ReadAllText($script:resultPath,$script:utf8) | ConvertFrom-Json
        if($code -ne 0 -or -not $r.success) {
            $message=[string]$r.error
            if($r.rollback_attempted){$message+="`r`n원복 결과: "+$(if($r.rollback_ok){'복원 완료'}else{'일부 복원 실패 - 백업 확인 필요'})}
            if($r.backups){$message+="`r`n백업: "+($r.backups -join "`r`n")}
            if($r.warnings){$message+="`r`n"+($r.warnings -join "`r`n")}
            throw $message
        }
        & $script:JobComplete $r $script:jobMode
    } catch {
        $log.AppendText("`r`n오류: "+$_.Exception.Message)
        Notify $_.Exception.Message $true
    }
    if($script:AfterJob){& $script:AfterJob}
})
function Show-MainForm {
    # Retain a full scrollable canvas on lower-resolution and scaled desktops.
    $canvas=$form.ClientSize
    $form.AutoScroll=$true
    $form.AutoScrollMinSize=$canvas
    $form.FormBorderStyle='Sizable'; $form.MaximizeBox=$true
    $area=[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $form.ClientSize=New-Object System.Drawing.Size([Math]::Min($canvas.Width,$area.Width-60),[Math]::Min($canvas.Height,$area.Height-100))

    $form.Add_FormClosing({param($sender,$eventArgs)
        if($null -ne $script:worker -and -not $script:worker.HasExited){
            $eventArgs.Cancel=$true
            Notify '파일 적용/컴파일 작업 중에는 창을 닫을 수 없습니다. 작업 결과가 나온 뒤 닫으세요.'
        }
    })
    try {[void]$form.ShowDialog()} finally {$timer.Dispose();$form.Dispose()}
}
