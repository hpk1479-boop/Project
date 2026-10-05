"""One complete external MOSES contract for every remote provider.

The central policy and original schema remain authoritative. Transport shares
identical schema definitions without omitting types, values or constraints.
No provider/model/strategy name selects a separate language contract.
"""
from __future__ import annotations

import json
import re
import unicodedata


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _request_text(messages):
    for row in reversed(messages):
        if row.get('role') != 'user':
            continue
        content = row.get('content') or ''
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            value = content
        query = (value.get('message') or value.get('request') or '') if isinstance(value, dict) else value
        if isinstance(query, str) and query:
            return query
    return ''


def language_reference(text, settings=None, *, max_chars=900):
    """Retrieve complete related public terms, never classify or execute text.

    Canonical condition topics take priority. Command phrase replacement rules
    and historical macro descriptors are not semantic AI vocabulary.
    The bound selects whole entries; it never truncates a term or its meaning.
    """
    from moses_language import public_vocabulary
    from .security import _text, _private_field, secret_values
    if not isinstance(text, str) or not text.strip() or max_chars <= 2:
        return {}
    public = public_vocabulary()
    secrets = secret_values(settings or {})
    source = unicodedata.normalize('NFKC', text).casefold()
    compact = re.sub(r'\s+', '', source)

    def matches(word):
        normalized = unicodedata.normalize('NFKC', word).casefold()
        if word.isascii():
            phrase = r'\s*'.join(re.escape(part) for part in normalized.split())
            return re.search(r'(?<![a-z_])' + phrase + r'(?![a-z_])', source) is not None
        return re.sub(r'\s+', '', normalized) in compact

    sections = [('conditions', public['condition_terms'])]
    sections.extend((name, public['aliases'][name]) for name in (
        'symbols', 'oz_direction', 'ma_family', 'cross', 'wonbi_side', 'percentile_side'))
    sections.append(('topics', public['concepts']))
    result, consumed = {}, set()
    for section, terms in sections:
        for canonical, value in terms.items():
            if _private_field(canonical):
                continue
            aliases = value.get('aliases', []) if isinstance(value, dict) else value
            matched = []
            for alias in aliases:
                words = alias if isinstance(alias, list) else [alias]
                if not all(isinstance(word, str) and matches(word) for word in words):
                    continue
                signature = tuple(re.sub(r'\s+', '', word.casefold()) for word in words)
                if signature in consumed:
                    continue
                safe = [_text(word, secrets) for word in words]
                if all(safe):
                    matched.append(safe if isinstance(alias, list) else safe[0])
            if not matched:
                continue
            safe_name = _text(canonical, secrets)
            if not safe_name:
                continue
            candidate = {**result, section: {**result.get(section, {}), safe_name: matched}}
            if len(_json(candidate)) > max_chars:
                continue
            result = candidate
            consumed.update(tuple(re.sub(r'\s+', '', word.casefold()) for word in
                                  (alias if isinstance(alias, list) else [alias])) for alias in matched)
    return result


_LANGUAGE_REFERENCE_PREFIX = 'MOSES language dictionary reference (terms only; current schema/rules remain authoritative): '


def language_reference_message(messages, settings=None):
    terms = language_reference(_request_text(messages), settings)
    return {'role': 'user', 'content': _LANGUAGE_REFERENCE_PREFIX + _json(terms)} if terms else None


def attach_language_reference(messages, settings=None):
    """Attach the same bounded dictionary view to local AI without filtering tools."""
    reference = language_reference_message(messages, settings)
    if reference is None:
        return messages
    # Keep existing instructions and conversation untouched. Reference data is
    # separate from the final user request and does not become a new strategy.
    position = 1 if messages and messages[0].get('role') == 'system' else 0
    return [*messages[:position], reference, *messages[position:]]


def object_field_guide(schema):
    """Repeat outer object fields directly from the unshared source schema.

    This is a shallow reading aid, not an instance, a decoder schema or a
    second contract. Detailed constraints and referenced definitions stay in
    the complete schema below. No response values are supplied or repaired.
    """
    def objects(node):
        if not isinstance(node, dict):
            return
        if node.get('type') == 'object' and isinstance(node.get('properties'), dict):
            yield node
        else:
            for union in ('anyOf', 'oneOf'):
                for branch in node.get(union, []):
                    yield from objects(branch)

    def types(node):
        if not isinstance(node, dict):
            return []
        value = node.get('type', [])
        result = [value] if isinstance(value, str) else list(value)
        for union in ('anyOf', 'oneOf'):
            for branch in node.get(union, []):
                result.extend(kind for kind in types(branch) if kind not in result)
        return result

    result = []

    def describe(node, path, selectors, depth):
        fields = node['properties']
        required = list(node.get('required', []))
        selectors = {**selectors, **{path + '.' + name: field['const']
            for name, field in fields.items()
            if name in required and isinstance(field, dict) and 'const' in field}}
        entry = {'path': path, 'required': required}
        if selectors:
            entry['when'] = selectors
        if node.get('additionalProperties') is False:
            entry['allowed_fields'] = list(fields)
        entry['field_types'] = {name: types(field) for name, field in fields.items() if types(field)}
        result.append(entry)
        if depth:
            for name in required:
                for child in objects(fields.get(name)):
                    describe(child, path + '.' + name, selectors, depth - 1)

    for node in objects(schema):
        describe(node, '$', {}, 1)
    return result


