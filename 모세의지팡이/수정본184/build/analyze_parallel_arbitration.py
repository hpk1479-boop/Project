"""Separate observed representative-output differences from unresolved state effects."""
from pathlib import Path
import argparse,collections,json
from check_parallel_evidence import alerts,compare
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();runs=Path(a.warehouse)/'runs'
    all_month=alerts(runs/'parallel26_a_month_all')
    all_old=alerts(runs/'parallel26_before_week_all_trace')
    result={}
    for i in range(1,8):
        name=f'SPECIAL{i}';solo_path=runs/f'parallel26_month_pairs_all_special{i}'
        if not (solo_path/'result.json').exists():continue
        solo=alerts(solo_path);subset=[x for x in all_month if x['strategy']==name]
        difference=compare(subset,solo);classified=[]
        for kind,rows in (('all_only',difference['removed']),('solo_only',difference['added'])):
            for row in rows:
                matching=[x for x in all_month if x['strategy']!=name and all(x[k]==row[k] for k in ('time_ms','symbol','tf','direction'))]
                classified.append({'difference':kind,'alert':row,'other_representative_at_same_market_point':matching,
                    'classification':'existing shared-output arbitration: same market point observed' if matching else
                    'shared state/identity difference; no direct same-time representative match; do not claim row-level proof'})
        old_solo=alerts(runs/f'engineopt_after_week_special{i}')
        result[name]={'revision25_first_week_solo_vs_all':compare([x for x in all_old if x['strategy']==name],old_solo),
            'revision26_month_solo_vs_all':difference,'diagnostic_rows':classified}
    (OUT/'arbitration_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print({name:len(row['diagnostic_rows']) for name,row in result.items()})

if __name__=='__main__':main()
