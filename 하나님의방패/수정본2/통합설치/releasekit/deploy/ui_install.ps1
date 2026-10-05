param([string]$EnginePath,[string]$ContextPath,[string]$SessionDir)
# DS5_COMMON
$form=New-Object System.Windows.Forms.Form
$form.Text='하나님의방패 통합설치 - MT5 역할 선택'
$form.Font=New-Object System.Drawing.Font('Malgun Gothic',10)
$form.AutoScaleMode=[System.Windows.Forms.AutoScaleMode]::Font
$form.ClientSize=New-Object System.Drawing.Size(1150,845)
$form.FormBorderStyle='FixedDialog';$form.MaximizeBox=$false;$form.StartPosition='CenterScreen'
[void](Label ('하나님의방패 '+$ctx.policy.version+' | 사용 만료일: '+$ctx.policy.expiry_date+' (당일 포함)') 22 16 1080 32)
$deadline=[DateTimeOffset]::FromUnixTimeSeconds([long]$ctx.policy.install_expires_at).LocalDateTime.ToString('yyyy-MM-dd HH:mm:ss')
[void](Label ('이 EXE의 설치 가능 기한: '+$deadline+' / PC 현지 시각. 설치는 매매를 시작하지 않습니다.') 22 54 1100 27)
[void](Label 'MT5마다 역할을 선택하세요. 계좌 표시는 저장된 힌트일 뿐이며, 실제 로그인 계좌는 MT5에서 확인해야 합니다.' 22 91 1110 27)
$grid=New-Object System.Windows.Forms.DataGridView
$grid.SetBounds(22,130,1102,280);$grid.AllowUserToAddRows=$false;$grid.AllowUserToDeleteRows=$false
$grid.AllowUserToResizeRows=$false;$grid.RowHeadersVisible=$false;$grid.AutoGenerateColumns=$false
$grid.SelectionMode='FullRowSelect';$grid.MultiSelect=$false;$grid.RowTemplate.Height=33
$roleCol=New-Object System.Windows.Forms.DataGridViewComboBoxColumn
$roleCol.Name='Role';$roleCol.HeaderText='설치 역할';$roleCol.Width=210;$roleCol.FlatStyle='Flat'
$script:roleCodes=@{'설치 안 함'='ignore'}
[void]$roleCol.Items.Add('설치 안 함')
if($ctx.policy.components.copier){
    [void]$roleCol.Items.Add('마스터');[void]$roleCol.Items.Add('슬레이브 (알림 포함)')
    $script:roleCodes['마스터']='master';$script:roleCodes['슬레이브 (알림 포함)']='slave'
}
if($ctx.policy.components.trading){[void]$roleCol.Items.Add('Part1 매매용 EA');$script:roleCodes['Part1 매매용 EA']='trading'}
if($ctx.policy.components.trading -and $ctx.policy.components.copier){[void]$roleCol.Items.Add('마스터 + Part1 매매용');$script:roleCodes['마스터 + Part1 매매용']='master_trading'}
[void]$grid.Columns.Add($roleCol)
foreach($spec in @(@('Terminal','터미널',165),@('Account','저장 계좌 / 서버 (힌트)',225),@('Path','MT5 데이터 폴더',475))){
    $c=New-Object System.Windows.Forms.DataGridViewTextBoxColumn
    $c.Name=$spec[0];$c.HeaderText=$spec[1];$c.Width=$spec[2];$c.ReadOnly=$true
    [void]$grid.Columns.Add($c)
}
$grid.Add_CurrentCellDirtyStateChanged({if($grid.IsCurrentCellDirty){[void]$grid.CommitEdit([System.Windows.Forms.DataGridViewDataErrorContexts]::Commit)}})
$grid.Add_DataError({param($sender,$eventArgs) $eventArgs.ThrowException=$false;Notify '역할을 목록에서 다시 선택하세요.' $true})
$form.Controls.Add($grid)
function Add-TerminalRow($t) {
    foreach($row in $grid.Rows){if([string]::Equals([string]$row.Tag.data_dir,[string]$t.data_dir,[StringComparison]::OrdinalIgnoreCase)){return}}
    $name=[string]$t.label;if($t.portable){$name+=' (포터블)'}
    $idx=$grid.Rows.Add('설치 안 함',$name,($t.login_hint+' / '+$t.server_hint),$t.data_dir)
    $grid.Rows[$idx].Tag=$t
}
foreach($t in $ctx.terminals){Add-TerminalRow $t}
$refresh=Button '터미널 다시 검색' 22 422 175
$manual=Button '데이터 폴더 추가' 209 422 175
$hint=Button '선택한 행의 계좌 힌트 가져오기' 397 422 270
[void](Label '대상 MT5를 모두 종료한 뒤 설치하세요. 강제 종료하지 않습니다.' 682 428 445 44)
$refresh.Add_Click({Start-EngineWork 'scan' @('--scan')})
$manual.Add_Click({$p=Pick-Folder 'MT5 > 파일 > 데이터 폴더 열기로 확인한 폴더를 선택하세요.' '';if($p){Start-EngineWork 'terminal' @('--terminal',$p)}})
[void](Label '연결 스트림' 22 478 108)
$stream=TextBox 'OZ_MAIN' 130 474 205
[void](Label '마스터 계좌번호' 357 478 135)
$login=TextBox '' 494 474 188
[void](Label '마스터 서버명' 705 478 133)
$server=TextBox '' 838 474 286
[void](Label '슬레이브는 위 마스터 계좌·서버에 연결하는 프리셋을 받습니다. 현재 슬레이브 계좌번호를 넣는 칸이 아닙니다.' 22 512 1100 28)
$hint.Add_Click({
    if($grid.SelectedRows.Count -lt 1){Notify '마스터 계좌로 사용할 터미널 행을 먼저 선택하세요.';return}
    $t=$grid.SelectedRows[0].Tag
    $login.Text=[string]$t.login_hint;$server.Text=[string]$t.server_hint
    Notify '저장된 힌트를 가져왔습니다. MT5에서 실제 마스터 계좌번호와 서버명을 다시 확인하세요.'
})
$ninja=CheckBox 'NinjaTrader 슬레이브 설치' 22 550 260 $false
$ninja.Visible=[bool]$ctx.policy.components.ninja
$ninjaDir=TextBox (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8') 285 549 706
$ninjaDir.Visible=$ninja.Visible;$ninjaDir.Enabled=$false
$ninjaBrowse=Button '폴더 선택' 1002 547 122
$ninjaBrowse.Visible=$ninja.Visible;$ninjaBrowse.Enabled=$false
$ninjaBrowse.Add_Click({$p=Pick-Folder 'bin\Custom이 있는 NinjaTrader 사용자 데이터 폴더' $ninjaDir.Text;if($p){$ninjaDir.Text=$p}})
$ninja.Add_CheckedChanged({$ninjaDir.Enabled=$ninja.Checked;$ninjaBrowse.Enabled=$ninja.Checked})
$consent=CheckBox '역할·실제 계좌를 확인했습니다. 설치 후 차트 부착, 프리셋 불러오기와 매매/DLL 허용은 직접 확인합니다.' 22 593 1104 $false
$install=Button '선택한 역할로 자동설치' 22 633 245 42
[void](Label '설치 안 함으로 둔 터미널에는 파일을 설치하지 않습니다. 기존 다른 EA와 계좌 설정은 유지합니다.' 286 641 835 40)
$log=New-Object System.Windows.Forms.TextBox
$log.Multiline=$true;$log.ReadOnly=$true;$log.ScrollBars='Vertical';$log.SetBounds(22,692,1102,131)
$log.Text='검증된 설치 파일입니다. 모든 역할의 기본값은 설치 안 함입니다.'
if($ctx.warnings){$log.AppendText("`r`n"+($ctx.warnings -join "`r`n"))}
$form.Controls.Add($log)
$script:busyControls=@($grid,$refresh,$manual,$hint,$stream,$login,$server,$ninja,$ninjaDir,$ninjaBrowse,$consent,$install)
$script:AfterJob={
    $ninjaDir.Enabled=$ninja.Checked;$ninjaBrowse.Enabled=$ninja.Checked
    $stream.Enabled=[bool]$ctx.policy.components.copier;$login.Enabled=$stream.Enabled;$server.Enabled=$stream.Enabled;$hint.Enabled=$stream.Enabled
}
$install.Add_Click({
    [void]$grid.EndEdit()
    $targets=@()
    foreach($row in $grid.Rows){$role=$script:roleCodes[[string]$row.Cells['Role'].Value];if(-not $role){$role='ignore'};$targets+=@{data_dir=[string]$row.Tag.data_dir;role=$role}}
    if(-not $consent.Checked){Notify '설치 역할·계좌 확인 항목에 동의해 주세요.' $true;return}
    $plan=@{targets=$targets;stream_id=$stream.Text.Trim();source_login=$login.Text.Trim();source_server=$server.Text.Trim();install_ninja=[bool]$ninja.Checked;ninja_dir=$ninjaDir.Text.Trim();accepted=[bool]$consent.Checked}
    $path=Join-Path $SessionDir ('plan_'+[guid]::NewGuid().ToString('N')+'.json')
    [IO.File]::WriteAllText($path,($plan|ConvertTo-Json -Depth 8),$script:utf8)
    Start-EngineWork 'install' @('--install-plan',$path)
})
$script:JobComplete={param($r,$mode)
    if($mode -eq 'scan'){
        foreach($t in $r.terminals){Add-TerminalRow $t}
        $log.Text='터미널 검색 완료. 기존 선택은 유지하고 새 폴더만 추가했습니다.'
        if($r.warnings){$log.AppendText("`r`n"+($r.warnings -join "`r`n"))}
    } elseif($mode -eq 'terminal'){
        Add-TerminalRow $r.terminal;$log.Text='데이터 폴더를 확인해 추가했습니다. 설치 역할을 선택하세요.'
    } elseif($mode -eq 'install'){
        $m="선택한 역할의 파일 설치를 완료했습니다.`r`n`r`n"+($r.installed -join "`r`n")
        $m+="`r`n`r`nMT5를 열고 Experts\DivineShield의 EA를 역할에 맞는 차트에 직접 부착하세요."
        $m+="`r`nMQL5\Presets\DivineShield의 새 .set을 불러온 뒤 계좌·심볼·수량·DLL 허용을 확인하세요."
        $m+="`r`n마스터 + 매매용은 같은 터미널의 서로 다른 차트에 부착합니다."
        if($r.backups){$log.AppendText("`r`n백업:`r`n"+($r.backups -join "`r`n"))}
        if($r.warnings){$log.AppendText("`r`n주의:`r`n"+($r.warnings -join "`r`n"));$m+="`r`n`r`n주의: "+($r.warnings -join "`r`n")}
        $log.AppendText("`r`n"+$m);Notify $m
    }
}
& $script:AfterJob
Show-MainForm
