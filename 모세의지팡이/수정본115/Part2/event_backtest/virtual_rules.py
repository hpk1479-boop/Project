"""Common post-alert predicates. All numerical calculation belongs to Facts."""
from indicator_facts import tf_seconds
from .virtual_contract import signal_is_oz


def selected_tf(value, alert):
    return (alert.get('signal_tf') or alert['tf']) if value == 'SIGNAL' else value


def confirm(config, alert, stamp, price, facts, state):
    long = alert['direction'] == 'LONG'
    tf = selected_tf(config['tf'], alert)
    # Touch history is shared predicate state, even when a separate predicate
    # (candle direction, position, engulfing) is currently false.
    for index, item in enumerate(config['conditions']):
        if item['kind'] != 'MA_TOUCH':
            continue
        ma_tf = selected_tf(item['tf'], alert)
        row = facts.row(ma_tf, stamp, closed=True)
        closed_ms = int(row['time']) * 1000 + tf_seconds(ma_tf) * 1000
        average = facts.ma(ma_tf, stamp, item['family'], item['period'], closed=True)
        key = ('touch', index)
        if key not in state and closed_ms > alert['time_ms'] and row['low'] <= average <= row['high']:
            state[key] = closed_ms
    if facts.polarity(tf, stamp) != (1 if long else -1):
        return False
    for index, item in enumerate(config['conditions']):
        kind = item['kind']
        if kind.startswith('MA_'):
            ma_tf = selected_tf(item['tf'], alert)
            args = (ma_tf, stamp, item['family'], item['period'])
            if kind == 'MA_POSITION':
                average = facts.ma(*args,price=price)
                if not (price > average if long else price < average):
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
        elif kind == 'ENGULFING':
            row = facts.row(tf, stamp, closed=True)
            old = facts.row(tf, stamp, closed=True, offset=-2)
            eligible = (old['close'] < old['open'] and row['open'] <= old['close'] and row['close'] >= old['open']) if long else (
                old['close'] > old['open'] and row['open'] >= old['close'] and row['close'] <= old['open'])
            if not eligible:
                return False
        elif kind == 'NECKLINE_BREAK':
            if not signal_is_oz(alert):
                raise ValueError('넥라인 확인 진입은 해당 올존 신호에만 사용할 수 있습니다.')
            level = facts.number(alert.get('neckline_price'), '해당 올존 넥라인')
            row = facts.row(tf, stamp, closed=True)
            old = facts.row(tf, stamp, closed=True, offset=-2)
            if not ((old['close'] <= level and row['close'] > level) if long
                    else (old['close'] >= level and row['close'] < level)):
                return False
    return True


def filters_pass(config, alert, stamp, price, facts):
    if not config['filters']:
        return True
    atr = facts.atr(selected_tf(config['atr']['tf'], alert), stamp, config['atr']['period'])
    for item in config['filters']:
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
    distance = facts.atr(tf, stamp, config['atr']['period']) * stop['multiplier']
    return entry - distance if long else entry + distance
