"""Register only revision-38 changes; preserve all earlier source history."""
from pathlib import Path
import argparse,ast,difflib,hashlib,importlib.util,json


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(root,name):
    spec=importlib.util.spec_from_file_location(name,root/'Part1/audit/source_integrity.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.verify_sources()


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);a=p.parse_args()
    before=a.before.resolve();after=a.after.resolve();out=after/'검증결과/part2_optimization38';out.mkdir(parents=True,exist_ok=True)
    previous=check(before,'_integrity38_before');expected=previous['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    changes=[];all_changes=[];diff=[]
    for base in ('Part1/program','Part2/event_backtest'):
        for path in sorted((after/base).rglob('*.py')):
            if '__pycache__' in path.parts:continue
            relative=path.relative_to(after).as_posix();old=before/relative
            if old.is_file() and sha(old)==sha(path):continue
            ast.parse(path.read_bytes(),filename=relative)
            entry={'file':relative,'before_sha256':sha(old) if old.exists() else empty,'after_sha256':sha(path)}
            all_changes.append(entry)
            if relative.startswith('Part1/'):
                item={**entry,'file':relative[len('Part1/'):]}
                if expected.get(item['file'],empty)!=item['before_sha256']:
                    raise ValueError('Pre-existing chain conflict: '+item['file'])
                changes.append(item)
            diff.extend(difflib.unified_diff(old.read_text(encoding='utf-8').splitlines(True) if old.exists() else [],
                                            path.read_text(encoding='utf-8').splitlines(True),
                                            fromfile='revision37/'+relative,tofile='revision38/'+relative))
    unit=after/'Part1/audit/remediation/71-part2-optimization38'
    if unit.exists():raise FileExistsError('Unit already registered; review before rerunning')
    unit.mkdir(parents=True)
    (unit/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
    (unit/'README.md').write_text('수정본37 기준: 불변 구독 재사용, 현재 설정 기반 고정 식별자 캐시, Fact 중복 변환 제거.\n'
                                'health·epoch·전략 판정·최초 plain·Signal 불변화는 유지합니다.\n'
                                '증거: 프로젝트 루트 검증결과/part2_optimization38.\n',encoding='utf-8')
    inventory=after/'build/part1_immutable_sha256.json';old_inventory=sha(inventory)
    items=json.loads(inventory.read_text(encoding='utf-8'))
    for item in changes:items['Part1/'+item['file']]=item['after_sha256']
    for path in unit.iterdir():items[path.relative_to(after).as_posix()]=sha(path)
    inventory.write_text(json.dumps(items,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8')
    current=check(after,'_integrity38_after')
    def inventory_mismatches(root,values):
        return [r for r,digest in values.items() if not (root/r).is_file() or sha(root/r)!=digest]
    protected=['Part2/event_backtest/warehouse.py','Part2/event_backtest/runner.py',
               'Part1/program/THE STAFF OF MOSES.py','Part1/program/staff_schema.py',
               'Part1/program/event_engine/board.py','Part1/program/event_engine/model.py',
               'Part1/program/config.txt','Part2/event_backtest/partition.py']
    protected.extend(p.relative_to(after).as_posix() for p in (after/'Part1/program/SPECIAL').rglob('*.py'))
    protection=[{'file':r,'unchanged':(before/r).exists() and sha(before/r)==sha(after/r)} for r in protected]
    report={'unit':unit.relative_to(after).as_posix(),'production_changes':all_changes,
            'source_errors_before':previous['integrity_errors'],'source_errors_after':current['integrity_errors'],
            'new_errors':sorted(set(current['integrity_errors'])-set(previous['integrity_errors'])),
            'inventory_before_sha256':old_inventory,'inventory_after_sha256':sha(inventory),
            'inventory_before_mismatches':inventory_mismatches(before,json.loads((before/'build/part1_immutable_sha256.json').read_text())),
            'inventory_after_mismatches':inventory_mismatches(after,items),'protected':protection}
    (out/'integrity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'production_changes.diff').write_text(''.join(diff),encoding='utf-8')
    assert not report['new_errors'] and not report['inventory_after_mismatches']
    assert all(v['unchanged'] for v in protection)
    print(json.dumps({'production_changes':len(all_changes),'part1_changes':len(changes),
                      'new_integrity_errors':report['new_errors'],'inventory_mismatches':report['inventory_after_mismatches']},ensure_ascii=False))


if __name__=='__main__':main()
