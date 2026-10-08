"""Read-only editor contracts and field diagnostics over the existing validators."""
from __future__ import annotations

import copy

from . import backtest_commands as commands
from .schema import output_schema, validate_intent, recipe_from_intent, sweep_codes
from .research_backtest import plan_schema, validate_plan_shape


READONLY_METADATA = (('interpretation', 'symbol_source'),)


def contract(snapshot, strategy=None):
    schema = output_schema()
    symbols = list(schema['properties']['interpretation']['anyOf'][0]['properties']['symbols']['items']['enum'])
    options = snapshot.get('options') or {}
    stored = list(dict.fromkeys(row for row in options.get('symbols', []) if isinstance(row, str) and row))
    from sweep_selectors import ALL_LEVEL_CODES, LEVEL_GROUPS
    selectors = sorted({'ALL', *ALL_LEVEL_CODES, *LEVEL_GROUPS})
    choices = {kind: list(selectors) for kind in ('PRICE_LEVEL', 'LIQUIDITY_LEVEL', 'EXTERNAL_LIQUIDITY_TOUCH')}
    choices['PRICE_LEVEL'].insert(0, 'DAY_OPEN')
    def current_levels(value):
        if isinstance(value, dict):
            kind, level = value.get('kind'), value.get('level')
            if kind in choices and isinstance(level, str) and level not in choices[kind]:
                try:
                    valid = bool(sweep_codes(level))
                except (ValueError, TypeError):
                    valid = False
                if valid:
                    choices[kind].append(level)
            for child in value.values():
                current_levels(child)
        elif isinstance(value, list):
            for child in value:
                current_levels(child)
    current_levels(strategy)
    recipes, draft = _virtual_frames(options, strategy)
    return {'schema': schema, 'plan_schema': plan_schema(), 'symbols': symbols,
        'today': snapshot.get('today'), 'readonly_metadata': [list(path) for path in READONLY_METADATA],
        'options': {'symbols': symbols, 'level_choices': choices,
            'timeframes': list(schema['$defs']['step']['anyOf'][0]['properties']['tfs']['items']['enum']),
            'oz_timeframes': list(schema['properties']['interpretation']['anyOf'][0]['properties']['final']['properties']['tfs']['items']['enum']),
            'backtest_symbols': stored, 'modes': list(commands.MODES),
            'result_modes': list(commands.RESULT_MODES), 'today': snapshot.get('today'),
            'virtual_frames': recipes, 'virtual_draft_frames': draft}}


def _virtual_frames(options, strategy):
    """The frames a virtual-entry summary names instead of SIGNAL and ENV.

    Each recipe's own and environment frames on every base frame its test can take ({name: {base: frames}}),
    and those of the strategy being written, for its draft backtest (None while they cannot be read).
    """
    commands.virtual_contract()
    from event_backtest.virtual_defaults import on_base
    recipes = {}
    for name, profile in (options.get('virtual_profiles') or {}).items():
        try:
            own = profile['timeframes'] if len(profile['timeframes']) == 1 else []
            recipes[name] = {tf: {key: on_base(profile, tf)[key] for key in ('timeframes', 'env_timeframes')}
                             for tf in dict.fromkeys(['SIGNAL', *own, *profile.get('bases', ())])}
        except (KeyError, TypeError, ValueError):
            continue
    return recipes, draft_frames(strategy)


def draft_frames(strategy):
    """Frames for the current server-checked draft; unfinished drafts have no numeric labels."""
    commands.virtual_contract()
    from event_backtest.virtual_defaults import strategy_frames
    try:
        return strategy_frames(strategy['interpretation']) if isinstance(strategy, dict) else None
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def _type_matches(value, kind):
    kinds = kind if isinstance(kind, list) else [kind]
    return any({'null': value is None, 'object': isinstance(value, dict),
        'array': isinstance(value, list), 'string': isinstance(value, str),
        'number': type(value) in (int, float), 'integer': type(value) is int,
        'boolean': type(value) is bool}.get(name, False) for name in kinds)


