"""Single-parent catalog writes; worker results are separate append-only CSVs."""
from pathlib import Path
import csv
import json
import re
import duckdb
from .settings import file_hash,warehouse_path

FIELDS=('run_id','time_ms','strategy','profile','grade','symbol','tf','direction','trigger','message','recipient','signal_id')

class Warehouse:
    def __init__(self,root,*,results=False):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=duckdb.connect(str(self.root/('results.duckdb' if results else 'captures.duckdb')))
        self.db.execute("SET TimeZone='UTC'")
        self.db.execute('CREATE TABLE IF NOT EXISTS captures (capture_id VARCHAR PRIMARY KEY, symbol VARCHAR, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, unit VARCHAR, mode VARCHAR, ea_build_hash VARCHAR, schema_id UBIGINT, tick_mode VARCHAR, path VARCHAR, files JSON, size_bytes UBIGINT, recorded_at TIMESTAMPTZ, metadata JSON)')
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (run_id VARCHAR PRIMARY KEY, status VARCHAR, scenario JSON, scenario_hash VARCHAR, symbol VARCHAR, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ, mode VARCHAR, code_hash VARCHAR, config_hash VARCHAR, captures JSON, overlap_days INTEGER, cores INTEGER, elapsed_seconds DOUBLE, approximate BOOLEAN, warnings JSON, metadata JSON)')
        self.db.execute('CREATE TABLE IF NOT EXISTS alerts ('+','.join(f'"{f}" '+('BIGINT' if f=='time_ms' else 'VARCHAR') for f in FIELDS)+')')
        self.db.execute('CREATE TABLE IF NOT EXISTS timings (run_id VARCHAR, processor VARCHAR, bundles BIGINT, ms_per_bundle DOUBLE, max_memory_bytes UBIGINT)')
        if not results and (self.root/'event_backtest.duckdb').is_file() and not self.db.execute('SELECT count(*) FROM captures').fetchone()[0]:
            # Prior warehouse remains read-only; original gzip is never replaced.
            old=duckdb.connect(str(self.root/'event_backtest.duckdb'),read_only=True)
            rows=old.execute('SELECT capture_id,symbol,CAST(start_time AS VARCHAR),CAST(end_time AS VARCHAR),unit,mode,ea_build_hash,schema_id,tick_mode,path,files,size_bytes,CAST(recorded_at AS VARCHAR),metadata FROM captures').fetchall();old.close()
            if rows:self.db.executemany('INSERT OR IGNORE INTO captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    def close(self):self.db.close()
    def find_capture(self,key,verify=True):
        row=self.db.execute('SELECT path,metadata FROM captures WHERE capture_id=?',[key]).fetchone()
        if not row:return None
        data=json.loads(row[1]);root=warehouse_path(self.root,row[0])
        if not (root/'complete.txt').exists():return None
        if verify and any(not (root/name).is_file() or file_hash(root/name)!=sha for name,sha in data['files'].items()):return None
        return data
    def register(self,data):
        from .portable import validate
        validate(data)
        warehouse_path(self.root,data['path'])
        self.db.execute('INSERT OR REPLACE INTO captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
            data['capture_id'],data['symbol'],data['start'],data['end'],data['unit'],data['mode'],data['ea_build_hash'],data['schema_id'],
            data['tick_evidence']['actual'],data['path'],json.dumps(data['files']),data['stored_bytes'],data['recorded_at'],json.dumps(data,ensure_ascii=False)])
    def run(self,run_id,status,data):
        from .portable import validate
        validate(data)
        from .settings import digest
        s=data['scenario']
        self.db.execute('INSERT OR REPLACE INTO runs VALUES ('+','.join(['?']*17)+')',[
            run_id,status,json.dumps(s,ensure_ascii=False),digest(s),s['symbol'],s['start'],s['end'],s['mode'],
            data['code_hash'],data['config_hash'],json.dumps(data.get('captures',[])),s['overlap_trading_days'],data['cores'],
            data.get('elapsed_seconds'),data.get('approximate'),json.dumps(data.get('warnings',[]),ensure_ascii=False),json.dumps(data,ensure_ascii=False)])
    def import_results(self,csv_path):
        # CSV is always created by our writer, header included even for zero signals.
        columns={f:('BIGINT' if f=='time_ms' else 'VARCHAR') for f in FIELDS}
        self.db.execute('INSERT INTO alerts SELECT * FROM read_csv(?,header=true,columns=?,nullstr=?)',[str(csv_path),columns,'__MOSES_NULL__'])

    def export_results(self,run_id,path):
        # COPY binds its filename parameter before parameters in its subquery.
        # Keep the filesystem destination separate from the SELECT bindings.
        columns=','.join('"'+f+'"' for f in FIELDS)
        cur=self.db.execute(f'SELECT {columns} FROM alerts WHERE run_id=? ORDER BY time_ms,strategy,signal_id,recipient',[run_id])
        count=0
        with Path(path).open('w',encoding='utf-8',newline='') as handle:
            writer=csv.writer(handle);writer.writerow(FIELDS)
            while rows:=cur.fetchmany(1024):writer.writerows(rows);count+=len(rows)
        return count

    def available(self,symbol,mode):
        return [json.loads(r[0]) for r in self.db.execute('SELECT metadata FROM captures WHERE symbol=? AND mode=? ORDER BY start_time,recorded_at DESC',[symbol,mode]).fetchall()]

    def compare(self,left,right,path,*,start_ms=0,end_ms=2**63-1):
        # Pair repeated semantic alerts chronologically; signal_id includes time and
        # therefore cannot identify a shifted alert by itself.
        # Remove exact matches first, otherwise one missing repeated alert would
        # incorrectly shift every later occurrence in its group.
        identity=('time_ms','strategy','profile','grade','symbol','tf','direction','trigger','message','recipient','signal_id')
        equality=' AND '.join(f'a0.{f} IS NOT DISTINCT FROM b0.{f}' for f in identity)
        sql='''WITH a0 AS (SELECT * FROM alerts WHERE run_id=? AND time_ms>=? AND time_ms<?),
        b0 AS (SELECT * FROM alerts WHERE run_id=? AND time_ms>=? AND time_ms<?),
        a AS (SELECT *, row_number() OVER (PARTITION BY strategy,profile,grade,symbol,tf,direction,trigger,message,recipient ORDER BY time_ms,signal_id) n FROM a0 WHERE NOT EXISTS(SELECT 1 FROM b0 WHERE '''+equality+''')),
        b AS (SELECT *, row_number() OVER (PARTITION BY strategy,profile,grade,symbol,tf,direction,trigger,message,recipient ORDER BY time_ms,signal_id) n FROM b0 WHERE NOT EXISTS(SELECT 1 FROM a0 WHERE '''+equality+'''))
        SELECT CASE WHEN a.run_id IS NULL THEN 'ADDED' WHEN b.run_id IS NULL THEN 'REMOVED' WHEN a.time_ms<>b.time_ms THEN 'TIME_CHANGED' ELSE 'ID_CHANGED' END difference,
        a.time_ms before_ms,b.time_ms after_ms,b.time_ms-a.time_ms delta_ms,
        coalesce(a.strategy,b.strategy) strategy,coalesce(a.message,b.message) message,coalesce(a.recipient,b.recipient) recipient,
        coalesce(a.symbol,b.symbol) symbol,coalesce(a.tf,b.tf) tf,coalesce(a.direction,b.direction) direction,
        coalesce(a.profile,b.profile) profile,coalesce(a.grade,b.grade) grade,coalesce(a.trigger,b.trigger) "trigger",
        a.signal_id before_signal_id,b.signal_id after_signal_id
        FROM a FULL JOIN b USING(strategy,profile,grade,symbol,tf,direction,trigger,message,recipient,n)
        WHERE a.run_id IS NULL OR b.run_id IS NULL OR a.time_ms<>b.time_ms OR a.signal_id<>b.signal_id ORDER BY coalesce(a.time_ms,b.time_ms)'''
        cur=self.db.execute(sql,[left,start_ms,end_ms,right,start_ms,end_ms]);count=0
        with Path(path).open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow([c[0] for c in cur.description])
            while rows:=cur.fetchmany(1024):w.writerows(rows);count+=len(rows)
        return count

