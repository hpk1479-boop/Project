"""Lossless JSON Schema sharing for prompt transport; validation stays original."""
from collections import Counter
import copy
import json
from urllib.parse import unquote


_MAPS = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'}
_LISTS = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
_SINGLE = {'items', 'additionalItems', 'additionalProperties', 'unevaluatedItems',
           'unevaluatedProperties', 'contains', 'propertyNames', 'not', 'if', 'then',
           'else', 'contentSchema'}


def _children(node, path=()):
    for key, value in node.items():
        if key in _MAPS or key == 'dependencies':
            if isinstance(value, dict):
                for name, child in value.items():
                    if isinstance(child, dict):
                        yield (key, name), child, (*path, key, name)
        elif key in _LISTS or (key == 'items' and isinstance(value, list)):
            if isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, dict):
                        yield (key, index), child, (*path, key, str(index))
        elif key in _SINGLE and isinstance(value, dict):
            yield (key,), value, (*path, key)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def compact_schema(schema):
    """Share identical schema nodes, preserving pointer targets and all literals.

    ID/anchor scopes are conservatively left intact. Only schema positions are
    visited, so enum/const/default objects are never mistaken for schema nodes.
    """
    original = copy.deepcopy(schema)
    if not isinstance(original, dict):
        return original
    nodes = []

    def walk(node, path=()):
        nodes.append((node, path))
        for _, child, child_path in _children(node, path):
            walk(child, child_path)

    walk(original)
    protected = {()}
    for node, _ in nodes:
        if any(key in node for key in ('$id', 'id', '$anchor', '$dynamicAnchor', '$dynamicRef', '$recursiveAnchor', '$recursiveRef')):
            return original
        ref = node.get('$ref')
        if isinstance(ref, str):
            if ref != '#' and not ref.startswith('#/'):
                return original
            parts = tuple(part.replace('~1', '/').replace('~0', '~') for part in unquote(ref[2:]).split('/')) if ref != '#' else ()
            protected.update(parts[:index] for index in range(len(parts) + 1))

    # reply_schema lifts definitions to its root; keep just that identical copy.
    root_defs = original.get('$defs')
    if isinstance(root_defs, dict):
        for node, path in nodes:
            if path and node.get('$defs') == root_defs and (*path, '$defs') not in protected:
                del node['$defs']

    nodes = []
    walk(original)
    counts = Counter(_encode(node) for node, path in nodes if path not in protected)
    shared, aliases = {}, {}
    existing_names = set(original.get('$defs', {}))

    def build(node, path=()):
        signature = _encode(node)
        eligible = path not in protected and counts[signature] > 1
        if eligible and signature in aliases:
            return dict(aliases[signature])
        result = copy.deepcopy(node)
        for position, child, child_path in _children(node, path):
            target = result
            for key in position[:-1]:
                target = target[key]
            target[position[-1]] = build(child, child_path)
        if eligible:
            index = len(shared)
            name = '_s' + str(index)
            while name in existing_names or name in shared:
                index += 1
                name = '_s' + str(index)
            reference = {'$ref': '#/$defs/' + name}
            size, count = len(_encode(result)), counts[signature]
            if size * (count - 1) > len(_encode(reference)) * count + len(name) + 4:
                shared[name] = result
                aliases[signature] = reference
                return dict(reference)
        return result

    result = build(original)
    if shared:
        result.setdefault('$defs', {}).update(shared)
    return result
