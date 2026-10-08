# -*- coding: utf-8 -*-
from __future__ import annotations
from durable_protocol import FactStream, source_health, atomic_json, read_json
from durable_protocol import Records, identity
import uuid

import json
import logging
import math
import os
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import zmq

# =============================================================================
# TREND 2.0
# =============================================================================
# 역할:
#   - 종목/타임프레임의 상승/하락 추세 강도를 독립적으로 계산합니다.
#   - WONBI/FVG/SWEEP/시간필터/OZ 로직을 포함하지 않습니다.
#   - 기존 strategy_DOUBLEB.py의 앙상블 추세 조건을 베이스로 사용합니다.
#   - 기존 DOUBLEB의 FVG 보너스는 TREND 분리를 위해 제외합니다.
#
# 기본더블비는 이 파일의 기능이 아닙니다.
#   TREND 상승 + 하단 WONBI 터치 = 기본더블비 매수
#   TREND 하락 + 상단 WONBI 터치 = 기본더블비 매도
# 위 조합은 이후 Composer가 담당합니다.
# =============================================================================

STAFF_ENDPOINT = "tcp://127.0.0.1:5555"
MANAGER_ALERT_ENDPOINT = "tcp://127.0.0.1:5556"
ZMQ_TIMEOUT_MS = 5000
MANAGER_TIMEOUT_MS = 15000
LOOP_SLEEP_SEC = 0.5
TREND_METRIC_PUSH_SEC = 1.0

# 기존 DOUBLEB 앙상블에서 FVG 보너스를 제외한 순수 추세 조건의 총 가중치 = 30점.
TREND_DENOMINATOR = 30.0
TREND_MAX_SCORE = 100.0
DEFAULT_TREND_THRESHOLD = 40.0
REQUIRED_INDS: list[str] = ["HMA"]


def load_runtime_config() -> dict[str, str]:
    """전략 통신 설정만 config.txt에서 읽습니다. 미설정 시 기존 기본값을 유지합니다."""
    path = Path(__file__).resolve().parent / "config.txt"
    config: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return config


def runtime_connection_settings(config: dict[str, str]) -> tuple[str, str, int, int]:
    staff_endpoint = str(
        config.get("STAFF_ENDPOINT", config.get("STAFF_BIND_ENDPOINT", STAFF_ENDPOINT))
    ).strip() or STAFF_ENDPOINT
    manager_endpoint = str(config.get("MANAGER_ALERT_ENDPOINT", MANAGER_ALERT_ENDPOINT)).strip() or MANAGER_ALERT_ENDPOINT
    staff_timeout_ms = int(config.get("ZMQ_TIMEOUT_MS", str(ZMQ_TIMEOUT_MS)))
    manager_timeout_ms = int(
        config.get("MANAGER_ALERT_TIMEOUT_MS", config.get("MANAGER_TIMEOUT_MS", str(MANAGER_TIMEOUT_MS)))
    )
    return staff_endpoint, manager_endpoint, staff_timeout_ms, manager_timeout_ms

# manager_KIM의 일반 Watch가 필요할 때만 요청할 수 있는 TREND 내부 지표 계약.
# 값 계산은 strategy_TREND가 소유하고, manager_KIM은 비교/조합만 담당합니다.
TREND_METRIC_FIELDS = {
    "price", "long_score", "short_score", "trend_score",
    "ema10_open", "ema50_open", "sma20_open", "wma17_open", "hma50_open", "hma50_slope",
    "supertrend", "psar", "plus_di", "minus_di", "adx",
    "linreg20", "rsi14", "cci20", "macd", "macd_signal",
    "aroon_up", "aroon_down", "vortex_plus", "vortex_minus", "vwap",
    "bop", "cmf20", "mfi14", "chop14", "hv20", "hvma20",
    "mss", "vol_state", "vol_surge",
}

MT5_TIMEFRAMES = (
    "1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m",
    "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d",
)


def normalize_tf(value: object) -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+)(분|시간|일)", raw)
    if m:
        unit = {"분": "m", "시간": "h", "일": "d"}[m.group(2)]
        raw = f"{int(m.group(1))}{unit}"
    if raw == "24h":
        raw = "1d"
    return raw if raw in MT5_TIMEFRAMES else ""


def tf_seconds(tf: str) -> int:
    m = re.fullmatch(r"(\d+)([mhd])", normalize_tf(tf))
    if not m:
        return 10**12
    n = int(m.group(1))
    unit = m.group(2)
    return n * (60 if unit == "m" else 3600 if unit == "h" else 86400)


def sort_timeframes(values) -> tuple[str, ...]:
    normalized = []
    seen: set[str] = set()
    for value in values:
        tf = normalize_tf(value)
        if tf and tf not in seen:
            seen.add(tf)
            normalized.append(tf)
    normalized.sort(key=lambda x: (tf_seconds(x), x))
    return tuple(normalized)


def finite(v) -> bool:
    try:
        x = float(v)
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


