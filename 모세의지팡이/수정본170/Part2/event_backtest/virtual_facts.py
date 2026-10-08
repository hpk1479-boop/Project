"""Read-only, as-of Facts for virtual fills; rules live in virtual_rules.

Completed OHLC and OPEN-based averages have separate frame cutoffs. One Fact
frame memoizes all requested calculations and is shared by concurrent signals.
"""
from collections import OrderedDict
import re
import numpy as np
import pandas as pd
from indicator_facts import (standalone_frame, tf_seconds, candle_direction_values, candle_shape_values,
                             parameterized_ma_fact, parameterized_atr_fact)

MA_NAME = re.compile(r'(SMA|EMA|WMA|HMA)([1-9][0-9]*)')


class VirtualFacts:
    def __init__(self):
        self.feeds = {}
        self.context = None
        self.frames = OrderedDict()

    def update(self, feeds, symbol, stamp):
        context = (symbol, stamp, tuple((tf, id(v)) for tf, v in sorted(feeds.items())))
        if self.context != context:
            self.context = context
            self.feeds = feeds
            self.frames.clear()

    def view(self, tf):
        view = self.feeds.get(tf)
        if view is None:
            raise ValueError('가상진입: 녹화에 필요한 타임프레임이 없습니다: ' + tf)
        return view

    def frame(self, tf, stamp, *, closed, price=None):
        key = (tf, int(stamp), bool(closed), price)
        if key in self.frames:
            return self.frames[key]
        view = self.view(tf)
        offset = tf_seconds(tf) * 1000 if closed else 0
        end = int(np.searchsorted(view.time, (stamp - offset) // 1000, side='right'))
        if end <= 0:
            raise ValueError('가상진입: 확정봉 데이터가 준비되지 않았습니다: ' + tf)
        from staff_schema import PIPE_VALUE_COLUMNS
        columns = getattr(view, 'columns', PIPE_VALUE_COLUMNS)
        data = {name: view.values[:end, i] for i, name in enumerate(columns)}
        data['time'] = view.time[:end]
        if not closed:
            if int(data['time'][-1])*1000+tf_seconds(tf)*1000<=stamp:
                raise ValueError('가상진입: 판정 시점의 진행봉 데이터가 없습니다: '+tf)
            # All TFs share this symbol's observable quote. For a new entry
            # candle it is its open; for an intrabar alert it is signal_price.
            quoted = self.number(data['open'][-1] if price is None else price, '판정 시점 가격')
            data['close'] = np.array(data['close'], copy=True)
            data['close'][-1] = quoted
        frame = standalone_frame(pd.DataFrame(data), tf)
        self.frames[key] = frame
        if len(self.frames) > 128:
            self.frames.popitem(last=False)
        return frame

    @staticmethod
    def number(value, label):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError('가상진입: ' + label + ' 값이 없습니다.') from None
        if not np.isfinite(number) or abs(number) > 1e100:
            raise ValueError('가상진입: ' + label + ' 값이 준비되지 않았습니다.')
        return number

    def row(self, tf, stamp, *, closed, offset=-1):
        frame = self.frame(tf, stamp, closed=closed)
        if len(frame) < abs(offset):
            raise ValueError('가상진입: 확정봉 개수가 부족합니다: ' + tf)
        row = frame.df.iloc[offset]
        return {name: self.number(row[name], tf + ' ' + name)
                for name in ('time', 'open', 'high', 'low', 'close')}

    def ma(self, tf, stamp, family, period, *, closed=False, offset=-1, price=None):
        frame = self.frame(tf, stamp, closed=closed,price=price)
        values = frame.get(parameterized_ma_fact(family, period, asof=not closed))
        if len(values) < abs(offset):
            raise ValueError('가상진입: 이평 입력 개수가 부족합니다.')
        return self.number(values.iloc[offset], f'{tf} {family}{period}')

    def ma_series(self, tf, stamp, names, *, closed, price=None):
        """Named averages up to the judged bar: the last closed one, or the forming one at `price`."""
        frame = self.frame(tf, stamp, closed=closed, price=price)
        result = {}
        for name in names:
            found = MA_NAME.fullmatch(name)
            if not found:
                raise ValueError('가상진입: 이평 이름을 확인하세요: ' + name)
            values = frame.get(parameterized_ma_fact(found[1], int(found[2]), asof=not closed))
            result[name] = np.asarray(values, dtype=float)
        return result

    def close(self, tf, stamp, *, closed, price=None):
        """The judged bar's close: a closed candle's close, or the forming candle's quote."""
        frame = self.frame(tf, stamp, closed=closed, price=price)
        return self.number(frame.df['close'].iloc[-1], tf + ' close')

    def atr(self, tf, stamp, period):
        value = self.frame(tf, stamp, closed=True).get(parameterized_atr_fact(period)).iloc[-1]
        value = self.number(value, f'{tf} ATR{period}')
        if value <= 0:
            raise ValueError('가상진입: ATR은 양수여야 합니다.')
        return value

    def polarity(self, tf, stamp):
        row = self.row(tf, stamp, closed=True)
        return int(candle_direction_values(row['open'], row['close']))

    def shape(self, tf, stamp):
        """The last closed candle's shape Fact: hammer 1, inverted hammer -1, neither 0."""
        row = self.row(tf, stamp, closed=True)
        return int(candle_shape_values(row['open'], row['high'], row['low'], row['close']))

    def extreme(self, tf, stamp, bars, *, long):
        frame = self.frame(tf, stamp, closed=True)
        if len(frame) < bars:
            raise ValueError('가상진입: 손절 기준 직전 N봉 데이터가 부족합니다.')
        values = np.asarray(frame.df['low' if long else 'high'].iloc[-bars:], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('가상진입: 손절 기준 N봉 값이 준비되지 않았습니다.')
        return float(values.min() if long else values.max())
