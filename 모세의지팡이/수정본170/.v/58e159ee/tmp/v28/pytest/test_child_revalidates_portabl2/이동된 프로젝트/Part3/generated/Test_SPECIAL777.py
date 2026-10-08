PART3_RECIPE = {'schema_version': 2, 'base': 'AI', 'name': 'Selected generated', 'strategy_intent': {'symbols': ['XAUUSD+'], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS', 'steps': [{'kind': 'MA_STATE', 'tfs': ['1m'], 'ma_family': 'HMA', 'fast_period': 90, 'slow_period': 270, 'side': 'ABOVE', 'bar_state': 'CLOSED'}], 'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}}
PART3_COMPILE_MODE = "CANONICAL"
raise RuntimeError("saved source must not execute")
def register(manager):
    raise RuntimeError("old source register must not execute")
