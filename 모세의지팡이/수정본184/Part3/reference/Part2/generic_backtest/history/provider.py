"""Read-only, explicitly selected MT5 terminal. No order/account-switch APIs.

API: https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py
"""
from datetime import datetime,timezone
from pathlib import Path
from ..canonical import file_hash,identity
from ..contracts import ROOT, GenericError
from ..paths import plain_path


class ReadOnlyMT5Provider:
    input_kind='BROKER_REAL_TICKS'
    def __init__(self,profile):
        from .selection import verify_running
        if profile.get('live') and not profile.get('live_confirmed'):
            raise GenericError('E_MT5_CONFIRM_REQUIRED','LIVE confirmation required')
        if not profile.get('account_server') or type(profile.get('account_login')) is not int or not profile.get('data_root'):
            raise GenericError('E_MT5_IDENTITY','MT5 계정 식별 정보가 없습니다. terminal 로그인을 확인해 주세요.')
        self.profile=dict(profile)
        executable=plain_path(profile['executable'])
        try: import MetaTrader5 as mt5
        except ImportError as exc: raise GenericError('E_MT5_UNAVAILABLE','use .venv-history') from exc
        verify_running(profile)  # Last preflight before initialize; no fallback path.
        self.mt5=mt5
        try:
            if not mt5.initialize(str(executable),portable=bool(profile.get('portable',False))):
                raise GenericError('E_MT5_UNAVAILABLE','선택한 MT5에 연결할 수 없습니다.')
            self._validate_connection()
            terminal=mt5.terminal_info();account=mt5.account_info()
            if terminal is None or account is None:
                raise GenericError('E_MT5_IDENTITY','MT5 연결 식별 정보를 읽을 수 없습니다.')
            self.server=str(account.server).strip()
            self.broker=str(getattr(account,'company','') or getattr(terminal,'company','') or 'MT5').strip()
            if not self.server:
                raise GenericError('E_MT5_IDENTITY','MT5 server 정보가 없습니다.')
            verify_running(profile)
            self.profile['server_fingerprint']=identity(profile['account_server'])
            self.connection_identity={key:profile[key] for key in
                ('pid','created_at','executable','data_root','account_server','account_login')}
            self.build=mt5.version()
        except BaseException:
            mt5.shutdown()
            raise

    def _validate_connection(self):
        from .selection import same_path
        info=self.mt5.terminal_info();account=self.mt5.account_info()
        p=self.profile
        if (info is None or account is None or not info.connected
            or not same_path(info.path,Path(p['executable']).parent)
            or not same_path(info.data_path,p['data_root'])
            or account.server!=p['account_server'] or account.login!=p['account_login']):
            self.mt5.shutdown()
            raise GenericError('E_MT5_IDENTITY','연결 대상/서버/계정이 예상과 달라 중단했습니다.')

    def symbols(self):
        self._validate_connection()
        rows=self.mt5.symbols_get()
        if rows is None: raise GenericError('E_MT5_UNAVAILABLE',self.mt5.last_error())
        self._validate_connection()
        # Selected-but-hidden conversion symbols are NOT visible Market Watch rows.
        # This is a read-only query; do not call symbol_select().
        return [{'symbol':r.name,'description':r.description} for r in rows
                if bool(getattr(r, 'visible', False))]

    def instrument(self,symbol,*,require_visible=False):
        self._validate_connection()
        info=self.mt5.symbol_info(symbol)
        if info is None: raise GenericError('E_MT5_UNAVAILABLE','symbol '+symbol)
        if require_visible and not bool(getattr(info, 'visible', False)):
            raise GenericError('E_MARKET_WATCH_SYMBOL',
                'MT5 종합시세에 해당 종목을 추가한 뒤 다시 시도하세요.')
        if require_visible: self._validate_connection()
        return {'broker_symbol':symbol,'server_fingerprint':self.profile['server_fingerprint'],
            'chart_mode':'LAST' if info.chart_mode==1 else 'BID','digits':info.digits,'point':info.point,
            'trade_tick_size':info.trade_tick_size,'description':info.description,
            'metadata_revision':identity((symbol,info.digits,info.point,info.trade_tick_size,info.chart_mode))}

    def ticks(self,symbol,start_ms,end_ms):
        self._validate_connection()
        # Query whole seconds broadly, then retain only the owned [a,b) ms.
        start=datetime.fromtimestamp(start_ms//1000,timezone.utc)
        end=datetime.fromtimestamp((end_ms+999)//1000,timezone.utc)
        rows=self.mt5.copy_ticks_range(symbol,start,end,self.mt5.COPY_TICKS_ALL)
        if rows is None: raise GenericError('E_HISTORY_PARTIAL',self.mt5.last_error())
        code,detail=self.mt5.last_error()
        if code!=1: raise GenericError('E_HISTORY_PARTIAL',(code,detail))
        self._validate_connection()
        return rows[(rows['time_msc']>=start_ms)&(rows['time_msc']<end_ms)]

    def bars_before(self,symbol,tf,cutoff_ns,count):
        self._validate_connection()
        unit=tf[-1];amount=int(tf[:-1])
        name={'m':'M','h':'H','d':'D'}[unit]+str(amount)
        rows=self.mt5.copy_rates_from(symbol,getattr(self.mt5,'TIMEFRAME_'+name),
            datetime.fromtimestamp((cutoff_ns-1)//10**9,timezone.utc),int(count))
        if rows is None or self.mt5.last_error()[0]!=1:
            raise GenericError('E_HISTORY_PARTIAL',('seed '+tf,self.mt5.last_error()))
        self._validate_connection()
        return rows

    def close(self): self.mt5.shutdown()
