"""Read-only mechanical extraction and stage-boundary proof."""
import ast
from staff_s3_evidence import *

def tree(path): return ast.parse(path.read_bytes())
def named(nodes,name): return next(n for n in nodes if getattr(n,'name',None)==name)
old=tree(SOURCE/'Part1/program/THE STAFF OF MOSES.py')
new=tree(ROOT/'Part1/program/THE STAFF OF MOSES.py')
result={}
for cls in ('StaffPipeCache','DataServer'):
    a=named(old.body,cls); b=named(new.body,cls)
    for n in a.body:
        if isinstance(n,ast.FunctionDef) and n.name not in ('handle','run'):
            result[f'{cls}.{n.name}']=ast.dump(n)==ast.dump(named(b.body,n.name))
assert all(result.values()),result
compat=tree(ROOT/'Part1/program/staff_compat.py')
for name in ('add_wonbi_features','apply_requested_features','validate_mt5_snapshot'):
    result['compat.'+name]=ast.dump(named(old.body,name))==ast.dump(named(compat.body,name))
old_handler=named(named(old.body,'DataServer').body,'_handle_request')
compose=named(named(compat.body,'StaffCompat').body,'_compose')
start=next(i for i,n in enumerate(old_handler.body) if isinstance(n,ast.ImportFrom) and n.module=='watch_ma')
expected=ast.Module(body=old_handler.body[start:],type_ignores=[])
class Sigma(ast.NodeTransformer):
    def visit_Call(self,node):
        if ast.unparse(node)=='self.wonbi_state.get_sigma()':
            return ast.Name(id='sigma',ctx=ast.Load())
        return self.generic_visit(node)
expected=Sigma().visit(expected)
result['compat.legacy_composition']=ast.dump(expected)==ast.dump(ast.Module(body=compose.body[1:],type_ignores=[]))
assert all(result.values()),result
write(OUT/'mechanical_scope_proof.json',{'equal':True,'checks':result,
    'scope':'Existing receiver, normalization, calculation and legacy handler bodies unchanged; only transport branches added.'})
print('S3 mechanical checks:',len(result))
