"""One-day new-schema BAR/BAR/TIMER validation via existing MT5 automation.

No warehouse catalog mutation. Normal installed files and terminal are restored.
"""
from pathlib import Path
import hashlib,json,sys,time,re
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.recording import installed_build,deployment_targets,retain_capture,journal_positions,tick_evidence
from event_backtest.settings import milliseconds,file_hash
from generic_backtest import native_mt5 as native
from generic_backtest.history.selection import discover_terminals
import staff_schema
OUT=ROOT/'검증결과/schema_cleanup';WORK=OUT/'mt5_work';WORK.mkdir(parents=True,exist_ok=True)

def main():
    profiles=discover_terminals()['terminals'];assert len(profiles)==1,'MT5 profile selection required'
    profile=profiles[0];targets=deployment_targets(profile)
    before={p.name:file_hash(p) if p.exists() else None for p in targets}
    report=[]
    def emit(kind,payload):
        safe={k:v for k,v in payload.items() if k not in ('expert','executable','data_root','path')}
        print(kind,json.dumps(safe,ensure_ascii=False),flush=True)
    try:
        with installed_build(profile,WORK/'restore',emit,lambda:None,build_root=WORK/'builds') as ea_hash:
            for label,mode in [('day_bar_a',1),('day_bar_b',1),('day_timer',0)]:
                dest=OUT/'captures'/label
                if dest.exists():raise FileExistsError('validation capture already exists: '+label)
                log_positions=journal_positions(profile)
                launch=native.run_native_tester(profile,'XAUUSD+',milliseconds('2026-09-25')*10**6,milliseconds('2026-09-26')*10**6,
                    WORK/label,emit=emit,cancel=lambda:None,shutdown_terminal=True,start_timeout=180,capture_only=True,
                    tester_inputs={'InpMode':1,'InpWireVersion':2,'InpRecordingMode':mode,
                                   'InpNativeExport':'false','InpPipeRecording':'true','STAFF_TIMER_MS':1000})
                dest.parent.mkdir(parents=True,exist_ok=True)
                size=retain_capture(launch['export'],dest)
                ticks=tick_evidence(profile,log_positions,dest)
                item={k:v for k,v in launch.items() if k!='export'}
                item.update(label=label,mode='BAR' if mode else 'TIMER',path=dest.relative_to(ROOT).as_posix(),raw_bytes=size,
                            stored_bytes=sum(p.stat().st_size for p in dest.iterdir()),ea_build_hash=ea_hash,
                            schema_id=staff_schema.WIRE_SCHEMA_ID,ticks=ticks,
                            files={p.name:file_hash(p) for p in dest.iterdir() if p.is_file()})
                report.append(item);(OUT/'captures.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print('CAPTURE_COMPLETE',label,launch['records'],round(launch['elapsed_seconds'],2),size,flush=True)
    finally:
        after={p.name:file_hash(p) if p.exists() else None for p in targets}
        state={'files':{k:{'before':before[k],'after':after[k],'same':before[k]==after[k]} for k in before},
               'restored':before==after,'normal_terminal_running':bool(discover_terminals()['terminals'])}
        (OUT/'mt5_restoration.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
        # Native runtime launch/compile files may contain required local paths. They are
        # diagnostics only: sanitize retained text after the terminal no longer needs it.
        replacements={str(ROOT):'PROJECT',str(Path(profile['data_root'])):'MT5_DATA',str(Path(profile['executable']).parent):'MT5_INSTALL',
                      str(native.common_files_root()):'MT5_COMMON'}
        for p in WORK.rglob('*'):
            if p.suffix not in ('.log','.ini','.json'):continue
            raw=p.read_bytes()
            for encoding in (('utf-16',) if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else ('utf-8-sig',)):
                try:text=raw.decode(encoding);break
                except UnicodeError:continue
            else:continue
            for old,new in replacements.items():text=text.replace(old,new).replace(old.replace('\\','/'),new)
            p.write_text(text,encoding='utf-8')
        print('RESTORED',state['restored'],state['normal_terminal_running'],flush=True)
        assert state['restored'] and state['normal_terminal_running']

if __name__=='__main__':main()
