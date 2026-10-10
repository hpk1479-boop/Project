"""Remember moved distributions without changing the original application."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys


REGISTRY_PATH = r'Software\MOSES\TradingSystem'
INSTALLATIONS_PATH = REGISTRY_PATH + r'\Installations'
REQUIRED_FILES = ('MOSES.exe', 'python.exe', 'runtime/code.bundle', 'runtime/install.json')


def _recorded_value(registry, key_path, name):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, key_path, 0, registry.KEY_READ) as key:
            value, kind = registry.QueryValueEx(key, name)
            return value if kind == registry.REG_SZ else None
    except FileNotFoundError:
        return None


def register_gui_installation(root, argv, *, executable=None, frozen=None, registry=None):
    """Best-effort registration only for the distribution's GUI double-click.

    The caller must have passed the license gate. Explicit Python/script/module
    requests remain workers even if they use the renamed MOSES executable.
    """
    try:
        if not (getattr(sys, 'frozen', False) if frozen is None else frozen) or argv:
            return False
        root = Path(root).resolve()
        launcher = Path(sys.executable if executable is None else executable).resolve()
        if launcher.name.lower() != 'moses.exe' or launcher.parent != root:
            return False
        if not all((root / name).is_file() for name in REQUIRED_FILES):
            return False
        if registry is None:
            import winreg as registry
        location = str(root)
        identity = hashlib.sha256(location.lower().encode('utf-8')).hexdigest()
        for key_path, name in ((INSTALLATIONS_PATH, identity), (REGISTRY_PATH, 'InstallPath')):
            if _recorded_value(registry, key_path, name) == location:
                continue
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, key_path, 0,
                                      registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, name, 0, registry.REG_SZ, location)
        return True
    except (OSError, ValueError, TypeError, ImportError):
        # Registry permissions or a partially removed installation must never
        # turn a licensed application's normal startup into a failure.
        return False
