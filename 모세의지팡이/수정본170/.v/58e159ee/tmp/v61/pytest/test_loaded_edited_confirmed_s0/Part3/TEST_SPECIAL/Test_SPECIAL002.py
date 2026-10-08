# Test_SPECIAL002.py
# Recipe v2 data; the common runtime owns evaluation and lifecycle.
from strategy_recipe.port import IntentPort

from strategy_recipe.registry import oz_declarations

PART3_STRATEGY_ID = 'Test_SPECIAL002'
PART3_RECIPE = {'schema_version': 2, 'base': 'AI', 'name': 'AI 자연어 전략', 'description': '사용자 연구 템플릿을 새 전략 초안으로 불러왔습니다. 원본은 변경하지 않습니다.', 'symbols': ['XAUUSD+', 'NAS100'], 'strategy_intent': {'direction': 'LONG', 'symbols': ['XAUUSD+', 'NAS100'], 'steps': [{'kind': 'TREND', 'tfs': ['1h'], 'direction': 'LONG', 'bar_state': 'CLOSED'}, {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER', 'direction': 'LONG'}], 'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None, 'final_window_sec': 600, 'persistent': True, 'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}, 'time_filters': ['MAIN_ASIA', 'MAIN_LONDON'], 'final_time_filters': {'MAIN_NEWYORK': {'enabled': True, 'start': '20:00', 'end': '23:00'}}}}
_INTENT_PLAN = {'direction': 'LONG', 'symbols': ['XAUUSD+', 'NAS100'], 'steps': [{'kind': 'TREND', 'tfs': ['1h'], 'direction': 'LONG', 'bar_state': 'CLOSED', '_resolved_direction': 'LONG', '_event_mode': False}, {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER', 'direction': 'LONG', '_resolved_direction': 'LONG', '_event_mode': False}], 'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None, 'final_window_sec': 600, 'persistent': True, 'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}, 'time_filters': ['MAIN_ASIA', 'MAIN_LONDON'], 'final_time_filters': {'MAIN_NEWYORK': {'enabled': True, 'start': '20:00', 'end': '23:00'}}}

recipe = PART3_RECIPE
OZ_DECLARATIONS = oz_declarations(_INTENT_PLAN)

def register(manager):
    port = IntentPort(manager, _INTENT_PLAN, PART3_RECIPE['name'], PART3_STRATEGY_ID)
    port.install()
