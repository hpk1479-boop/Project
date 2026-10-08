"""Offline adapter: execute byte-identical source copies, replace only external I/O.

No production import occurs in the real program directory. No network, MT5,
Telegram, background threads or OS pipes are opened. The pyobj adapter performs
real pickle round trips; it is NOT a test of libzmq or Windows pipe scheduling.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import logging
import pickle
import queue
import shutil
import sys
import tempfile
import threading
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Clock:
    def __init__(self, epoch=1789387200.0):
        self.epoch, self.mono = float(epoch), 1000.0

    def time(self): return self.epoch
    def time_ns(self): return int(self.epoch * 1_000_000_000)
    def monotonic(self): return self.mono
    def advance(self, seconds):
        self.epoch += seconds
        self.mono += seconds

    def datetime_module(self):
        clock = self
        class FrozenDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(clock.epoch, tz=tz)
        return types.SimpleNamespace(datetime=FrozenDateTime, timezone=dt.timezone,
                                     timedelta=dt.timedelta, date=dt.date, time=dt.time)


class OfflineBus:
    class Again(Exception): pass
    class ZMQError(Exception): pass

    def __init__(self):
        self.handlers, self.trace, self.fail_next, self.lose_ack_next = {}, [], set(), set()
        self.sockets = []
        self.multipart_handlers = {}

    def module(self):
        bus = self
        class Socket:
            def __init__(self):
                self.endpoint, self.closed, self.pending = None, False, None
                self.options = {}
                bus.sockets.append(self)
            def setsockopt(self, key, value): self.options[key] = value
            def connect(self, endpoint): self.endpoint = endpoint
            def close(self, *args): self.closed = True
            def send_multipart(self, parts):
                if self.closed: raise bus.ZMQError("closed")
                self.pending = [bytes(p) for p in parts]
            def recv_multipart(self):
                endpoint = self.endpoint
                if endpoint in bus.fail_next or endpoint not in bus.multipart_handlers:
                    bus.fail_next.discard(endpoint)
                    raise bus.Again("offline transport fault")
                parts = self.pending
                reply = bus.multipart_handlers[endpoint](parts)
                bus.trace.append({"endpoint": endpoint, "request": json.loads(parts[1]),
                                  "reply": [bytes(p) for p in reply], "protocol": "SNAPSHOT"})
                if endpoint in bus.lose_ack_next:
                    bus.lose_ack_next.remove(endpoint)
                    raise bus.Again("reply lost after handler committed")
                self.pending = None
                return [bytes(p) for p in reply]
            def send_pyobj(self, value):
                if self.closed: raise bus.ZMQError("closed")
                self.pending = pickle.loads(pickle.dumps(value))
            def recv_pyobj(self):
                endpoint = self.endpoint
                if endpoint in bus.fail_next or endpoint not in bus.handlers:
                    bus.fail_next.discard(endpoint)
                    raise bus.Again("offline transport fault")
                request = self.pending
                reply = bus.handlers[endpoint](request)
                bus.trace.append({"endpoint": endpoint, "request": request, "reply": reply})
                if endpoint in bus.lose_ack_next:
                    bus.lose_ack_next.remove(endpoint)
                    raise bus.Again("reply lost after handler committed")
                self.pending = None
                return pickle.loads(pickle.dumps(reply))
        class Context:
            @classmethod
            def instance(cls): return cls()
            def socket(self, *args): return Socket()
            def term(self): pass
        mod = types.ModuleType("zmq")
        mod.Context, mod.Again, mod.ZMQError = Context, self.Again, self.ZMQError
        for i, name in enumerate(("REQ", "REP", "RCVTIMEO", "SNDTIMEO", "LINGER", "POLLIN")):
            setattr(mod, name, i + 1)
        return mod


class OfflineHTTP:
    def __init__(self): self.deliveries, self.status_code = [], 200
    def module(self):
        mod = types.ModuleType("requests")
        def post(url, **kwargs):
            if url != "https://api.telegram.org/botOFFLINE/sendMessage":
                raise AssertionError("Unexpected external request blocked")
            self.deliveries.append(dict(kwargs["data"]))
            message_id = len(self.deliveries)
            return types.SimpleNamespace(status_code=self.status_code, text="offline",
                                         json=lambda: {"ok": self.status_code == 200,
                                                       "result": {"message_id": message_id}})
        def blocked(*args, **kwargs): raise AssertionError("External HTTP disabled")
        mod.post, mod.get = post, blocked
        return mod


class OneCycle:
    """Run the REAL JSONL worker loop exactly once, with no sleeping/thread race."""
    def __init__(self): self.done = False
    def is_set(self): return self.done
    def wait(self, *args): self.done = True
    def set(self): self.done = True


class World:
    names = ("domain_memory", "domain_clock", "durable_protocol", "sweep_selectors", "watch_ma", "watch_ma_features", "oz_profiles", "command_interpreter", "watch_orchestrator",
             "indicator_facts", "indicator_score", "strategy_INDICATOR", "strategy_FVG", "strategy_SWEEP", "monitor_OZ", "staff_schema", "staff_baseline", "manager_KIM", "staff_snapshot", "staff_compat")
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="moses-baseline-")
        self.path = Path(self.tmp.name) / "program"
        self.path.mkdir()
        manifest = json.loads((FIXTURES / "source_manifest.json").read_text(encoding="utf-8"))
        # Config contains operational secrets: never copy or load it.
        for name in manifest:
            rel = Path(name)
            if name.startswith("program/") and (rel.suffix == ".py" or rel.name == "command_aliases.json"):
                dest = self.path / rel.relative_to("program")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / rel, dest)
        shutil.copyfile(ROOT / 'program/durable_protocol.py', self.path / 'durable_protocol.py')
        shutil.copyfile(ROOT / 'program/sweep_selectors.py', self.path / 'sweep_selectors.py')
        for name in ("domain_memory.py", "domain_clock.py", "watch_ma.py", "watch_ma_features.py", "oz_profiles.py",
                     "indicator_facts.py", "indicator_score.py", "strategy_INDICATOR.py",
                     "staff_schema.py", "staff_snapshot.py", "staff_compat.py"):
            shutil.copyfile(ROOT / "program" / name, self.path / name)
        self.clock, self.bus, self.http = Clock(), OfflineBus(), OfflineHTTP()
        self.old_modules = {name: sys.modules.get(name) for name in (*self.names, "zmq", "requests")}
        self.old_prefix, self.old_bytecode = sys.pycache_prefix, sys.dont_write_bytecode
        self.old_handlers = list(logging.getLogger().handlers)
        sys.dont_write_bytecode = True
        sys.modules["zmq"], sys.modules["requests"] = self.bus.module(), self.http.module()
        self.modules = {}
        for name in self.names:
            filename = "THE STAFF OF MOSES.py" if name == "staff_baseline" else name + ".py"
            spec = importlib.util.spec_from_file_location(name, self.path / filename)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            # Per-module clock injection, never monkeypatch the shared stdlib time module.
            if hasattr(mod, "time"): mod.time = self.clock
            if hasattr(mod, "dt"): mod.dt = self.clock.datetime_module()
            if name == "strategy_SWEEP": mod.datetime = self.clock.datetime_module()
            self.modules[name] = mod
        self.staff = self.modules["staff_baseline"]
        self.kim = self.modules["manager_KIM"]
        self.oz = self.modules["monitor_OZ"]
        self.indicator = self.modules["strategy_INDICATOR"]
        self.trend = self.indicator  # 추세 Fact를 제공하는 엔진 (wire family "TREND")
        self.fvg = self.modules["strategy_FVG"]
        self.sweep = self.modules["strategy_SWEEP"]
        self.chain = self.modules["watch_orchestrator"]
        self.config = {"TELEGRAM_TOKEN": "OFFLINE", "TELEGRAM_CHAT_ID": "OFFLINE_OFFICIAL",
                       "TARGET_SYMBOLS": "BTCUSD", "GEMINI_FALLBACK_ENABLED": "false"}
        self.server = self.staff.DataServer({}, self.staff.WonbiState(3.0))
        self.bus.handlers[self.server.endpoint] = self.staff_handle
        self.bus.multipart_handlers[self.server.endpoint] = self.server.dispatch_multipart
        self.compat_module = self.modules['staff_compat']
        self.snapshots = self.modules['staff_snapshot'].SnapshotClient(transport=self.server.dispatch_multipart)
        self.compat = self.compat_module.StaffCompat(self.snapshots)
        self.manager = None
        self.restart_manager()
        self.restart_oz()
        self.restart_engines()
        self.sequence = 0

    def raw_frame(self, symbol, tf, cache=None):
        cache = cache or self.server.cache
        return self.modules['staff_schema'].legacy_frame(symbol, tf, cache.snapshot(symbol, tf), cache.max_bars)

    def data_request(self, request):
        # Preserve exception assertions at the new public snapshot boundary.
        if str(request.get('kind', '')).upper() in ('PING','SOURCE_HEALTH','SET_WONBI_SIGMA'):
            return self.server.handle(request)
        self.server.snapshot_reply(request)
        result = self.compat.request(request)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result

    def staff_handle(self, request):
        try: return self.server.handle(request)
        except Exception as exc: return {"error": f"request processing failed: {type(exc).__name__}: {exc}"}

    def manager_handle(self, request):
        try: return self.manager._handle_event(request)
        except Exception as exc: return {"ok": False, "delivered": False, "error": f"{type(exc).__name__}: {exc}"}

    def restart_manager(self):
        self.command_queue = self.kim.OZCommandQueue(self.config)
        self.manager = self.kim.ComposerManager(self.config, threading.Event(), self.command_queue,
                                              self.kim.WonbiState(3.0))
        self.bus.handlers[self.manager.alert_endpoint] = self.manager_handle

    def isolate_specs(self):
        # A scenario chooses its own test-only Composer specs. Production SPECIAL
        # files are still loaded unmodified and have a separate smoke test.
        self.manager.official_specs.clear()
        self.manager.official_chain_specs.clear()
        self.manager.timed_chains.clear()
        self.manager.manual_specs.clear()
        self.manager._active_children.clear()
        self.manager._save_active_children_state_locked()
        self.manager._special_watch_handlers.clear()
        self.manager._special_watch_event_handlers.clear()
        self.manager._special_oz_event_handlers.clear()

    def restart_oz(self):
        self.sender = self.oz.TelegramSender(self.config)
        self.external = self.oz.ExternalLiquidityController()
        self.watch = self.oz.OZWatchController(self.sender, self.external)
        self.generic = self.oz.GenericWatchController(self.sender)
        self.oz_worker = self.oz.OZCommandFileWorker(self.watch, self.generic, OneCycle(), self.config)
        self.sweep_event_worker = self.oz.SweepEventFileWorker(self.watch, OneCycle())
        self.monitor = self.oz.OZMonitor("BTCUSD", self.config, self.sender, self.watch, validation_mode="BLIND")

    def restart_engines(self, family=None):
        for key, mod, registry, worker, engine in (
            ("TREND", self.indicator, "IndicatorWatchRegistry", "IndicatorCommandWorker", "IndicatorEngine"),
            ("FVG", self.fvg, "FVGWatchRegistry", "FVGCommandWorker", "FVGEngine"),
            ("SWEEP", self.sweep, "SweepWatchRegistry", "SweepCommandWorker", "SweepEngine"),
        ):
            if family is not None and family != key: continue
            reg = getattr(mod, registry)()
            setattr(self, key.lower() + "_registry", reg)
            setattr(self, key.lower() + "_worker", getattr(mod, worker)(reg, queue.Queue(), OneCycle()))
            setattr(self, key.lower() + "_engine", getattr(mod, engine)())

    @staticmethod
    def pump(worker):
        worker.stop_event = OneCycle()
        worker.run()

    def commands(self):
        for worker in (self.trend_worker, self.fvg_worker, self.sweep_worker, self.oz_worker):
            self.pump(worker)

    def feed(self, df, tf="1m", symbol="BTCUSD", snapshot=None, truncate=0):
        """Actual SMOS bytes -> actual _consume_one; only ReadFile is replaced."""
        self.sequence += 1
        seq = self.sequence if snapshot is None else snapshot
        cols = self.staff.PIPE_VALUE_COLUMNS
        values = np.array([[row.get(c, np.nan) for c in cols] for _, row in df.iterrows()], dtype="<f8")
        times = np.array([pd.Timestamp(t).timestamp() for t in df.time], dtype="<i8")
        volumes = np.array(df.volume, dtype="<i8")
        sym, tfb = symbol.encode(), tf.encode()
        raw = self.staff.wire.pack_v2(symbol, tf, times, volumes, values, seq=seq)
        stream = io.BytesIO(raw[:-truncate] if truncate else raw)
        def read_exact(_k32, _handle, size):
            result = stream.read(size)
            if len(result) != size: raise OSError("fixture truncated pipe message")
            return result
        cache = self.server.cache
        old = cache._read_exact
        cache._read_exact = read_exact
        try: cache._consume_one(None, None)
        finally: cache._read_exact = old

    def market(self, phase="OUT"):
        recipe = json.loads((FIXTURES / "replay.json").read_text(encoding="utf-8"))
        rows = []
        for i in range(recipe["bars"]):
            price = recipe["base_price"] + i * recipe["step"]
            row = {"time": pd.Timestamp(recipe["epoch"] + i * 60, unit="s"),
                   "open": price, "high": price + 1, "low": price - 1, "close": price + .5,
                   "volume": 100 + (i % 7) * 10}
            for col in self.staff.PIPE_VALUE_COLUMNS:
                if col not in row: row[col] = price
            row.update(hma_6=price, hma_17=price - .5, hma_50=price - 2, hma_168=price - 4,
                       price_hma_6=50, price_band_lower=40, price_band_upper=60,
                       price_regime_basis=50+i*.01, price_regime_lower=40, price_regime_upper=60)
            for family in ("RSI", "STO", "DI"):
                row.update({family+"_val": 50, family+"_db": 40, family+"_ub": 60,
                            family+"_basis": 50+i*.01, family+"_regime_lower": 40, family+"_regime_upper": 60})
            row.update(recipe["tail"].get(str(i), {}))
            rows.append(row)
        if phase == "OUT":
            for field in ("RSI_val", "STO_val", "DI_val", "price_hma_6"): rows[-1][field] = 30
        if phase == "NEXT_BAR":
            row = dict(rows[-1])
            row["time"] += pd.Timedelta(minutes=1)
            rows.append(row)
        frame = pd.DataFrame(rows)
        # Synthetic fixture only: EA's two-pass OPEN4 calculation, no production fallback.
        for i in range(len(frame)):
            mid=upper=lower=float('nan')
            if i>=3:
                values=[float(x) for x in frame.open.iloc[i-3:i+1]]
                mid=sum(values)/4.;std=(sum((x-mid)**2 for x in values)/4.)**.5
                upper=mid+3.*std;lower=mid-3.*std
            frame.loc[i,'open_band_4_mid']=mid
            frame.loc[i,'wonbi_upper']=upper;frame.loc[i,'wonbi_lower']=lower;frame.loc[i,'wonbi_sigma']=3.
        return frame

    def feed_all(self, phase="OUT"):
        recipe = json.loads((FIXTURES / "replay.json").read_text(encoding="utf-8"))
        base = self.market(phase)
        for tf in recipe["timeframes"]:
            frame = base.copy()
            seconds = self.modules["command_interpreter"].tf_seconds(tf)
            # Align the initial live row across TFs. Do not put higher-TF bars in the future.
            live_epoch = recipe["epoch"] + (recipe["bars"] - 1) * 60
            frame["time"] = pd.to_datetime([live_epoch + (i - recipe["bars"] + 1) * seconds for i in range(len(frame))], unit="s")
            if tf == "1d":
                for key, value in recipe["sweep_daily_previous"].items(): frame.loc[len(frame)-2, key] = value
            self.feed(frame, tf)
        return base

    def close(self):
        for handler in list(logging.getLogger().handlers):
            if handler not in self.old_handlers:
                logging.getLogger().removeHandler(handler)
                handler.close()
        # Imported modules construct FileHandlers even when basicConfig is already configured.
        for ref in list(logging._handlerList):
            handler = ref()
            if isinstance(handler, logging.FileHandler) and str(self.path) in str(handler.baseFilename): handler.close()
        for name, old in self.old_modules.items():
            if old is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old
        sys.pycache_prefix, sys.dont_write_bytecode = self.old_prefix, self.old_bytecode
        self.tmp.cleanup()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()
