"""One ordered symbol hint shared by live commands, status and settings.

EA input registration and stored backtest symbols remain separate. Old config
keys are read only here for migration; current readers and saves use SYMBOLS.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

SYMBOL_KEY = 'SYMBOLS'
_LEGACY_KEYS = ('STAFF_ALLOWED_SYMBOLS', 'TARGET_SYMBOLS')
_SETTING = re.compile(r'^([ \t]*)([A-Z][A-Z0-9_+]*)([ \t]*=[ \t]*)([^\r\n]*)(\r?\n)?$')


def _symbols(value):
    return tuple(dict.fromkeys(item.strip() for item in str(value or '').split(',') if item.strip()))


def configured_symbols(config: Mapping) -> tuple[str, ...]:
    """Current list, or an ordered union when importing the two former keys."""
    if SYMBOL_KEY in config:
        return _symbols(config[SYMBOL_KEY])
    return tuple(dict.fromkeys(symbol for key in _LEGACY_KEYS for symbol in _symbols(config.get(key))))


def migrate_symbol_lines(lines: Sequence[str]) -> list[str]:
    """Return canonical config text without changing the source file.

    A later normal settings save writes this text atomically. Unrelated lines,
    comments and line endings are retained; no symbol absent from the old lists
    is introduced.
    """
    keys = {SYMBOL_KEY, *_LEGACY_KEYS}
    values = {}
    for line in lines:
        match = _SETTING.match(line)
        if match and match[2] in keys and match[2] not in values:
            values[match[2]] = match[4].strip()
    if not values:
        return list(lines)
    value = ','.join(configured_symbols(values))
    output = []
    written = False
    for line in lines:
        match = _SETTING.match(line)
        if match and match[2] in keys:
            if not written:
                output.append(match[1] + SYMBOL_KEY + match[3] + value + (match[5] or ''))
                written = True
        else:
            output.append(line)
    return output
