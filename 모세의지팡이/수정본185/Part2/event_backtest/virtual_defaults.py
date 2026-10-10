"""What a virtual-entry target is: its recipe's own entry policy and the timeframes it uses.

The recipe's virtual_entry fills the policy as ordinary values; a recipe without
one enters immediately. Timeframe fields are labelled with real timeframes: the
alerts' own frames (SIGNAL) and their first condition's frames (ENV). A virtual
entry runs exactly one strategy; a WATCH command is alert-only.

The policy's tf is the base frame (기준 프레임) of the test. SIGNAL tests the recipe as
written; another frame tests the recipe as if it had been written on that frame (base_frames),
strategy conditions and entry alike. A base frame always refills every value from the recipe.
Frames come only from the base frame: no field of the entry or of the strategy conditions sets
its own frame (check_frames, check_strategy). Their other values edited after the refill are this
test's own; the recipe file never changes.
"""
from copy import deepcopy

from .base_frames import alert_frames, base_choices, base_frame, label, move, move_frame, recipe_entry, sorted_frames
from .virtual_contract import check_for_strategy, immediate_virtual_entry, normalize_virtual_entry

# The strategy fields a test may change (the panel shows these); frames and everything else stay the recipe's.
STEP_LISTS = ('steps', 'cancel_conditions', 'final_conditions', 'after_conditions')
STEP_FIELDS = ('ma_left', 'ma_right', 'ma_family', 'slow_period', 'fast_period', 'lookback', 'side', 'bar_state', 'shape')
FRAME_KEYS = ('tfs', 'tf', 'price_tf')
FRAMES_FIXED = '시간봉은 기준 프레임으로만 바꿀 수 있습니다.'


def _oz(parts):
    return all(unit['final']['kind'] == 'OZ' for unit in parts)


def _frame_rows(policy):
    """Every row of a policy that has a frame field."""
    return [policy['atr'], policy['stop'], *policy['conditions'], *policy['filters']]


def _fixed_frames(policy):
    """The policy's real frames (not SIGNAL/ENV) besides its base frame: they move with the base frame."""
    return [row['tf'] for row in _frame_rows(policy) if row.get('tf') not in (None, 'SIGNAL', 'ENV')]


def recipe_policy(recipe):
    """The recipe's own entry policy, checked for its alerts; without one it enters immediately.

    Every recipe reads its virtual_entry here: a shipped SPECIAL, a generated strategy, and Part3's
    check before a recipe is saved.
    """
    from strategy_recipe.registry import units
    policy = normalize_virtual_entry(recipe.get('virtual_entry')) or immediate_virtual_entry()
    return check_for_strategy(policy, _oz(units(recipe['strategy_intent'])))


def strategy_frames(meaning):
    """A strategy's own frames (each alert's own) and its environment frames (its units' first conditions)."""
    from strategy_recipe.registry import units
    parts = list(units(meaning))
    return {'timeframes': alert_frames(meaning),
            'env_timeframes': sorted_frames(tf for unit in parts for tf in (unit['steps'][0]['tfs'] if unit.get('steps') else ()))}


def strategy_profile(name):
    """OZ or not (B0 and neckline), the alert and environment frames, the recipe's policy and
    strategy conditions, and the base frames its test can run on (none when its conditions
    complete on several frames)."""
    from strategy_recipe.registry import units
    recipe = recipe_entry(name)['recipe']
    meaning = recipe['strategy_intent']
    policy = recipe_policy(recipe)
    for key in ('conditions', 'filters'):
        for index, row in enumerate(policy[key]):
            row['recipe_index'] = index
    choices = base_choices(meaning, _fixed_frames(policy))
    return {'oz': _oz(list(units(meaning))), **strategy_frames(meaning),
            'default': policy, 'strategy': deepcopy(meaning),
            'base': base_frame(meaning) if choices else None, 'bases': choices}


def check_base(tf, profile):
    """A base frame this strategy's test can run on: SIGNAL (its own frames) or one of its choices."""
    if tf == 'SIGNAL' or tf in profile['bases'] or profile['timeframes'] == [tf]:
        return
    if len(profile['timeframes']) != 1:
        raise ValueError('시간봉이 여러 개인 전략은 기준 프레임을 바꿀 수 없습니다.')
    if not profile['bases']:
        raise ValueError('이 전략은 기준 프레임을 바꿀 수 없습니다. 기준·중위·상위·최상위 표에 없는 시간봉이나 초 단위 대기를 씁니다.')
    raise ValueError('기준 프레임은 ' + '·'.join(label(tf) for tf in profile['bases']) + ' 중에서 고르세요.')