class ResultWriter:
    def __init__(self,path,metadata,start_ms,end_ms):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('w',encoding='utf-8',newline='');self.writer=csv.DictWriter(self.file,fieldnames=FIELDS)
        self.writer.writeheader();self.metadata=metadata;self.start=start_ms;self.end=end_ms
        self.count=0;self.notifications=0;self.external_error=None;self.context={}
    def note_emission(self,consumer,event,output):
        from event_engine.model import Signal,signal_id
        if not isinstance(output,Signal) or output.content.get('type')!='NOTIFICATION':return
        raw=event.payload.get('content',{}).get('event',{})
        self.context[signal_id(consumer,output.symbol,event.source_time,output.condition_key)]={
            'strategy':raw.get('strategy',consumer),'profile':raw.get('profile',raw.get('profile_id','')) or
                '/'.join(str(raw.get(k,'')) for k in ('validation_mode','trigger_mode')).strip('/'),
            'grade':raw.get('grade',''),'trigger':raw.get('trigger',raw.get('final_trigger',raw.get('kind',''))),
            'tf':raw.get('source_tf',raw.get('tf','')),'direction':raw.get('direction','')}
    def accept(self,event):
        from event_engine.domain_support import plain
        p=plain(event.payload);c=p['content']
        if c.get('type')=='EXTERNAL_REQUEST':
            self.external_error='결정적으로 해석되지 않는 명령: '+str(c.get('text',''))
            return
        if c.get('type')!='NOTIFICATION' and c.get('status')!='DEGRADED':return
        context=self.context.pop(p['signal_id'],{})
        if not self.start<=event.source_time<self.end:return
        raw=c.get('event',c)
        text=str(c.get('message',''));special=re.match(r'^\[(\d)[.]',text)
        grade=re.search(r'\b([SABC])급\b',text)
        for recipient in c.get('recipients') or ['']:
            self.writer.writerow({'run_id':self.metadata['run_id'],'time_ms':event.source_time,'signal_id':p['signal_id'],
                'strategy':'SPECIAL'+special[1] if special else context.get('strategy',p['strategy']),
                'symbol':p['symbol'],'tf':context.get('tf') or raw.get('source_tf',raw.get('tf','')),
                'direction':context.get('direction') or raw.get('direction',''),'grade':context.get('grade') or raw.get('grade',raw.get('level','')) or (grade[1] if grade else ''),
                'profile':context.get('profile') or raw.get('profile',raw.get('profile_id','')),
                'trigger':context.get('trigger') or raw.get('trigger',raw.get('kind','')),
                'message':c.get('message',''),'recipient':recipient})
            self.count+=1;self.notifications+=int(c.get('type')=='NOTIFICATION')
        if self.count%1024==0:self.file.flush()
    def close(self):self.file.close()