def _diagnostic_schema(node, value, root):
    """Select the existing discriminator branch only to localize union errors."""
    node = copy.deepcopy(node)
    if '$ref' in node and node['$ref'].startswith('#/'):
        target = root
        for segment in node['$ref'][2:].split('/'):
            target = target[segment.replace('~1', '/').replace('~0', '~')]
        return _diagnostic_schema(target, value, root)
    for union in ('anyOf', 'oneOf'):
        variants = node.get(union)
        if not variants:
            continue
        choices = [row for row in variants if 'type' not in row or _type_matches(value, row['type'])]
        if isinstance(value, dict) and 'kind' in value:
            exact = [row for row in choices if (row.get('properties') or {}).get('kind', {}).get('const') == value['kind']]
            if exact:
                choices = exact
        if len(choices) == 1:
            return _diagnostic_schema(choices[0], value, root)
    if isinstance(value, dict):
        node['properties'] = {key: _diagnostic_schema(child, value.get(key), root)
            for key, child in node.get('properties', {}).items()}
    elif isinstance(value, list) and isinstance(node.get('items'), dict):
        item_contract = node.pop('items')
        # Each row may have a different condition kind; preserve its own branch.
        node['prefixItems'] = [_diagnostic_schema(item_contract, row, root) for row in value]
    return node


def schema_errors(value, schema, prefix):
    from jsonschema import Draft202012Validator
    native = list(Draft202012Validator(schema).iter_errors(value))
    if not native:
        return []
    focused = _diagnostic_schema(schema, value, schema)
    errors = list(Draft202012Validator(focused).iter_errors(value)) or native
    result = []
    for error in errors:
        path = [*prefix, *error.absolute_path]
        if error.validator == 'required' and isinstance(error.instance, dict):
            for field in error.validator_value:
                if field not in error.instance:
                    result.append({'path': [*path, field], 'message': '값을 선택하거나 입력해 주세요.'})
            continue
        if error.validator == 'additionalProperties' and isinstance(error.instance, dict):
            unexpected = set(error.instance) - set(error.schema.get('properties', {}))
            if unexpected:
                result.extend({'path': [*path, field], 'message': '이 조건에서 사용하지 않는 항목입니다.'}
                    for field in sorted(unexpected))
                continue
        message = {'enum': '허용된 목록에서 선택해 주세요.', 'const': '허용된 값을 선택해 주세요.',
            'type': '입력 형식을 확인해 주세요.', 'minimum': '최솟값은 ' + str(error.validator_value) + '입니다.',
            'exclusiveMinimum': str(error.validator_value) + '보다 큰 숫자를 입력해 주세요.',
            'minItems': '한 개 이상 선택해 주세요.', 'pattern': '허용된 값 형식을 선택해 주세요.',
            'additionalProperties': '이 조건에서 사용하지 않는 항목이 있습니다.'}.get(error.validator,
                '이 항목의 입력값을 확인해 주세요.')
        result.append({'path': path, 'message': message})
    return _unique(result)


def _unique(errors):
    seen, result = set(), []
    for row in errors:
        marker = (tuple(row['path']), row['message'])
        if marker not in seen:
            seen.add(marker); result.append(row)
    return result


def changed_paths(before, after, prefix):
    if type(before) is not type(after):
        return [list(prefix)]
    if isinstance(after, dict):
        result = []
        for key in dict.fromkeys([*before, *after]):
            if key not in before or key not in after:
                result.append([*prefix, key])
            else:
                result.extend(changed_paths(before[key], after[key], [*prefix, key]))
        return result
    if isinstance(after, list):
        result = []
        for index, row in enumerate(after):
            result.extend(changed_paths(before[index], row, [*prefix, index]) if index < len(before)
                else [[*prefix, index]])
        return result or ([list(prefix)] if len(before) != len(after) else [])
    return [list(prefix)] if before != after else []


def semantic_error(message, before, after, prefix, validator=None):
    paths = changed_paths(before, after, prefix) if before is not None else []
    implicated = []
    if validator is not None and before is not None:
        for path in paths:
            relative = path[len(prefix):]
            if not relative:
                continue
            candidate = copy.deepcopy(after)
            try:
                old, new = before, candidate
                for key in relative[:-1]:
                    old, new = old[key], new[key]
                leaf = relative[-1]
                if isinstance(old, dict) and leaf not in old:
                    new.pop(leaf, None)
                else:
                    new[leaf] = copy.deepcopy(old[leaf])
                validator(candidate)
            except (ValueError, TypeError, KeyError, IndexError):
                continue
            implicated.append(path)
    # Never paint all changed cells red when the common validator cannot
    # identify one culprit. The table-level message remains explicit instead.
    return [{'path': path, 'message': message} for path in implicated or [list(prefix)]]