def _moves(profile, tf):
    """Whether base frame tf differs from the recipe's own, so its frames move."""
    return tf not in (None, 'SIGNAL') and profile['base'] is not None and tf != profile['base']


def recipe_on_base(profile, tf):
    """The recipe's own entry policy as if the recipe had been written on base frame tf."""
    check_base(tf, profile)
    policy = deepcopy(profile['default'])
    if not _moves(profile, tf):
        return policy
    for row in _frame_rows(policy):
        if row.get('tf') not in (None, 'SIGNAL', 'ENV'):
            row['tf'] = move_frame(row['tf'], profile['base'], tf)
    policy['tf'] = tf
    return policy


def strategy_on_base(profile, tf):
    """The recipe's strategy conditions as if the recipe had been written on base frame tf."""
    check_base(tf, profile)
    if not _moves(profile, tf):
        return deepcopy(profile['strategy'])
    return move(profile['strategy'], profile['base'], tf)


def _same_recipe(recipe, edited, fields=(), where='전략 조건'):
    """Only the shown fields may differ; the conditions, their order, their frames and everything else stay the recipe's."""
    if isinstance(recipe, dict):
        if not isinstance(edited, dict) or set(edited) != set(recipe):
            raise ValueError(where + ': 레시피에 없는 항목이 있거나 항목이 빠졌습니다.')
        for key, value in recipe.items():
            if key in fields:
                if type(edited[key]) is not type(value):
                    raise ValueError(where + ': ' + key + ' 값의 형식이 레시피와 다릅니다.')
            elif key in FRAME_KEYS and edited[key] != value:
                raise ValueError(where + ': ' + FRAMES_FIXED)
            else:
                _same_recipe(value, edited[key], STEP_FIELDS if key in STEP_LISTS else (), where)
    elif isinstance(recipe, list):
        if not isinstance(edited, list) or len(edited) != len(recipe):
            raise ValueError(where + ': 조건 수가 레시피와 다릅니다.')
        for a, b in zip(recipe, edited):
            _same_recipe(a, b, fields, where)
    elif type(edited) is not type(recipe) or edited != recipe:
        raise ValueError(where + ': 바꿀 수 없는 값이 레시피와 다릅니다.')


def check_strategy(profile, tf, intent):
    """The strategy conditions edited for this test, or None when they are the recipe's own on base tf.

    Only the panel's fields may differ (averages, periods, sides, candle basis, shape); frames are the
    base frame's. The edited conditions must pass the recipe language's own checks.
    """
    if intent is None:
        return None
    recipe = strategy_on_base(profile, tf)
    _same_recipe(recipe, intent)
    if intent == recipe:
        return None
    from strategy_recipe.contract import execution_plan
    checked = deepcopy(intent)
    checked['symbols'] = ['*']          # the run fills its symbols; the conditions are checked here
    execution_plan(checked)
    return deepcopy(intent)


def on_base(profile, tf):
    """The profile's alert and environment frames when its test runs on base frame tf."""
    if tf in (None, 'SIGNAL') or profile['timeframes'] == [tf]:
        return profile
    check_base(tf, profile)
    return {**profile, 'timeframes': [tf],
            'env_timeframes': sorted_frames(move_frame(frame, profile['base'], tf) for frame in profile['env_timeframes'])}


def target_profile(strategies, commands=()):
    strategies = list(strategies or ())
    if len(strategies) != 1 or strategies == ['ALL']:
        raise ValueError('가상 진입은 전략 1개만 선택할 수 있습니다.')
    if strategies[0] == 'WATCH':
        raise ValueError('WATCH 명령은 얼럿 온리만 됩니다.')
    return strategy_profile(strategies[0])


