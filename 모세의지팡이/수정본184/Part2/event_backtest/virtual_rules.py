"""Common post-alert predicates. All numerical calculation belongs to Facts.

Candle direction and shape, a touch of an average, a price against an average and
environment states are judged by the recipe's own rules (strategy_recipe.state_judge),
so an entry reads a market state exactly as an alert does.
"""
from decimal import Decimal, localcontext
from .virtual_contract import ATR_FILTERS, alert_tf, signal_is_oz
from indicator_facts import tf_seconds
from strategy_recipe.state_judge import (candle_matches, ma_names, shape_matches, state_inputs, state_matches,
                                         state_ready, touch_matches)


def selected_tf(value, alert):
    return alert_tf(value, alert)


def _state(step, direction, tf, stamp, facts, *, closed, price=None):
    """One recipe state on `tf` at `stamp`: the last closed bar, or the forming bar at `price`.

    Recorded values the rule reads must be usable: a broken recording stops the run
    instead of reading as an unmet condition.
    """
    names = ma_names(step)
    series = facts.ma_series(tf, stamp, names, closed=closed, price=price)
    if not state_ready(step, series, 0, names):
        raise ValueError('가상진입: 판정에 필요한 이평 입력 개수가 부족합니다: ' + tf)
    close = facts.close(tf, stamp, closed=closed, price=price) if step['kind'] == 'MA_PRICE_STATE' else None
    for value in state_inputs(step, series, close, 0, names):
        facts.number(value, tf + ' 판정 값')
    return state_matches(step, direction, series, close, 0, names)


def environment_step(item):
    """An ENVIRONMENT limit as the recipe step it repeats; the side follows each alert."""
    step = {'kind': item['condition']}
    if item['condition'] == 'MA_STATE':
        step.update(ma_family=item['family'], fast_period=item['fast'], slow_period=item['slow'])
    elif item['condition'] != 'TREND':
        step.update(ma_family=item['family'], slow_period=item['period'])
        if 'lookback' in item:
            step['lookback'] = item['lookback']
    return step


def environment_holds(config, alert, stamp, price, facts):
    """Every environment limit still holds at `stamp`; a forming bar is read at `price`."""
    for item in config['filters']:
        if item['kind'] != 'ENVIRONMENT':
            continue
        closed = item['bar_state'] == 'CLOSED'
        if not _state(environment_step(item), alert['direction'], selected_tf(item['tf'], alert), stamp, facts,
                      closed=closed, price=None if closed else price):
            return False
    return True


def track_touches(config, alert, stamp, facts, state):
    """Record each MA_TOUCH condition's first touch after the alert; recording it again changes nothing.

    Touch history is shared predicate state, even when a separate predicate (candle direction,
    position, engulfing) is currently false. A conditional entry with 이평 시가 위·아래 records it on
    every candle it waits through, though it judges its conditions on one candle only.
    """
    for index, item in enumerate(config['conditions']):
        if item['kind'] != 'MA_TOUCH':
            continue
        ma_tf = selected_tf(item['tf'], alert)
        row = facts.row(ma_tf, stamp, closed=True)
        closed_ms = int(row['time']) * 1000 + tf_seconds(ma_tf) * 1000
        average = facts.ma(ma_tf, stamp, item['family'], item['period'], closed=True)
        key = ('touch', index)
        if key not in state and closed_ms > alert['time_ms'] and touch_matches(row['low'], row['high'], average):
            state[key] = closed_ms


def open_on_average(item, config, alert, stamp, price, facts):
    """MA_OPEN_POSITION `item`: None while this candle's open is below its average (a sell: above),
    else whether the open is within item.max_atr ATR of it. Equal open and average count as on it.

    Both are read at the open: an SMA/WMA/HMA averages opens, so this candle's value is fixed when it
    opens; an EMA takes the open as this candle's price, as the open-to-average limit does. The ATR is
    the policy's, of the last closed candle. Decimal arithmetic makes an exact limit inclusive.
    """
    average = facts.ma(selected_tf(item['tf'], alert), stamp, item['family'], item['period'], price=price)
    if (price < average) if alert['direction'] == 'LONG' else (price > average):
        return None
    atr = facts.atr(selected_tf(config['atr']['tf'], alert), stamp, config['atr']['period'])
    with localcontext() as context:
        context.prec = 50
        distance = abs(Decimal(str(price)) - Decimal(str(average)))
        return distance <= Decimal(str(item['max_atr'])) * Decimal(str(atr))


