"""Resident condition monitor. Integer source times and direct NumPy views."""
from __future__ import annotations
import logging
from monitor_OZ import TF_LABELS, PERCENTILES, finite_number
from oz_engine.common import percentile_states
from indicator_facts import tf_seconds
from watch_ma import parse_ma_expression
from .market import select
from watch_array_facts import WatchMAStore

class WatchMonitor:
    def __init__(self,symbol,controller):
        self.symbol=symbol;self.controller=controller
        self.last_bar={};self.touch_state={};self.percentile_state={};self.known_ids=set()
        self.ma=WatchMAStore();self.source_time=0.
    @staticmethod
    def _closed_time(view):return int(view.time[-2]) if view is not None and len(view)>=3 else None
    @staticmethod
    def _close_event_epoch(ct,tf):return float(ct)+tf_seconds(tf)
    def evaluate(self,board,source_time):
        self.source_time=source_time
        _,watches=self.controller.snapshot_for_symbol(self.symbol)
        ids={w.watch_id for w in watches}
        for values in (self.last_bar,self.touch_state,self.percentile_state):
            for key in tuple(values):
                if key[0] not in ids:values.pop(key)
        for w in watches:
            required=self._indicators_for_watch(w)
            for tf in w.timeframes:
                view=select(board,self.symbol,tf,required)
                if w.watch_type=='MA_EXPRESSION':self._ma_expression_event(w,tf,view)
                elif w.watch_type in ('EMA_CROSS','HMA_CROSS'):self._ma_cross_event(w,tf,view)
                elif w.watch_type=='BAR':self._bar_close(w,tf,view)
                elif w.watch_type=='PREV_DAY_TOUCH':self._prev_day_touch(w,tf,view,select(board,self.symbol,'1d'))
                elif w.watch_type=='WONBI_TOUCH':self._wonbi_touch(w,tf,view)
                elif w.watch_type=='PERCENTILE_OUT':self._percentile_out(w,tf,view)
                elif w.watch_type=='PERCENTILE_OUT_IN':self._percentile_out_in(w,tf,view)
    def _closed_row_once(
        self, w: GenericWatchSpec, tf: str, df: pd.DataFrame
    ) -> Optional[tuple[pd.Series, pd.Timestamp]]:
        if df is None or len(df) < 2:
            return None
        ct = int(df.time[-2])
        key = (w.watch_id, tf)
        previous = self.last_bar.get(key)
        if previous is None:
            # 등록/재시작 직후 이미 닫힌 과거 봉은 새 이벤트로 재생하지 않습니다.
            self.last_bar[key] = ct
            return None
        if ct <= previous:
            return None
        self.last_bar[key] = ct
        return df.row(-2), ct

    def _bar_close(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        closed = self._closed_row_once(w, tf, df)
        if closed is None:
            return
        _, ct = closed
        event_time = self._close_event_epoch(ct, tf)
        bar_open_ts = event_time - float(tf_seconds(tf))
        event_id = f"BAR_CLOSE:{self.symbol}:{tf}:{bar_open_ts:.6f}"
        msg = f"✅ {self.symbol} · {tf} 봉마감"
        if self.controller.fire(
            w.watch_id, msg, source_tf=tf, direction=w.direction,
            event_time=event_time, event_id=event_id,
        ):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [BAR Watch] %s %s CLOSE", self.symbol, tf)

    def _ma_expression_event(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        expression = parse_ma_expression(w.ma_expression)
        key = (w.watch_id, tf)
        if w.evaluation_mode == "CLOSE":
            closed = self._closed_row_once(w, tf, df)
            if closed is None:
                return
            _, bar_time = closed
            current = -2
            event_time = self._close_event_epoch(bar_time, tf)
        else:
            current = -1
            bar_time = int(df.time[-1])
            event_time = self.source_time
        features = self.ma.get(self.symbol, tf, df, expression.dependencies)
        matched = expression.evaluate(features, current)
        if matched is None:
            self.touch_state.pop(key, None)
            return
        if w.evaluation_mode == "LIVE":
            previous = self.touch_state.get(key, False)
            self.touch_state[key] = matched
            if previous or not matched:
                return
        elif not matched:
            return
        direction = w.direction
        if direction is None:
            kinds = {condition.kind for group in expression.groups for condition in group}
            direction = "LONG" if kinds == {"GOLDEN"} else "SHORT" if kinds == {"DEAD"} else None
        event_id = f"WATCH_MA:{self.symbol}:{tf}:{w.watch_id}:{(bar_time * 1000000000)}:{event_time:.6f}"
        self.controller.fire(w.watch_id, f"✅ {self.symbol} · {tf} {expression.canonical}",
                             source_tf=tf, direction=direction,
                             event_time=event_time, event_id=event_id)

    def _ma_cross_event(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 3:
            return
        key = (w.watch_id, tf)
        ct = self._closed_time(df)
        if ct is None:
            return
        if key not in self.last_bar:
            self.last_bar[key] = ct
            return
        if ct == self.last_bar[key]:
            return
        self.last_bar[key] = ct

        family, fast, slow, names = self._cross_names(w)
        # Shared MA Fact store: EA-sent periods return the EA column, others are computed.
        features = self.ma.get(self.symbol, tf, df, names)
        fast_values, slow_values = features[names[0]], features[names[1]]
        vals = (fast_values[-3], slow_values[-3], fast_values[-2], slow_values[-2])
        if not all(finite_number(x) for x in vals):
            return
        p_fast, p_slow, c_fast, c_slow = map(float, vals)
        direction = None
        if p_fast <= p_slow and c_fast > c_slow:
            direction = "LONG"
        elif p_fast >= p_slow and c_fast < c_slow:
            direction = "SHORT"
        if direction is None or (w.direction is not None and w.direction != direction):
            return

        if direction == "LONG":
            msg = f"🟢 [{family}{fast}/{slow} 골든크로스]\n{self.symbol} · {tf}"
        else:
            msg = f"🔴 [{family}{fast}/{slow} 데드크로스]\n{self.symbol} · {tf}"
        close_event_ts = self._close_event_epoch(ct, tf)
        event_id = f"{family}_CROSS:{self.symbol}:{tf}:{fast}:{slow}:{direction}:{int(close_event_ts)}"
        if self.controller.fire(
            w.watch_id, msg, source_tf=tf, direction=direction,
            event_time=close_event_ts, event_id=event_id,
        ):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [%s Cross Watch] %s %s %s/%s %s", family, self.symbol, tf, fast, slow, direction)

    def _prev_day_touch(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame, daily: pd.DataFrame) -> None:
        if df is None or len(df) < 1 or daily is None or len(daily) < 2:
            return
        live = df.row(-1)
        prev_day = daily.row(-2)
        hi, lo = live.get("high"), live.get("low")
        pdh, pdl = prev_day.get("high"), prev_day.get("low")
        if not all(finite_number(x) for x in (hi, lo, pdh, pdl)):
            return
        hi, lo, pdh, pdl = map(float, (hi, lo, pdh, pdl))
        sides = []
        if w.level_side in {"HIGH", "BOTH"} and lo <= pdh <= hi:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= pdl <= hi:
            sides.append("LOW")
        current = bool(sides)
        key = (w.watch_id, tf)
        if key not in self.touch_state:
            self.touch_state[key] = current
            return
        previous = self.touch_state[key]
        self.touch_state[key] = current
        if not current or previous:
            return
        side = sides[0]
        if side == "HIGH":
            msg = f"🔔 전일고가 터치 · {self.symbol}"
        else:
            msg = f"🔔 전일저가 터치 · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=side, direction=("SHORT" if side == "HIGH" else "LONG")):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [PDH/PDL Watch] %s %s", self.symbol, side)

    def _wonbi_touch_close(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        closed = self._closed_row_once(w, tf, df)
        if closed is None:
            return
        row, ct = closed
        hi, lo = row.get("high"), row.get("low")
        upper, lower = row.get("wonbi_upper"), row.get("wonbi_lower")
        if not all(finite_number(x) for x in (hi, lo, upper, lower)):
            return
        hi, lo, upper, lower = map(float, (hi, lo, upper, lower))
        sides: list[str] = []
        if w.level_side in {"HIGH", "BOTH"} and hi >= upper:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= lower:
            sides.append("LOW")
        if w.direction == "LONG":
            sides = [x for x in sides if x == "LOW"]
        elif w.direction == "SHORT":
            sides = [x for x in sides if x == "HIGH"]
        if not sides:
            return

        event_time = self._close_event_epoch(ct, tf)
        bar_open_ts = event_time - float(tf_seconds(tf))
        tf_label = TF_LABELS.get(tf, tf)
        for side in sides:
            direction = "SHORT" if side == "HIGH" else "LONG"
            side_label = "상단" if side == "HIGH" else "하단"
            msg = (
                f"✅ {self.symbol} · {tf} {direction} 원비 터치 봉마감"
                if w.chain_id else
                f"🔔 {tf_label} {side_label} 원비 터치 봉마감 · {self.symbol}"
            )
            event_id = f"WONBI_CLOSE:{self.symbol}:{tf}:{direction}:{bar_open_ts:.6f}"
            if self.controller.fire(
                w.watch_id, msg, source_tf=tf, level_side=side, direction=direction,
                event_time=event_time, event_id=event_id,
            ):
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("✅ [원비 Watch] %s %s %s CLOSE", self.symbol, tf, side)

    def _wonbi_touch(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if w.evaluation_mode == "CLOSE":
            self._wonbi_touch_close(w, tf, df)
            return
        if df is None or len(df) < 1:
            return
        live = df.row(-1)
        hi, lo = live.get("high"), live.get("low")
        upper, lower = live.get("wonbi_upper"), live.get("wonbi_lower")
        if not all(finite_number(x) for x in (hi, lo, upper, lower)):
            return
        hi, lo, upper, lower = map(float, (hi, lo, upper, lower))
        sides = []
        if w.level_side in {"HIGH", "BOTH"} and hi >= upper:
            sides.append("HIGH")
        if w.level_side in {"LOW", "BOTH"} and lo <= lower:
            sides.append("LOW")

        current = bool(sides)
        key = (w.watch_id, tf)
        if key not in self.touch_state:
            self.touch_state[key] = current
            return
        previous = self.touch_state[key]
        self.touch_state[key] = current
        if not current or previous:
            return

        side_label = "상단" if sides[0] == "HIGH" else "하단"
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {side_label} 원비 터치 · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=sides[0], direction=("SHORT" if sides[0] == "HIGH" else "LONG")):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [원비 Watch] %s %s %s", self.symbol, tf, sides[0])

    @staticmethod
    def _percentile_matches(states: dict[str, str], level_side: Optional[str]) -> list[tuple[str, str]]:
        allowed = {"UPPER_OUT"} if level_side == "HIGH" else {"LOWER_OUT"} if level_side == "LOW" else {"UPPER_OUT", "LOWER_OUT"}
        return [(name, state) for name, state in states.items() if state in allowed]

    def _percentile_out(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 1:
            return
        current = percentile_states(df.row(-1))
        key = (w.watch_id, tf)
        previous = self.percentile_state.get(key)
        self.percentile_state[key] = current
        if previous is None:
            return

        matches = []
        for name, state in self._percentile_matches(current, w.level_side):
            if previous.get(name) != state:
                matches.append((name, state))
        if not matches:
            return

        details = " · ".join(f"{'상단' if state == 'UPPER_OUT' else '하단'} OUT · {name}" for name, state in matches)
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {details} · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=w.level_side):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [Percentile OUT Watch] %s %s | %s", self.symbol, tf, details)

    def _percentile_out_in(self, w: GenericWatchSpec, tf: str, df: pd.DataFrame) -> None:
        if df is None or len(df) < 1:
            return
        current = percentile_states(df.row(-1))
        key = (w.watch_id, tf)
        previous = self.percentile_state.get(key)
        self.percentile_state[key] = current
        if previous is None:
            return

        allowed_prev = {"UPPER_OUT"} if w.level_side == "HIGH" else {"LOWER_OUT"} if w.level_side == "LOW" else {"UPPER_OUT", "LOWER_OUT"}
        matches = []
        for name in PERCENTILES:
            prev_state = previous.get(name)
            if prev_state in allowed_prev and current.get(name) == "IN":
                matches.append((name, prev_state))
        if not matches:
            return

        details = " · ".join(f"{'상단' if state == 'UPPER_OUT' else '하단'} OUT→IN · {name}" for name, state in matches)
        tf_label = TF_LABELS.get(tf, tf)
        msg = f"🔔 {tf_label} {details} · {self.symbol}"
        if self.controller.fire(w.watch_id, msg, source_tf=tf, level_side=w.level_side):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("✅ [Percentile OUT→IN Watch] %s %s | %s", self.symbol, tf, details)

    @staticmethod
    def _cross_names(w: GenericWatchSpec) -> tuple[str, int, int, tuple[str, str]]:
        family = (w.ma_family or ("EMA" if w.watch_type == "EMA_CROSS" else "HMA")).upper()
        fast = int(w.fast_period or (50 if family == "EMA" else 6))
        slow = int(w.slow_period or (200 if family == "EMA" else 17))
        return family, fast, slow, (f"{family}{fast}", f"{family}{slow}")

    @staticmethod
    def _indicators_for_watch(w: GenericWatchSpec) -> tuple[str, ...]:
        """Watch 종류별 STAFF 요청 지표를 최소 계약으로 분리합니다."""
        if w.watch_type == "MA_EXPRESSION":
            return tuple(parse_ma_expression(w.ma_expression).dependencies)
        if w.watch_type in ("EMA_CROSS", "HMA_CROSS"):
            # Same rule as MA_EXPRESSION: the two MA names, from the shared MA path.
            return WatchMonitor._cross_names(w)[3]
        if w.watch_type == "WONBI_TOUCH":
            return ("WONBI",)
        if w.watch_type in {"PERCENTILE_OUT", "PERCENTILE_OUT_IN"}:
            return ("RSI", "STO", "DI", "PRICE")
        # PREV_DAY_TOUCH는 OHLC만 사용합니다.
        return ()
