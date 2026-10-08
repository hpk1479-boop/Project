"""Validate AI Recipe v2 and generate an independent Test_SPECIAL module."""
from __future__ import annotations
import copy
import re
from . import catalog

FILE_RE = re.compile(r'^Test_SPECIAL([0-9]{3})\.py$')

def compile_recipe(recipe: dict, filename: str = 'Test_SPECIAL008.py') -> str:
    checked = copy.deepcopy(recipe)
    catalog.validate(checked)
    match = FILE_RE.fullmatch(filename)
    if not match or int(match[1]) == 0:
        raise ValueError('생성 파일명은 Test_SPECIAL001.py~Test_SPECIAL999.py 형식입니다.')
    from .ai_compiler import compile_ai
    return compile_ai(checked, filename)