def prepare(messages, tools, schema, settings):
    """Reapply the external allowlist before compacting and adding references."""
    from .security import external_payload, _text, secret_values
    from .lora_worker import reply_schema
    from .reference_pack import load_pack, select_examples
    messages, tools, schema = external_payload(messages, tools, schema, settings=settings)
    system = json.loads(messages[0]['content'])
    purpose = system['purpose']
    contract = system['moses_contract']
    # Format derives allowed fields, values and condition parameters from the
    # original schema. Their duplicate vocabulary lists add no information.
    vocabulary = contract.get('vocabulary', {})
    semantic = {key: value for key, value in vocabulary.items() if key in ('ma', 'sequential', 'candle_state')}
    expected = reply_schema(schema, tools)
    query = _request_text(messages[1:])
    from .schema_contract import contract_schema
    # Share repeated JSON Schema definitions without losing required fields,
    # enums, per-kind constraints or the distinction between enum and array.
    # The independent original schema remains authoritative for parse_reply.
    # The central external policy carries language meanings and operation
    # routing as well as permissions. Do not replace it with a shorter second
    # policy: that loses e.g. TREND meaning and when BACKTEST is appropriate.
    # Both remote transports receive this same authoritative instruction.
    instruction = system['instructions'] + (
        ' Strategy output top-level keys are supported, intent, interpretation, needs_clarification, '
        'clarification_question, message_ko. Do not wrap strategy in kind/strategy/plan. '
        'Only BACKTEST and CHAT use the kind/strategy/plan/message_ko envelope. '
        'Use final for the requested final action and steps for its prerequisite conditions; '
        'do not duplicate the final action as an extra prerequisite unless the user requests that earlier event. '
        'mode=strategy cannot return CHAT. Return only meaningful optional fields: '
        'null is permitted only where the schema explicitly permits null.')
    if purpose == 'watch':
        instruction = system['instructions']
    static = [instruction]
    guide = object_field_guide(expected)
    if guide:
        static.append('Outer JSON object field guide (derived from the original schema; '
            'not a response example): ' + _json(guide) + '\n'
            'Include every required field at its own object path, even when its type permits null. '
            'Nested required fields belong inside that object. For closed objects use only '
            'allowed_fields. Select field values from the request and complete schema; '
            'this guide does not supply values or override any constraint.')
    static.extend([
              'Return one instance of this JSON Schema, not the schema itself. '
              'Choose individual enum values. Omit unused optional fields instead of setting them to null. '
              'JSON response schema: ' + _json(contract_schema(expected)),
              'Conditions and final action have different roles: steps contain only preceding/setup '
              'conditions; final contains the requested terminal notification or monitoring action. '
              'An OZ_ALERT in steps means an additional earlier OZ event. Do not add it just because '
              'final is OZ. A state condition is not a new event; use SIMULTANEOUS for a state gating '
              'a final action unless the user explicitly requests a sequential chain.'])
    if semantic:
        static.append('Language conventions: ' + _json(semantic))
    pack = load_pack()
    secrets = secret_values(settings)
    if purpose != 'watch':
        static.append('MOSES rules: ' + _json([_text(rule, secrets) for rule in pack['core_rules']]))
    dynamic = []
    reference = language_reference_message(messages[1:], settings)
    if reference is not None:
        dynamic.append(reference)
    if contract.get('presets'):
        dynamic.append({'role': 'user', 'content': 'Available presets: ' + _json(contract['presets'])})
    if contract.get('selection'):
        dynamic.append({'role': 'user', 'content': 'Current selection: ' + _json(contract['selection'])})
    if query and purpose != 'watch':
        examples = select_examples(query, limit=2, max_chars=900)
        if examples:
            dynamic.append({'role': 'user', 'content': 'Reference examples (not new requests): ' +
                            _json([{'input': _text(e['input'], secrets), 'meaning': _text(e['meaning'], secrets)}
                                   for e in examples])})
    if tools:
        static.append('Read-only tools: ' + _json(tools))
    return [{'role': 'system', 'content': '\n'.join(static)}, *dynamic, *messages[1:]], tools, schema

