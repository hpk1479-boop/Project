"""Headless module names and connection states shared by web diagnostics.

Connection readiness is separate from watch activity reported in logs.
"""
from module_diagnostics import DELAY_ALERT_SECONDS

DISPLAY_NAMES = {'STAFF':'STAFF','ENGINE':'EVENT','OZ':'OZ',
                 'SWEEP':'SWEEP','FVG':'FVG','INDICATOR':'INDICATOR',
                 'WATCH':'WATCH','KIM':'김비서'}
MAIN_MODULES = ('STAFF','ENGINE','OZ','SWEEP','FVG','INDICATOR','WATCH','KIM')
def preset_modules():
    from strategy_recipe.registry import list_presets
    return list_presets('Part1')

def display_special_state(module,data,error=None):
    if error is not None or not data or module not in data.get('enabled_specials',()):return '연결 대기'
    row=data.get('modules',{}).get(module)
    if row is None:return '연결 대기'
    return '오류' if row.get('status') in ('지연','오류·끊김','끊김','오류') else '연결중'

def display_state(module,data,error=None):
    """Connection readiness is independent of registered or active watches."""
    if error is not None or not data:return '연결 대기'
    if data.get('engine_state') in ('starting','stopping','stopped'):return '연결 대기'
    if data.get('engine_state')=='failed' and module in ('STAFF','ENGINE'):return '오류'
    if module not in MAIN_MODULES:return display_special_state(module,data)
    row=data.get('modules',{}).get(module)
    if row is None:return '연결 대기'
    if module in ('KIM','WATCH'):
        if not data.get('telegram_attempted',False):return '연결 대기'
        if not data.get('telegram_connected',False):return '오류'
    if row.get('status') in ('오류·끊김','끊김','오류') or (row.get('status')=='지연' and module!='ENGINE'):
        return '오류'
    # A one-second engine wait is normal; a lasting one makes every alert late.
    if module=='ENGINE' and float(row.get('delay_seconds') or 0)>=DELAY_ALERT_SECONDS:
        return '오류'
    if module=='KIM':return '연결중'
    if not data.get('pipe_connected',False):
        return '오류' if module=='STAFF' and data.get('pipe_seen',False) else '연결 대기'
    return '연결중'

def display_name(module):
    if module in DISPLAY_NAMES:return DISPLAY_NAMES[module]
    from strategy_recipe.registry import preset_entry
    try:return preset_entry(module).get('name',module)
    except ValueError:return module

