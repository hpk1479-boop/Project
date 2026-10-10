"""Record the user's stop/default decision without launching any replay."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/parallel_oz'

def main():
    results=[json.loads((OUT/f'year_month_{n}_keyframe.json').read_text('utf-8')) for n in (6,12)]
    selected=results[1]
    assert selected['elapsed_seconds']<results[0]['elapsed_seconds']
    value={'best_workers':12,'month_best_workers':12,'best_work_size':'MONTH',
           'best_run_id':selected['run_id'],
           'criterion':'User fixed MONTH / 12 using completed 6 and 12 worker measurements; not a global optimum claim',
           'omitted':['14 workers (stopped while running)','FORTNIGHT','beginning comparison'],
           'partial_run_id':'8bd36cda59ca49ae942aae103afc25e9',
           'partial_evidence':'year_month_14_keyframe_progress.json'}
    (OUT/'measured_default.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print('User stop/default decision recorded; no replay started.')

if __name__=='__main__':main()
