"""Read current project contracts; validate canonical AI Recipes only."""
from __future__ import annotations
import copy
import json
import types
from pathlib import Path
from .source_edit import constants

ROOT = Path(__file__).resolve().parents[1]
CURRENT_PROGRAM = ROOT.parent / 'Part1/program'
import sys
if str(CURRENT_PROGRAM) not in sys.path:sys.path.insert(0,str(CURRENT_PROGRAM))
from strategy_recipe.registry import builtin_sources, entries, preset_entry
NAMES = {name:row['name'] for name,row in entries().items()}

def preset_names():
    return {name:row['name'] for name,row in entries().items()}
_COMMAND_CONTRACT = constants((CURRENT_PROGRAM/'command_interpreter.py').read_text('utf-8-sig'))

def current_symbols():
    from symbol_settings import configured_symbols
    values={}
    for line in (CURRENT_PROGRAM/'config.txt').read_text('utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);values[key.strip()]=value.strip()
    return tuple(configured_symbols(values))

SYMBOLS = current_symbols()
TF_LABELS = {tf:f'{tf[:-1]}'+{'m':'분','h':'시간','d':'일봉'}[tf[-1]] for tf in _COMMAND_CONTRACT['MT5_TIMEFRAMES']}
OZ_TFS = list(_COMMAND_CONTRACT['OZ_BASE_TFS'])
SESSIONS = ['MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK']
_LANGUAGE_CONTRACT = constants((CURRENT_PROGRAM/'command_models.py').read_text('utf-8-sig'))
CONDITIONS = sorted(_LANGUAGE_CONTRACT['CONDITION_KINDS'])
METRICS = set(_LANGUAGE_CONTRACT['TREND_METRIC_FIELDS'])

def profile_module():
    path=CURRENT_PROGRAM/'oz_profiles.py'
    mod=types.ModuleType('_part3_profiles')
    mod.__file__=str(path)
    exec(compile(path.read_text('utf-8-sig'),str(path),'exec'),mod.__dict__)
    return mod

PROFILES = profile_module()

def describe_special(number: int) -> dict:
    """Read-only preset data; no source parsing or numbered executor."""
    name=str(number)
    if name.isdigit():name='SPECIAL'+name
    row=preset_entry(name)
    # The one source of the shipped presets is the SPECIAL folder (installed: 스페셜/기본 or 스페셜/내 전략);
    # generated test strategies live in Part3.
    source=builtin_sources().get(name,'Part1/program/SPECIAL')
    return {'id':name,'name':row['name'],'source':source,'recipe':row['recipe']}


def validate(recipe: dict) -> None:
    from .ai.schema import validate_recipe
    validate_recipe(recipe)


def canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def summary(recipe: dict) -> dict:
    return copy.deepcopy(recipe)
