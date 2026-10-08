"""Asserted active-code cleanup. Does not touch reference trees, tests or config."""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program'
def edit(path,fn):
    raw=path.read_bytes();text=raw.decode('utf-8-sig')
    # Operate on bytes-equivalent text to preserve mixed line endings.
    updated=fn(text)
    if updated!=text:path.write_bytes(updated.encode('utf-8'))
def normalized(path,fn):
    raw=path.read_bytes();nl='\r\n' if raw.count(b'\r\n')>raw.count(b'\n')/2 else '\n'
    text=raw.decode('utf-8-sig').replace('\r\n','\n');value=fn(text)
    path.write_bytes(value.replace('\n',nl).encode('utf-8'))
def rep(text,before,after):
    assert before in text,before[:90]
    return text.replace(before,after)

def server(s):
    s=rep(s,'4) 원비 sigma 설정만 보관·전달합니다. 계산식은 staff_compat에 유지합니다.',
            '4) 원비 원본은 고정 3σ MT5 열이며 config 적용은 공용 클라이언트 Fact의 책임입니다.')
    # No v1 compatibility remains in the finalized schema receiver.
    start=s.index('        if version != PIPE_VERSION:',s.index('    def receive_one('))
    end=s.index('    @staticmethod',start)
    s=s[:start]+'''        with self._lock:
            self._schema_connection_error = -1
        raise wire.UnknownWireSchema(0)  # v1 has no schema id; use the original revision.

'''+s[end:]
    start=s.index('class WonbiState:');end=s.index('# ---------------------------------------------------------------------',start)
    s=s[:start]+s[end:]
    s=rep(s,'config: dict[str, str], wonbi_state: WonbiState, *, cache=None,','config: dict[str, str], *, cache=None,')
    s=s.replace('        self.wonbi_state = wonbi_state\n','')
    start=s.index('    def _check_wonbi_sigma(');end=s.index('    def snapshot_reply(',start)
    s=s[:start]+s[end:]
    s=s.replace('self.wonbi_state.get_sigma()','WONBI_DEFAULT_SIGMA')
    s=s.replace('                self._check_wonbi_sigma(symbol, tf, snapshot, sigma, reject_legacy=True)\n','')
    start=s.index('        # manager_KIM 전용 내부 제어.');end=s.index('        if str(req.get("kind", "")).upper() == "PING":',start)
    s=s[:start]+s[end:]
    start=s.index('            for tf, (snapshot, age) in self.cache.snapshots_with_age(symbol, health_tfs).items():')
    end=s.index("            return {'ok':True, 'feeds':feeds}",start)
    s=s[:start]+s[end:]
    start=s.index('def _initial_wonbi_sigma(');end=s.index('def main()',start)
    s=s[:start]+s[end:]
    start=s.index('    initial_sigma =');end=s.index('    try:',start)
    s=s[:start]+'    server = DataServer(config)\n'+s[end:]
    s=s.replace("'wonbi_lower', 'wonbi_sigma'","'wonbi_lower'")
    return s

def main():
    # Literal EMA21 callers/aliases are changed; arbitrary variable periods remain supported.
    for path in P.rglob('*'):
        if path.suffix not in ('.py','.json') or any(part in ('logs','__pycache__') for part in path.parts):continue
        edit(path,lambda s:s.replace('ema_21','ema_20').replace('ema21','ema20').replace('EMA21','EMA20').replace('EMA_21','EMA_20'))
    normalized(P/'THE STAFF OF MOSES.py',server)
    # Old schema normalization is gone: source metadata no longer tags legacy Wonbi.
    # Keep the NamedTuple compatibility slot for checkpoint diagnostics only.
    path=ROOT/'Part2/event_backtest/recording.py'
    edit(path,lambda s:s.replace(" and c.get('wonbi_sigma',3)==scenario.get('wonbi_sigma',3)",'')
         .replace(",'wonbi_sigma':scenario.get('wonbi_sigma',3.0)",'')
         .replace(",'InpWonbiSigma':identity['wonbi_sigma']",''))
    print('server, EMA and recording config paths updated')
if __name__=='__main__':main()
