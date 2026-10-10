"""Small, schema-derived format hints; the wire schema remains authoritative.

This module only describes schema structure. It never rewrites a schema,
conversation, literal enum/const data, or tool definition for the decoder.
"""
from __future__ import annotations

import heapq
import json


INLINE_SCHEMA_CHARS = 4096
MAX_SUMMARY_CHARS = 6000
MAX_STRUCTURE_DEPTH = 4


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _literal_type(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    return 'number'


def _unique(values):
    return list(dict.fromkeys(values))


def _resolve(node, root, references=()):
    """Follow local schema pointers for the outline, without fetching resources."""
    references = set(references)
    while isinstance(node, dict) and isinstance(node.get('$ref'), str):
        reference = node['$ref']
        if not reference.startswith('#/') or reference in references:
            break
        target = root
        try:
            for segment in reference[2:].split('/'):
                name = segment.replace('~1', '/').replace('~0', '~')
                target = target[int(name)] if isinstance(target, list) else target[name]
        except (KeyError, IndexError, TypeError, ValueError):
            break
        if not isinstance(target, dict):
            break
        references.add(reference)
        node = dict(target, **{key: value for key, value in node.items() if key != '$ref'})
    return node, references


def _hint(node, depth=0, root=None):
    node, _ = _resolve(node, root if root is not None else node)
    if not isinstance(node, dict):
        return 'any' if node is True else 'never' if node is False else 'JSON'
    if 'const' in node:
        value = _json(node['const'])
        return 'const ' + value if len(value) <= 160 else _literal_type(node['const']) + ' const'
    if isinstance(node.get('enum'), list):
        value = _json(node['enum'])
        if len(value) <= 200:
            return 'enum ' + value
        return '|'.join(_unique(_literal_type(item) for item in node['enum'])) + ' enum(' + str(len(node['enum'])) + ')'
    for key in ('anyOf', 'oneOf'):
        if isinstance(node.get(key), list):
            return '|'.join(_unique(_hint(item, depth + 1, root) for item in node[key])) if depth < 4 else key
    kind = node.get('type')
    if isinstance(kind, list):
        return '|'.join(str(item) for item in kind)
    if kind == 'array' or 'items' in node:
        return 'array<' + (_hint(node.get('items', True), depth + 1, root) if depth < 4 else 'JSON') + '>'
    if isinstance(kind, str):
        return kind
    if isinstance(node.get('properties'), dict):
        return 'object'
    if '$ref' in node:
        return 'ref ' + str(node['$ref'])
    return 'JSON'


def _variants(nodes, root=None, references=()):
    """Visit schema branches only; values inside enum/const are literal data."""
    for node in nodes:
        node, visited = _resolve(node, root if root is not None else node, references)
        if not isinstance(node, dict):
            continue
        branches = next((node[key] for key in ('anyOf', 'oneOf')
                         if isinstance(node.get(key), list)), None)
        if branches is not None:
            yield from _variants(branches, root, visited)
        else:
            yield node


def schema_prompt(schema):
    """Keep small contracts inline; bound large contracts to a format outline.

    The outline includes field names, types and required fields, without copying
    every repeated variant and constraint. All constraints still go to llama.cpp
    as response_format.schema and to the unchanged final response validator.
    """
    encoded = _json(schema)
    if len(encoded) <= INLINE_SCHEMA_CHARS:
        return encoded
    header = ('응답 필드·구조 요약 (*는 해당 객체의 공통 필수 필드). '
              '변형별 필수 필드와 모든 값 제한은 별도로 적용되는 전체 JSON schema를 따르세요.\n')
    lines = []
    queue = [(0, 0, 0, 0, '$', [schema])]
    sequence = 0
    seen = {}
    used = len(header)
    while queue:
        optional_ancestors, _, depth, _, path, nodes = heapq.heappop(queue)
        objects = [node for node in _variants(nodes, schema) if isinstance(node.get('properties'), dict)]
        if not objects:
            continue
        required = set(objects[0].get('required') or [])
        fields = {}
        for node in objects:
            required.intersection_update(node.get('required') or [])
            for name, child in node['properties'].items():
                fields.setdefault(name, []).append(child)
        shape = 'object {' + '; '.join(
            _json(name) + ('*' if name in required else '') + ': ' +
            '|'.join(_unique(_hint(child, root=schema) for child in children))
            for name, children in fields.items()) + '}'
        if shape in seen:
            line = path + ': ' + seen[shape] + '와 같은 필드 구조'
        else:
            line = path + ': ' + shape
            if depth < MAX_STRUCTURE_DEPTH:
                for name, children in fields.items():
                    nested = []
                    for child in _variants(children, schema):
                        if isinstance(child.get('properties'), dict):
                            nested.append(child)
                        elif isinstance(child.get('items'), dict):
                            nested.append(child['items'])
                    if nested:
                        is_array = all(_hint(child, root=schema).startswith('array<') for child in children)
                        suffix = '[]' if is_array else ''
                        sequence += 1
                        # Required descendants remain ahead of optional metadata,
                        # even when nested below an operation envelope. Optional
                        # array items precede large option dictionaries.
                        heapq.heappush(queue, (optional_ancestors + (name not in required),
                            0 if is_array else 1, depth + 1, sequence,
                            path + '[' + _json(name) + ']' + suffix, nested))
        if used + len(line) + 1 > MAX_SUMMARY_CHARS:
            continue
        if shape not in seen:
            seen[shape] = path
        lines.append(line)
        used += len(line) + 1
    if not lines:
        lines.append('$: ' + _hint(schema, root=schema)[:MAX_SUMMARY_CHARS - len(header) - 4])
    return header + '\n'.join(lines)
