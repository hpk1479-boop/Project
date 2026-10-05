"""Profile and compare individual hot functions, separate from backtest timings.

Role-copy extraction compiles the actual runner's single own_tick expression;
it does not rewrite or execute a substitute coordinator. Old oracles are from
the supplied current Part2. JIT is not used by these Python/struct candidates.
"""
import argparse
import ast
import copy
import cProfile
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time


def extract_role_tick_copy(runner):
    tree=ast.parse(Path(runner.__file__).read_text())
    assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='own_tick' for t in n.targets)]
    if len(assignments)!=1:raise AssertionError('Review the production own_tick copy expression')
    function=ast.parse('def role_copy(tick,state):\n    return None\n').body[0]
    function.body[0].value=copy.deepcopy(assignments[0].value)
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    namespace=dict(vars(runner))
    exec(compile(module,runner.__file__+':own_tick_expression','exec'),namespace)
    return namespace['role_copy']


def original_role_tick_copy(tick,state):
    return replace(tick,source_ordinal=state['ordinal'])


def profile_rows(prof):
    return sorted([dict(file=getattr(e.code,'co_filename','builtin'),
                        function=getattr(e.code,'co_name',str(e.code)),calls=e.callcount,
                        self_seconds=e.inlinetime,cumulative_seconds=e.totaltime)
                   for e in prof.getstats()],key=lambda x:x['self_seconds'],reverse=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--case',choices=('quote','role_tick','hma','all'),default='all')
    p.add_argument('--rounds',type=int,default=5)
    p.add_argument('--repeat',type=int,default=10,help='Raw pass repeats; HMA uses this many 682-row pairs')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.root.resolve();sys.path.insert(0,str(root))
    from generic_backtest import market,runner
    from generic_backtest.canonical import bits,encode
    from generic_backtest.history.cache import GenericArchiveReader
    from pit.features.hma_open import SourceHMAOpenKernel
    from conditional_validation.optimization_reference import quote_values,BaselineHMAOpenKernel
    ticks=list(GenericArchiveReader(args.archive))
    states=[{'ordinal':i} for i in range(1,len(ticks)+1)]
    values=[1000.+math.sin(i/17.)*15.+math.sin(i/31.)*7. for i in range(682)]
    cases={'quote':(quote_values,market.quote_values),
           'role_tick':(original_role_tick_copy,extract_role_tick_copy(runner)),
           'hma':(BaselineHMAOpenKernel.calculate,SourceHMAOpenKernel.calculate)}
    if args.case!='all':cases={args.case:cases[args.case]}
    def execute(name,fn):
        out=None
        if name=='hma':
            for _ in range(args.repeat):out=(fn(values,6),fn(values,17))
        else:
            for _ in range(args.repeat):
                if name=='quote':
                    for tick in ticks:out=fn(tick)
                else:
                    for tick,state in zip(ticks,states):out=fn(tick,state)
        return out
    result={'scope':'ISOLATED_FUNCTIONS_NOT_BACKTEST_WALL_TIME','jit_used':False,
            'root':str(root),'rounds':args.rounds,'repeat':args.repeat,'ticks_per_pass':len(ticks),'cases':{}}
    for name,functions in cases.items():
        samples=[[],[]];outputs=[None,None]
        for r in range(args.rounds):
            for i in ((0,1) if r%2==0 else (1,0)):
                before=time.perf_counter();outputs[i]=execute(name,functions[i]);samples[i].append(time.perf_counter()-before)
        assert encode(bits(outputs[0]))==encode(bits(outputs[1]))
        row={}
        for i,label in enumerate(('baseline','current')):
            prof=cProfile.Profile();prof.enable();out=execute(name,functions[i]);prof.disable()
            assert encode(bits(out))==encode(bits(outputs[i]))
            row[label]={'samples_seconds':samples[i],'median_seconds':statistics.median(samples[i]),
                        'output_sha256':hashlib.sha256(encode(bits(out))).hexdigest(),'profile':profile_rows(prof)}
        row['same_output_bits']=True
        row['improvement_percent']=(1-row['current']['median_seconds']/row['baseline']['median_seconds'])*100
        result['cases'][name]=row
        print(name,row['improvement_percent'],flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
