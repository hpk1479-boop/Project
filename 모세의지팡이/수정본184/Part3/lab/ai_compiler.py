"""Trusted Recipe lowering and independent modules using the common runtime."""
from __future__ import annotations


def execution_plan(meaning):
    from .ai.schema import contract_context
    from strategy_recipe.contract import execution_plan as lower
    return lower(meaning, contract_context())


def compile_ai(recipe, filename):
    plan = execution_plan(recipe['strategy_intent'])
    stem = filename[:-3]
    source = ('# ' + filename + '\n'
        '# Recipe v2 data; the common runtime owns evaluation and lifecycle.\n'
        'from strategy_recipe.port import IntentPort\n\n'
        'from strategy_recipe.registry import oz_declarations\n\n'
        'PART3_STRATEGY_ID = ' + repr(stem) + '\n'
        'PART3_RECIPE = ' + repr(recipe) + '\n'
        '_INTENT_PLAN = ' + repr(plan['meaning']) + '\n\n'
        'recipe = PART3_RECIPE\n'
        'OZ_DECLARATIONS = oz_declarations(_INTENT_PLAN)\n\n'
        'def register(manager):\n'
        "    port = IntentPort(manager, _INTENT_PLAN, PART3_RECIPE['name'], PART3_STRATEGY_ID)\n"
        '    port.install()\n')
    compile(source, filename, 'exec')
    return source