class StaffClient:
    def __init__(self, endpoint: str = STAFF_ENDPOINT, timeout_ms: int = ZMQ_TIMEOUT_MS):
        self.endpoint = endpoint
        self.timeout_ms = int(timeout_ms)
        self.context = zmq.Context.instance()
        self.socket = None
        self._connect()

    def _connect(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close(0)
            except Exception:
                pass
        self.socket = self.context.socket(zmq.REQ)
        self.socket.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        self.socket.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(self.endpoint)

    def request(self, symbol: str, timeframes, indicators):
        try:
            self.socket.send_pyobj({
                "symbol": symbol,
                "timeframes": list(timeframes),
                "indicators": list(indicators),
            })
            data = self.socket.recv_pyobj()
            if isinstance(data, dict) and data.get("error"):
                logging.warning("[%s] Staff 오류: %s", symbol, data["error"])
                return None
            return data
        except (zmq.Again, zmq.ZMQError):
            logging.exception("[%s] Staff 요청 실패 - 재연결", symbol)
            self._connect()
            return None
        except Exception:
            logging.exception("[%s] Staff 응답 처리 실패", symbol)
            return None


class ManagerClient:
    """TREND 상태/조회 결과를 김매니저 알림관문으로 전달합니다."""

    def __init__(self, endpoint: str = MANAGER_ALERT_ENDPOINT, timeout_ms: int = MANAGER_TIMEOUT_MS):
        self.endpoint = endpoint
        self.timeout_ms = int(timeout_ms)
        self.context = zmq.Context.instance()
        self.local = threading.local()
        self.stream = FactStream(Path(__file__).resolve().parent / "logs" / "trend_stream.json", "TREND")

    def _new_socket(self):
        old = getattr(self.local, "socket", None)
        if old is not None:
            try:
                old.close(0)
            except Exception:
                pass
        sock = self.context.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        sock.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(self.endpoint)
        self.local.socket = sock
        return sock

    def _socket(self):
        return getattr(self.local, "socket", None) or self._new_socket()

    def send(self, event: dict) -> dict:
        self.stream.prepare(event)
        try:
            sock = self._socket()
            sock.send_pyobj(event)
            reply = sock.recv_pyobj()
            return reply if isinstance(reply, dict) else {"ok": False, "error": "invalid_ack"}
        except (zmq.Again, zmq.ZMQError):
            logging.exception("[TREND] 김매니저 ACK 실패 - REQ 재연결")
            self._new_socket()
            return {"ok": False, "error": "manager_timeout"}
        except Exception:
            logging.exception("[TREND] 김매니저 이벤트 전송 오류")
            self._new_socket()
            return {"ok": False, "error": "manager_error"}

    def verify_connection(self) -> bool:
        reply = self.send({"kind": "PING", "strategy": "TREND"})
        ok = bool(reply.get("ok"))
        logging.info("%s [TREND] 김매니저 알림관문", "✅" if ok else "❌")
        return ok

def ema(s, n): return pd.to_numeric(s, errors="coerce").ewm(span=n, adjust=False, min_periods=n).mean()
def sma(s, n): return pd.to_numeric(s, errors="coerce").rolling(n).mean()
def wma(s, n):
    s = pd.to_numeric(s, errors="coerce")
    w = np.arange(1, n+1, dtype=float)
    return s.rolling(n).apply(lambda x: float(np.dot(x, w)/w.sum()), raw=True)
def hma(s, n):
    n2, ns = max(1,n//2), max(1,int(math.sqrt(n)))
    return wma(2*wma(s,n2)-wma(s,n), ns)

def true_range(df):
    h,l,c = [pd.to_numeric(df[x], errors="coerce") for x in ("high","low","close")]
    pc = c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)

def rma(s,n): return pd.to_numeric(s, errors="coerce").ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def dmi(df,n=14):
    h,l = pd.to_numeric(df["high"],errors="coerce"), pd.to_numeric(df["low"],errors="coerce")
    up, down = h.diff(), -l.diff()
    pdm = up.where((up>down)&(up>0),0.0)
    mdm = down.where((down>up)&(down>0),0.0)
    atr = rma(true_range(df),n)
    plus, minus = 100*rma(pdm,n)/atr, 100*rma(mdm,n)/atr
    dx = 100*(plus-minus).abs()/(plus+minus).replace(0,np.nan)
    return plus, minus, rma(dx,n)

def rsi(s,n=14):
    s = pd.to_numeric(s,errors="coerce")
    d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)
    rs=rma(up,n)/rma(dn,n)
    return 100-(100/(1+rs))

def cci(df,n=20):
    tp=(pd.to_numeric(df.high,errors="coerce")+pd.to_numeric(df.low,errors="coerce")+pd.to_numeric(df.close,errors="coerce"))/3
    ma=tp.rolling(n).mean()
    md=tp.rolling(n).apply(lambda x: np.mean(np.abs(x-np.mean(x))),raw=True)
    return (tp-ma)/(0.015*md.replace(0,np.nan))

def linreg(s,n=20):
    s=pd.to_numeric(s,errors="coerce"); x=np.arange(n,dtype=float)
    return s.rolling(n).apply(lambda y: np.polyfit(x,y,1)[0]*(n-1)+np.polyfit(x,y,1)[1],raw=True)

def psar(df, af0=.02, step=.02, afmax=.2):
    h=pd.to_numeric(df.high,errors="coerce").to_numpy(float)
    l=pd.to_numeric(df.low,errors="coerce").to_numpy(float)
    n=len(df); out=np.full(n,np.nan)
    if n<2: return pd.Series(out,index=df.index)
    bull=True; af=af0; ep=h[0]; sar=l[0]; out[0]=sar
    for i in range(1,n):
        sar=sar+af*(ep-sar)
        if bull:
            if i>=2: sar=min(sar,l[i-1],l[i-2])
            else: sar=min(sar,l[i-1])
            if l[i]<sar:
                bull=False; sar=ep; ep=l[i]; af=af0
            elif h[i]>ep:
                ep=h[i]; af=min(af+step,afmax)
        else:
            if i>=2: sar=max(sar,h[i-1],h[i-2])
            else: sar=max(sar,h[i-1])
            if h[i]>sar:
                bull=True; sar=ep; ep=h[i]; af=af0
            elif l[i]<ep:
                ep=l[i]; af=min(af+step,afmax)
        out[i]=sar
    return pd.Series(out,index=df.index)

