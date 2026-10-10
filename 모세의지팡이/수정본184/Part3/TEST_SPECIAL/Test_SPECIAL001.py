# Test_SPECIAL001.py
# Recipe v2 data; the common runtime owns evaluation and lifecycle.
from strategy_recipe.port import IntentPort

from strategy_recipe.registry import oz_declarations

PART3_STRATEGY_ID = 'Test_SPECIAL001'
PART3_RECIPE = {'schema_version': 2, 'base': 'AI', 'name': 'AI 자연어 전략', 'description': '15분 기본더블비 방향에 따른 3분 이하(1분, 2분, 3분) 올존 감시 전략을 생성했습니다.', 'symbols': ['XAUUSD+'], 'strategy_intent': {'direction': 'BOTH', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m']}], 'order_mode': 'SIMULTANEOUS', 'final': {'kind': 'OZ', 'tfs': ['1m', '2m', '3m'], 'direction': 'SAME_AS_PREVIOUS_DIRECTION', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}}
_INTENT_PLAN = {'direction': 'BOTH', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m'], '_resolved_direction': 'BOTH', '_event_mode': False}], 'order_mode': 'SIMULTANEOUS', 'final': {'kind': 'OZ', 'tfs': ['1m', '2m', '3m'], 'direction': 'BOTH', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}

recipe = PART3_RECIPE
OZ_DECLARATIONS = oz_declarations(_INTENT_PLAN)

def register(manager):
    port = IntentPort(manager, _INTENT_PLAN, PART3_RECIPE['name'], PART3_STRATEGY_ID)
    port.install()
