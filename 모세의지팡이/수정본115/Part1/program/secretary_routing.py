"""Language routing returns commands; Composer owns every execution effect."""
from __future__ import annotations

import logging
import re

from command_interpreter import grammar_terms
from command_models import SecretaryCommand, DEFAULT_SWEEP_LEVELS
from domain_clock import time
from moses_language import syntax_literal
from watch_orchestrator import stable_id


_UNKNOWN_STRATEGY = "❓ 전략을 이해하지 못했습니다. 예: 골드 1시간 상승추세중 하단 원비에서 1분 무지성 브레이커 올존 알려줘"


class CommandRouter:
    """Mixin for the pure KimSecretary parser, with no manager or output port."""

    def _handle_fallback_strategy(self, clean, owner):
        try:
            spec = self._parse_private_strategy_local(clean, owner)
        except Exception:
            logging.exception("[Composer] 로컬 개인전략 파싱 오류")
            spec = None
        if spec is None:
            return SecretaryCommand('ERROR', message=_UNKNOWN_STRATEGY)
        return SecretaryCommand('REGISTER_STRATEGY', spec)

    def _handle_oz_intent(self, clean, owner, raw_clean=None, external_retry=False):
        # Preserve OZ chain -> timed chain -> new FVG -> strategy -> direct OZ.
        try:
            oz_chain = self._parse_oz_watch_chain_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] OZ 연쇄/예약 명령 거부 | %s", exc)
            return SecretaryCommand('ERROR', message=f"❌ OZ 연쇄/예약 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] OZ 연쇄/예약 로컬 파싱 오류")
            oz_chain = None
        if oz_chain is not None:
            return SecretaryCommand('REGISTER_CHAIN', oz_chain)

        try:
            chain = self._parse_timed_chain_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] 시간연쇄 명령 거부 | %s", exc)
            return SecretaryCommand('ERROR', message=f"❌ 시간연쇄 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] 시간연쇄 로컬 파싱 오류")
            chain = None
        if chain is not None:
            return SecretaryCommand('REGISTER_CHAIN', chain)

        try:
            fvg_created_oz = self._parse_fvg_created_oz_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] FVG 생성→OZ 명령 거부 | %s", exc)
            return SecretaryCommand('ERROR', message=f"❌ FVG 생성→올존 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] FVG 생성→OZ 로컬 파싱 오류")
            fvg_created_oz = None
        if fvg_created_oz is not None:
            return SecretaryCommand('FVG_WATCH', fvg_created_oz)

        try:
            spec = self._parse_private_strategy_local(clean, owner)
        except Exception:
            logging.exception("[Composer] 로컬 개인전략 파싱 오류")
            spec = None
        if spec is not None:
            return SecretaryCommand('REGISTER_STRATEGY', spec)

        try:
            direct_oz = self._parse_direct_oz_watch_local(clean, owner)
        except ValueError as exc:
            logging.warning("[Composer] 직접 OZ 명령 거부 | %s", exc)
            return SecretaryCommand('ERROR', message=f"❌ 올존 감시 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] 직접 OZ 파싱 오류")
            direct_oz = None
        if direct_oz:
            return SecretaryCommand('WATCH', direct_oz)
        if not external_retry:
            return SecretaryCommand('EXTERNAL_NORMALIZE', raw_clean or clean)
        return SecretaryCommand('ERROR', message=_UNKNOWN_STRATEGY)

    def _trend_query_from_text(self, text, owner):
        low = str(text).lower()
        if syntax_literal('routing_query_trend') not in low or not any(x in low for x in grammar_terms('query_words')):
            return None
        symbol, tf = self._query_target_from_text(text, "추세")
        return {'action': 'TREND_QUERY', 'request_id': stable_id('QUERY', owner, time.time_ns()),
                'request_chat_id': owner, 'symbol': symbol, 'source_tf': tf}

    def _trend_score_query_from_text(self, text, owner):
        if not self.command_interpreter.is_trend_score_query(text):
            return None
        symbol, tf = self._query_target_from_text(text, "추세점수")
        return {'action': 'TREND_QUERY', 'request_id': stable_id('QUERY', owner, time.time_ns()),
                'request_chat_id': owner, 'purpose': 'TREND_SCORE_QUERY',
                'symbol': symbol, 'source_tf': tf,
                'requested_fields': ['trend_score', 'long_score', 'short_score']}

    def _fvg_query_from_text(self, text, owner):
        low = str(text).lower()
        if syntax_literal('routing_query_fvg') not in low or not any(x in low for x in grammar_terms('query_words')):
            return None
        symbol, tf = self._query_target_from_text(text, "FVG")
        return {'action': 'FVG_QUERY', 'request_id': stable_id('QUERY', owner, time.time_ns()),
                'request_chat_id': owner, 'symbol': symbol, 'source_tf': tf}

    def _sweep_query_from_text(self, text, owner):
        low = str(text).lower()
        if syntax_literal('routing_query_sweep') not in low or not any(x in low for x in grammar_terms('query_words')):
            return None
        symbol, tf = self._query_target_from_text(text, "SWEEP")
        london = str(self.config.get('LONDON') or self.config.get('MAIN_LONDON') or '').strip()
        newyork = str(self.config.get('NEWYORK') or self.config.get('MAIN_NEWYORK') or '').strip()
        return {'action': 'SWEEP_QUERY', 'request_id': stable_id('QUERY', owner, time.time_ns()),
                'request_chat_id': owner, 'symbol': symbol, 'source_tf': tf,
                'levels': list(DEFAULT_SWEEP_LEVELS), 'session_london': london, 'session_newyork': newyork}

    def parse(self, text, owner, external_retry=False):
        owner = str(owner or '').strip()
        raw_clean = str(text or '').strip()
        if raw_clean == syntax_literal('routing_cancel'):
            return SecretaryCommand('CANCEL_REPLY' if owner else 'IGNORE')
        # Canonical MA expressions must be validated before alias replacement.
        if owner:
            try:
                canonical_ma = self._parse_canonical_ma_watch(raw_clean, owner)
            except ValueError as exc:
                return SecretaryCommand('ERROR', message=f"❌ 조건 감시 명령 오류 · {exc}")
            if canonical_ma is not None:
                return SecretaryCommand('WATCH', canonical_ma)
        if re.search(syntax_literal('routing_retired_sweep'), raw_clean):
            return SecretaryCommand('ERROR', message="❌ 지원하지 않는 SWEEP selector입니다.")
        clean = self._normalize_command_text(raw_clean)
        if not clean or not owner:
            return SecretaryCommand('IGNORE')

        try:
            intent = self._detect_intent(clean)
        except ValueError as exc:
            logging.warning("[Composer] 명령 의도 판정 거부 | %s", exc)
            return SecretaryCommand('ERROR', message=f"❌ 조건 감시 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] 명령 의도 판정 오류")
            intent = 'FALLBACK'
        if intent == 'RESET':
            return SecretaryCommand('RESET')
        if intent == 'LIST':
            return SecretaryCommand('LIST')
        if intent == 'OZ':
            return self._handle_oz_intent(clean, owner, raw_clean=raw_clean, external_retry=external_retry)

        try:
            scheduled = self._parse_scheduled_generic_chain_local(clean, owner)
        except ValueError as exc:
            return SecretaryCommand('ERROR', message=f"❌ 예약 감시 명령 오류 · {exc}")
        except Exception:
            logging.exception("[Composer] 예약 Generic Watch 파싱 오류")
            scheduled = None
        if scheduled is not None:
            return SecretaryCommand('REGISTER_CHAIN', scheduled)

        if intent == 'CONDITION_NOTIFY':
            try:
                spec = self._parse_condition_notify_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 조건알림 명령 거부 | %s", exc)
                return SecretaryCommand('ERROR', message=f"❌ 조건알림 명령 오류 · {exc}")
            except Exception:
                logging.exception("[Composer] 조건알림 파싱 오류")
                spec = None
            if spec is not None:
                return SecretaryCommand('REGISTER_STRATEGY', spec)
            return SecretaryCommand('ERROR', message="❌ 조건알림 명령 형식이 불완전합니다")

        if intent == 'GENERIC_WATCH':
            try:
                payload = self._parse_generic_watch_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 단일 조건 명령 거부 | %s", exc)
                return SecretaryCommand('ERROR', message=f"❌ 조건 감시 명령 오류 · {exc}")
            except Exception:
                logging.exception("[Composer] 단일 조건 파싱 오류")
                payload = None
            if payload:
                kind = 'FVG_WATCH' if str(payload.get('watch_type') or '').upper() == 'FVG_NEW' else 'WATCH'
                return SecretaryCommand(kind, payload)
            return SecretaryCommand('ERROR', message=f"❌ 조건 감시 명령 오류 · {self._generic_invalid_message(clean)}")
        if intent == 'GENERIC_INVALID':
            return SecretaryCommand('ERROR', message=f"❌ 조건 감시 명령 오류 · {self._generic_invalid_message(clean)}")

        queries = {'TREND_SCORE_QUERY': (self._trend_score_query_from_text, '추세점수'),
                   'TREND_QUERY': (self._trend_query_from_text, '추세'),
                   'FVG_QUERY': (self._fvg_query_from_text, 'FVG'),
                   'SWEEP_QUERY': (self._sweep_query_from_text, 'SWEEP')}
        if intent in queries:
            parser, label = queries[intent]
            try:
                payload = parser(clean, owner)
            except ValueError as exc:
                return SecretaryCommand('ERROR', message=f"❌ {label} 조회 명령 오류 · {exc}")
            if payload is not None:
                return SecretaryCommand('QUERY', payload)
            return SecretaryCommand('ERROR', message=f"❌ {label} 조회 명령 형식이 불완전합니다")

        if intent == 'TIMED_CHAIN':
            try:
                chain = self._parse_timed_chain_local(clean, owner)
            except ValueError as exc:
                logging.warning("[Composer] 시간연쇄 명령 거부 | %s", exc)
                return SecretaryCommand('ERROR', message=f"❌ 시간연쇄 명령 오류 · {exc}")
            except Exception:
                logging.exception("[Composer] 시간연쇄 로컬 파싱 오류")
                chain = None
            if chain is not None:
                return SecretaryCommand('REGISTER_CHAIN', chain)
        if not external_retry:
            return SecretaryCommand('EXTERNAL_NORMALIZE', raw_clean)
        return self._handle_fallback_strategy(clean, owner)
