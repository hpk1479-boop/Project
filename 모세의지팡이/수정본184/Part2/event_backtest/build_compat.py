"""Explicitly approved EA recording compatibility; never inferred from captures."""
import json
import re

from .settings import ROOT


COMPATIBILITY_FILE = ROOT / 'Part2/ea_build_compatibility.json'
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def compatible_hashes(ea_build_hash):
    """Return the approved build group, or only the requested unknown build."""
    if not isinstance(ea_build_hash, str) or not ea_build_hash:
        return frozenset((ea_build_hash,))
    if not COMPATIBILITY_FILE.is_file():
        return frozenset((ea_build_hash,))
    data = json.loads(COMPATIBILITY_FILE.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('groups'), list):
        raise ValueError('EA 녹화 호환 목록 형식 오류')
    seen = set()
    for group in data['groups']:
        hashes = group.get('ea_build_hashes')
        if not isinstance(hashes, list) or len(hashes) < 2 or any(not isinstance(h, str) or not _HASH.fullmatch(h) for h in hashes):
            raise ValueError('EA 녹화 호환 해시 형식 오류')
        if len(set(hashes)) != len(hashes) or seen.intersection(hashes):
            raise ValueError('EA 녹화 호환 해시 중복')
        seen.update(hashes)
        if ea_build_hash in hashes:
            return frozenset(hashes)
    return frozenset((ea_build_hash,))


def compatible_pair(left, right):
    return right in compatible_hashes(left)


def compatible_group(hashes):
    """True when every actual capture build belongs to one approved group."""
    values = set(hashes)
    return not values or all(value in compatible_hashes(next(iter(values))) for value in values)
