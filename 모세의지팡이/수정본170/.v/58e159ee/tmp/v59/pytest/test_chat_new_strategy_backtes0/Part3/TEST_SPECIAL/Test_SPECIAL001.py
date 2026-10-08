# Test_SPECIAL001.py
# Recipe v2 data; the common runtime owns evaluation and lifecycle.
from strategy_recipe.port import IntentPort

from strategy_recipe.registry import oz_declarations

PART3_STRATEGY_ID = 'Test_SPECIAL001'
PART3_RECIPE = {'schema_version': 2, 'base': 'AI', 'name': 'AI 자연어 전략', 'description': '조건 충족 시 브레이커 감시', 'symbols': ['XAUUSD+'], 'strategy_intent': {'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG', 'bar_state': 'CLOSED'}, {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER', 'direction': 'LONG'}], 'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None, 'final_window_sec': 600, 'persistent': True, 'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}}}
_INTENT_PLAN = {'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG', 'bar_state': 'CLOSED', '_resolved_direction': 'LONG', '_event_mode': False}, {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER', 'direction': 'LONG', '_resolved_direction': 'LONG', '_event_mode': False}], 'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None, 'final_window_sec': 600, 'persistent': True, 'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}}

recipe = PART3_RECIPE
OZ_DECLARATIONS = oz_declarations(_INTENT_PLAN)

def register(manager):
    port = IntentPort(manager, _INTENT_PLAN, PART3_RECIPE['name'], PART3_STRATEGY_ID)
    port.install()
