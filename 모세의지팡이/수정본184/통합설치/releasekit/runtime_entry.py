"""Compiled console worker and desktop launcher for the licensed distribution."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import types

from releasekit.runtime_bundle import RuntimeBundle


def project_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def prepare_multiprocessing():
    """Keep frozen worker spawning while rebuilding each dispatched main script."""
    import multiprocessing
    from multiprocessing import spawn
    if getattr(sys, 'frozen', False):
        # This executable dispatches arbitrary scripts, so it has no fixed app
        # __main__. Windows' normal frozen-app shortcut would omit that script
        # from preparation data and break pickling functions defined in it.
        # get_command_line still uses sys.frozen and the PyInstaller fork hook.
        spawn.WINEXE = False
        spawn.set_executable(sys.executable)
    return multiprocessing


def prepare_python_path():
    """Honor application-selected helper folders in a frozen child interpreter."""
    if getattr(sys, 'frozen', False) and os.environ.get('PYTHONPATH'):
        folders = [str(Path(value or os.curdir).absolute())
                   for value in os.environ['PYTHONPATH'].split(os.pathsep)]
        sys.path[:0] = folders


def dispatch(argv, bundle):
    """Accept the original application's Python script/module invocation shape."""
    arguments = list(argv)
    while arguments:
        flag = arguments[0]
        if flag in ('-B', '-u', '-E', '-I', '-s', '-S', '-P', '-q', '-O', '-OO', '-v', '-x'):
            arguments.pop(0)
        elif flag in ('-X', '-W'):
            if len(arguments) < 2:
                raise ValueError('Missing Python option value: ' + flag)
            del arguments[:2]
        elif flag.startswith(('-X', '-W')):
            arguments.pop(0)
        elif flag == '--':
            arguments.pop(0)
            break
        else:
            break
    if not arguments:
        arguments = [str(bundle.root / 'START_MOSES.pyw')]
    operation = arguments.pop(0)
    if operation == '-m':
        if not arguments:
            raise ValueError('A Python module name is required.')
        module = arguments.pop(0)
        sys.path.insert(0, str(Path.cwd()))
        sys.argv = [module, *arguments]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
    elif operation == '-c':
        if not arguments:
            raise ValueError('Python command code is required.')
        command = arguments.pop(0)
        sys.path.insert(0, str(Path.cwd()))
        sys.argv = ['-c', *arguments]
        module = types.ModuleType('__main__')
        module.__dict__.update(__package__=None, __builtins__=__builtins__)
        previous = sys.modules.get('__main__')
        sys.modules['__main__'] = module
        try:
            exec(compile(command, '<string>', 'exec'), module.__dict__)
        finally:
            if previous is None:
                sys.modules.pop('__main__', None)
            else:
                sys.modules['__main__'] = previous
    elif operation in ('--version', '-V'):
        print(sys.version)
    elif operation.startswith('-'):
        raise ValueError('Unsupported Python worker option: ' + operation)
    else:
        script = Path(operation).absolute()
        sys.argv = [str(script), *arguments]
        sys.path.insert(0, str(script.parent))
        bundle.run_path(script, run_name='__main__')
    return 0


def main(argv=None, root=None):
    root = Path(root).resolve() if root is not None else project_root()
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Import the lightweight gate before any project code or GUI is loaded.
    from releasekit.license_runtime import check_license
    if not check_license(root):
        return 0
    from releasekit.install_location import register_gui_installation
    register_gui_installation(root, arguments)
    prepare_python_path()
    bundle = RuntimeBundle(root).install()
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    worker = root / 'python.exe'
    if worker.is_file():
        sys.executable = str(worker)
    # Both app child processes and Windows multiprocessing must use the same
    # compiled loader. PyInstaller's hook also handles tracker -c invocations.
    prepare_multiprocessing().freeze_support()
    return dispatch(arguments, bundle)


if __name__ == '__main__':
    raise SystemExit(main())
