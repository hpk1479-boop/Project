"""Load the actual Part1 LIVE strategy modules for backtest reuse.

The LIVE source remains the single owner of FVG/TREND/OZ calculations. Part2
imports those definitions instead of maintaining a second mathematical
implementation. Import-only LIVE side effects (notably monitor_OZ logging
setup) are suppressed so BACKTEST never creates or configures LIVE logging.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LIVE_PROGRAM = _PROJECT_ROOT / "Part1" / "program"
_LIVE_LOG_DIR = (_LIVE_PROGRAM / "logs").resolve()


def _import_without_live_logging(module_name: str):
    """Import a LIVE module while suppressing monitor_OZ import-time logging only."""
    original_basic_config = logging.basicConfig
    original_file_handler = logging.FileHandler
    original_mkdir = Path.mkdir

    def _no_basic_config(*args, **kwargs):
        return None

    def _null_file_handler(*args, **kwargs):
        return logging.NullHandler()

    def _guarded_mkdir(self: Path, *args, **kwargs):
        try:
            if self.resolve() == _LIVE_LOG_DIR:
                return None
        except Exception:
            pass
        return original_mkdir(self, *args, **kwargs)

    logging.basicConfig = _no_basic_config
    logging.FileHandler = _null_file_handler
    Path.mkdir = _guarded_mkdir
    try:
        return importlib.import_module(module_name)
    finally:
        Path.mkdir = original_mkdir
        logging.FileHandler = original_file_handler
        logging.basicConfig = original_basic_config


def load_live_program_module(module_name: str):
    live_path = str(_LIVE_PROGRAM)
    if live_path not in sys.path:
        sys.path.insert(0, live_path)
    if module_name == "monitor_OZ" and module_name not in sys.modules:
        return _import_without_live_logging(module_name)
    return importlib.import_module(module_name)
