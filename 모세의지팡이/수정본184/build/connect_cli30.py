from pathlib import Path
R=Path(__file__).resolve().parents[1];p=R/'Part2/event_backtest/__main__.py';s=p.read_text('utf8')
s=s.replace('import argparse,json','import argparse,json,uuid,re,traceback')
s=s.replace("    p.add_argument('--mode',", "    p.add_argument('--result-mode',choices=('ALERT_ONLY','VIRTUAL_ENTRY'))\n    p.add_argument('--spread-points',type=float)\n    p.add_argument('--build-only',action='store_true')\n    p.add_argument('--session-id')\n    p.add_argument('--mode',",1)
s=s.replace("    def emit(kind,data):print(json.dumps({'event':kind,**data},ensure_ascii=False),flush=True)",'''    s['build_only']=bool(a.build_only or s.get('build_only'))
    if a.result_mode:s['result_mode']=a.result_mode
    if a.spread_points is not None:s['spread_points']={s['symbol']:a.spread_points}
    run_id=a.session_id or uuid.uuid4().hex
    if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')
    s['_run_id']=run_id
    journal=None
    if a.action=='run':
        folder=warehouse_path(cfg['warehouse'],'runs/'+run_id);folder.mkdir(parents=True,exist_ok=True)
        journal=folder/'progress.jsonl'
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,Path):value=str(value)
        if isinstance(value,str):
            value=value.replace(str(ROOT),'<project>').replace(cfg['warehouse'],'<warehouse>')
            return re.sub(r'[A-Za-z]:[\\\\/][^\\s"\\n]+',lambda m:Path(m[0]).name,value)
        return value
    def emit(kind,data):
        line=json.dumps(clean({'event':kind,**data}),ensure_ascii=False)
        if journal:
            with journal.open('a',encoding='utf-8') as log:log.write(line+'\\n')
        print(line,flush=True)''')
s=s.replace("        raise\n    if a.output", "        emit('ERROR',{'message':str(exc),'exception':traceback.format_exc(),'completed_pieces_preserved':True});return 1\n    if a.output",1)
p.write_text(s,encoding='utf8')
# Add result mode and per-symbol spread to the persisted GUI model.
p=R/'Part2/event_backtest/ui_model.py';s=p.read_text('utf8');s=s.replace("    data=dict(common)","    data=dict(common)\n    data.update(result_mode=ui.get('result_mode','ALERT_ONLY'),spread_points=dict(ui.get('spread_points',{})))",1);p.write_text(s,encoding='utf8')
