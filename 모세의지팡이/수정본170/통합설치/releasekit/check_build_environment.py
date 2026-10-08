"""File-based dependency check safe for Windows PowerShell 5.1 arguments."""
import importlib
from importlib.metadata import version


def main():
    for name in ('tkinter', 'PyInstaller', 'numpy', 'pandas', 'webview',
                 'MetaTrader5', 'pytest', 'duckdb', 'requests', 'jsonschema'):
        importlib.import_module(name)
    expected = {'numpy': '2.3.5', 'pandas': '3.0.1',
                'pywebview': '6.2.1', 'MetaTrader5': '5.0.6180',
                'pyinstaller': '6.22.2'}
    for name, value in expected.items():
        if version(name) != value:
            raise RuntimeError('Build dependency version differs: ' + name)
    print('BUILD_ENV_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
