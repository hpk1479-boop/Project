from pathlib import Path
import hashlib,json,difflib,shutil
ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT.parent/'수정본26'
SUPPLIED=ROOT.parent/'SPECIAL_거래시간슬롯'
OUT=ROOT/'검증결과/ui_slots'
OUT.mkdir(parents=True,exist_ok=True)
sha=lambda b:hashlib.sha256(b).hexdigest()
before={p.relative_to(OLD).as_posix():sha(p.read_bytes()) for p in OLD.rglob('*') if p.is_file()}
assert all((ROOT/n).is_file() and sha((ROOT/n).read_bytes())==h for n,h in before.items())
(OUT/'previous_sha256.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
rows=[]
for i in range(1,8):
    rel=f'Part1/program/SPECIAL/SPECIAL{i}.py'
    original=(OLD/rel).read_bytes()
    base=(ROOT.parent/'수정본25'/rel).read_bytes()
    supplied=(SUPPLIED/f'SPECIAL/SPECIAL{i}.py').read_bytes()
    inserts=[]
    a=base.splitlines(keepends=True);b=supplied.splitlines(keepends=True)
    for tag,x,y,u,v in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if tag=='equal':continue
        assert tag=='insert',(i,tag)
        inserts.append((x,b[u:v]))
    assert len(inserts)==2 and [len(v) for _,v in inserts]==[4,4]
    if i==5:
        candidate=original.splitlines(keepends=True)
        assert len(candidate)==len(a)
        for pos,lines in reversed(inserts):candidate[pos:pos]=lines
        merged=b''.join(candidate)
        assert b'for wid in sorted({str(x) for x in watch_ids if str(x)}):' in merged
        changes=[op for op in difflib.SequenceMatcher(None,original.splitlines(keepends=True),merged.splitlines(keepends=True),autojunk=False).get_opcodes() if op[0]!='equal']
        assert all(t=='insert' and v-u==4 for t,x,y,u,v in changes) and len(changes)==2
        assert merged.count(b'\r\n')==original.count(b'\r\n')+8
        assert merged.count(b'\n')-merged.count(b'\r\n')==original.count(b'\n')-original.count(b'\r\n')
        (OUT/'SPECIAL5_slots_only.diff').write_text(''.join(difflib.unified_diff(original.decode('utf-8').splitlines(True),merged.decode('utf-8').splitlines(True),fromfile='수정본26/SPECIAL5.py',tofile='수정본27/SPECIAL5.py')),encoding='utf-8')
    else:
        assert original==base
        merged=supplied
    (ROOT/rel).write_bytes(merged)
    rows.append({'file':rel,'before':sha(original),'after':sha(merged),'added_lines':8,'removed_lines':0})
shutil.copyfile(SUPPLIED/'special_time_slot.py',ROOT/'Part1/program/special_time_slot.py')
(OUT/'slot_merge.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('All seven merged; SPECIAL5 exactly two 4-line insertions; CRLF and sorted loop retained.')
