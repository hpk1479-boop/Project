"""Persistent OZ profile. Existing decision flow/operators; NumPy view input."""
from __future__ import annotations
import logging
from typing import Optional,Iterable
from durable_protocol import identity
from .common import *
from .common import _resolve_oz_modes
from .market import OZMarketView,MarketRow
from .checkpoint import encode,decode

class OZProfile:
    def __init__(self,symbol,config,telegram,watch,validation_mode='NORMAL',trigger_mode='OZ',*,source_time,base_tfs=None):
        self.symbol=symbol;self.config=dict(config);self.telegram=telegram;self.watch=watch
        self.validation_mode,self.trigger_mode=_resolve_oz_modes(validation_mode,trigger_mode)
        self._source_time=source_time;self.watch_generation=-1;self._in_cycle=False
        self.max_bars=int(config.get('MAX_BARS_AFTER_B0','10'))
        self.max_bars_after_hma_cross=int(config.get('MAX_BARS_AFTER_HMA_CROSS','7'))
        self.max_bars_after_outin=int(config.get('MAX_BARS_AFTER_OUTIN','7'))
        self.min_bars=int(config.get('MIN_BARS_AFTER_B0','1'))
        self.alert_long=as_bool(config.get('ALERT_LONG'),True);self.alert_short=as_bool(config.get('ALERT_SHORT'),True)
        self.base_tfs=list(TF_MAP if base_tfs is None else base_tfs)
        self.episodes={(tf,d,p):IndicatorEpisode() for tf in self.base_tfs for d in ('LONG','SHORT') for p in PERCENTILES}
        self.prev_states={};self.prev_bar_time={tf:None for tf in self.base_tfs}
        self.candidates={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.percentile_candidates={(tf,d,p):None for tf in self.base_tfs for d in ('LONG','SHORT') for p in PERCENTILES}
        self.alert_keys=set();self.bootstrapped=set()
        self.environment_identities={(tf,d):frozenset() for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.hma_cross_extremes={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self._resume_outin=set();self._resume_cross=set();self._resume_cross_seen={}
        self.checkpoint_name='oz_observed_'+identity(symbol,self.validation_mode,self.trigger_mode)[:24]+'.json'

    def restore_observed_checkpoint(self,payload):
        if payload.get('version')!=1:raise ValueError('Unsupported OZ checkpoint')
        for name in self._checkpoint_fields:setattr(self,name,decode(payload['observed'][name]))
        self._resume_outin=set(self.prev_states);self._resume_cross=set(self.base_tfs)

    _checkpoint_fields = ('episodes','prev_states','prev_bar_time','candidates','percentile_candidates',
                          'alert_keys','bootstrapped','environment_identities','hma_cross_extremes')

    # 프로필 수식어. trigger_mode 문자열에서 매번 계산하므로 테스트/재설정 시에도 일관됩니다.
    @property
    def use_breaker(self) -> bool:
        return oz_profiles.has_flag(self.trigger_mode, "BREAKER")

    @property
    def use_regime(self) -> bool:
        return oz_profiles.has_flag(self.trigger_mode, "REGIME")

    @property
    def use_super(self) -> bool:
        return oz_profiles.has_flag(self.trigger_mode, "SUPER")

    def export_event_state(self):
        fields=self._checkpoint_fields+('watch_generation','_resume_outin','_resume_cross','_resume_cross_seen')
        return {name:encode(getattr(self,name)) for name in fields}

    def restore_event_state(self,state):
        for name,value in state.items():setattr(self,name,decode(value))


    # ------------------------------------------------------------------
    # 공통 OZ Fact (프로필 무관 순수 계산, OZFactMemo로 1회 계산 후 공유)
    # ------------------------------------------------------------------
    def _fact(self,view,key,compute):
        return view.memo(key,compute)

    def _super_fact(self,view,percentile,direction,b0_time):
        return view.memo(('super',percentile,direction,b0_time),lambda:view.super_filter(percentile,direction,b0_time))

    def _live_row(self, df):
        return self._fact(df, ('row', -1), lambda: df.row(-1))

    def _live_states(self, df) -> dict[str, str]:
        return dict(self._fact(df, 'percentile_states', lambda: percentile_states(self._live_row(df))))

    def _bars_since(self, df, event_time) -> Optional[int]:
        return self._fact(df, ('bars_since', event_time), lambda: self._bars_since_event(df, event_time))

    @staticmethod
    def _hma_cross_observation(view):return view.hma_cross()

    def _reset_watch_state(self, timeframes: Iterable[str], generation: int) -> None:
        """Refresh Watch bookkeeping without touching live base-OZ structure."""
        self.watch_generation = generation
        logging.info(
            "[%s] OZ Watch revision 반영 generation=%d | TF=%s | base state preserved",
            self.symbol, generation, ",".join(timeframes),
        )

    @staticmethod
    def _row_time(row: MarketRow) -> epoch:
        return epoch(row.get("time"))

    @staticmethod
    def _update_episode_extreme(ep: IndicatorEpisode, direction: str, row: MarketRow) -> None:
        t = OZProfile._row_time(row)
        px_raw = row.get("low") if direction == "LONG" else row.get("high")
        if not finite_number(px_raw):
            return
        px = float(px_raw)
        if ep.extreme_price is None:
            ep.extreme_price, ep.extreme_time = px, t
        elif direction == "LONG" and px < ep.extreme_price:
            ep.extreme_price, ep.extreme_time = px, t
        elif direction == "SHORT" and px > ep.extreme_price:
            ep.extreme_price, ep.extreme_time = px, t

    def _active_percentile_candidates(self, tf: str, direction: str) -> list[PercentileCandidate]:
        out: list[PercentileCandidate] = []
        for percentile in PERCENTILES:
            item = self.percentile_candidates.get((tf, direction, percentile))
            if item is not None and not item.invalidated:
                out.append(item)
        return out

    def _clear_percentile_candidates(self, tf: str, direction: str) -> None:
        for percentile in PERCENTILES:
            self.percentile_candidates[(tf, direction, percentile)] = None

    def _sync_common_candidate(self, tf: str, direction: str) -> Optional[Candidate]:
        """Build/update the common price structure from independent Percentile registrations."""
        active = self._active_percentile_candidates(tf, direction)
        if not active:
            return self.candidates.get((tf, direction))

        chosen = (
            min(active, key=lambda x: x.outin_b0_price)
            if direction == "LONG"
            else max(active, key=lambda x: x.outin_b0_price)
        )
        percentiles = {x.percentile for x in active}
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            cand = Candidate(
                direction=direction,
                outin_b0_price=float(chosen.outin_b0_price),
                outin_b0_time=epoch(chosen.outin_b0_time),
                trigger_time=max(epoch(x.trigger_time) for x in active),
                trigger_indicators=percentiles,
            )
            self._apply_latest_hma_extreme(tf, cand)
            self.candidates[(tf, direction)] = cand
        else:
            cand.outin_b0_price = float(chosen.outin_b0_price)
            cand.outin_b0_time = epoch(chosen.outin_b0_time)
            cand.trigger_indicators = percentiles
            cand.trigger_time = max(epoch(x.trigger_time) for x in active)
        self._refresh_true_b0(cand)
        return cand

    def _register_percentile_candidate(
        self, tf: str, direction: str, percentile: str,
        b0_price: float, b0_time: epoch, trigger_time: epoch,
    ) -> Candidate:
        self.percentile_candidates[(tf, direction, percentile)] = PercentileCandidate(
            percentile=percentile,
            direction=direction,
            outin_b0_price=float(b0_price),
            outin_b0_time=epoch(b0_time),
            trigger_time=epoch(trigger_time),
        )
        cand = self._sync_common_candidate(tf, direction)
        assert cand is not None
        return cand

    def _process_out_in(self, tf: str, df: OZMarketView) -> None:
        """Track live intra-bar OUT->IN transitions for each percentile independently."""
        if df is None or len(df) < 2:
            return
        live = self._live_row(df)
        current = self._live_states(df)
        if tf in self._resume_outin:
            self._resume_outin.remove(tf)
            self.prev_states[tf] = current
            for direction in ('LONG','SHORT'):
                target = 'LOWER_OUT' if direction == 'LONG' else 'UPPER_OUT'
                for ind in PERCENTILES:
                    ep = self.episodes[(tf,direction,ind)]
                    if current.get(ind) == target:
                        ep.active = True
                        self._update_episode_extreme(ep,direction,live)
                    else:
                        self.episodes[(tf,direction,ind)] = IndicatorEpisode()
            return  # No inferred OUT->IN spanning an unobserved restart gap.
        previous_observed = self.prev_states.get(tf)

        # On first observation, seed active OUT episodes but do not invent an OUT->IN transition.
        if previous_observed is None:
            for direction in ("LONG", "SHORT"):
                target = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"
                for ind in PERCENTILES:
                    if current.get(ind) == target:
                        ep = self.episodes[(tf, direction, ind)]
                        ep.active = True
                        self._update_episode_extreme(ep, direction, live)
            self.prev_states[tf] = current
            return

        events: dict[str, list[tuple[str, float, epoch]]] = {"LONG": [], "SHORT": []}

        for direction in ("LONG", "SHORT"):
            target = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"
            for ind in PERCENTILES:
                ep = self.episodes[(tf, direction, ind)]
                cur_state = current.get(ind, "NA")
                prev_state = previous_observed.get(ind, "NA")

                if cur_state == target:
                    if not ep.active:
                        ep.active = True
                        ep.extreme_price = None
                        ep.extreme_time = None
                    self._update_episode_extreme(ep, direction, live)
                    continue

                # Include the return-IN candle's full high/low in B0 extreme.
                if ep.active and prev_state == target and cur_state == "IN":
                    self._update_episode_extreme(ep, direction, live)
                    if ep.extreme_price is not None and ep.extreme_time is not None:
                        events[direction].append((ind, ep.extreme_price, ep.extreme_time))
                    ep.active = False
                    ep.extreme_price = None
                    ep.extreme_time = None
                elif ep.active and cur_state not in (target, "IN"):
                    # Opposite-side jump / unavailable value: reset the episode safely.
                    ep.active = False
                    ep.extreme_price = None
                    ep.extreme_time = None

        self.prev_states[tf] = current

        # Each of the four Percentiles registers its own OZ family candidate.
        # The HMA/price structure is common and uses the most extreme registered OUT->IN price.
        trigger_time = self._row_time(live)
        for direction, evs in events.items():
            for ind, b0_price, b0_time in evs:
                cand = self._register_percentile_candidate(
                    tf, direction, ind, float(b0_price), epoch(b0_time), trigger_time,
                )
                logging.info(
                    "🟡 [OZ 후보등록] %s %s %s | %s OUT→IN | OUT/IN B0=%s @ %s | TRUE B0=%s @ %s",
                    self.symbol, tf, direction, ind,
                    f"{float(b0_price):.6f}", b0_time,
                    f"{cand.true_b0_price:.6f}" if cand.true_b0_price is not None else "-", cand.true_b0_time,
                )

    @staticmethod
    def _refresh_true_b0(cand: Candidate) -> None:
        """Combine OUT->IN and HMA pre-cross extremes into the true structural B0."""
        choices: list[tuple[float, epoch]] = [
            (float(cand.outin_b0_price), epoch(cand.outin_b0_time))
        ]
        if cand.hma_b0_price is not None and cand.hma_b0_time is not None:
            choices.append((float(cand.hma_b0_price), epoch(cand.hma_b0_time)))

        if cand.direction == "LONG":
            price, when = min(choices, key=lambda x: x[0])
        else:
            price, when = max(choices, key=lambda x: x[0])
        cand.true_b0_price = float(price)
        cand.true_b0_time = epoch(when)

    @staticmethod
    def _family_true_b0_time(cand: Candidate, reg: PercentileCandidate) -> Optional[epoch]:
        """해당 Percentile family의 첫 번째 저점(LONG)/고점(SHORT) 시각 = family TRUE B0."""
        choices: list[tuple[float, epoch]] = [
            (float(reg.outin_b0_price), epoch(reg.outin_b0_time))
        ]
        if cand.hma_b0_price is not None and cand.hma_b0_time is not None:
            choices.append((float(cand.hma_b0_price), epoch(cand.hma_b0_time)))
        if cand.direction == "LONG":
            return min(choices, key=lambda x: x[0])[1]
        return max(choices, key=lambda x: x[0])[1]

    def _apply_latest_hma_extreme(self, tf: str, cand: Candidate) -> None:
        item = self.hma_cross_extremes.get((tf, cand.direction))
        if item is None:
            return
        price, extreme_time, cross_time = item
        cand.hma_b0_price = float(price)
        cand.hma_b0_time = epoch(extreme_time)
        cand.hma_cross_time = epoch(cross_time)

    @staticmethod
    def _reconstruct_pre_cross_extreme(view,direction):return view.pre_cross_extreme(direction)

    def _reset_direction_cycle(self, tf: str, direction: str) -> None:
        """Clear one base-TF/direction OZ cycle and wait for a fresh structure."""
        self.candidates[(tf, direction)] = None
        self.hma_cross_extremes[(tf, direction)] = None
        self._clear_percentile_candidates(tf, direction)
        for ind in PERCENTILES:
            self.episodes[(tf, direction, ind)] = IndicatorEpisode()

    def _cancel_base_candidate(self, tf: str, direction: str, reason: str) -> None:
        """Common base-TF invalidation used by every validation/trigger profile."""
        cand = self.candidates.get((tf, direction))
        had_state = cand is not None or bool(self._active_percentile_candidates(tf, direction))
        if not had_state and self.hma_cross_extremes.get((tf, direction)) is None:
            return
        logging.info(
            "🛑 [OZ BASE 취소] %s %s %s | validation=%s trigger=%s | reason=%s",
            self.symbol, tf, direction, self.validation_mode, self.trigger_mode, reason,
        )
        self._reset_direction_cycle(tf, direction)

    @staticmethod
    def _one_way_reason(view,direction,cross_time):return view.one_way(direction,cross_time)

    def _check_base_invalidation(
        self,
        tf: str,
        direction: str,
        df: OZMarketView,
        cand: Candidate,
    ) -> tuple[bool, Optional[int], Optional[int]]:
        """Return (cancelled, true_b0_bars, hma_cross_bars).

        Base invalidation is independent of NORMAL/BLIND and OZ/BREAKER:
          1) opposite HMA cross (handled at cross event time),
          2) one-way 3-candle continuation,
          3) existing TRUE-B0/HMA-cross timers.
        """
        one_way = self._fact(df, ('one_way', direction, cand.hma_cross_time),
                             lambda: self._one_way_reason(df, direction, cand.hma_cross_time))
        if one_way:
            self._cancel_base_candidate(tf, direction, one_way)
            return True, None, None

        bars_since = self._bars_since(df, cand.true_b0_time)
        if bars_since is None:
            return False, None, None
        if bars_since > self.max_bars:
            self._cancel_base_candidate(
                tf, direction,
                f"TIMER_TRUE_B0:{bars_since}>{self.max_bars}",
            )
            return True, bars_since, None

        cross_bars = self._bars_since(df, cand.hma_cross_time)
        if cross_bars is None:
            return False, bars_since, None
        if cross_bars > self.max_bars_after_hma_cross:
            self._cancel_base_candidate(
                tf, direction,
                f"TIMER_HMA_CROSS:{cross_bars}>{self.max_bars_after_hma_cross}",
            )
            return True, bars_since, cross_bars

        return False, bars_since, cross_bars

    def _maintain_base_candidate(self, tf: str, direction: str, df: OZMarketView) -> None:
        """Maintain base-OZ survival independently of environment Watch eligibility."""
        # Each registered 1X family keeps its existing own OUT->IN timer.
        for percentile in PERCENTILES:
            reg = self.percentile_candidates.get((tf, direction, percentile))
            if reg is None or reg.invalidated:
                continue
            outin_bars = self._bars_since(df, reg.trigger_time)
            if outin_bars is None:
                continue
            if outin_bars > self.max_bars_after_outin:
                reg.invalidated = True
                logging.info(
                    "⚪ [OZ Percentile 만료] %s %s %s | %s | validation=%s trigger=%s | 후보등록 이후 %d봉 > 제한 %d봉",
                    self.symbol, tf, direction, percentile,
                    self.validation_mode, self.trigger_mode,
                    outin_bars, self.max_bars_after_outin,
                )

        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return

        # 등록된 Percentile family가 모두 만료/탈락하면 후보의 트리거 누적도 초기화합니다.
        # (TRUE B0 / HMA cross 타이머 판정은 아래에서 기존과 동일하게 계속 수행)
        alive = bool(self._active_percentile_candidates(tf, direction))
        if not alive and cand.trigger_hits:
            cand.trigger_hits = {}

        # Preserve the existing meaning of TRUE-B0/HMA-cross survival: those clocks
        # become actionable only after both structural timing events are present.
        if cand.hma_cross_time is None or cand.hma_b0_price is None:
            return
        self._refresh_true_b0(cand)
        if cand.true_b0_price is None or cand.true_b0_time is None:
            return
        cancelled, bars_since, _cross_bars = self._check_base_invalidation(tf, direction, df, cand)
        if cancelled:
            return
        if alive and not self.use_breaker:
            self._accumulate_trigger_hits(tf, df, direction, cand, bars_since)

    @staticmethod
    def _breaker_bo_break_trigger(view,direction,level,cross_time):return view.bo_break(direction,level,cross_time)

    def _process_hma_cross(self, tf: str, df: OZMarketView) -> None:
        """Track HMA6/17 cross independently so it may occur before or after OUT->IN."""
        if df is None or len(df) < 2:
            return
        observed = self._fact(df, 'hma_cross_observation', lambda: self._hma_cross_observation(df))
        if observed is None:
            return
        p6, p17, c6, c17, cross_time = observed
        observation = (cross_time, c6 > c17, c6 < c17)
        resuming = tf in self._resume_cross
        if resuming:
            self._resume_cross.remove(tf)
            self._resume_cross_seen[tf] = observation
        elif self._resume_cross_seen.get(tf) == observation:
            return
        else:
            self._resume_cross_seen.pop(tf, None)

        direction = None
        if p6 <= p17 and c6 > c17:
            direction = "LONG"
        elif p6 >= p17 and c6 < c17:
            direction = "SHORT"
        if direction is None:
            return

        # Base-TF common invalidation: an actual opposite HMA6/17 cross kills
        # the currently tracked opposite-direction OZ cycle in every profile.
        opposite = "SHORT" if direction == "LONG" else "LONG"
        self._cancel_base_candidate(
            tf, opposite,
            "OPPOSITE_HMA_CROSS:GC" if direction == "LONG" else "OPPOSITE_HMA_CROSS:DC",
        )
        if resuming:
            return  # Cancellation is valid; new cross structures are not reconstructed on restore.

        # 같은 cross가 이미 기록돼 있으면 결과와 상관없이 반환하므로(기존 순서와 동일한 결과)
        # pre-cross 극값 재구성보다 먼저 확인해 cross 봉 동안의 반복 계산을 없앱니다.
        existing = self.hma_cross_extremes.get((tf, direction))
        if existing is not None and epoch(existing[2]) == cross_time:
            return
        ext = self._fact(df, ('pre_cross_extreme', direction),
                         lambda: self._reconstruct_pre_cross_extreme(df, direction))
        if ext is None:
            return
        price, extreme_time = ext
        self.hma_cross_extremes[(tf, direction)] = (float(price), epoch(extreme_time), cross_time)

        cand = self._sync_common_candidate(tf, direction)
        if cand is not None and not cand.alerted:
            before = cand.true_b0_price
            cand.hma_b0_price = float(price)
            cand.hma_b0_time = epoch(extreme_time)
            cand.hma_cross_time = cross_time
            self._refresh_true_b0(cand)
            if before != cand.true_b0_price:
                logging.info(
                    "🔄 [OZ TRUE B0 갱신] %s %s %s | 기존=%s → HMA pre-cross=%s | TRUE=%s",
                    self.symbol, tf, direction, before, price, cand.true_b0_price,
                )

        logging.info(
            "🔵 [OZ HMA CROSS] %s %s %s | pre-cross extreme=%s @ %s | cross=%s",
            self.symbol, tf, direction, f"{price:.6f}", extreme_time, cross_time,
        )

    @staticmethod
    def _bars_since_event(view,event_time):return view.bars_since(event_time)

    @staticmethod
    def _hma_slope(df: OZMarketView) -> Optional[float]:
        if df is None or len(df) < 2:
            return None
        a = df.row(-2).get("hma_6")
        b = df.row(-1).get("hma_6")
        if not finite_number(a) or not finite_number(b):
            return None
        return float(b) - float(a)

    @staticmethod
    def _higher_tf_open_vs_hma6(df: OZMarketView, direction: str) -> bool:
        """Require the mapped 3X candle to open on the trend side of its HMA6."""
        if df is None or len(df) < 1:
            return False
        row = df.row(-1)
        o = row.get("open")
        h6 = row.get("hma_6")
        if not finite_number(o) or not finite_number(h6):
            return False
        if direction == "LONG":
            return float(o) > float(h6)
        return float(o) < float(h6)

    @staticmethod
    def _hma_aligned(df: OZMarketView, direction: str) -> bool:
        if df is None or len(df) < 1:
            return False
        h6, h17 = df.row(-1).get("hma_6"), df.row(-1).get("hma_17")
        if not finite_number(h6) or not finite_number(h17):
            return False
        if direction == "LONG":
            return float(h6) > float(h17)
        return float(h6) < float(h17)

    @staticmethod
    def _trigger_state(df: OZMarketView, direction: str, level: float, kind: str) -> str:
        """Touch only: the live candle range must include the level (NEAR 없음)."""
        if df is None or len(df) < 1 or not finite_number(level):
            return "MISS"
        row = df.row(-1)
        low, high = row.get("low"), row.get("high")
        if not all(finite_number(x) for x in (low, high)):
            return "MISS"
        if float(low) <= float(level) <= float(high):
            return "TOUCH"
        return "MISS"

    @staticmethod
    def _candle_pullback_trigger(view,cross_time,direction):return view.candle_pullback(direction,cross_time)

    @staticmethod
    def _hma6_turn_pullback_trigger(view,cross_time,direction):return view.hma6_turn(direction,cross_time)

    @staticmethod
    def _level_hma17(df: OZMarketView) -> Optional[float]:
        if df is None or len(df) < 1:
            return None
        v = df.row(-1).get("hma_17")
        return float(v) if finite_number(v) else None

    @staticmethod
    def _level_wonbi(df: OZMarketView, direction: str) -> Optional[float]:
        if df is None or len(df) < 1:
            return None
        col = "wonbi_lower" if direction == "LONG" else "wonbi_upper"
        v = df.row(-1).get(col)
        return float(v) if finite_number(v) else None

    # 누적 조건 표시 순서.
    TRIGGER_HIT_ORDER = ("B0", "HMA17", "WONBI", "CANDLE", "HMA6_TURN")
    TRIGGER_MIN_OTHER_HITS = 2

    def _current_trigger_hits(
        self,
        df: OZMarketView,
        direction: str,
        true_b0_price: Optional[float],
        hma_cross_time: Optional[epoch],
        b0_eligible: bool,
    ) -> dict[str, str]:
        """현재 관측 시점에 TRUE인 OZ 트리거 조건 (터치만 허용)."""
        key = ('trigger_hits', direction, true_b0_price, hma_cross_time, bool(b0_eligible))
        return dict(self._fact(df, key, lambda: self._compute_trigger_hits(
            df, direction, true_b0_price, hma_cross_time, b0_eligible)))

    def _compute_trigger_hits(
        self,
        df: OZMarketView,
        direction: str,
        true_b0_price: Optional[float],
        hma_cross_time: Optional[epoch],
        b0_eligible: bool,
    ) -> dict[str, str]:
        hits: dict[str, str] = {}
        if b0_eligible and self._trigger_state(df, direction, true_b0_price, "B0") == "TOUCH":
            hits["B0"] = "B0"
        hma17 = self._level_hma17(df)
        if hma17 is not None and self._trigger_state(df, direction, hma17, "HMA17") == "TOUCH":
            hits["HMA17"] = "HMA17"
        wonbi_level = self._level_wonbi(df, direction)
        if wonbi_level is not None and self._trigger_state(df, direction, wonbi_level, "WONBI") == "TOUCH":
            hits["WONBI"] = "WONBI"
        candle_trigger = self._candle_pullback_trigger(df, hma_cross_time, direction)
        if candle_trigger in {"ENGULF_PLUS_BEAR", "ENGULF_PLUS_BULL"}:
            hits["CANDLE"] = "하락장악+추가음봉" if direction == "LONG" else "상승장악+추가양봉"
        elif candle_trigger in {"THREE_BEAR", "THREE_BULL"}:
            hits["CANDLE"] = "3연속음봉" if direction == "LONG" else "3연속양봉"
        if self._hma6_turn_pullback_trigger(df, hma_cross_time, direction):
            hits["HMA6_TURN"] = "HMA6 하방꺾임" if direction == "LONG" else "HMA6 상방꺾임"
        return hits

    def _accumulate_trigger_hits(
        self,
        tf: str,
        df: OZMarketView,
        direction: str,
        cand: Candidate,
        bars_since: Optional[int],
    ) -> None:
        """OZ 후보가 살아있는 동안 조건 충족을 누적합니다(동시조건 아님)."""
        b0_eligible = bars_since is not None and bars_since >= self.min_bars
        current = self._current_trigger_hits(
            df, direction, cand.true_b0_price, cand.hma_cross_time, b0_eligible,
        )
        new_keys = [key for key in current if key not in cand.trigger_hits]
        for key in new_keys:
            cand.trigger_hits[key] = current[key]
        if new_keys:
            logging.info(
                "🟠 [OZ 트리거 누적] %s %s %s | validation=%s trigger=%s | 신규=%s | 누적=%s",
                self.symbol, tf, direction, self.validation_mode, self.trigger_mode,
                ",".join(new_keys), ",".join(self._ordered_hit_keys(cand.trigger_hits)),
            )

    @classmethod
    def _ordered_hit_keys(cls, hits: dict[str, str]) -> list[str]:
        return [key for key in cls.TRIGGER_HIT_ORDER if key in hits]

    def _final_trigger_decision(
        self,
        df: OZMarketView,
        direction: str,
        cand: Candidate,
    ) -> Optional[FinalTriggerDecision]:
        """Evaluate the profile final-trigger family without sending anything."""
        if self.use_breaker:
            if not self._fact(df, ('bo_break', direction, cand.true_b0_price, cand.hma_cross_time),
                              lambda: self._breaker_bo_break_trigger(
                                  df, direction, cand.true_b0_price, cand.hma_cross_time)):
                return None
            return FinalTriggerDecision(trigger_name="BO_BREAK", b0_state="BREAK")

        # The caller already enforced MIN_BARS since TRUE B0, so the live B0 touch is eligible.
        current = self._current_trigger_hits(
            df, direction, cand.true_b0_price, cand.hma_cross_time, True,
        )
        hits = dict(cand.trigger_hits)
        for key, label in current.items():
            hits.setdefault(key, label)

        b0_state = "TOUCH" if "B0" in hits else "MISS"
        h17_state = "TOUCH" if "HMA17" in hits else "MISS"
        wonbi_state = "TOUCH" if "WONBI" in hits else "MISS"

        if "B0" in hits:
            trigger_name = "B0"
        else:
            others = [key for key in self._ordered_hit_keys(hits) if key != "B0"]
            if len(others) < self.TRIGGER_MIN_OTHER_HITS:
                return None
            trigger_name = ", ".join(hits[key] for key in others)

        return FinalTriggerDecision(
            trigger_name=trigger_name,
            b0_state=b0_state,
            h17_state=h17_state,
            wonbi_state=wonbi_state,
        )

    def _candidate_completion_decision(
        self,
        data: dict,
        tf: str,
        direction: str,
        *,
        require_external: bool,
        commit_validation: bool,
    ) -> Optional[CandidateCompletionDecision]:
        """Run the existing completion semantics without coupling them to Telegram delivery.

        ``commit_validation=False`` is used only by the passive/silent probe. It applies
        the same NORMAL/BLIND predicates and selected trigger-profile filters to decide whether the cycle is complete, while
        leaving the existing percentile validation state untouched until an environment
        is actually eligible for the normal evaluation path.
        """
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return None

        df = data.get(tf)
        if df is None or len(df) < 2:
            return None

        # HMA cross and OUT->IN remain independent timing events.
        if cand.hma_cross_time is None or cand.hma_b0_price is None:
            return None
        self._refresh_true_b0(cand)
        if cand.true_b0_price is None or cand.true_b0_time is None:
            return None

        # Base survival is maintained every loop before this function. MIN_BARS keeps
        # its existing final-evaluation meaning in both normal and silent completion.
        bars_since = self._bars_since(df, cand.true_b0_time)
        cross_bars = self._bars_since(df, cand.hma_cross_time)
        if bars_since is None or cross_bars is None or bars_since < self.min_bars:
            return None

        active_regs = self._active_percentile_candidates(tf, direction)
        if not active_regs:
            return None

        # ------------------------------------------------------------------
        # LAYER 2. Existing validation mode.
        # NORMAL = mapped middle OUT + upper IN + middle open/HMA6 gate.
        # BLIND  = no upper-frame validation at all.
        # ------------------------------------------------------------------
        matched_regs: list[PercentileCandidate] = []
        if self.validation_mode == "NORMAL":
            tf3, tf6 = TF_MAP[tf]
            df3, df6 = data.get(tf3), data.get(tf6)
            if any(x is None or len(x) < 2 for x in (df3, df6)):
                return None

            states3 = self._live_states(df3)
            states6 = self._live_states(df6)
            target_out = "LOWER_OUT" if direction == "LONG" else "UPPER_OUT"

            for percentile in PERCENTILES:
                reg = self.percentile_candidates.get((tf, direction, percentile))
                if reg is None or reg.invalidated:
                    continue

                mid_is_out = states3.get(percentile) == target_out
                upper_is_in = states6.get(percentile) == "IN"

                if not reg.mid_validated:
                    if not mid_is_out:
                        if commit_validation:
                            reg.invalidated = True
                            logging.info(
                                "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 중위TF(%s) OUT 아님",
                                self.symbol, tf, direction, percentile, tf3,
                            )
                        continue
                    if commit_validation:
                        reg.mid_validated = True

                if not mid_is_out:
                    if commit_validation:
                        reg.invalidated = True
                        logging.info(
                            "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 중위TF(%s) OUT 유지 실패",
                            self.symbol, tf, direction, percentile, tf3,
                        )
                    continue

                if not upper_is_in:
                    if commit_validation:
                        reg.invalidated = True
                        logging.info(
                            "⚪ [OZ 상위검증 탈락] %s %s %s | %s | 상위TF(%s) IN 유지 실패",
                            self.symbol, tf, direction, percentile, tf6,
                        )
                    continue

                # REGIME은 기존 NORMAL 검증을 그대로 통과한 family에만 추가 적용합니다.
                # 이 조건 실패는 NORMAL 후보를 무효화하지 않고 현재 시점의 추가 필터 실패로만 처리합니다.
                if self.use_regime and not self._fact(
                    df6, ('regime', percentile, direction),
                    lambda: regime_filter(self._live_row(df6), percentile, direction),
                ):
                    continue

                matched_regs.append(reg)

            if not matched_regs:
                return None
            if not self._fact(df3, ('open_vs_hma6', direction),
                              lambda: self._higher_tf_open_vs_hma6(df3, direction)):
                return None
        else:
            # BLIND: active base-TF registrations alone count.
            matched_regs = [reg for reg in active_regs if not reg.invalidated]
            if matched_regs and self.use_regime:
                # 무지성 레짐: 중위/상위 검증은 무시하고 상위 TF 레짐 필터만 적용합니다.
                df6 = data.get(TF_MAP[tf][1])
                if df6 is None or len(df6) < 1:
                    return None
                matched_regs = [
                    reg for reg in matched_regs
                    if self._fact(df6, ('regime', reg.percentile, direction),
                                  lambda p=reg.percentile: regime_filter(self._live_row(df6), p, direction))
                ]
            if not matched_regs:
                return None

        # SUPER: 기준 프레임 레짐밴드 중심선 교차. 실패는 영구 무효화가 아닌 현재 시점 필터 실패입니다.
        if self.use_super:
            matched_regs = [
                reg for reg in matched_regs
                if self._super_fact(df, reg.percentile, direction, self._family_true_b0_time(cand, reg))
            ]
            if not matched_regs:
                return None

        # For the normal path, preserve the existing mutation semantics exactly:
        # failed NORMAL families were invalidated above, then the common TRUE B0 is
        # rebuilt from the surviving registrations. The silent probe computes the
        # same surviving structure on a temporary Candidate so it does not alter the
        # live validation state merely because no environment is present.
        if self.use_regime or self.use_super:
            # REGIME/SUPER filter failures are not permanent invalidations, so they remain in the
            # base registration state but must not participate in this completion decision.
            surviving_regs = matched_regs
        else:
            surviving_regs = self._active_percentile_candidates(tf, direction) if commit_validation else matched_regs
        if not surviving_regs:
            return None
        chosen_reg = (
            min(surviving_regs, key=lambda x: x.outin_b0_price)
            if direction == "LONG"
            else max(surviving_regs, key=lambda x: x.outin_b0_price)
        )

        if commit_validation:
            cand.outin_b0_price = float(chosen_reg.outin_b0_price)
            cand.outin_b0_time = epoch(chosen_reg.outin_b0_time)
            cand.trigger_indicators = {x.percentile for x in surviving_regs}
            self._refresh_true_b0(cand)
            eval_cand = cand
        else:
            eval_cand = Candidate(
                direction=cand.direction,
                outin_b0_price=float(chosen_reg.outin_b0_price),
                outin_b0_time=epoch(chosen_reg.outin_b0_time),
                trigger_time=cand.trigger_time,
                trigger_indicators={x.percentile for x in surviving_regs},
                hma_b0_price=cand.hma_b0_price,
                hma_b0_time=cand.hma_b0_time,
                hma_cross_time=cand.hma_cross_time,
                trigger_hits=dict(cand.trigger_hits),
            )
            self._refresh_true_b0(eval_cand)

        if eval_cand.true_b0_price is None or eval_cand.true_b0_time is None:
            return None

        # External liquidity is an environment/final-evaluation gate. Silent completion
        # deliberately does not manufacture or require an external Watch; the normal
        # path keeps the existing second ATR qualification unchanged.
        if require_external and not self.watch.validate_external_true_b0(
            self.symbol, tf, direction, self.validation_mode, self.trigger_mode, eval_cand.true_b0_price
        ):
            return None

        consensus_count = len(matched_regs)
        consensus_grade = {4: "S", 3: "A", 2: "B", 1: "C"}.get(consensus_count)
        if consensus_grade is None:
            return None

        # Base HMA alignment remains the existing live entry/completion gate.
        if not self._fact(df, ('hma_aligned', direction), lambda: self._hma_aligned(df, direction)):
            return None

        trigger = self._final_trigger_decision(df, direction, eval_cand)
        if trigger is None:
            return None

        return CandidateCompletionDecision(
            grade=consensus_grade,
            consensus_count=consensus_count,
            matched_trigger_contexts=tuple(reg.percentile for reg in matched_regs),
            trigger=trigger,
            bars_since=bars_since,
            cross_bars=cross_bars,
            true_b0_price=float(eval_cand.true_b0_price),
            true_b0_time=epoch(eval_cand.true_b0_time),
        )

    def _silent_consume_candidate(
        self,
        tf: str,
        direction: str,
        decision: CandidateCompletionDecision,
        reason: str,
    ) -> None:
        """Consume a completed OZ cycle without Telegram/Watch delivery."""
        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return
        alert_key = (tf, direction, decision.true_b0_time)
        self.alert_keys.add(alert_key)
        cand.completed_outside_window = True
        self._clear_percentile_candidates(tf, direction)
        logging.info(
            "⚫ [OZ silent consume] %s %s %s | validation=%s trigger_mode=%s | reason=%s | 최종트리거=%s | TRUE_B0=%.6f | 완성Percentile=%s",
            self.symbol, tf, direction, self.validation_mode, self.trigger_mode,
            reason, decision.trigger.trigger_name, decision.true_b0_price,
            "/".join(decision.matched_trigger_contexts),
        )

    def _probe_silent_completion(self, data: dict, tf: str, direction: str, reason: str) -> bool:
        decision = self._candidate_completion_decision(
            data, tf, direction, require_external=False, commit_validation=False
        )
        if decision is None:
            return False
        self._silent_consume_candidate(tf, direction, decision, reason)
        return True

    def _evaluate_candidate(self, data: dict, tf: str, direction: str) -> None:
        decision = self._candidate_completion_decision(
            data, tf, direction, require_external=True, commit_validation=True
        )
        if decision is None:
            return

        cand = self.candidates.get((tf, direction))
        if cand is None or cand.alerted or cand.completed_outside_window:
            return

        alert_key = (tf, direction, decision.true_b0_time)
        if alert_key in self.alert_keys:
            cand.alerted = True
            return

        logging.info(
            "[OZ 판정] %s · %s · %s | %s급 | validation=%s trigger_mode=%s | 완성올존=%d/4 | 최종트리거=%s | TRUE_B0=%s | HMA17=%s | 원비=%s | B0bars=%d | CROSSbars=%d | 완성Percentile=%s",
            self.symbol, tf, "매수" if direction == "LONG" else "매도",
            decision.grade, self.validation_mode, self.trigger_mode,
            decision.consensus_count, decision.trigger.trigger_name,
            decision.trigger.b0_state, decision.trigger.h17_state, decision.trigger.wonbi_state,
            decision.bars_since, decision.cross_bars, "/".join(decision.matched_trigger_contexts),
        )

        df = data.get(tf)
        if cand.completion_time is None:
            cand.completion_time = self._source_time

        fired = self.watch.try_fire(
            self.symbol,
            tf,
            direction,
            decision.grade,
            self.validation_mode,
            self.trigger_mode,
            trigger_name=decision.trigger.trigger_name,
            alert_identity=identity(self.symbol, tf, direction, self.validation_mode,
                                    self.trigger_mode, timestamp_text(decision.true_b0_time)),
            completion_time=cand.completion_time,
            indicators_text=", ".join(decision.matched_trigger_contexts),
            current_price=(
                float(df.row(-1).get("close"))
                if df is not None and not df.empty and finite_number(df.row(-1).get("close"))
                else None
            ),
        )
        if fired:
            self.alert_keys.add(alert_key)
            cand.alerted = True
            self._clear_percentile_candidates(tf, direction)

    def run_once(self,revision,watched_tfs,data,evaluate_tfs=None):
        self._in_cycle=True
        try:return self._run_once(revision,watched_tfs,data,self.base_tfs if evaluate_tfs is None else evaluate_tfs)
        finally:self._in_cycle=False

    def _run_once(self, revision: int, watched_tfs: Iterable[str], data, evaluate_tfs):
        # watched_tfs is environment eligibility only. Base OZ tracking always uses
        # the monitor's full supported base-TF range.
        watched = [tf for tf in watched_tfs if tf in TF_MAP]

        needed = set(self.base_tfs)
        if self.validation_mode == "NORMAL":
            # Silent completion must preserve the existing NORMAL meaning even when
            # no environment Watch exists. The mapped TF union adds only confirmation
            # feeds; Base OZ tracking itself remains the unchanged TF_MAP.keys() set.
            for tf in self.base_tfs:
                tf3, tf6 = TF_MAP[tf]
                needed.update((tf3, tf6))
        elif self.use_regime:
            # 무지성 레짐은 상위 TF 레짐 필터용 feed만 추가합니다.
            for tf in self.base_tfs:
                needed.add(TF_MAP[tf][1])

        # Pending/active external setups also need their source TF so ATR14 snapshot and
        # 1.5x survival are evaluated here, not in SWEEP.
        needed.update(self.watch.external_source_tfs_for_profile(
            self.symbol, self.validation_mode, self.trigger_mode
        ))
        required_tfs = sorted(needed)

        if not data or any(tf not in data for tf in required_tfs):
            return

        missing_base = [tf for tf in self.base_tfs if tf not in data]
        if missing_base:
            logging.warning("[%s] OZ BASE 누락 TF: %s", self.symbol, ",".join(missing_base))

        self.watch.update_external_market(
            self.symbol, self.validation_mode, self.trigger_mode, data
        )

        # BASE OZ ENGINE: always-on, independent of environment Watch existence/direction.
        # 계산 단계 (수정본6):
        #   1) 후보 전 최소 상태: HMA6/17 cross 관측과 4개 Percentile OUT/IN 상태만 추적합니다.
        #      이 값은 공통 OZ Fact(OZFactMemo)로 종목당 1회 계산되고 16개 프로필이 공유합니다.
        #   2) 후보 생성 후(OUT->IN 등록): B0·타이머·트리거 누적·탈락조건(_maintain_base_candidate),
        #      중상위 검증·Regime·Super·최종 트리거(_candidate_completion_decision)를 계산합니다.
        #      후보가 없으면 두 함수는 첫 줄에서 바로 반환합니다. 프로필 무관 값은 역시 공통 Fact입니다.
        for tf in evaluate_tfs:
            df = data.get(tf)
            if df is None:
                continue
            self._process_hma_cross(tf, df)
            self._process_out_in(tf, df)
            for direction in ("LONG", "SHORT"):
                self._maintain_base_candidate(tf, direction, df)

        # COMPLETION / ENVIRONMENT GATE:
        # - no eligible environment: run the same profile completion semantics silently;
        # - False->True eligibility: probe once before firing, so a trigger already
        #   accumulated in the live candle is consumed instead of being back-dated;
        # - continuously eligible environment: keep the existing normal final path.
        for tf in evaluate_tfs:
            if tf not in data:
                continue

            allowed: set[str] = set()
            if tf in watched:
                _current_revision, current_tfs = self.watch.snapshot_for_symbol(
                    self.symbol, self.validation_mode, self.trigger_mode
                )
                if tf in current_tfs:
                    allowed = self.watch.allowed_directions(
                        self.symbol, tf, self.validation_mode, self.trigger_mode
                    )

            for direction in ("LONG", "SHORT"):
                key = (tf, direction)
                previous_environment_ids = self.environment_identities.get(key, frozenset())
                is_allowed = direction in allowed

                if not is_allowed:
                    self.environment_identities[key] = frozenset()
                    self._probe_silent_completion(data, tf, direction, "NO_ENVIRONMENT")
                    continue

                current_environment_ids = self.watch.allowed_environment_identities(
                    self.symbol, tf, direction, self.validation_mode, self.trigger_mode
                )
                fresh_environment = (
                    not previous_environment_ids
                    or previous_environment_ids.isdisjoint(current_environment_ids)
                )

                # On first eligibility, or when every previously eligible environment identity
                # was replaced, the current OHLC snapshot may already contain a final trigger
                # that predates the new environment. A surviving identity keeps continuity.
                if fresh_environment and self._probe_silent_completion(
                    data, tf, direction, "PREEXISTING_AT_ENVIRONMENT_ACTIVATION"
                ):
                    self.environment_identities[key] = current_environment_ids
                    continue

                self.environment_identities[key] = current_environment_ids
                if direction == "LONG" and self.alert_long:
                    self._evaluate_candidate(data, tf, direction)
                elif direction == "SHORT" and self.alert_short:
                    self._evaluate_candidate(data, tf, direction)

