"""Compare persisted evidence, respecting OZ's explicitly tagged set type.

S0's _observed_encode wrote sets in Python hash iteration order. Preserve the
original bytes and raw hashes; normalize only tagged set/frozenset order during
read-only comparison. Lists, tuples, dictionary-entry order and values stay exact.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .contracts import json_bytes


def ordered_sets(value):
    if isinstance(value, list):
        return [ordered_sets(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: ordered_sets(v) for k, v in value.items()}
    if (set(out) == {'type', 'value'} and out['type'] in ('set', 'frozenset')
            and isinstance(out['value'], list)):
        # Sort, never deduplicate: malformed repeated elements remain detectable.
        out['value'] = sorted(out['value'], key=json_bytes)
    return out


def evidence_compare(expected, candidate):
    fields = {}
    for name in expected.keys() | candidate.keys():
        if name not in expected or name not in candidate:
            fields[name] = False
        elif name == 'oz_state':
            fields[name] = json_bytes(ordered_sets(expected[name])) == json_bytes(ordered_sets(candidate[name]))
        else:
            fields[name] = json_bytes(expected[name]) == json_bytes(candidate[name])
    return {'equal': all(fields.values()), 'fields': fields,
            'raw_hash_equal': json_bytes(expected) == json_bytes(candidate),
            'expected_raw_sha256': hashlib.sha256(json_bytes(expected)).hexdigest(),
            'candidate_raw_sha256': hashlib.sha256(json_bytes(candidate)).hexdigest(),
            'comparison_policy': 'Only explicitly tagged OZ set/frozenset element order is unordered'}


def compare_files(expected_path, candidate_path):
    expected = json.loads(Path(expected_path).read_text('utf-8'))['evidence']
    candidate = json.loads(Path(candidate_path).read_text('utf-8'))['evidence']
    return evidence_compare(expected, candidate)
