# Test_SPECIAL003.py
# Recipe v2 data; the common runtime owns evaluation and lifecycle.
from strategy_recipe.port import IntentPort

from strategy_recipe.registry import oz_declarations

PART3_STRATEGY_ID = 'Test_SPECIAL003'
PART3_RECIPE = {'schema_version': 2, 'base': 'AI', 'name': 'AI 자연어 전략', 'description': '15분 상승추세 조건 속의 1분 올존 백테스트를 설정합니다.', 'symbols': ['XAUUSD+'], 'strategy_intent': {'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}], 'order_mode': 'SIMULTANEOUS', 'final': {'kind': 'OZ', 'tfs': ['1m'], 'direction': 'LONG', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}, 'persistent': True}}
_INTENT_PLAN = {'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG', '_resolved_direction': 'LONG', '_event_mode': False}], 'order_mode': 'SIMULTANEOUS', 'final': {'kind': 'OZ', 'tfs': ['1m'], 'direction': 'LONG', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}, 'persistent': True}

recipe = PART3_RECIPE
OZ_DECLARATIONS = oz_declarations(_INTENT_PLAN)

def register(manager):
    port = IntentPort(manager, _INTENT_PLAN, PART3_RECIPE['name'], PART3_STRATEGY_ID)
    port.install()
