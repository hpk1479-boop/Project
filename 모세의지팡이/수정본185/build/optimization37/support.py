"""Validation-only imports; production files and database behavior are not patched."""
from pathlib import Path
import ast
import importlib.util
import sys
import types


def load_warehouse(project, name='event_backtest.warehouse'):
    """Load the real module; omit ONLY its unused DuckDB import when unavailable.

    ResultWriter and Warehouse.find_capture retain their exact production bodies.
    Calling Warehouse.__init__ still fails clearly without a real DuckDB package.
    """
    project=Path(project).resolve()
    sys.path[:0]=[str(project/'Part1/program'),str(project/'Part2')]
    path=project/'Part2/event_backtest/warehouse.py'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    if importlib.util.find_spec('duckdb') is None:
        tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        tree.body=[n for n in tree.body if not (isinstance(n,ast.Import) and any(a.name=='duckdb' for a in n.names))]
        def unavailable(*args,**kwargs):
            raise RuntimeError('DuckDB is unavailable: database integration is NOT being tested')
        module.duckdb=types.SimpleNamespace(connect=unavailable)
        module._validation_without_duckdb=True
        exec(compile(tree,str(path),'exec'),module.__dict__)
    else:
        spec.loader.exec_module(module)
        module._validation_without_duckdb=False
    return module


def install_warehouse(project):
    module=load_warehouse(project)
    sys.modules['event_backtest.warehouse']=module
    return module
