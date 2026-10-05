"""S5 server ownership and legacy rejection; no expected/golden regeneration."""
import ast
import json
from pathlib import Path
import pickle
import subprocess
import sys

import pytest
from test_staff_s3 import env, publish

ROOT=Path(__file__).resolve().parents[2]
PROGRAM=ROOT/'Part1/program'

def assert_staff_scope():
    before=ast.parse((ROOT.parent/'수정본11/Part1/program/THE STAFF OF MOSES.py').read_bytes())
    after=ast.parse((PROGRAM/'THE STAFF OF MOSES.py').read_bytes())
    removed={'get','_legacy_frame','add_wonbi_features','apply_requested_features','validate_mt5_snapshot'}
    changed={'_SnapshotEntry.__init__','DataServer.__init__','DataServer._handle_request','DataServer.snapshot_reply'}
    def methods(tree):
        result={}
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and node.name not in removed:
                result[node.name]=ast.dump(node)
            if isinstance(node,ast.ClassDef):
                for method in node.body:
                    key=node.name+'.'+getattr(method,'name','')
                    if isinstance(method,ast.FunctionDef) and method.name not in removed and key not in changed:
                        # Docstrings can describe the new ownership without altering behavior.
                        if method.body and isinstance(method.body[0],ast.Expr) and isinstance(method.body[0].value,ast.Constant):
                            method.body=method.body[1:]
                        result[key]=ast.dump(method)
        return result
    assert methods(before)==methods(after)
    assert not removed.intersection(n.name for n in ast.walk(after) if isinstance(n,ast.FunctionDef))
    # Validate snapshot arithmetic, readiness, and validation ordering unchanged;
    # the only addition clears the existing throttled recovery log.
    def snapshot(tree):
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='snapshot_reply')
        for n in ast.walk(node):
            if isinstance(n,ast.For):
                n.body=[x for x in n.body if not (isinstance(x,ast.If) and '_feed_wait_logs' in ast.unparse(x.test))]
        return ast.dump(node)
    assert snapshot(before)==snapshot(after)

def test_server_removal_scope():
    assert_staff_scope()

@pytest.mark.parametrize('legacy_request',[{}, {'symbol':'BTCUSD','timeframes':['1m']},
    {'symbol':'BTCUSD','timeframes':['1m'],'indicators':['PRICE','SMA4']},
    {'kind':'unknown','symbol':'BTCUSD','timeframes':'bad'}])
def test_legacy_data_explicit_error(env,legacy_request):
    _,_,cache,server,_,_,_=env
    publish(cache)
    result=pickle.loads(server.dispatch_multipart([pickle.dumps(legacy_request)])[0])
    assert result['error_code']=='SNAPSHOT_API_REQUIRED'
    assert 'SNAPSHOT API 사용' in result['error'] and result['retryable'] is False

def test_control_sigma_forwarded_and_no_server_history(env):
    _,staff,cache,server,_,client,compat=env
    publish(cache)
    def control(req):return pickle.loads(server.dispatch_multipart([pickle.dumps(req)])[0])
    assert control({'kind':'PING'})['pong']
    assert control({'kind':'SET_WONBI_SIGMA','sigma':2.5})['sigma']==2.5
    request={'symbol':'BTCUSD','timeframes':['1m'],'indicators':['PRICE','SMA4']}
    assert client.request(request).sigma==2.5
    assert compat.request(request)['1m'].wonbi_sigma.iloc[-1]==2.5
    assert control(dict(request,kind='SOURCE_HEALTH'))['feeds']['1m']['status']=='FRESH'
    for name in ('_watch_ma_features','apply_requested_features','add_wonbi_features','get','_legacy_frame'):
        assert not hasattr(server,name) and not hasattr(cache,name) and not hasattr(staff,name)

