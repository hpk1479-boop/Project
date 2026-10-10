"""Report the exact changed function bodies and protected production files."""
from pathlib import Path
import argparse,ast,hashlib,json


def functions(path):
    tree=ast.parse(path.read_bytes());result={}
    def visit(nodes,prefix=''):
        for node in nodes:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                result[prefix+node.name]=hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
            elif isinstance(node,ast.ClassDef):visit(node.body,prefix+node.name+'.')
    visit(tree.body);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('before',type=Path);p.add_argument('after',type=Path);args=p.parse_args()
    before=args.before.resolve();after=args.after.resolve();changes={};unchanged=0;syntax=[]
    for base in ('Part1/program','Part2/event_backtest'):
        for path in sorted((after/base).rglob('*.py')):
            if '__pycache__' in path.parts:continue
            relative=path.relative_to(after).as_posix();old=before/relative
            if old.is_file() and old.read_bytes()==path.read_bytes():unchanged+=1;continue
            try:new=functions(path);prior=functions(old) if old.exists() else {}
            except SyntaxError as exc:syntax.append({'file':relative,'error':str(exc)});continue
            changes[relative]={'added_functions':sorted(set(new)-set(prior)),
                               'removed_functions':sorted(set(prior)-set(new)),
                               'changed_functions':sorted(k for k in set(new)&set(prior) if new[k]!=prior[k])}
    # These decision bodies must be completely untouched by this optimization.
    composer=changes.get('Part1/program/event_composer_domain.py',{})
    assert composer.get('changed_functions')==['ComposerManager._condition_source_binding','ComposerManager._sweep_subscription_payload']
    assert not composer.get('removed_functions') and not syntax
    out=after/'검증결과/part2_optimization38/scope_audit.json'
    out.write_text(json.dumps({'unchanged_production_python_files':unchanged,'changes':changes,'syntax_errors':syntax},ensure_ascii=False,indent=2))
    print(json.dumps({'unchanged_files':unchanged,'changed_files':len(changes),'syntax_errors':syntax},ensure_ascii=False))


if __name__=='__main__':main()
