"""Hash-first golden comparison; exact frozen comparator only on byte differences."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part2'))
from staff_golden.contracts import compare

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def compare_hash_first(left,right):
    a,b=digest(left),digest(right)
    if a==b:
        # Do not deserialize/recompare every frame of the identical database.
        with sqlite3.connect(Path(left).resolve().as_uri()+'?mode=ro',uri=True) as db:
            cases=db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
            frames=db.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
        result={'equal':True,'left_cases':cases,'right_cases':cases,
                'exact_frames_checked':0,'different_cases':[],'frames_covered_by_file_hash':frames}
    else:
        result=compare(left,right)
    return {**result,'comparison_mode':'file_sha256' if a==b else 'exact_fallback',
            'left_sha256':a,'right_sha256':b}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('left',type=Path)
    parser.add_argument('right',type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    result=compare_hash_first(args.left,args.right)
    with args.out.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(int(not result['equal']))
