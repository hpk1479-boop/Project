from pathlib import Path
import duckdb,json
R=Path(__file__).resolve().parents[1];w=R.parent.with_name(R.parent.name+'_warehouse')
db=duckdb.connect(str(w/'captures.duckdb'),read_only=True)
rows=db.execute("SELECT metadata FROM captures WHERE symbol='XAUUSD+' ORDER BY recorded_at DESC").fetchall();db.close()
d=next(json.loads(row[0]) for row in rows if json.loads(row[0])['start']=='2025-09-01');keys=list(d);print(keys)
print({k:v for k,v in d.items() if 'point' in k or 'symbol' in k or 'tick' in k})
(R/'검증결과/log_restore/capture_fields.json').write_text(json.dumps(keys,ensure_ascii=False),encoding='utf8')
