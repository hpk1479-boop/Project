"""Diagnostic changing-cell counts and native Wonbi comparison, streaming."""
import sys,importlib.util,gzip,struct,json,collections,itertools
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
import staff_schema as new
from event_backtest.settings import settings
from indicator_facts import wonbi_bands
spec=importlib.util.spec_from_file_location('old_wire',OUT/'hosts/revision22/Part1/program/staff_schema.py')
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
H=struct.Struct('<IIII');R=struct.Struct('<qiI')

def feeds(root):
    return {p[2]:(root/p[3],int(p[4])) for line in (root/'manifest.tsv').read_text('ascii').splitlines() if (p:=line.split('\t'))[0]=='pipe_feed'}

def records(file,wire):
    with gzip.open(file,'rb') as h:
        magic,version,cols,bars=H.unpack(h.read(H.size));assert cols==len(wire.PIPE_VALUE_COLUMNS)
        state=None
        while header:=h.read(R.size):
            t,flags,size=R.unpack(header);f=wire.decode_v2(h.read(size))
            if f.kind==wire.WIRE_FULL:state=[f.times.copy(),f.volumes.copy(),f.values.copy()]
            elif f.kind==wire.WIRE_ROW:
                for a,b in zip(state,(f.times,f.volumes,f.values)):a[-1:]=b
            yield t,f.kind,state

def summary(file,wire):
    previous=None;counts=np.zeros((3,len(wire.PIPE_VALUE_COLUMNS)),dtype='int64');pairs=0;rows=0
    for t,kind,s in records(file,wire):
        if kind!=wire.WIRE_FULL:continue
        if previous is not None:
            _,ai,bi=np.intersect1d(previous[0],s[0],return_indices=True)
            diff=previous[2][ai].view('uint64')!=s[2][bi].view('uint64');pairs+=1;rows+=len(ai)
            counts[0]+=diff.sum(axis=0);counts[1]+=diff[bi<64].sum(axis=0);counts[2]+=diff[bi>=64].sum(axis=0)
        previous=[a.copy() for a in s]
    return {'full_pairs':pairs,'overlap_rows':rows,'changed_cells':int(counts[0].sum()),'front64_cells':int(counts[1].sum()),
            'remainder_cells':int(counts[2].sum()),'by_column':{c:{'all':int(counts[0,i]),'front64':int(counts[1,i]),'remainder':int(counts[2,i])} for i,c in enumerate(wire.PIPE_VALUE_COLUMNS)}}

def main():
    info=json.loads((ROOT/'검증결과/part2_connection/day_BAR.json').read_text('utf-8'))[0]
    before=feeds(Path(settings()['warehouse'])/info['path']);after=feeds(OUT/'captures/day_bar_a')
    result={'recording':'2026-09-25 XAUUSD+ BAR','window_definition':'Match identical bar timestamps between consecutive FULL; front64 is newer FULL leading 64 rows, remainder includes forming row.',
            'old':{},'new':{},'wonbi':{'records':0,'sigma3_different_records':0,'old_new_band_different_cells':0,'sigma_rescale_formula_samples':0},'common_columns':collections.Counter(),'common_examples':[]}
    for tf in before:
        result['old'][tf]=summary(before[tf][0],old);result['new'][tf]=summary(after[tf][0],new)
        for a,b in itertools.zip_longest(records(before[tf][0],old),records(after[tf][0],new)):
            assert a is not None and b is not None and a[0]==b[0],tf
            oldx,newx=a[2][2],b[2][2];_,ai,bi=np.intersect1d(a[2][0],b[2][0],return_indices=True)
            bands=wonbi_bands(*(newx[:,new.PIPE_VALUE_COLUMNS.index(k)] for k in ('open_band_4_mid','wonbi_upper','wonbi_lower')),3)
            result['wonbi']['records']+=1
            for c in ('wonbi_upper','wonbi_lower'):
                assert bands[c].tobytes()==newx[:,new.PIPE_VALUE_COLUMNS.index(c)].tobytes()
                result['wonbi']['old_new_band_different_cells']+=int((oldx[ai,old.PIPE_VALUE_COLUMNS.index(c)].view('uint64')!=newx[bi,new.PIPE_VALUE_COLUMNS.index(c)].view('uint64')).sum())
            for c in set(old.PIPE_VALUE_COLUMNS)&set(new.PIPE_VALUE_COLUMNS):
                x,y=oldx[ai,old.PIPE_VALUE_COLUMNS.index(c)],newx[bi,new.PIPE_VALUE_COLUMNS.index(c)]
                diff=x.view('uint64')!=y.view('uint64');result['common_columns'][c]+=int(diff.sum())
                if diff.any() and len(result['common_examples'])<30:
                    i=int(np.flatnonzero(diff)[0]);result['common_examples'].append({'tf':tf,'observation':a[0],'bar_time':int(a[2][0][ai[i]]),'column':c,'before':str(x[i]),'after':str(y[i])})
        print('CELLS',tf,result['old'][tf]['changed_cells'],result['new'][tf]['changed_cells'],flush=True)
    (OUT/'cell_diagnostics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
