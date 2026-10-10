"""Client DataFrame adapter for the remaining COMPOSER interface.

Wonbi uses the shared config-only Fact; native OUT and slope stay authoritative.
"""
import threading
from typing import Iterable
import numpy as np
import pandas as pd
from staff_schema import BASE_COLUMNS
from indicator_facts import add_ema_derived, add_atr14_feature, add_supertrend, add_mt5_basis_slopes, wonbi_bands, add_native_band_state_features
from strategy_FVG import add_fvg_features
WONBI_DEFAULT_SIGMA = 3.0

def apply_requested_features(df: pd.DataFrame, indicators: Iterable[str], wonbi_sigma: float = WONBI_DEFAULT_SIGMA, *, prepared_atr=None, prepared_wonbi=None) -> pd.DataFrame:
    out = df.copy()
    req = {str(x).strip().upper() for x in indicators if str(x).strip()}
    bands = prepared_wonbi if prepared_wonbi is not None else wonbi_bands(
        df['open_band_4_mid'],df['wonbi_upper'],df['wonbi_lower'],wonbi_sigma)
    for name, value in bands.items():out[name]=value

    # MT5 값의 단순 파생값은 필요 지표 요청 시 생성.
    if "EMA" in req:
        out = add_ema_derived(out)
    for name in ('PRICE','RSI','STO','DI'):
        if name in req:out=add_native_band_state_features(out,'price' if name=='PRICE' else name)
    if req.intersection({"RSI", "STO", "DI"}):
        out = add_mt5_basis_slopes(out)

    # Client-only ATR14_GENERAL and optional features; STAFF performs no arithmetic.
    if prepared_atr is None:
        out = add_atr14_feature(out)
    else:
        out['atr_14'] = prepared_atr.copy(deep=True)
    if "SUPERTREND" in req:
        out = add_supertrend(out)
    if "FVG" in req:
        out = add_fvg_features(out)

    return out

MT5_REQUIRED_BY_INDICATOR = {
    # TREND/FVG/SWEEP 2.0은 OHLCV만 요청할 수 있으므로 지표를 무조건 요구하지 않습니다.
    "EMA": ["ema_20", "ema_50", "ema_200"],
    # PRICE percentile을 요청하는 OZ는 HMA6/17 구조도 함께 사용합니다.
    "PRICE": [
        "price_hma_6", "price_band_lower", "price_band_upper", "hma_6", "hma_17",
        "price_regime_basis", "price_regime_upper", "price_regime_lower",
    ],
    # 전략 HMA 공용 계약: MT5 EA가 OPEN 기준으로 전달하는 6/17/50/168을 모두 검증합니다.
    "HMA": ["hma_6", "hma_17", "hma_50", "hma_168"],
    "RSI": ["RSI_val", "RSI_db", "RSI_ub", "RSI_basis", "RSI_regime_upper", "RSI_regime_lower"],
    "STO": ["STO_val", "STO_db", "STO_ub", "STO_basis", "STO_regime_upper", "STO_regime_lower"],
    "DI": ["DI_val", "DI_db", "DI_ub", "DI_basis", "DI_regime_upper", "DI_regime_lower"],
    # WONBI는 EA가 계산한 원본 열만 사용합니다.
    "WONBI": ['open_band_4_mid', 'wonbi_upper', 'wonbi_lower'],
}

def validate_mt5_snapshot(df: pd.DataFrame, symbol: str, tf: str, indicators: Iterable[str]) -> None:
    if df is None or df.empty:
        raise RuntimeError(f"[{symbol} {tf}] MT5 데이터 없음")

    # 모든 엔진이 공유하는 최소 계약. 선택 지표와 무관하게 OHLCV는 반드시 존재해야 합니다.
    missing_base = [c for c in BASE_COLUMNS if c not in df.columns]
    if missing_base:
        raise RuntimeError(
            f"[{symbol} {tf}] MT5 기본 컬럼 누락: {', '.join(missing_base)}"
        )

    requested = {str(ind).strip().upper() for ind in indicators if str(ind).strip()}
    required: list[str] = []
    for ind in requested:
        required.extend(MT5_REQUIRED_BY_INDICATOR.get(ind, []))

    required = list(dict.fromkeys(required))
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"[{symbol} {tf}] 요청 지표 컬럼 누락: {', '.join(missing)} | "
            f"요청={','.join(sorted(requested)) or 'OHLCV'}"
        )

    bad = []
    for col in required:
        # 최근 20개 봉이 전부 NaN이면 그 지표를 요구한 요청만 실패시킵니다.
        if df[col].tail(20).isna().all():
            bad.append(col)
        elif not np.isfinite(float(df[col].iloc[-1])):
            bad.append(col)

    if bad:
        raise RuntimeError(
            f"[{symbol} {tf}] 요청 지표값 비정상(최근 20봉 전부 NaN): {', '.join(bad)} | "
            f"요청={','.join(sorted(requested)) or 'OHLCV'}"
        )


