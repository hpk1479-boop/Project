"""S7 input contract updates: retain storage coverage on generic BB; require native Wonbi."""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'tests/sparse_events/test_event_catalog.py';s=p.read_text(encoding='utf-8')
# All completed-OHLCV storage tests explicitly select the unchanged generic BB
# lane. Wonbi without MT5 observations is now an error, verified separately below.
def rewrite_function(match):
    body=match.group(0)
    if 'C.build_ohlcv(' not in body:return body
    body=body.replace("event_types=('WONBI',)","event_types=('BB_OPEN_4_3_TOUCH',)")
    # Calls are single-line; preserve every argument including pause/rebuild/fail_day.
    lines=[]
    for line in body.splitlines(keepends=True):
        if 'C.build_ohlcv(' in line:
            # Add explicit BB selection directly after the database argument.
            line=re.sub(r'C\.build_ohlcv\((\w+),',r'build_raw_bb(\1,',line)
        lines.append(line)
    body=''.join(lines)
    body=body.replace("('TEST','15m','WONBI')","('TEST','15m','BB_OPEN_4_3_TOUCH')").replace("('TEST','1h','WONBI')","('TEST','1h','BB_OPEN_4_3_TOUCH')")
    return body
s=re.sub(r'^def test_.*?(?=^def |^@pytest|\Z)',rewrite_function,s,flags=re.M|re.S)
marker='def events(db):'
s=s.replace(marker,"""def build_raw_bb(db,*args,**kwargs):
    # Explicit S7 scope: generic BB storage mechanics still run unchanged.
    kwargs.setdefault('event_types',('BB_OPEN_4_3_TOUCH',))
    return C.build_ohlcv(db,*args,**kwargs)


"""+marker)
s=s.replace("assert db.state(('TEST','15m','BB_OPEN_4_3_TOUCH')) is None", "assert db.state(('TEST','15m','WONBI')) is None")
# Two raw types cannot both be derived any more. Check rejection, untouched DB,
# then preserve the positive generic-BB sides/time/deduplication assertions.
s=s.replace("        assert a==b and len(a)>2\n        assert {'LOWER','UPPER'}<={side for _,_,p in a", "        assert a==[] and len(b)>2\n        with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):\n            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',))\n        assert {'LOWER','UPPER'}<={side for _,_,p in b")
start=s.index('def test_add_new_event_only_and_keep_old_bytes():');end=s.index('\ndef ',start+5)
s=s[:start]+'''def test_add_new_event_only_and_keep_old_bytes():
    rows=bars()
    with sql_store() as db:
        db.put_ohlcv('TEST','15m',rows,source_key='test')
        build_raw_bb(db,'TEST',('15m',))
        old=events(db);state=copy.deepcopy(db.state(('TEST','15m','BB_OPEN_4_3_TOUCH')))
        with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):
            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',))
        assert events(db)==old and old
        assert db.state(('TEST','15m','BB_OPEN_4_3_TOUCH'))==state
        assert db.state(('TEST','15m','WONBI')) is None
        assert build_raw_bb(db,'TEST',('15m',))['event_evaluations']=={}

'''+s[end+1:]
s=s.replace("with pytest.raises(C.ReplayError,match='SOURCE_CHANGED'):\n            build_raw_bb(db,'TEST',('15m',),config={'WONBI_SIGMA':4})", "with pytest.raises(C.ReplayError,match='MT5_WONBI_REQUIRED'):\n            C.build_ohlcv(db,'TEST',('15m',),event_types=('WONBI',),config={'WONBI_SIGMA':4})")
p.write_text(s,encoding='utf-8')
print('Root raw storage checks retain generic BB coverage; missing native Wonbi now explicitly rejected')