def check_frames(profile, policy):
    """Each frame field holds the frame the base frame gives that field, never one chosen for it.

    A retained row identifies its original recipe slot, independent of changes to
    its condition, averages or periods. A removed slot stays removed, while an
    added row has no recipe_index and uses SIGNAL. Equal predicate kinds never
    share frames. The ATR and stop each have one fixed recipe slot.
    """
    recipe = recipe_on_base(profile, policy['tf'])
    if any(policy[key]['tf'] != recipe[key]['tf'] for key in ('atr', 'stop')):
        raise ValueError('가상진입: ' + FRAMES_FIXED)
    for key in ('conditions', 'filters'):
        previous = -1
        for row in policy[key]:
            index = row.get('recipe_index')
            if index is None:
                expected = 'SIGNAL'
            else:
                if type(index) is not int or index <= previous or index >= len(recipe[key]):
                    raise ValueError('가상진입: 레시피 칸 번호가 잘못됐거나 중복·순서 변경됐습니다.')
                previous = index
                expected = recipe[key][index].get('tf', 'SIGNAL')
            if row.get('tf', 'SIGNAL') != expected:
                raise ValueError('가상진입: ' + FRAMES_FIXED)


def restore_recipe_rows(profile, policy):
    """Recover slot identity only for an old saved policy whose row layout still matches.

    Never infer slots in incoming API/AI policies. An ambiguous old layout falls
    back to the recipe through the normal saved-policy validation instead of
    assigning one field another field's frame.
    """
    chosen = normalize_virtual_entry(policy)
    if chosen is None or any('recipe_index' in row for key in ('conditions', 'filters') for row in chosen[key]):
        return chosen
    recipe = recipe_on_base(profile, chosen['tf'])
    for key in ('conditions', 'filters'):
        if len(chosen[key]) != len(recipe[key]):
            continue
        for index, (row, original) in enumerate(zip(chosen[key], recipe[key])):
            if row['kind'] == original['kind'] and row.get('tf', 'SIGNAL') == original.get('tf', 'SIGNAL'):
                row['recipe_index'] = index
    return chosen


def target_policy(strategies, commands=(), policy=None):
    """The given policy checked for the one target, or that target's recipe policy."""
    profile = target_profile(strategies, commands)
    chosen = normalize_virtual_entry(policy)
    if chosen is None:
        chosen = profile['default']
    check_base(chosen['tf'], profile)
    check_frames(profile, chosen)
    return check_for_strategy(chosen, profile['oz'])


def target_base_frames(strategies, policy):
    """{strategy: frame} when the checked policy tests the strategy on another base frame, else {}."""
    profile = target_profile(strategies)
    tf = policy['tf']
    return {strategies[0]: tf} if profile['base'] and tf not in ('SIGNAL', profile['base']) else {}


def target_strategy(strategies, policy, intent):
    """The one target's strategy conditions as edited for this test; None runs the recipe's own."""
    return check_strategy(target_profile(strategies), policy['tf'], intent)


def target_bases(strategies, bases):
    """Several base frames to compare in one replay (수정본161): each is the recipe as if written on it.

    Only a strategy with base frame choices; at least two distinct frames, smallest first.
    """
    from indicator_facts import tf_seconds
    profile = target_profile(strategies)
    if not isinstance(bases, (list, tuple)) or any(not isinstance(tf, str) for tf in bases):
        raise ValueError('비교할 기준 프레임 목록 형식이 올바르지 않습니다.')
    if not profile['bases']:
        raise ValueError('이 전략은 기준 프레임을 바꿀 수 없어 여러 기준 프레임을 비교할 수 없습니다.')
    if len(set(bases)) != len(bases) or len(bases) < 2:
        raise ValueError('비교할 기준 프레임은 서로 다른 두 개 이상이어야 합니다.')
    for tf in bases:
        if tf == 'SIGNAL' or tf not in profile['bases']:
            raise ValueError('기준 프레임은 ' + '·'.join(label(item) for item in profile['bases']) + ' 중에서 고르세요.')
    return sorted(bases, key=tf_seconds)


def base_variants(strategies, commands, bases):
    """Each base frame's virtual entry: the recipe's own policy and strategy conditions on that frame."""
    profile = target_profile(strategies, commands)
    variants = []
    for tf in bases:
        policy = target_policy(strategies, commands, recipe_on_base(profile, tf))
        variants.append({'virtual_entry': policy, 'base_frames': target_base_frames(strategies, policy),
                         'virtual_strategy': None})
    return variants
