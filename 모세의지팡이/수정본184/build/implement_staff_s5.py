"""One-time, explicit S5 source migration; never touches the frozen source."""
import ast,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(rel,fn):
    p=ROOT/rel;p.write_text(fn(p.read_text('utf-8-sig')),encoding='utf-8')
def strip_nodes(text,names):
    lines=text.splitlines(True)
    for node in sorted((n for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.ImportFrom)) and
                        (getattr(n,'name',None) in names or getattr(n,'module',None) in names)),key=lambda n:n.lineno,reverse=True):
        del lines[node.lineno-1:node.end_lineno]
    return ''.join(lines)
def staff(text):
    text=strip_nodes(text,{'get','_legacy_frame','add_wonbi_features','apply_requested_features','validate_mt5_snapshot',
                          'indicator_facts','monitor_OZ','strategy_FVG'})
    text=text.replace('import pandas as pd\n','').replace('        self.frame = None\n        self.frame_lock = threading.Lock()\n','')
    text=text.replace('        self._watch_ma_features = None\n','')
    start=text.index('        # 휴장 자체는 통신 오류가 아닙니다.',text.index('    def _handle_request'))
    end=text.index('\n    def run(',start)
    text=text[:start]+'''        return {"error": "SNAPSHOT API 사용: legacy 데이터 요청은 지원하지 않습니다.",
                "error_code": "SNAPSHOT_API_REQUIRED", "retryable": False}
'''+text[end:]
    # Reject every legacy data shape explicitly; retain control validation verbatim.
    start=text.index('        symbol = str(req.get("symbol", "")).strip()',text.index('    def _handle_request'))
    text=text[:start]+'''        if str(req.get('kind', '')).upper() != 'SOURCE_HEALTH':
            return {"error": "SNAPSHOT API 사용: legacy 데이터 요청은 지원하지 않습니다.",
                    "error_code": "SNAPSHOT_API_REQUIRED", "retryable": False}

'''+text[start:]
    text=text.replace('3) ATR14(Wilder)는 이 프로세스에서만 계산하고 전략에는 순수 값만 제공합니다.','3) 계산은 클라이언트가 소유합니다. 서버는 원시 Snapshot만 전달합니다.')
    text=text.replace('4) 원비는 이 프로세스에서만 계산합니다: OPEN / length=4 / sigma=config(WONBI_SIGMA, 기본 3.0).','4) 원비 sigma 설정만 보관·전달합니다. 계산식은 staff_compat에 유지합니다.')
    text=text.replace('pandas is only used by readers.','No DataFrame or derived calculation is retained here.')
    text=text.replace('정상 DataFrame 캐시','정상 Snapshot 캐시')
    text=text.replace('# Requests share WATCH history; serialize readers independently of the\n        # receiver\'s publication lock, without changing any calculation rules.','# Serialize requests independently of the receiver publication lock.')
    text=text.replace('ATR14_LENGTH = 14\n','')
    text=text.replace('# Python-only derived features (same public column names as old MOSES)','# Runtime control metadata (calculation is client-owned)')
    text=text.replace('# Pure Fact owners. Public legacy function names remain compatible.','')
    # Preserve the original recovery log/cleanup on the remaining data path.
    needle='                # Match legacy per-TF validation order'
    text=text.replace(needle,"                if self._feed_wait_logs.pop((symbol, tf), None) is not None:\n                    logging.info('[MT5 데이터 수신 재개] %s %s', symbol, tf)\n"+needle)
    return text
edit('Part1/program/THE STAFF OF MOSES.py',staff)
edit('Part1/program/staff_schema.py',lambda t:t.replace('import pandas as pd\n','').replace('import numpy as np','from __future__ import annotations\nimport numpy as np').replace('    times, volumes, values = snapshot.time', '    import pandas as pd  # Client-only normalization; server imports only the wire schema.\n    times, volumes, values = snapshot.time'))
edit('Part2/live_replay/event_catalog.py',lambda t:t.replace('self.cache.get(*k)',"__import__('staff_schema').legacy_frame(*k, self.cache.snapshot(*k), self.cache.max_bars)").replace('from .reference import staff as S','from staff_compat import apply_requested_features\n            import staff_compat as S'))
# Verification requests use an explicit client boundary; the legacy transport handler stays strict.
edit('Part2/part1_host/runtime.py',lambda t:t.replace('    def _staff_handle(self, request):', '''    def staff_data_request(self, request):
        """Client-side verification/data API, including decoding and compatibility features."""
        if not hasattr(self, '_verification_compat'):
            client = self.modules['staff_snapshot'].SnapshotClient(transport=self.staff_server.dispatch_multipart)
            self._verification_compat = self.modules['staff_compat'].StaffCompat(client)
        return self._verification_compat.request(request)

    def _staff_handle(self, request):'''))
for file in ('record.py','actual.py','benchmark.py'):
    edit('Part2/staff_golden/'+file,lambda t:t.replace("rt._staff_handle, req)","rt.staff_data_request, req)").replace("rt._staff_handle, request)","rt.staff_data_request, request)").replace("rt._staff_handle(request)","rt.staff_data_request(request)"))
print('S5 source migration complete')
