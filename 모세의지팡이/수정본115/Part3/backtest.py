"""선택한 Test_SPECIAL 파일을 기존 PART2 이벤트 백테스트에 연결하는 진입점.

중요: Part1/Part2 소스파일은 수정하지 않습니다. Part1 공식 plugin loader API에
이 독립 프로세스의 선택 전략만 등록합니다. multiprocessing의 Windows spawn에서도
이 모듈이 재로딩되어 같은 등록이 적용됩니다.
"""
from __future__ import annotations
import hashlib
import ast
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.dont_write_bytecode=True

def resolve_request(request):
    """Resolve portable requests using only this process's selected roots."""
    if request.get('version') != 1:
        # Keep the existing public in-memory loader API used by engine tests.
        # The common job adapter never persists this legacy representation.
        return {'project_root': Path(request['project_root']).resolve(),
                'strategy': Path(request['strategy']).resolve() if request.get('strategy') else None,
                'job_dir': Path(request['job_dir']).resolve(),
                'warehouse': Path(request['warehouse']).resolve() if request.get('warehouse') else None}
    from lab.backtest_adapters import relative_reference
    root = Path(os.environ.get('PART3_BT_PROJECT_ROOT') or Path(__file__).resolve().parent.parent).resolve()
    warehouse_value = os.environ.get('PART3_BT_WAREHOUSE')
    if not warehouse_value:
        raise ValueError('현재 컴퓨터의 데이터 창고 연결이 필요합니다.')
    warehouse = Path(warehouse_value).resolve()
    if request.get('project_root') != '.' or request.get('warehouse') != '.':
        raise ValueError('실행 요청에는 프로젝트·창고 루트 위치를 저장할 수 없습니다.')
    identifier = request.get('session_id')
    if (not isinstance(identifier, str) or len(identifier) != 32 or
            any(c not in '0123456789abcdef' for c in identifier)):
        raise ValueError('실행 ID를 확인하세요.')
    folder = relative_reference(warehouse, request['job_dir'])
    if folder != warehouse / 'runs' / identifier:
        raise ValueError('실행 폴더가 공통 실행 ID와 일치하지 않습니다.')
    strategy = relative_reference(root, request['strategy'])
    from lab.compiler import FILE_RE
    from strategy_recipe.user_catalog import test_directory
    if strategy.parent != test_directory(root).resolve() or not FILE_RE.fullmatch(strategy.name):
        raise ValueError('현재 생성 전략 폴더의 Test_SPECIAL 파일만 실행할 수 있습니다.')
    if hashlib.sha256(strategy.read_bytes()).hexdigest() != request.get('strategy_sha256'):
        raise ValueError('계획 확인 후 생성 전략 파일이 변경되었습니다. 새 백테스트를 시작하세요.')
    return {'project_root': root, 'strategy': strategy, 'job_dir': folder, 'warehouse': warehouse}


def initialize(request:dict):
    paths = resolve_request(request)
    root = paths['project_root']
    sys.path.insert(0,str(root/'Part1/program'))
    sys.path.insert(0,str(root/'Part2'))
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    os.environ['MOSES_LOG_DIRECTORY']=str(paths['job_dir']/'logs')
    # WATCH requests use the unchanged engine loader; nothing is injected.
    if request.get('watch_text'):return 'WATCH'
    strategy=paths['strategy']
    import event_application
    key=strategy.stem
    # Lowered common plans declare only their actual Fact/processor requirements.
    def loader():
        raw=strategy.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if request.get('version') == 1 and digest != request['strategy_sha256']:
            raise ValueError('계획 확인 후 생성 전략 파일이 변경되었습니다. 새 백테스트를 시작하세요.')
        # The file is an immutable Recipe container. Its imports, callbacks
        # and former source-template execution are never evaluated.
        declarations = {}
        for node in ast.parse(raw.decode('utf-8-sig'), filename=strategy.name).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ('PART3_RECIPE','PART3_COMPILE_MODE'):
                    if target.id in declarations: raise ValueError('생성 전략의 Recipe 선언이 중복되었습니다.')
                    try: declarations[target.id] = ast.literal_eval(node.value)
                    except (ValueError,TypeError): raise ValueError('생성 전략은 literal Recipe v2 데이터여야 합니다.') from None
        recipe = declarations.get('PART3_RECIPE')
        if (not isinstance(recipe,dict) or recipe.get('schema_version') != 2 or
                recipe.get('base') != 'AI' or not isinstance(recipe.get('strategy_intent'),dict) or
                declarations.get('PART3_COMPILE_MODE','CANONICAL') != 'CANONICAL' or
                recipe['strategy_intent'].get('inherit_base_rules')):
            raise ValueError('과거 소스 복제·INHERITED 전략은 공통 Recipe v2로 다시 생성해 실행하세요. 원본 파일은 보존했습니다.')
        from strategy_recipe.contract import execution_plan
        from strategy_recipe.registry import oz_declarations
        from strategy_recipe.port import IntentPort
        plan = execution_plan(recipe['strategy_intent'])['meaning']
        def register(manager):
            IntentPort(manager,plan,recipe.get('name') or key,key).install()
        return SimpleNamespace(__name__=key, PART3_RECIPE=recipe,
            _INTENT_PLAN=plan, register=register, OZ_DECLARATIONS=oz_declarations(plan))
    plugin=loader()
    recipe=getattr(plugin,'PART3_RECIPE',None)
    from strategy_recipe.registry import dependencies
    def verified_plugin():
        if (request.get('version') == 1 and
                hashlib.sha256(strategy.read_bytes()).hexdigest() != request['strategy_sha256']):
            raise ValueError('계획 확인 후 생성 전략 파일이 변경되었습니다. 새 백테스트를 시작하세요.')
        return plugin
    event_application.register_strategy_loader(key, verified_plugin,
        dependencies=dependencies(plugin._INTENT_PLAN) or ('RECIPE',))
    return key