def test_server_import_and_wire_delivery_never_load_pandas(tmp_path):
    # Isolated import with pandas and derived owner imports actively forbidden.
    script='''import builtins,importlib.util,sys
sys.path.insert(0,sys.argv[1])
original=builtins.__import__
def checked(name,*a,**kw):
    assert name.split('.')[0] not in {'pandas','indicator_facts','monitor_OZ','strategy_FVG','watch_ma','watch_ma_features','staff_compat'}, name
    return original(name,*a,**kw)
builtins.__import__=checked
spec=importlib.util.spec_from_file_location('isolated_staff',sys.argv[1]+'/THE STAFF OF MOSES.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
import numpy as np
cache=mod.StaffPipeCache('')
values=np.ones((8,45),dtype='<f8');times=np.arange(8,dtype='<i8')+1790035200
raw=mod.PIPE_HEADER.pack(mod.PIPE_MAGIC,1,1,6,2,8,45)+b'BTCUSD1m'+times.tobytes()+times.tobytes()+values.tobytes()
cache.publish_frame(raw)
server=mod.DataServer({},mod.WonbiState(3),cache=cache)
assert len(server.snapshot_reply({'symbol':'BTCUSD','timeframes':['1m']}))==4
assert 'pandas' not in sys.modules
'''
    import shutil
    for name in ('THE STAFF OF MOSES.py','staff_schema.py','staff_snapshot.py'):
        shutil.copy2(PROGRAM/name,tmp_path/name)
    run=subprocess.run([sys.executable,'-X','utf8','-B','-c',script,str(tmp_path)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr

def test_static_no_server_derivation_or_legacy_production_data_callers():
    from pit.adapters.legacy_staff import LegacyStaffViewAdapter
    from pit.contracts import PitError
    with pytest.raises(PitError, match='SNAPSHOT API 사용'):
        LegacyStaffViewAdapter(None,None)
    tree=ast.parse((PROGRAM/'THE STAFF OF MOSES.py').read_bytes())
    forbidden={'pandas','indicator_facts','monitor_OZ','strategy_FVG','watch_ma','watch_ma_features','staff_compat'}
    for node in ast.walk(tree):
        names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
        assert not forbidden.intersection(n.split('.')[0] for n in names)
    allowed={'strategy_SWEEP.py','strategy_FVG.py','strategy_INDICATOR.py','monitor_OZ.py','manager_KIM.py','THE STAFF OF MOSES.py'}
    for path in PROGRAM.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='send_pyobj':
                assert path.name in allowed
                if path.name=='manager_KIM.py' and isinstance(node.args[0],ast.Dict):
                    text=ast.unparse(node.args[0])
                    assert 'SET_WONBI_SIGMA' in text or 'SOURCE_HEALTH' in text
                else:
                    assert ast.unparse(node.args[0]) in {'event','reply','resp'}
    for name in ('record.py','actual.py','benchmark.py'):
        text=(ROOT/'Part2/staff_golden'/name).read_text('utf-8')
        assert 'rt._staff_handle, req' not in text
        assert 'rt._staff_handle(request)' not in text
    for path in (ROOT/'Part2').rglob('*.py'):
        if path.name.startswith('test_') or any(x.startswith(('.venv','tmp_')) for x in path.parts):
            continue
        tree=ast.parse(path.read_bytes())
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute):continue
            assert node.func.attr!='send_pyobj', (path,node.lineno)
            if node.func.attr=='_staff_handle':
                assert node.args and isinstance(node.args[0],ast.Dict)
                assert 'SOURCE_HEALTH' in ast.unparse(node.args[0]), (path,node.lineno)
            if ast.unparse(node.func) in {'self.server.handle','self.staff_server.handle'}:
                assert path.name=='runtime.py'
                assert ast.unparse(node.args[0])=='request' or 'SOURCE_HEALTH' in ast.unparse(node.args[0])
    # No client arithmetic, strategy decision, EA, or Watch history implementation changed.
    old=ROOT.parent/'수정본11/Part1/program'
    for path in old.rglob('*'):
        if path.is_file() and path.suffix in {'.py','.mq5','.mqh'} and path.name not in {'THE STAFF OF MOSES.py','staff_schema.py'}:
            assert path.read_bytes()==(PROGRAM/path.relative_to(old)).read_bytes()
    def normalized_body(path):
        node=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='legacy_frame')
        return [ast.dump(n) for n in node.body if not isinstance(n,ast.Import)]
    assert normalized_body(old/'staff_schema.py')==normalized_body(PROGRAM/'staff_schema.py')