def confirm(config, alert, stamp, price, facts, state):
    long = alert['direction'] == 'LONG'
    tf = selected_tf(config['tf'], alert)
    track_touches(config, alert, stamp, facts, state)
    for index, item in enumerate(config['conditions']):
        kind = item['kind']
        if kind == 'MA_OPEN_POSITION':
            # Judged at the open (open_on_average), which chooses the one candle the others are judged on.
            continue
        if kind == 'CANDLE_CLOSE':
            # The confirmation candle closed bullish for a buy, bearish for a sell.
            if not candle_matches('BULL' if long else 'BEAR', facts.polarity(tf, stamp)):
                return False
        elif kind == 'CANDLE_SHAPE':
            # The confirmation candle is a hammer or an inverted hammer, whatever its colour.
            if not shape_matches(item['shape'], facts.shape(tf, stamp)):
                return False
        elif kind.startswith('MA_'):
            ma_tf = selected_tf(item['tf'], alert)
            args = (ma_tf, stamp, item['family'], item['period'])
            if kind == 'MA_POSITION':
                # That candle closed above the average for a buy, below for a sell.
                step = {'kind': 'MA_PRICE_STATE', 'ma_family': item['family'], 'slow_period': item['period']}
                if not _state(step, alert['direction'], ma_tf, stamp, facts, closed=True):
                    return False
            elif kind == 'MA_CROSS':
                row = facts.row(ma_tf, stamp, closed=True)
                old = facts.row(ma_tf, stamp, closed=True, offset=-2)
                current = facts.ma(*args, closed=True)
                previous = facts.ma(*args, closed=True, offset=-2)
                if not ((old['close'] <= previous and row['close'] > current) if long
                        else (old['close'] >= previous and row['close'] < current)):
                    return False
            elif kind == 'MA_TOUCH':
                key = ('touch', index)
                # A separate, subsequent confirmation candle must complete.
                if key not in state or stamp <= state[key]:
                    return False
            elif kind == 'MA_TOUCH_CANDLE':
                # The confirmation candle itself touched the average (one candle, like a hammer).
                row = facts.row(ma_tf, stamp, closed=True)
                if not touch_matches(row['low'], row['high'], facts.ma(*args, closed=True)):
                    return False
        elif kind == 'ENGULFING':
            row = facts.row(tf, stamp, closed=True)
            old = facts.row(tf, stamp, closed=True, offset=-2)
            eligible = (old['close'] < old['open'] and row['open'] <= old['close'] and row['close'] >= old['open']) if long else (
                old['close'] > old['open'] and row['open'] >= old['close'] and row['close'] <= old['open'])
            if not eligible:
                return False
        elif kind == 'NECKLINE_BREAK':
            if not signal_is_oz(alert):
                raise ValueError('넥라인 종가 돌파는 해당 올존 신호에만 쓸 수 있습니다.')
            level = facts.number(alert.get('neckline_price'), '해당 올존 넥라인')
            row = facts.row(tf, stamp, closed=True)
            old = facts.row(tf, stamp, closed=True, offset=-2)
            if not ((old['close'] <= level and row['close'] > level) if long
                    else (old['close'] >= level and row['close'] < level)):
                return False
    return True


def filters_pass(config, alert, stamp, price, facts):
    """The ATR size limits on the entry candle; environment and N-bar limits pass the alert instead."""
    filters = [item for item in config['filters'] if item['kind'] in ATR_FILTERS]
    if not filters:
        return True
    atr = facts.atr(selected_tf(config['atr']['tf'], alert), stamp, config['atr']['period'])
    for item in filters:
        if item['kind'] == 'CANDLE_ATR':
            row = facts.row(selected_tf(config['tf'], alert), stamp, closed=True)
            size = abs(row['close'] - row['open']) if item['measure'] == 'BODY' else row['high'] - row['low']
        else:
            average = facts.ma(selected_tf(item['tf'], alert), stamp, item['family'], item['period'],price=price)
            size = abs(price - average)
        ratio = size / atr
        if item['min'] is not None and ratio < item['min']:
            return False
        if item['max'] is not None and ratio >= item['max']:
            return False
    return True


def stop_price(config, alert, stamp, entry, facts):
    stop = config['stop']
    long = alert['direction'] == 'LONG'
    if stop['kind'] == 'OZ_B0':
        if not signal_is_oz(alert):
            raise ValueError('올존 B0 손절은 해당 올존 신호에만 사용할 수 있습니다.')
        return facts.number(alert.get('b0_price'), 'OZ B0')
    tf = selected_tf(stop['tf'], alert)
    if stop['kind'] == 'RECENT_EXTREME':
        return facts.extreme(tf, stamp, stop['bars'], long=long)
    # The ATR Fact is unchanged; only price arithmetic is decimal (수정본178). In particular,
    # 0.3 - 0.1 must produce the same 0.2 stop that recorded prices can touch. Keep the float API.
    with localcontext() as context:
        context.prec = 50
        distance = Decimal(str(facts.atr(tf, stamp, stop['period']))) * Decimal(str(stop['multiplier']))
        return float(Decimal(str(entry)) + (-distance if long else distance))
