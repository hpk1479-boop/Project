"""Canonical selectors for levels actually produced by strategy_SWEEP."""
LEVEL_GROUPS = {
    'PDH': {'PDH'}, 'PDL': {'PDL'},
    '4H': {'PREV_4H_HIGH','PREV_4H_LOW'},
    '8H': {'PREV_8H_HIGH','PREV_8H_LOW'},
    'PWH': {'PWH'}, 'PWL': {'PWL'},
    'SESSION': {'PREV_SESSION_HIGH','PREV_SESSION_LOW'},
    'SESSION_HIGH': {'PREV_SESSION_HIGH'}, 'SESSION_LOW': {'PREV_SESSION_LOW'},
}
ALL_LEVEL_CODES = set().union(*LEVEL_GROUPS.values())
DEFAULT_LEVEL_SELECTORS = ('PDH','PDL','4H','8H','PWH','PWL','SESSION')

def normalize_level_selectors(value):
    if value is None or value == '': return DEFAULT_LEVEL_SELECTORS
    raw = value.replace(';', ',').split(',') if isinstance(value,str) else list(value) if isinstance(value,(tuple,list,set)) else [value]
    tokens = tuple(dict.fromkeys(str(x).strip().upper() for x in raw if str(x).strip()))
    invalid = [x for x in tokens if x not in LEVEL_GROUPS and x not in ALL_LEVEL_CODES and x != 'ALL']
    if invalid or not tokens:
        raise ValueError('Unsupported SWEEP selector: ' + (', '.join(invalid) or '<empty>'))
    return DEFAULT_LEVEL_SELECTORS if 'ALL' in tokens else tokens

def selector_codes(selectors):
    codes = set()
    for token in normalize_level_selectors(selectors):
        codes.update(LEVEL_GROUPS.get(token, {token}))
    return codes
