"""Observed source/runtime identity, never an execution approval gate."""
import importlib.metadata
import platform
import sys
from .contracts import ROOT
from .canonical import file_hash, identity
from .paths import project_reference


def environment():
    packages = {}
    for name in ('numpy','MetaTrader5','pytest','tzdata'):
        try: packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    return {'executable':project_reference(sys.executable),
            'executable_sha256':file_hash(sys.executable),
            'python':platform.python_version(),'packages':packages,
            'isolation':'PROCESS_CAPABILITY_V1_NOT_OS_SANDBOX'}


def source_snapshot():
    paths = list((ROOT/'generic_backtest').rglob('*.py'))
    paths += list((ROOT/'calculations').rglob('*.py'))
    paths += list((ROOT/'data_warehouse').rglob('*.py'))
    paths += [ROOT/p for p in ('BACKTEST CONTROL.pyw','pit/__init__.py',
        'pit/models.py','pit/clock.py','pit/contracts.py','pit/archive/__init__.py',
        'pit/archive/reader.py','pit/features/__init__.py','pit/features/session.py','pit/features/hma_open.py')]
    paths += list((ROOT/'pit/features/percentile').rglob('*.py'))
    files = {p.relative_to(ROOT).as_posix():file_hash(p) for p in sorted(set(paths))}
    runtime = environment()
    return {'files':files,'source_hash':identity(files),'environment':runtime,
            'environment_identity':identity(runtime),'approval_required':False}
