"""Write this task's fresh evidence. No model calls, training or Telegram."""
import ast
import hashlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from confirmed_answers import cases
from test_confirmed_50 import evaluate,STAGES

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    evidence=ROOT/'검증결과/AI_자연어_인터페이스_20261001'
    evidence.mkdir(exist_ok=True,parents=True)
    # Only related logic/security/interface tests. All providers are scripted.
    suite=unittest.TestSuite()
    for pattern in ('test_ai*.py','test_part3_http_flow.py'):
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern))
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    output=stream.getvalue().replace(str(ROOT),'Part3').replace(str(ROOT.parent),'PROJECT')
    (evidence/'related_tests.txt').write_text(output,encoding='utf-8')
    rows=[]
    for case in cases():
        row=evaluate(case)
        # Trace the human-approved source without exporting a fine-tuning corpus.
        pack=ROOT.parent.parent/'AI 교육'/case['source']
        content=pack.read_text('utf-8-sig')
        sections=re.split(r'^#{1,2} #(\d{3})\s*$',content,flags=re.M)
        section=next(sections[i+1] for i in range(1,len(sections),2) if sections[i]==case['id'])
        match=re.search(r'#{2,3} INPUT\s*\n(.*?)(?=\n#{2,3} )',section,flags=re.S)
        row['approved_input']=match[1].strip().strip('`') if match else None
        row['source_sha256']=hashlib.sha256(pack.read_bytes()).hexdigest()
        rows.append(row)
    counts={s:sum(r['stages'].get(s)=='PASS' for r in rows) for s in STAGES}
    save(evidence/'50_case_matrix.json',{'count':len(rows),'stage_pass_counts':counts,'cases':rows,
        'model_calls':0,'purpose':'USER_CONFIRMED_MEANING_TO_GENERATED_CODE_INTERFACE'})
    syntax=[]
    for relative in ('lab','tests'):
        for path in (ROOT/relative).rglob('*.py'):
            if '__pycache__' in path.parts: continue
            ast.parse(path.read_text('utf-8-sig'),filename=path.relative_to(ROOT).as_posix())
            syntax.append(path.relative_to(ROOT).as_posix())
    before=json.loads((evidence/'protected_before.json').read_text('utf-8'))
    after={relative:hashlib.sha256((ROOT.parent/relative).read_bytes()).hexdigest()
        for relative in before if (ROOT.parent/relative).is_file()}
    changed=[p for p in before if before[p]!=after.get(p)]
    save(evidence/'protected_after.json',after)
    save(evidence/'verification_summary.json',{'related_tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'five_stage_counts':counts,'python_syntax_files':len(syntax),
        'protected_part1_part2_files':len(before),'protected_changed_files':changed,
        'model_calls':0,'telegram_sends':0,'fine_tuning_data_created':False})
    print(json.dumps({'tests':result.testsRun,'successful':result.wasSuccessful(),
        'stages':counts,'protected_files':len(before),'changed':changed},ensure_ascii=False))
    return 0 if result.wasSuccessful() and not changed and all(v==50 for v in counts.values()) else 1

if __name__=='__main__': raise SystemExit(main())