# spawn 자식도 환경변수의 같은 요청을 읽어 loader를 구성합니다.
REQUEST_PATH=os.environ.get('PART3_BT_REQUEST','')
REQUEST=None
STRATEGY_KEY=None
REQUEST_ERROR=None
if REQUEST_PATH:
    try:
        REQUEST=json.loads(Path(REQUEST_PATH).read_text('utf-8'))
        STRATEGY_KEY=initialize(REQUEST)
    except Exception as exc:
        REQUEST_ERROR=exc

def main():
    if REQUEST_ERROR is not None:
        message=str(REQUEST_ERROR)
        for key,label in (('PART3_BT_PROJECT_ROOT','<project>'),('PART3_BT_WAREHOUSE','<warehouse>')):
            value=os.environ.get(key)
            if value:message=message.replace(value,label)
        message=re.sub(r'[A-Za-z]:[\\/][^\s"\n]+','[경로]',message)
        print(json.dumps({'event':'ERROR','message':message,'completed_pieces_preserved':True},ensure_ascii=False),flush=True)
        return 1
    if REQUEST is None:raise SystemExit('PART3 화면 또는 cli.py backtest를 통해 실행하세요.')
    paths=resolve_request(REQUEST)
    journal=paths['job_dir']/'progress.jsonl' if REQUEST.get('action')=='run' else None
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,Path):value=str(value)
        if isinstance(value,str):
            for root,label in ((paths['project_root'],'<project>'),(paths['warehouse'],'<warehouse>')):
                if root is not None:value=value.replace(str(root),label)
            return re.sub(r'[A-Za-z]:[\\/][^\s"\n]+',lambda match:Path(match[0]).name,value)
        return value
    def emit(kind,data):
        line=json.dumps(clean({'event':kind,**data}),ensure_ascii=False,default=str)
        if journal:
            with journal.open('a',encoding='utf-8') as out:out.write(line+'\n')
        print(line,flush=True)
    from event_backtest.settings import scenario
    from event_backtest.workflow import proposal,execute
    # 실행 결과 식별자에 Test 파일의 코드 해시도 포함합니다.
    # loader 초기화와 분리하여 엔진 등록 점검에는 데이터베이스가 필요하지 않습니다.
    if REQUEST.get('watch_text'):
        # Same COMMAND contract as the Part2 WATCH mode.
        s=scenario(**{**REQUEST['scenario'],'strategies':['WATCH'],
                   'commands':[{'strategy':'WATCH','text':REQUEST['watch_text'],'chat_id':'BACKTEST'}]})
    else:
        from event_backtest import runner
        old_hash=runner.code_hash
        digest=hashlib.sha256(paths['strategy'].read_bytes()).hexdigest()
        runner.code_hash=lambda:hashlib.sha256((old_hash()+digest).encode()).hexdigest()
        s=scenario(**{**REQUEST['scenario'],'strategies':[STRATEGY_KEY],'oz_evaluation':'all'})
    s['_run_id']=REQUEST.get('session_id') or s.get('_run_id')
    try:
        if REQUEST['action']=='plan':
            result=proposal(s,paths['warehouse'],rebuild=bool(REQUEST.get('rebuild')))
        elif REQUEST['action']=='run':
            from event_backtest.cancellation import file_check
            result=execute(s,paths['warehouse'],yes=False,approved_token=REQUEST.get('approved_token'),
                rebuild=bool(REQUEST.get('rebuild')),cores=REQUEST.get('cores'),emit=emit,
                cancel=file_check(paths['job_dir']/'stop.request'))
        else:raise ValueError('plan 또는 run을 선택하세요.')
    except Exception as exc:
        from event_backtest.build_plan import ConfirmationRequired
        from event_backtest.history_check import HistoryMissing
        if isinstance(exc,ConfirmationRequired):emit('CONFIRMATION_REQUIRED',exc.plan);return 2
        if isinstance(exc,HistoryMissing):emit('HISTORY_MISSING',{'gaps':exc.gaps,'backtest_started':False});return 3
        emit('ERROR',{'message':str(exc),'completed_pieces_preserved':True});return 1
    emit('COMPLETE',{'result':result})
    return 0

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
