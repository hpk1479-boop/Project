"""Local browser fixture: actual dashboard, actual read-only APIs, synthetic files.

Run from the revision root: python verification/dashboard63_preview.py
Stop with Ctrl+C. It neither connects to MT5 nor runs a backtest.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('Part3', 'Part2', 'Part1/program', 'verification')]
from dashboard63_fixtures import CASES, create_case
from lab import server, unified_backtest


class PreviewHandler(server.Handler):
    def do_GET(self):
        if self.path == '/':
            html = (ROOT / 'Part3/web/index.html').read_text('utf-8')
            html = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', '', html)
            html = html.replace('<body>', '<body><div id="fixture-toolbar"><label>테스트 결과 <select id="fixture-case">' +
                ''.join(f'<option value="{case}">{label}</option>' for case, label in zip(CASES,
                    ('정상 결과', 'analytics 없음', '일부 필드 비어 있음', '거래 0건', 'LONG만', 'SHORT만'))) +
                '</select></label><span>합성 데이터 · 실제 엔진 실행 없음</span><output id="fixture-errors"></output></div>')
            html = html.replace('</body>', '<script src="/backtest_dashboard.js"></script><script src="/fixture.js"></script></body>')
            return self.send(200, html, 'text/html; charset=utf-8')
        if self.path == '/fixture.js':
            script = '''
const token = TOKEN;
const cases = CASES;
window.onerror = message => {document.querySelector('#fixture-errors').textContent = message;};
window.onunhandledrejection = event => {document.querySelector('#fixture-errors').textContent = event.reason?.message || String(event.reason);};
async function api(route) {
  const response = await fetch('/api/' + route, {headers: {'X-Lab-Token': token}});
  const data = await response.json(); if (!response.ok) throw new Error(data.error); return data;
}
document.querySelectorAll('.moses-view, .moses-subview, #view-strategy').forEach(node => node.classList.add('hidden'));
document.querySelector('#view-backtest').classList.remove('hidden');
document.querySelector('#mo-bt-result-page').classList.remove('hidden');
document.querySelector('#mo-global-back').classList.add('hidden');
async function show() {
  const id = cases[document.querySelector('#fixture-case').value];
  const data = await api('mo/backtest/result?id=' + id);
  window.MosesBacktestDashboard.render(document.querySelector('#mo-bt-result'), data, id);
}
document.querySelector('#fixture-case').onchange = show; show();
'''.replace('TOKEN', json.dumps(self.server.token)).replace('CASES', json.dumps(self.server.cases))
            return self.send(200, script, 'application/javascript; charset=utf-8')
        return super().do_GET()


def main():
    with tempfile.TemporaryDirectory(prefix='moses_dashboard63_') as folder:
        jobs = {}; cases = {}
        for index, case in enumerate(CASES):
            job, _ = create_case(folder, case, index)
            jobs[job['id']] = job; cases[case] = job['id']
        unified_backtest.JOBS.update(jobs)
        host = server.LabServer(0); host.RequestHandlerClass = PreviewHandler; host.cases = cases
        print(f'http://127.0.0.1:{host.server_port}/', flush=True)
        try: host.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            host.server_close()
            for identifier in jobs: unified_backtest.JOBS.pop(identifier, None)


if __name__ == '__main__': main()
