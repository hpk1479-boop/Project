"""Exact JSON Schema sharing for external response contracts.

Only transport representation changes. The original schema remains the final
validator, and every property, required field and constraint stays in this
Draft 2020-12 representation. Closed union objects share an open base, while
``unevaluatedProperties`` closes each complete base-plus-variant object.
"""
from __future__ import annotations

import copy
import json
from urllib.parse import unquote

from .schema_transport import compact_schema


_MAPS = {'properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'}
_LISTS = {'allOf', 'anyOf', 'oneOf', 'prefixItems'}
_SINGLE = {'items', 'additionalItems', 'additionalProperties', 'unevaluatedItems',
           'unevaluatedProperties', 'contains', 'propertyNames', 'not', 'if',
           'then', 'else', 'contentSchema'}
_SCOPES = {'$id', 'id', '$anchor', '$dynamicAnchor', '$dynamicRef',
           '$recursiveAnchor', '$recursiveRef'}
# Do not broaden closure through conditional or pattern-property annotations.
# These simple objects are sufficient for the current discriminator contracts.
_CLOSED_KEYS = {'type', 'properties', 'required', 'additionalProperties',
                'minProperties', 'maxProperties', 'title', 'description', '$comment'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


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


def _simple_closed(node):
    return (isinstance(node, dict) and node.get('type') == 'object'
            and node.get('additionalProperties') is False
            and isinstance(node.get('properties'), dict)
            and set(node) <= _CLOSED_KEYS)


def contract_schema(schema):
    """Return a smaller, equivalent Draft 2020-12 transport schema.

    Union branch order and ``oneOf`` cardinality are unchanged. Literal values
    are never walked as schemas. Existing reference paths are kept in place;
    resource IDs, anchors, dynamic references and external references disable
    this relocation pass. ``compact_schema`` still safely shares exact nodes.
    """
    root = copy.deepcopy(schema)
    if not isinstance(root, dict):
        return root
    nodes = []

    def collect(node, path=()):
        nodes.append((node, path))
        for _, child, child_path in _children(node, path):
            collect(child, child_path)

    collect(root)
    protected = set()
    for node, _ in nodes:
        if '$schema' in node and node['$schema'] != 'https://json-schema.org/draft/2020-12/schema':
            return compact_schema(root)
        if set(node) & _SCOPES:
            return compact_schema(root)
        reference = node.get('$ref')
        if isinstance(reference, str):
            if reference != '#' and not reference.startswith('#/'):
                return compact_schema(root)
            parts = (() if reference == '#' else tuple(
                part.replace('~1', '/').replace('~0', '~')
                for part in unquote(reference[2:]).split('/')))
            protected.update(parts[:index] for index in range(len(parts) + 1))

    shared = {}
    signatures = {}
    names = set(root.get('$defs', {}))

    def shared_name(base):
        signature = _json(base)
        if signature in signatures:
            return signatures[signature], False
        index = len(shared)
        name = '_b' + str(index)
        while name in names or name in shared:
            index += 1
            name = '_b' + str(index)
        return name, True

    def factor(node, path):
        for union in ('anyOf', 'oneOf'):
            variants = node.get(union)
            if not isinstance(variants, list):
                continue
            positions = [index for index, variant in enumerate(variants)
                         if _simple_closed(variant)
                         and (*path, union, str(index)) not in protected]
            if len(positions) < 2:
                continue
            first = variants[positions[0]]
            common = dict(first['properties'])
            common_required = set(first.get('required', []))
            for index in positions[1:]:
                candidate = variants[index]
                common = {name: child for name, child in common.items()
                          if name in candidate['properties']
                          and candidate['properties'][name] == child}
                common_required.intersection_update(candidate.get('required', []))
            if not common:
                continue
            # A discriminator can be required in the base while each variant
            # retains its own literal property schema and evaluation annotation.
            base = {'type': 'object', 'properties': common}
            ordered_required = [name for name in first.get('required', [])
                                if name in common_required]
            if ordered_required:
                base['required'] = ordered_required
            name, new = shared_name(base)
            replacements = {}
            for index in positions:
                variant = variants[index]
                rest = {key: copy.deepcopy(value) for key, value in variant.items()
                        if key not in {'type', 'properties', 'required', 'additionalProperties'}}
                fields = {field: copy.deepcopy(value)
                          for field, value in variant['properties'].items() if field not in common}
                required = [field for field in variant.get('required', [])
                            if field not in common_required]
                if fields:
                    rest['properties'] = fields
                if required:
                    rest['required'] = required
                replacements[index] = {'allOf': [{'$ref': '#/$defs/' + name}, rest],
                                       'unevaluatedProperties': False}
            before = sum(len(_json(variants[index])) for index in positions)
            after = sum(len(_json(value)) for value in replacements.values())
            if new:
                after += len(_json(base)) + len(name) + 4
            if after >= before:
                continue
            if new:
                shared[name] = base
                signatures[_json(base)] = name
            for index, replacement in replacements.items():
                variants[index] = replacement

    def visit(node, path=()):
        # Children first so identical nested structures stay identical when
        # common fields are compared. Root definitions are appended only later.
        for _, child, child_path in list(_children(node, path)):
            visit(child, child_path)
        factor(node, path)

    visit(root)
    if shared:
        root.setdefault('$defs', {}).update(shared)
    result = compact_schema(root)
    original_compact = compact_schema(schema)
    # Sharing a short object can cost more than repeated-node compaction. Keep
    # the smallest equivalent representation rather than forcing a rewrite.
    return result if len(_json(result)) < len(_json(original_compact)) else original_compact