def supertrend_dir(df, period=10, mult=3.0):
    h,l,c=[pd.to_numeric(df[x],errors="coerce").to_numpy(float) for x in ("high","low","close")]
    tr=true_range(df).to_numpy(float)
    atr=pd.Series(tr).ewm(alpha=1/period,adjust=False,min_periods=period).mean().to_numpy()
    hl2=(h+l)/2; bu=hl2+mult*atr; bl=hl2-mult*atr
    fu=bu.copy(); fl=bl.copy(); bull=np.ones(len(df),dtype=bool)
    for i in range(1,len(df)):
        if not np.isfinite(atr[i-1]): continue
        fu[i]=bu[i] if (bu[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=bl[i] if (bl[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        if c[i]>fu[i-1]: bull[i]=True
        elif c[i]<fl[i-1]: bull[i]=False
        else: bull[i]=bull[i-1]
    return pd.Series(bull,index=df.index)

def atr_trend_state(df):
    c=pd.to_numeric(df.close,errors="coerce"); h=pd.to_numeric(df.high,errors="coerce"); l=pd.to_numeric(df.low,errors="coerce")
    e=ema((h+l)/2,10); a=rma(true_range(df),14); bu=e+2*a; bl=e-2*a
    state=1; final_u=np.nan; final_l=np.nan; states=[]
    for i in range(len(df)):
        if not finite(bu.iloc[i]) or not finite(bl.iloc[i]):
            states.append(state); continue
        if state==1:
            final_l=max(float(bl.iloc[i]), float(final_l) if finite(final_l) else float(bl.iloc[i]))
            final_u=float(bu.iloc[i])
        else:
            final_u=min(float(bu.iloc[i]), float(final_u) if finite(final_u) else float(bu.iloc[i]))
            final_l=float(bl.iloc[i])
        prev_u=final_u; prev_l=final_l
        if state==-1 and finite(c.iloc[i]) and float(c.iloc[i])>prev_u: state=1
        elif state==1 and finite(c.iloc[i]) and float(c.iloc[i])<prev_l: state=-1
        states.append(state)
    return pd.Series(states,index=df.index)

def mss_state(df,left=5,right=5):
    h=pd.to_numeric(df.high,errors="coerce"); l=pd.to_numeric(df.low,errors="coerce"); c=pd.to_numeric(df.close,errors="coerce")
    last_ph=last_pl=None; state=0; states=[]
    for i in range(len(df)):
        p=i-right
        if p>=left and p+right<len(df):
            hw=h.iloc[p-left:p+right+1]; lw=l.iloc[p-left:p+right+1]
            if hw.notna().all() and float(h.iloc[p])==float(hw.max()): last_ph=float(h.iloc[p])
            if lw.notna().all() and float(l.iloc[p])==float(lw.min()): last_pl=float(l.iloc[p])
        cv=c.iloc[i]
        if finite(cv):
            if last_ph is not None and float(cv)>last_ph: state=1
            elif last_pl is not None and float(cv)<last_pl: state=-1
        states.append(state)
    return pd.Series(states,index=df.index)

def anchored_vwap(df, tf):
    t=pd.to_datetime(df["time"],errors="coerce")
    tp=(pd.to_numeric(df.high,errors="coerce")+pd.to_numeric(df.low,errors="coerce")+pd.to_numeric(df.close,errors="coerce"))/3
    v=pd.to_numeric(df.volume,errors="coerce").fillna(0)
    secs = tf_seconds(tf)
    # Pine anchor: <15m=D, 15m~<1h=W, 1h~<4h=M, >=4h=Q.
    if secs < 15 * 60:
        key=t.dt.to_period("D")
    elif secs < 60 * 60:
        key=t.dt.to_period("W")
    elif secs < 4 * 60 * 60:
        key=t.dt.to_period("M")
    else:
        key=t.dt.to_period("Q")
    pv=tp*v
    return pv.groupby(key).cumsum()/v.groupby(key).cumsum().replace(0,np.nan)

def mfi(df,n=14):
    tp=(pd.to_numeric(df.high,errors="coerce")+pd.to_numeric(df.low,errors="coerce")+pd.to_numeric(df.close,errors="coerce"))/3
    vol=pd.to_numeric(df.volume,errors="coerce").fillna(0)
    flow=tp*vol; d=tp.diff()
    pos=flow.where(d>0,0.0).rolling(n).sum(); neg=flow.where(d<0,0.0).rolling(n).sum()
    ratio=pos/neg.replace(0,np.nan)
    return 100-(100/(1+ratio))

def cmf(df,n=20):
    h,l,c,v=[pd.to_numeric(df[x],errors="coerce") for x in ("high","low","close","volume")]
    ad=(((c-l)-(h-c))/(h-l).replace(0,np.nan)*v).fillna(0)
    return ad.rolling(n).sum()/v.rolling(n).sum().replace(0,np.nan)


class TrendWatchRegistry:
    """김매니저가 요청한 TREND 상태/metric 구독을 보관합니다."""

    def __init__(self):
        self._lock = threading.RLock()
        self._watches: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        root = Path(__file__).resolve().parent
        self._state_path = root / "logs" / "trend_watch_state.json"
        self._load_state()

    @staticmethod
    def _normalize_fields(values) -> tuple[str, ...]:
        return tuple(sorted({
            str(x or "").strip().lower()
            for x in (values or ())
            if str(x or "").strip().lower() in TREND_METRIC_FIELDS
        }))

    def _load_state(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            raw = read_json(self._state_path)
            items = raw.get("watches", {}) if isinstance(raw, dict) else {}
            restored: dict[str, tuple[str, str, tuple[str, ...]]] = {}
            if isinstance(items, dict):
                for watch_id, value in items.items():
                    if not isinstance(value, (list, tuple)) or len(value) < 2:
                        continue
                    symbol = str(value[0] or "").strip()
                    tf = str(value[1] or "").strip().lower()
                    fields = self._normalize_fields(value[2] if len(value) >= 3 else ())
                    if watch_id and symbol and tf:
                        restored[str(watch_id)] = (symbol, tf, fields)
            with self._lock:
                self._watches = restored
            if restored:
                logging.info("♻️ [TREND Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[TREND Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 3,
            "watches": {k: [v[0], v[1], list(v[2])] for k, v in self._watches.items()},
        }
        atomic_json(self._state_path, payload, default=None, allow_nan=True, indent=2)

    def add(self, watch_id: str, symbol: str, source_tf: str, requested_fields=()) -> None:
        symbol = str(symbol or "").strip()
        tf = str(source_tf or "").strip().lower()
        fields = self._normalize_fields(requested_fields)
        if not watch_id or not symbol or not tf:
            return
        with self._lock:
            self._watches[str(watch_id)] = (symbol, tf, fields)
            self._save_state_locked()
        metric_text = f" | metrics={','.join(fields)}" if fields else ""
        logging.info("🟣 [TREND Watch] 등록 | %s | %s %s%s", watch_id, symbol, tf, metric_text)

    def cancel(self, watch_id: str) -> None:
        with self._lock:
            removed = self._watches.pop(str(watch_id or ""), None)
            if removed:
                self._save_state_locked()
        if removed:
            logging.info("🛑 [TREND Watch] 취소 | %s | %s %s", watch_id, removed[0], removed[1])

    def reset(self) -> None:
        with self._lock:
            count = len(self._watches)
            self._watches.clear()
            if count:
                self._save_state_locked()
        logging.info("♻️ [TREND Watch] 전체 초기화 | %d건", count)

    def snapshot(self) -> dict[str, dict[str, tuple[str, ...]]]:
        with self._lock:
            grouped: dict[str, dict[str, set[str]]] = {}
            for symbol, tf, fields in self._watches.values():
                grouped.setdefault(symbol, {}).setdefault(tf, set()).update(fields)
        return {
            symbol: {
                tf: tuple(sorted(fields))
                for tf, fields in sorted(tf_map.items(), key=lambda x: (tf_seconds(x[0]), x[0]))
            }
            for symbol, tf_map in grouped.items()
        }


class TrendCommandWorker(threading.Thread):
    """공유 JSONL 큐에서 TREND 관련 액션만 처리합니다."""

    def __init__(
        self,
        registry: TrendWatchRegistry,
        query_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(name="TREND-Command", daemon=True)
        self.registry = registry
        self.query_queue = query_queue
        self.stop_event = stop_event
        root = Path(__file__).resolve().parent
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # manager_KIM / monitor_OZ와 동일한 OZ_COMMAND_FILE 설정을 사용합니다.
        # 상대경로는 기존 계약대로 logs 하위의 파일명으로 해석합니다.
        command_name = "oz_watch_command.jsonl"
        config_path = root / "config.txt"
        try:
            with config_path.open("r", encoding="utf-8-sig") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "OZ_COMMAND_FILE" and value.strip():
                        command_name = value.strip()
                        break
        except FileNotFoundError:
            pass

        command_path = Path(command_name)
        self.path = command_path if command_path.is_absolute() else log_dir / command_path.name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        # 재시작 시 과거 명령은 실행하지 않고 새 명령부터 처리합니다.
        self._offset = self.path.stat().st_size

    def _apply(self, payload: dict) -> None:
        action = str(payload.get("action", "")).upper()
        if action == "TREND_WATCH":
            self.registry.add(
                str(payload.get("watch_id") or ""),
                str(payload.get("symbol") or ""),
                str(payload.get("source_tf") or ""),
                payload.get("requested_fields") or (),
            )
        elif action == "CANCEL_TREND":
            self.registry.cancel(str(payload.get("watch_id") or ""))
        elif action == "RESET_TREND":
            self.registry.reset()
        elif action == "TREND_QUERY":
            symbol = str(payload.get("symbol") or "").strip()
            tf = str(payload.get("source_tf") or "").strip().lower()
            if symbol and tf:
                self.query_queue.put(dict(payload))

    def run(self) -> None:
        logging.info("🟢 [TREND] 김매니저 명령 대기 | %s", self.path)
        while not self.stop_event.is_set():
            try:
                with self.path.open("rb") as f:
                    f.seek(self._offset)
                    while True:
                        line = f.readline()
                        if not line or not line.endswith(b"\n"):
                            break
                        self._offset = f.tell()
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self._apply(json.loads(line))
                        except Exception:
                            logging.exception("[TREND] 명령 처리 실패 | %s", line[:300])
            except Exception:
                logging.exception("[TREND] 명령 큐 처리 오류")
            self.stop_event.wait(0.20)


class TrendEngine:
    """원비/FVG/시간과 완전히 분리된 순수 추세 판독 엔진."""

    def __init__(
        self,
        threshold: float = DEFAULT_TREND_THRESHOLD,
        *,
        staff_endpoint: str = STAFF_ENDPOINT,
        staff_timeout_ms: int = ZMQ_TIMEOUT_MS,
        manager_endpoint: str = MANAGER_ALERT_ENDPOINT,
        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms)
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms)
        self.threshold = float(threshold)
        self._last_watch_state: dict[tuple[str, str], str] = {}
        self._pending_states = Records(Path(__file__).resolve().parent / 'logs' / 'trend_pending_events.json')
        self._last_metric_push: dict[tuple[str, str], tuple[tuple[str, ...], float]] = {}

    def score(self, df: pd.DataFrame, tf: str, direction: str):
        """기존 DOUBLEB의 추세 앙상블 조건을 방향별 0~100점으로 계산합니다."""
        if df is None or len(df) < 60:
            return None
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return None

        o = pd.to_numeric(df.open, errors="coerce")
        h = pd.to_numeric(df.high, errors="coerce")
        l = pd.to_numeric(df.low, errors="coerce")
        c = pd.to_numeric(df.close, errors="coerce")
        v = pd.to_numeric(df.volume, errors="coerce").fillna(0)

        e10, e50 = ema(o, 10), ema(o, 50)
        s20 = sma(o, 20)
        w17 = wma(o, 17)
        h50 = pd.to_numeric(df["hma_50"], errors="coerce")
        st = supertrend_dir(df, 10, 3)
        sar = psar(df)
        plus, minus, adx = dmi(df, 14)
        lr = linreg(c, 20)
        rv = rsi(c, 14)
        cv = cci(df, 20)
        e12, e26 = ema(c, 12), ema(c, 26)
        macd = e12 - e26
        sig = ema(macd, 9)

        aroon_n = 14
        aup = h.rolling(aroon_n).apply(
            lambda x: 100 * (aroon_n - 1 - np.argmax(x[::-1])) / aroon_n,
            raw=True,
        )
        adn = l.rolling(aroon_n).apply(
            lambda x: 100 * (aroon_n - 1 - np.argmin(x[::-1])) / aroon_n,
            raw=True,
        )
        vmp = (h - l.shift(1)).abs().rolling(14).sum()
        vmm = (l - h.shift(1)).abs().rolling(14).sum()
        trs = true_range(df).rolling(14).sum()
        vip, vim = vmp / trs.replace(0, np.nan), vmm / trs.replace(0, np.nan)
        vw = anchored_vwap(df, tf)
        bop = (c - o) / (h - l).replace(0, np.nan)
        e13 = ema(c, 13)
        bullp, bearp = h - e13, l - e13
        cmfv = cmf(df, 20)
        mfiv = mfi(df, 14)
        vol_ema = ema(v, 20)
        vol_surge = v > vol_ema * 1.2
        vol_state = atr_trend_state(df)
        std = o.rolling(4).std(ddof=0)
        width = 6 * std
        safe = width > width.rolling(8).mean()
        trsum = true_range(df).rolling(14).sum()
        hh, ll = h.rolling(14).max(), l.rolling(14).min()
        chop = 100 * np.log10(trsum / (hh - ll).clip(lower=1e-12)) / math.log10(14)
        logret = np.log(c / c.shift(1))
        hv = logret.rolling(20).std(ddof=0) * math.sqrt(252) * 100
        hvma = hv.rolling(20).mean()
        mss = mss_state(df, 5, 5)
        ten = (l.rolling(5).min() + h.rolling(5).max()) / 2
        kij = (l.rolling(13).min() + h.rolling(13).max()) / 2
        sa = (ten + kij) / 2
        sb = (l.rolling(26).min() + h.rolling(26).max()) / 2
        cloud_top = pd.concat([sa.shift(13), sb.shift(13)], axis=1).max(axis=1)
        cloud_bot = pd.concat([sa.shift(13), sb.shift(13)], axis=1).min(axis=1)

        i = -1

        def b(x):
            return bool(x.iloc[i]) if hasattr(x, "iloc") and pd.notna(x.iloc[i]) else False

        if direction == "LONG":
            cond = [
                (e10.iloc[i] > e50.iloc[i], 1),
                (vol_state.iloc[i] == 1, 1),
                (plus.iloc[i] > minus.iloc[i] and adx.iloc[i] >= 25 and plus.iloc[i] > 15, 2),
                (h50.iloc[i] > h50.iloc[-3], 1),
                (rv.iloc[i] >= 50, 1),
                (cmfv.iloc[i] > 0, 1),
                (mfiv.iloc[i] >= 50, 1),
                (macd.iloc[i] > 0 and macd.iloc[i] > sig.iloc[i], 1),
                (s20.iloc[i] > s20.iloc[-2], 1),
                (c.iloc[i] > vw.iloc[i], 2),
                (w17.iloc[i] > s20.iloc[i], 1),
                (b(safe), 1),
                (ten.iloc[i] > kij.iloc[i] and sa.shift(13).iloc[i] > sb.shift(13).iloc[i] and c.iloc[i] > cloud_top.iloc[i], 1),
                (chop.iloc[i] < 45, 1),
                (cv.iloc[i] > 0, 1),
                (lr.iloc[i] > lr.iloc[-2], 1),
                (aup.iloc[i] > adn.iloc[i], 1),
                (vip.iloc[i] > vim.iloc[i], 1),
                (bop.iloc[i] > 0, 1),
                (bullp.iloc[i] > bullp.iloc[-2], 1),
                (mss.iloc[i] != -1, 2),
                (b(st), 2),
                (c.iloc[i] > sar.iloc[i], 1),
                (b(vol_surge), 2),
                (hv.iloc[i] > hvma.iloc[i], 1),
            ]
        else:
            cond = [
                (e10.iloc[i] < e50.iloc[i], 1),
                (vol_state.iloc[i] == -1, 1),
                (minus.iloc[i] > plus.iloc[i] and adx.iloc[i] >= 25 and minus.iloc[i] > 15, 2),
                (h50.iloc[i] < h50.iloc[-3], 1),
                (rv.iloc[i] <= 50, 1),
                (cmfv.iloc[i] < 0, 1),
                (mfiv.iloc[i] <= 50, 1),
                (macd.iloc[i] < 0 and macd.iloc[i] < sig.iloc[i], 1),
                (s20.iloc[i] < s20.iloc[-2], 1),
                (c.iloc[i] < vw.iloc[i], 2),
                (w17.iloc[i] < s20.iloc[i], 1),
                (b(safe), 1),
                (ten.iloc[i] < kij.iloc[i] and sa.shift(13).iloc[i] < sb.shift(13).iloc[i] and c.iloc[i] < cloud_bot.iloc[i], 1),
                (chop.iloc[i] < 45, 1),
                (cv.iloc[i] < 0, 1),
                (lr.iloc[i] < lr.iloc[-2], 1),
                (adn.iloc[i] > aup.iloc[i], 1),
                (vim.iloc[i] > vip.iloc[i], 1),
                (bop.iloc[i] < 0, 1),
                (bearp.iloc[i] < bearp.iloc[-2], 1),
                (mss.iloc[i] != 1, 2),
                (not b(st), 2),
                (c.iloc[i] < sar.iloc[i], 1),
                (b(vol_surge), 2),
                (hv.iloc[i] > hvma.iloc[i], 1),
            ]

        raw_score = sum(weight for ok, weight in cond if bool(ok))
        return min(TREND_MAX_SCORE, raw_score / TREND_DENOMINATOR * TREND_MAX_SCORE)

    @staticmethod
    def _metric_number(value):
        """JSON/ZMQ로 전달 가능한 유한 실수만 반환합니다."""
        try:
            if isinstance(value, (bool, np.bool_)):
                return 1.0 if bool(value) else 0.0
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    def metric_snapshot(self, df: pd.DataFrame, tf: str, requested_fields) -> dict | None:
        """요청된 TREND 내부 지표만 계산하여 최신 진행봉 값으로 반환합니다.

        일반 TREND score 경로와 분리되어 있어 metric 구독이 없으면 이 계산은 실행되지 않습니다.
        서로 같은 계산군(DMI, MACD 등)의 필드가 여러 개 요청되면 한 번만 계산합니다.
        """
        if df is None or len(df) < 60:
            return None
        tf = normalize_tf(tf)
        if not tf:
            return None

        requested = tuple(dict.fromkeys(str(x or "").strip().lower() for x in requested_fields if str(x or "").strip()))
        if not requested:
            return {}
        invalid = [x for x in requested if x not in TREND_METRIC_FIELDS]
        if invalid:
            raise ValueError("unsupported metric fields: " + ",".join(invalid))

        live = df.iloc[-1]
        bar_time = pd.to_datetime(live.get("time"), errors="coerce")
        if pd.isna(bar_time):
            return None

        o = pd.to_numeric(df.open, errors="coerce")
        h = pd.to_numeric(df.high, errors="coerce")
        l = pd.to_numeric(df.low, errors="coerce")
        c = pd.to_numeric(df.close, errors="coerce")
        v = pd.to_numeric(df.volume, errors="coerce").fillna(0)
        values: dict[str, object] = {}
        wanted = set(requested)

        if "price" in wanted:
            values["price"] = c.iloc[-1]

        score_fields = wanted & {"long_score", "short_score", "trend_score"}
        if score_fields:
            result = self.evaluate("_metric_", df, tf)
            if result is None:
                return None
            values.update({
                "long_score": result.get("long_score"),
                "short_score": result.get("short_score"),
                "trend_score": result.get("score"),
            })

        if wanted & {"ema10_open", "ema50_open"}:
            if "ema10_open" in wanted:
                values["ema10_open"] = ema(o, 10).iloc[-1]
            if "ema50_open" in wanted:
                values["ema50_open"] = ema(o, 50).iloc[-1]
        if "sma20_open" in wanted:
            values["sma20_open"] = sma(o, 20).iloc[-1]
        if "wma17_open" in wanted:
            values["wma17_open"] = wma(o, 17).iloc[-1]
        if wanted & {"hma50_open", "hma50_slope"}:
            # HMA50 값은 MT5 -> STAFF가 전달한 OPEN 기준 hma_50을 그대로 사용합니다.
            hma50_v = pd.to_numeric(df["hma_50"], errors="coerce")
            if "hma50_open" in wanted:
                values["hma50_open"] = hma50_v.iloc[-1]
            if "hma50_slope" in wanted:
                # Hull 컬러체인지 기준과 동일: 현재 HMA50 vs 2봉 전 HMA50 (hull > hull[2]).
                values["hma50_slope"] = hma50_v.iloc[-1] - hma50_v.iloc[-3]
        if "supertrend" in wanted:
            values["supertrend"] = supertrend_dir(df, 10, 3).iloc[-1]
        if "psar" in wanted:
            values["psar"] = psar(df).iloc[-1]

        if wanted & {"plus_di", "minus_di", "adx"}:
            plus, minus, adx_v = dmi(df, 14)
            values.update({"plus_di": plus.iloc[-1], "minus_di": minus.iloc[-1], "adx": adx_v.iloc[-1]})
        if "linreg20" in wanted:
            values["linreg20"] = linreg(c, 20).iloc[-1]
        if "rsi14" in wanted:
            values["rsi14"] = rsi(c, 14).iloc[-1]
        if "cci20" in wanted:
            values["cci20"] = cci(df, 20).iloc[-1]

        if wanted & {"macd", "macd_signal"}:
            macd_v = ema(c, 12) - ema(c, 26)
            signal_v = ema(macd_v, 9)
            values.update({"macd": macd_v.iloc[-1], "macd_signal": signal_v.iloc[-1]})

        if wanted & {"aroon_up", "aroon_down"}:
            n = 14
            aup = h.rolling(n).apply(lambda x: 100 * (n - 1 - np.argmax(x[::-1])) / n, raw=True)
            adn = l.rolling(n).apply(lambda x: 100 * (n - 1 - np.argmin(x[::-1])) / n, raw=True)
            values.update({"aroon_up": aup.iloc[-1], "aroon_down": adn.iloc[-1]})

        if wanted & {"vortex_plus", "vortex_minus"}:
            vmp = (h - l.shift(1)).abs().rolling(14).sum()
            vmm = (l - h.shift(1)).abs().rolling(14).sum()
            trs = true_range(df).rolling(14).sum().replace(0, np.nan)
            values.update({"vortex_plus": (vmp / trs).iloc[-1], "vortex_minus": (vmm / trs).iloc[-1]})

        if "vwap" in wanted:
            values["vwap"] = anchored_vwap(df, tf).iloc[-1]
        if "bop" in wanted:
            values["bop"] = ((c - o) / (h - l).replace(0, np.nan)).iloc[-1]
        if "cmf20" in wanted:
            values["cmf20"] = cmf(df, 20).iloc[-1]
        if "mfi14" in wanted:
            values["mfi14"] = mfi(df, 14).iloc[-1]
        if "chop14" in wanted:
            trsum = true_range(df).rolling(14).sum()
            hh, ll = h.rolling(14).max(), l.rolling(14).min()
            values["chop14"] = (100 * np.log10(trsum / (hh - ll).clip(lower=1e-12)) / math.log10(14)).iloc[-1]
        if wanted & {"hv20", "hvma20"}:
            logret = np.log(c / c.shift(1))
            hv_v = logret.rolling(20).std(ddof=0) * math.sqrt(252) * 100
            values["hv20"] = hv_v.iloc[-1]
            values["hvma20"] = hv_v.rolling(20).mean().iloc[-1]
        if "mss" in wanted:
            values["mss"] = mss_state(df, 5, 5).iloc[-1]
        if "vol_state" in wanted:
            values["vol_state"] = atr_trend_state(df).iloc[-1]
        if "vol_surge" in wanted:
            vol_ema = ema(v, 20)
            values["vol_surge"] = (v > vol_ema * 1.2).iloc[-1]

        cleaned = {name: self._metric_number(values.get(name)) for name in requested}
        return {
            "bar_time": pd.Timestamp(bar_time),
            "metrics": {name: value for name, value in cleaned.items() if value is not None},
        }

    def evaluate(self, symbol: str, df: pd.DataFrame, tf: str):
        if df is None or len(df) < 60:
            return None
        tf = normalize_tf(tf)
        if not tf:
            return None

        live = df.iloc[-1]
        bar_time = pd.to_datetime(live.get("time"), errors="coerce")
        close = live.get("close")
        if pd.isna(bar_time) or not finite(close):
            return None

        long_score = self.score(df, tf, "LONG")
        short_score = self.score(df, tf, "SHORT")
        if long_score is None or short_score is None:
            return None

        if long_score >= self.threshold and long_score > short_score:
            trend = "UP"
            direction = "LONG"
            score = long_score
        elif short_score >= self.threshold and short_score > long_score:
            trend = "DOWN"
            direction = "SHORT"
            score = short_score
        else:
            trend = "NEUTRAL"
            direction = "NEUTRAL"
            score = max(long_score, short_score)

        return {
            "symbol": str(symbol),
            "source_tf": tf,
            "trend": trend,
            "direction": direction,
            "score": float(score),
            "long_score": float(long_score),
            "short_score": float(short_score),
            "threshold": float(self.threshold),
            "price": float(close),
            "bar_time": pd.Timestamp(bar_time),
        }

    def _send_watch_state_if_changed(self, result: dict) -> None:
        key = (result["symbol"], result["source_tf"])
        trend = result["trend"]
        scope = identity(*key)
        pending = self._pending_states.get(scope, [])
        observed = pending[-1]['trend'] if pending else self._last_watch_state.get(key)
        if observed != trend:
            pending.append({'kind': 'TREND_STATE', 'strategy': 'TREND', **result,
                            'event_id': uuid.uuid4().hex})
            self.manager.stream.prepare(pending[-1])
            self._pending_states.put(scope, pending)
        if not pending:
            return
        while pending:
            event = pending[0]
            reply = self.manager.send(event)
            if not reply.get('ok'):
                return
            self._last_watch_state[key] = event['trend']
            pending.pop(0)
            self._pending_states.put(scope, pending)
        logging.info(
            "📈 [TREND 상태] %s %s | %s | LONG %.1f / SHORT %.1f",
            result["symbol"], result["source_tf"], result["trend"],
            result["long_score"], result["short_score"],
        )

    def run_watch_once(self, active: dict[str, dict[str, tuple[str, ...]]]) -> None:
        now_mono = time.monotonic()
        for symbol, tf_map in active.items():
            if not tf_map:
                continue
            tfs = tuple(tf_map)
            data = self.staff.request(symbol, tfs, REQUIRED_INDS)
            if not data:
                continue
            for tf, requested_fields in tf_map.items():
                df = data.get(tf)
                result = self.evaluate(symbol, df, tf)
                if result is not None:
                    result['source_health'] = source_health({tf:df}, REQUIRED_INDS)
                    self._send_watch_state_if_changed(result)
                    if not self._pending_states.get(identity(symbol, tf), []):
                        self.manager.send(self.manager.stream.snapshot(symbol, tf, [dict(kind='TREND_STATE', strategy='TREND', **result)], source_health=result['source_health']))

                requested_fields = tuple(requested_fields or ())
                if not requested_fields:
                    continue
                last_fields, last_sent = self._last_metric_push.get((symbol, tf), ((), 0.0))
                if requested_fields == last_fields and now_mono - last_sent < TREND_METRIC_PUSH_SEC:
                    continue
                try:
                    snapshot = self.metric_snapshot(df, tf, requested_fields)
                except Exception:
                    logging.exception(
                        "[TREND] metric 구독 계산 실패 | %s %s | %s",
                        symbol, tf, requested_fields,
                    )
                    continue
                if snapshot is None:
                    continue
                self.manager.send({
                    "kind": "TREND_METRIC_STATE",
                    "strategy": "TREND",
                    "symbol": symbol,
                    "source_tf": tf,
                    "requested_fields": list(requested_fields),
                    **snapshot,
                })
                self._last_metric_push[(symbol, tf)] = (requested_fields, now_mono)

    def run_query(self, payload: dict) -> None:
        symbol = str(payload.get("symbol") or "").strip()
        tf = normalize_tf(payload.get("source_tf"))
        if not symbol or not tf:
            return
        data = self.staff.request(symbol, [tf], REQUIRED_INDS)
        df = data.get(tf) if data else None
        bar_mode = str(payload.get("bar_mode") or "LIVE").strip().upper()
        if bar_mode == "CLOSED":
            # STAFF 마지막 행은 진행봉입니다. query에서만 -2 확정봉을 마지막 행으로 보이게 잘라
            # 기존 TREND evaluate/score 로직을 그대로 재사용합니다. 일반 TREND_WATCH 의미는 변경하지 않습니다.
            if df is not None and len(df) >= 2:
                df = df.iloc[:-1].copy()
            else:
                df = None
        requested_fields = tuple(dict.fromkeys(
            str(x or "").strip().lower()
            for x in (payload.get("requested_fields") or [])
            if str(x or "").strip()
        ))
        event = {
            "kind": "TREND_QUERY_RESULT",
            "strategy": "TREND",
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "purpose": payload.get("purpose"),
            "symbol": symbol,
            "source_tf": tf,
            "bar_mode": bar_mode,
        }
        if requested_fields:
            event["requested_fields"] = list(requested_fields)
            try:
                snapshot = self.metric_snapshot(df, tf, requested_fields)
            except ValueError as exc:
                event.update({"ok": False, "error": str(exc)})
            except Exception:
                logging.exception("[TREND] metric query 계산 실패 | %s %s | %s", symbol, tf, requested_fields)
                event.update({"ok": False, "error": "trend_metric_query_failed"})
            else:
                if snapshot is None:
                    event.update({"ok": False, "error": "trend_data_unavailable"})
                else:
                    event.update({"ok": True, **snapshot})
        else:
            result = self.evaluate(symbol, df, tf)
            if result is None:
                event.update({"ok": False, "error": "trend_data_unavailable"})
            else:
                event.update({"ok": True, **result})
        self.manager.send(event)


def main() -> None:
    root = Path(__file__).resolve().parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "trend_monitor.log", encoding="utf-8", mode="a"),
            logging.StreamHandler(),
        ],
    )

    config = load_runtime_config()
    staff_endpoint, manager_endpoint, staff_timeout_ms, manager_timeout_ms = runtime_connection_settings(config)

    stop_event = threading.Event()
    registry = TrendWatchRegistry()
    query_queue: queue.Queue = queue.Queue()
    engine = TrendEngine(
        staff_endpoint=staff_endpoint,
        staff_timeout_ms=staff_timeout_ms,
        manager_endpoint=manager_endpoint,
        manager_timeout_ms=manager_timeout_ms,
    )
    worker = TrendCommandWorker(registry, query_queue, stop_event)

    # 명령 수신 준비를 먼저 끝낸 뒤 KIM에 연결을 알립니다.
    # KIM은 이 PING을 받으면 현재 유효한 감시를 다시 보내므로 시작 직후 명령 유실을 막습니다.
    worker.start()
    engine.manager.verify_connection()
    logging.info(
        "🟢 [TREND] 온디맨드 대기 | 원비/FVG/시간필터 없음 | 추세 기준 %.1f점",
        engine.threshold,
    )

    try:
        while not stop_event.is_set():
            while True:
                try:
                    payload = query_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    engine.run_query(payload)
                except Exception:
                    logging.exception("❌ [TREND] 조회 처리 오류")

            active = registry.snapshot()
            if active:
                try:
                    engine.run_watch_once(active)
                except Exception:
                    logging.exception("❌ [TREND] 감시 계산 오류")
            stop_event.wait(LOOP_SLEEP_SEC if active else 0.25)
    except KeyboardInterrupt:
        print("\n사용자 요청으로 종료합니다.")
    finally:
        stop_event.set()
        worker.join(timeout=3.0)


if __name__ == "__main__":
    main()
