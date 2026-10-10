"""User-requested stop at a committed capture boundary, then restore MT5."""
from pathlib import Path
import datetime as dt,json,os,shutil,subprocess,sys,time
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings,file_hash,relative_path
from event_backtest.recording import deployment_targets
from generic_backtest import native_mt5 as native
from generic_backtest.history.selection import _text
from generic_backtest.history.live_status import running_processes

def main():
    root=Path(settings()['warehouse']);work=root/'mt5_work';e=R/'검증결과/part2_connection'
    wanted='2026-01-01'
    expected=file_hash(R/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5')
    profiles=[]
    terminals=Path(os.environ['APPDATA'])/'MetaQuotes/Terminal'
    for folder in terminals.iterdir():
        source=folder/'MQL5/Experts/THE_STAFF_OF_MOSES.mq5'
        if source.is_file() and file_hash(source)==expected and (folder/'origin.txt').is_file():
            executable=Path(_text(folder/'origin.txt').strip())/'terminal64.exe'
            if executable.is_file():profiles.append({'data_root':str(folder),'executable':str(executable),'portable':False})
    if len(profiles)!=1:raise RuntimeError('Cannot uniquely identify the deployed MT5 installation for restoration')
    profile=profiles[0];targets=deployment_targets(profile)
    assert all((work/f'original_{i}').exists() for i in range(10))
    print('Waiting for committed capture',wanted,flush=True)
    while True:
        rows=[]
        for line in (e/'year_recording_progress.jsonl').read_text('utf-8').splitlines():
            try:row=json.loads(line)
            except json.JSONDecodeError:continue
            if row.get('kind')=='CAPTURE_COMPLETE':rows.append(row)
        if any(row.get('start')==wanted for row in rows):break
        time.sleep(.1)
    # CAPTURE_COMPLETE is emitted after files, SHA256 list, and catalog commit.
    # This old recorder predates cooperative stop; stop only its two known PIDs.
    command="$rows=Get-CimInstance Win32_Process; foreach($idToStop in @(18820,4208)){ $p=$rows|Where-Object ProcessId -eq $idToStop; if($p){if($p.Name -ne 'python.exe' -or $p.CommandLine -notmatch 'record_part2_year[.]py'){throw 'recorder identity changed'};Stop-Process -Id $idToStop -ErrorAction SilentlyContinue}}"
    subprocess.run(['powershell.exe','-NoProfile','-Command',command],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    # A boundary race may have launched the next tester. Close this installation
    # normally before restoring its original sources/EX5; never retain that piece.
    for process in running_processes():
        if os.path.normcase(process.get('executable',''))==os.path.normcase(profile['executable']):
            native._request_terminal_close(process['pid']);native._windows_process_wait(process['pid'],20)
    restored=[]
    for i,path in enumerate(targets):
        backup=work/f'original_{i}'
        if backup.exists():shutil.copy2(backup,path);restored.append({'file':path.name,'restored':True})
        else:path.unlink(missing_ok=True);restored.append({'file':path.name,'removed_new_install_file':True})
    reopened=native._restart_selected_terminal(profile)
    captures=[]
    for row in rows:
        c={k:v for k,v in row.items() if k not in ('at','kind','export')}
        if Path(c['path']).is_absolute():c['path']=relative_path(root,c['path'])
        captures.append(c)
    (e/'recorded_captures.json').write_text(json.dumps(captures,ensure_ascii=False,indent=2),encoding='utf-8')
    receipt={'user_requested_current_piece_only':True,'last_completed_start':wanted,'captured_pieces':len(captures),
        'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'normal_terminal_reopened':True,'restored_files':restored,
        'method':'Stopped the old recorder only after CAPTURE_COMPLETE catalog commit; restored installed files explicitly.'}
    (e/'recording_stopped_at_boundary.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print('COMPLETED CURRENT MONTH; ORIGINAL MT5 RESTORED',flush=True)

if __name__=='__main__':main()