class StaffCompat:
    """Own WATCH history per client; copy every response and serialize requests."""
    def __init__(self, client):
        self.client = client
        self._watch_ma_features = None
        self._lock = threading.RLock()


    def _compose(self, req, symbol, indicators, prepared, sigma, *, prepared_atr=None, prepared_wonbi=None):
        response = {}
        # Canonical MA requests are opt-in; the pipe snapshot, legacy indicators,
        # and existing common derived calculations retain their original scope.
        from watch_ma import canonical_ma
        ma_names = [canonical_ma(name) for name in indicators
                    if name.startswith(("SMA", "WMA", "EMA", "HMA", "SMMA"))
                    and name not in {"EMA", "HMA"}]
        history_rows = req.get("watch_ma_history_rows", 0)
        if ma_names and (type(history_rows) is not int or history_rows < 0):
            raise ValueError("WATCH MA history requirement must be a nonnegative integer")
        if ma_names and self._watch_ma_features is None:
            from watch_ma_features import WatchMAFeatures
            self._watch_ma_features = WatchMAFeatures()

        # Reject an unavailable multi-TF request before doing any derived calculations.
        for tf, df in prepared.items():
            # 기존 SWEEP/FVG가 기대하는 동일 컬럼명을 유지합니다.
            enriched = apply_requested_features(df, indicators, wonbi_sigma=sigma,
                prepared_atr=None if prepared_atr is None else prepared_atr[tf],
                prepared_wonbi=None if prepared_wonbi is None else prepared_wonbi[tf])
            if ma_names:
                ma_frame = self._watch_ma_features.prepare(symbol, tf, df, ma_names, history_rows)
                # Keep common derived columns calculated on the original snapshot.
                # Expanded MA history belongs exclusively to this WATCH request.
                if len(ma_frame) == len(enriched):
                    for name in ma_names:
                        enriched[name] = ma_frame[name].to_numpy(copy=True)
                    enriched.attrs.update(ma_frame.attrs)
                else:
                    common = enriched.set_index("time")
                    for column in enriched.columns:
                        if column not in ma_frame and column != "time":
                            ma_frame[column] = ma_frame["time"].map(common[column])
                    enriched = ma_frame

            # Python 파생지표 생성 결과까지 확인
            requested = {str(x).upper() for x in indicators}
            derived_required = []
            derived_required += ["atr_14",
                                 "wonbi_mid", "wonbi_upper", "wonbi_lower"]
            if "PRICE" in requested:
                derived_required += [
                    "price_percentile_zone", "price_percentile_in",
                    "price_regime_zone", "price_regime_in", "price_regime_slope",
                ]
            if "RSI" in requested:
                derived_required += [
                    "RSI_percentile_zone", "RSI_percentile_in",
                    "RSI_regime_zone", "RSI_regime_in", "RSI_regime_slope",
                ]
            if "STO" in requested:
                derived_required += [
                    "STO_percentile_zone", "STO_percentile_in",
                    "STO_regime_zone", "STO_regime_in", "STO_regime_slope",
                ]
            if "DI" in requested:
                derived_required += [
                    "DI_percentile_zone", "DI_percentile_in",
                    "DI_regime_zone", "DI_regime_in", "DI_regime_slope",
                ]
            if "SUPERTREND" in requested:
                derived_required += ["supertrend_is_positive"]
            if "FVG" in requested:
                derived_required += ["is_bull_fvg", "is_bear_fvg", "bull_fvg_top", "bull_fvg_bot",
                                     "bear_fvg_top", "bear_fvg_bot"]

            missing_derived = [c for c in derived_required if c not in enriched.columns]
            if missing_derived:
                raise RuntimeError(
                    f"[{symbol} {tf}] THE STAFF OF MOSES 파생지표 생성 실패: {', '.join(missing_derived)}"
                )

            response[tf] = enriched

        if not response:
            raise RuntimeError(f"[{symbol}] 유효한 MT5 응답 데이터가 하나도 없음")
        return response
