param([string]$EnginePath,[string]$ContextPath,[string]$SessionDir)
# DS5_COMMON
$form=New-Object System.Windows.Forms.Form
$form.Text='하나님의방패 배포 5.0 - 개발자용'
$form.Font=New-Object System.Drawing.Font('Malgun Gothic',10)
$form.AutoScaleMode=[System.Windows.Forms.AutoScaleMode]::Font
$form.ClientSize=New-Object System.Drawing.Size(1010,831)
$form.FormBorderStyle='FixedDialog';$form.MaximizeBox=$false;$form.StartPosition='CenterScreen'
[void](Label '하나님의방패 자동설치 배포 만들기' 24 16 930 34)
[void](Label '선택한 만료일을 빌드 복사본에 넣고 새로 컴파일합니다. 원본 소스는 변경하지 않습니다.' 24 53 955 28)
[void](Label '프로젝트' 24 93 130)
$root=TextBox $ctx.project_root 155 90 820;$root.ReadOnly=$true
[void](Label '배포 버전' 24 136 130)
$version=TextBox $ctx.version 155 132 180
[void](Label '사용 만료일' 365 136 115)
$date=New-Object System.Windows.Forms.DateTimePicker
$date.Format='Custom';$date.CustomFormat='yyyy-MM-dd';$date.SetBounds(480,132,165,28)
$date.MinDate=(Get-Date).Date;$date.MaxDate=[datetime]::new(2999,12,31)
$date.Value=[datetime]::ParseExact($ctx.expiry_date,'yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture)
$form.Controls.Add($date)
[void](Label '선택한 날짜의 23:59:59까지 (실행 PC 현지 날짜)' 480 164 500 24)
$i=0
foreach($m in @(1,3,6,12)){
    $b=Button ($m.ToString()+'개월') (656+80*$i) 130 76 30;$b.Tag=$m
    $b.Add_Click({param($sender,$eventArgs) $date.Value=(Get-Date).Date.AddMonths([int]$sender.Tag)})
    $i++
}
[void](Label '설치 유효시간' 24 201 130)
$minutes=New-Object System.Windows.Forms.NumericUpDown
$minutes.Minimum=1;$minutes.Maximum=10080;$minutes.Value=$ctx.install_minutes;$minutes.SetBounds(155,197,120,28)
$form.Controls.Add($minutes)
[void](Label '분 / 컴파일 완료 시점부터 계산. 사용 만료일과 별개입니다.' 285 201 680)
$trading=CheckBox '매매용 EA' 24 245 215 $ctx.components.trading
$copier=CheckBox 'MT5 마스터·슬레이브·알림' 250 245 420 $ctx.components.copier
$ninja=CheckBox 'NinjaTrader 슬레이브 (선택)' 665 245 330 $ctx.components.ninja
[void](Label 'MetaEditor64.exe' 24 292 155)
$editor=TextBox $ctx.metaeditor 182 287 680
$editorBrowse=Button '파일 선택' 875 286 100
$editorBrowse.Add_Click({
    $d=New-Object System.Windows.Forms.OpenFileDialog
    $d.Filter='MetaEditor64|metaeditor64.exe';$d.Title='빌드할 MT5의 MetaEditor64.exe 선택'
    if([IO.File]::Exists($editor.Text)){$d.FileName=$editor.Text}
    try{if($d.ShowDialog($form) -eq 'OK'){
        $editor.Text=$d.FileName
        $portable=Join-Path (Split-Path $d.FileName -Parent) 'MQL5'
        if([IO.File]::Exists((Join-Path $portable 'Include\Trade\Trade.mqh'))){$mql.Text=$portable}
    }}finally{$d.Dispose()}
})
[void](Label 'MQL5 폴더' 24 335 155)
$mql=TextBox $ctx.mql5_root 182 330 680
$mqlBrowse=Button '폴더 선택' 875 329 100
$mqlBrowse.Add_Click({$p=Pick-Folder 'Include\Trade\Trade.mqh가 있는 MQL5 폴더 (Include 자체가 아님)' $mql.Text;if($p){$mql.Text=$p}})
[void](Label 'MT5에서 파일 → 데이터 폴더 열기 → MQL5를 선택합니다. Include 폴더 자체를 선택하지 마세요.' 182 365 800 27)
[void](Label 'NinjaTrader bin' 24 407 155)
$ninjaBin=TextBox $ctx.ninja_bin 182 401 680
$ninjaBrowse=Button '폴더 선택' 875 400 100
$ninjaBrowse.Add_Click({$p=Pick-Folder 'NinjaTrader.Core.dll 및 NinjaTrader.Gui.dll이 있는 설치 bin 폴더' $ninjaBin.Text;if($p){$ninjaBin.Text=$p}})
[void](Label 'Roslyn csc.exe' 24 452 155)
$ninjaCompiler=TextBox $ctx.ninja_compiler 182 447 680
$compilerBrowse=Button '파일 선택' 875 446 100
$compilerBrowse.Add_Click({
    $d=New-Object System.Windows.Forms.OpenFileDialog
    $d.Filter='Roslyn C# compiler|csc.exe';$d.Title='Visual Studio / Build Tools의 Roslyn csc.exe 선택'
    if([IO.File]::Exists($ninjaCompiler.Text)){$d.FileName=$ninjaCompiler.Text}
    try{if($d.ShowDialog($form) -eq 'OK'){$ninjaCompiler.Text=$d.FileName}}finally{$d.Dispose()}
})
[void](Label 'Ninja 포함 시에만 필요합니다. 구형 Windows Framework csc.exe가 아닌 Roslyn 컴파일러를 사용하세요.' 182 485 800 35)
$build=Button '자동설치 EXE 만들기' 24 532 235 42
$open=Button '생성 폴더 열기' 275 532 180 42;$open.Enabled=$false
$open.Add_Click({if([IO.Directory]::Exists($script:lastOutput)){Start-Process -FilePath 'explorer.exe' -ArgumentList (Quote-Arg $script:lastOutput)}})
[void](Label '고객에게는 생성된 「하나님의방패 통합설치.exe」만 전달하세요.' 480 540 490 30)
$log=New-Object System.Windows.Forms.TextBox
$log.Multiline=$true;$log.ReadOnly=$true;$log.ScrollBars='Vertical';$log.SetBounds(24,591,951,190)
$log.Text='실제 MT5 컴파일에 성공하고 패키지 검증이 끝난 경우에만 고객용 EXE를 생성합니다.'
$form.Controls.Add($log)
[void](Label '발급키는 최초 빌드 시 이 Windows 사용자에 맞게 생성됩니다. .keys / 소스 / 배포 도구는 고객용이 아닙니다.' 24 790 955 35)
$script:busyControls=@($build,$version,$date,$minutes,$trading,$copier,$ninja,$editor,$editorBrowse,$mql,$mqlBrowse,$ninjaBin,$ninjaBrowse,$ninjaCompiler,$compilerBrowse,$open)
$script:AfterJob={
    $ninjaBin.Enabled=$ninja.Checked;$ninjaBrowse.Enabled=$ninja.Checked
    $ninjaCompiler.Enabled=$ninja.Checked;$compilerBrowse.Enabled=$ninja.Checked
    $editor.Enabled=($trading.Checked -or $copier.Checked);$editorBrowse.Enabled=$editor.Enabled
    $mql.Enabled=$editor.Enabled;$mqlBrowse.Enabled=$editor.Enabled
    $open.Enabled=[IO.Directory]::Exists($script:lastOutput)
}
$ninja.Add_CheckedChanged({& $script:AfterJob})
$trading.Add_CheckedChanged({& $script:AfterJob});$copier.Add_CheckedChanged({& $script:AfterJob})
$build.Add_Click({
    if(-not ($trading.Checked -or $copier.Checked -or $ninja.Checked)){Notify '구성요소를 하나 이상 선택하세요.' $true;return}
    $settings=@{
        project_root=$root.Text;version=$version.Text.Trim();expiry_date=$date.Value.ToString('yyyy-MM-dd')
        install_minutes=[int]$minutes.Value;metaeditor=$editor.Text.Trim();mql5_root=$mql.Text.Trim()
        components=@{trading=[bool]$trading.Checked;copier=[bool]$copier.Checked;ninja=[bool]$ninja.Checked}
        ninja_bin=$ninjaBin.Text.Trim();ninja_compiler=$ninjaCompiler.Text.Trim()
    }
    $path=Join-Path $SessionDir ('build_'+[guid]::NewGuid().ToString('N')+'.json')
    [IO.File]::WriteAllText($path,($settings|ConvertTo-Json -Depth 8),$script:utf8)
    Start-EngineWork 'build' @('--build-config',$path)
})
$script:JobComplete={param($r,$mode)
    if($mode -ne 'build'){return}
    $script:lastOutput=Split-Path -Parent $r.installer
    $log.AppendText("`r`nSHA-256: "+$r.installer_sha256)
    $log.AppendText("`r`n개발자용 소스·실행 파일: "+$r.artifacts_dir)
    Notify ("고객용 자동설치 EXE를 생성했습니다.`r`n`r`n"+$r.installer+"`r`n`r`n사용 만료일: "+$r.expiry_date+"`r`n설치 가능 기한: "+$r.install_expires_at+"`r`n원본 변경 없음: "+$r.sources_unchanged)
}
& $script:AfterJob
Show-MainForm