def check_strategy(value, previous=None):
    schema_value = copy.deepcopy(value)
    metadata_errors = []
    # This validator-owned provenance is absent from the model response schema.
    # Retain an already validated value unchanged; it is not a new editable slot.
    for path in READONLY_METADATA:
        old, new = previous, schema_value
        try:
            for key in path[:-1]:
                old = old[key] if isinstance(old, dict) else None
                new = new[key]
            if path[-1] in new:
                if not isinstance(old, dict) or new[path[-1]] != old.get(path[-1]):
                    metadata_errors.append({'path': ['strategy', *path], 'message': '이 항목은 읽기 전용입니다.'})
                else:
                    new.pop(path[-1])
        except (KeyError, TypeError):
            pass
    errors = metadata_errors + schema_errors(schema_value, output_schema(), ['strategy'])
    if errors:
        return None, errors
    try:
        result = validate_intent(value)
        if not result['supported']:
            return None, [{'path': ['strategy', 'interpretation'], 'message': '지원되는 전략 조건을 입력해 주세요.'}]
        if result['needs_clarification']:
            return None, [{'path': ['strategy', 'interpretation'], 'message': result['clarification_question'] or '전략 조건을 확인해 주세요.'}]
        recipe_from_intent(result)
    except (ValueError, TypeError, KeyError) as exc:
        return None, semantic_error(str(exc), previous, value, ['strategy'], recipe_from_intent)
    return result, []


def check_plan(value, snapshot, strategy, previous=None):
    errors = schema_errors(value, plan_schema(), ['plan'])
    if errors:
        return errors
    try:
        validate_plan_shape(value)
    except (ValueError, TypeError, KeyError) as exc:
        return semantic_error(str(exc), previous, value, ['plan'])
    allowed = set(snapshot.get('options', {}).get('symbols') or [])
    if strategy:
        allowed.update(strategy['interpretation']['symbols'])
    for index, row in enumerate(value['steps']):
        prefix = ['plan', 'steps', index, 'command']
        command = row['command']
        if command.get('action') != 'START':
            errors.append({'path': [*prefix, 'action'], 'message': '편집표에서는 백테스트 시작 작업을 선택하세요.'})
            continue
        request = command.get('request')
        if not isinstance(request, dict):
            errors.append({'path': [*prefix, 'request'], 'message': '백테스트 실행 설정을 선택해 주세요.'})
            continue
        if request.get('virtual_entry') is not None:
            try:
                commands.virtual_contract().normalize_virtual_entry(request['virtual_entry'])
            except (ValueError, TypeError) as exc:
                errors.append({'path': [*prefix, 'request', 'virtual_entry'], 'message': str(exc)})
        for key in ('symbol', 'start', 'end'):
            if not request.get(key):
                errors.append({'path': [*prefix, 'request', key], 'message': '종목을 선택해 주세요.' if key == 'symbol' else '날짜를 선택해 주세요.'})
        symbol = request.get('symbol')
        if symbol and symbol not in allowed:
            errors.append({'path': [*prefix, 'request', 'symbol'], 'message': '현재 종목 목록에서 선택해 주세요.'})
        elif symbol and row['draft'] and strategy and symbol not in strategy['interpretation']['symbols']:
            errors.append({'path': [*prefix, 'request', 'symbol'],
                'message': '백테스트 종목을 현재 전략의 감시 종목에도 포함해 주세요.'})
        for key in ('start', 'end'):
            if request.get(key):
                try:
                    commands._date(request[key])
                except (ValueError, TypeError):
                    errors.append({'path': [*prefix, 'request', key], 'message': '올바른 날짜를 선택해 주세요.'})
        if not any(error['path'][-1] in ('start', 'end') and error['path'][:len(prefix)] == prefix for error in errors):
            if commands._date(request['start']) >= commands._date(request['end']):
                errors.append({'path': [*prefix, 'request', 'end'], 'message': '종료일은 시작일 이후로 선택해 주세요(종료일 미포함).'} )
    if value.get('needs_clarification'):
        errors.append({'path': ['plan'], 'message': value.get('clarification_question') or '백테스트 설정을 확인해 주세요.'})
    return errors
