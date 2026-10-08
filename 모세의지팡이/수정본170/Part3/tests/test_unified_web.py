"""The web shell delegates to existing engines and never sends Telegram."""
from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab import live_processes, server, unified_backtest, unified_live, unified_settings


class UnifiedSettingsTests(unittest.TestCase):
    def test_live_secret_is_masked_and_existing_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.txt'
            original = (b'# keep this comment\r\nTELEGRAM_TOKEN=private-test-value\r\n'
                        b'WONBI_SIGMA=3.0\r\nSTAFF_ALLOWED_TIMEFRAMES=1m,5m\r\n')
            path.write_bytes(original)
            with patch.object(unified_settings, 'LIVE_CONFIG', path):
                masked = {item['key']: item for item in unified_settings.read()['live']}
                self.assertEqual(masked['TELEGRAM_TOKEN']['value'], '')
                self.assertTrue(masked['TELEGRAM_TOKEN']['configured'])
                unified_settings.save_live({'TELEGRAM_TOKEN': '', 'WONBI_SIGMA': '2.5'})
                self.assertEqual(path.read_bytes(), original.replace(b'WONBI_SIGMA=3.0', b'WONBI_SIGMA=2.5'))

    def test_invalid_live_contract_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.txt'
            source = b'STAFF_ALLOWED_TIMEFRAMES=1m,5m\r\nOZ_COMMAND_FILE=watch.jsonl\r\n'
            path.write_bytes(source)
            with patch.object(unified_settings, 'LIVE_CONFIG', path):
                for changes in ({'STAFF_ALLOWED_TIMEFRAMES': '99m'},
                                {'OZ_COMMAND_FILE': '../outside.txt'},
                                {'UNKNOWN_SETTING': 'x'}):
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        unified_settings.save_live(changes)
            self.assertEqual(path.read_bytes(), source)

    def test_part2_config_validation_does_not_change_invalid_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'event_backtest.json'
            path.write_text('{"cores": null, "overlap_trading_days": 3}\n', encoding='utf-8')
            before = path.read_bytes()
            with patch.object(unified_settings, 'PART2_CONFIG', path):
                with self.assertRaises(ValueError):
                    unified_settings.save_part2({'overlap_trading_days': 99})
            self.assertEqual(path.read_bytes(), before)


class UnifiedBridgeTests(unittest.TestCase):
    def test_live_and_backtest_controls_remain_on_their_subpages(self):
        class PagePaths(HTMLParser):
            VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                    'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

            def __init__(self):
                super().__init__()
                self.stack = []
                self.paths = {}
                self.classes = {}

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                identifier = attributes.get('id')
                if identifier:
                    self.paths[identifier] = [item[1] for item in self.stack if item[1]]
                    self.classes[identifier] = attributes.get('class', '')
                if tag not in self.VOID:
                    self.stack.append((tag, identifier))

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index:]
                        break

        pages = PagePaths()
        pages.feed((ROOT / 'web' / 'index.html').read_text('utf-8'))
        for control, page in {
            'live-start': 'live-main-page',
            'live-stop': 'live-main-page',
            'live-open-settings': 'live-main-page',
            'live-modules': 'live-main-page',
            'live-specials': 'live-settings-page',
            'live-specials-save': 'live-settings-page',
            'live-log-module': 'live-log-page',
            'live-log': 'live-log-page',
            'mo-bt-start-button': 'mo-bt-setup-page',
            'mo-bt-specials': 'mo-bt-specials-page',
            'mo-bt-edit-specials': 'mo-bt-setup-page',
            'mo-bt-stop-button': 'mo-bt-progress-page',
            'mo-bt-confirm-button': 'mo-bt-progress-page',
            'mo-bt-log': 'mo-bt-progress-page',
            'mo-bt-result': 'mo-bt-result-page',
        }.items():
            with self.subTest(control=control):
                self.assertIn(page, pages.paths[control])
        for page in ('live-settings-page', 'live-log-page',
                     'mo-bt-specials-page', 'mo-bt-progress-page', 'mo-bt-result-page'):
            self.assertIn('hidden', pages.classes[page].split())

    def test_header_has_back_navigation_three_destinations_and_settings_icon(self):
        html = (ROOT / 'web' / 'index.html').read_text('utf-8')
        script = (ROOT / 'web' / 'unified.js').read_text('utf-8')
        self.assertIn('id="mo-global-back"', html)
        self.assertIn('aria-hidden="true">◀</span>', html)
        self.assertNotIn('← 뒤로', html)
        self.assertEqual(re.findall(r'class="moses-nav-banner"[^>]+data-moses-view="([^"]+)"', html),
                         ['live', 'backtest', 'strategy'])
        self.assertRegex(html, r'class="moses-settings-button"[^>]+data-moses-view="settings"[^>]+aria-label="설정"')
        self.assertIn("mo('#mo-global-back').onclick = () => moBack()", script)
        self.assertIn("moBacktestPage('progress')", script)
        self.assertIn("moBacktestPage('setup')", script)
        self.assertIn("moLivePage('main')", script)
        self.assertIn('firstResult && onProgress && finished', script)

    def test_top_menus_are_roots_and_backtest_retains_existing_inputs(self):
        html = (ROOT / 'web' / 'index.html').read_text('utf-8')
        script = (ROOT / 'web' / 'unified.js').read_text('utf-8')
        css = (ROOT / 'web' / 'style.css').read_text('utf-8')
        self.assertNotIn('viewHistory', script)
        self.assertIn("moBacktestPage('setup');", script)
        self.assertIn("['setup', 'specials', 'progress', 'result']", script)
        self.assertIn('withinLive || withinBacktest;', script)
        self.assertLess(html.index('moses-bt-input-card'), html.index('moses-bt-choice-grid'))
        self.assertLess(html.index('moses-bt-choice-grid'), html.index('mo-bt-setup-error'))
        for identifier in ('mo-bt-symbol', 'mo-bt-start', 'mo-bt-end', 'mo-bt-mode',
                           'mo-bt-target', 'mo-bt-result-mode', 'mo-bt-build-only',
                           'mo-bt-rebuild', 'mo-bt-watch', 'mo-bt-specials', 'mo-bt-spread'):
            self.assertIn('id="' + identifier + '"', html)
        for segment in ('data-bt-target="SPECIAL"', 'data-bt-target="WATCH"',
                        'data-bt-result="ALERT_ONLY"', 'data-bt-result="VIRTUAL_ENTRY"',
                        'data-bt-result="BUILD_ONLY"'):
            self.assertIn(segment, html)
        self.assertIn('background:#101f31', css)
        self.assertIn('background:linear-gradient(#3183eb,#2475de)', css)

    def test_rebuild_option_is_visible_and_sent_only_for_data_build(self):
        script = (ROOT / 'web' / 'unified.js').read_text('utf-8')
        css = (ROOT / 'web' / 'style.css').read_text('utf-8')
        self.assertIn("mo('#mo-bt-rebuild').closest('label').classList.toggle('active', build)", script)
        self.assertIn('.moses-bt-options .moses-bt-rebuild:not(.active){display:none}', css)
        self.assertIn('.moses-bt-options{display:flex;align-items:end;gap:15px;height:96px;min-height:96px;', css)
        self.assertIn("rebuild: mo('#mo-bt-build-only').checked && mo('#mo-bt-rebuild').checked", script)

    def test_live_dashboard_follows_part1_control_sections(self):
        html = (ROOT / 'web' / 'index.html').read_text('utf-8')
        css = (ROOT / 'web' / 'style.css').read_text('utf-8')
        script = (ROOT / 'web' / 'unified.js').read_text('utf-8')
        self.assertLess(html.index('class="moses-live-brand"'), html.index('id="live-start"'))
        self.assertLess(html.index('id="live-start"'), html.index('id="live-modules"'))
        self.assertLess(html.index('id="live-modules"'), html.index('id="live-message"'))
        self.assertIn('TRADING SYSTEM CONTROL', html)
        self.assertIn('grid-template-rows:repeat(4,minmax(84px,1fr))', css)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', css)
        self.assertIn('moses-module-icon', script)

    def test_view_selection_never_marks_the_page_body_as_primary_button(self):
        script = (ROOT / 'web' / 'unified.js').read_text('utf-8')
        self.assertEqual(script.count("document.querySelectorAll('.moses-nav button[data-moses-view]')"), 2)
        self.assertNotIn("document.querySelectorAll('[data-moses-view]')", script)

    def test_unified_ids_do_not_shadow_existing_part3_dialog(self):
        html = (ROOT / 'web' / 'index.html').read_text('utf-8')
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('mo-bt-start', ids)
        self.assertNotIn('bt-start', ids)

    def test_live_start_and_stop_use_part1_public_api(self):
        calls = []
        owner = {
            'start_live': lambda **options: (calls.append('start') or True, '시작됨'),
        }
        processes = Mock()
        processes.stop_all_engines.side_effect = lambda: (calls.append('stop') or {'ok': True, 'message': '종료됨', 'warnings': []})
        with patch.object(unified_live, 'control', return_value=owner), \
             patch.object(live_processes, 'current_process_identity', return_value={'pid': 101, 'created': '200'}), \
             patch.object(live_processes, 'process_api', return_value=processes):
            self.assertTrue(unified_live.start()['ok'])
            self.assertTrue(unified_live.stop()['ok'])
        self.assertEqual(calls, ['start', 'stop'])

    def test_web_scenario_uses_current_part2_scenario_contract(self):
        request = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08',
                   'mode': 'BAR', 'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'],
                   'special_settings': {'SPECIAL2': {'enabled': True,
                       'trigger': '무지성 브레이커 올존', 'time_filters': None}}}
        web_scenario = unified_backtest._scenario(request)
        settings, ui_model, _ = unified_backtest._part2()
        old_ui = ui_model.load()
        old_ui['target_mode'] = 'SPECIAL'
        old_ui['specials'] = request['special_settings']
        old_ui['result_mode'] = 'ALERT_ONLY'
        old_ui['spread_points'] = {'XAUUSD+': 0.0}
        native = ui_model.make_scenario(old_ui, {key: request[key] for key in ('symbol', 'start', 'end', 'mode')})
        self.assertEqual(web_scenario, native)
        with self.assertRaises(ValueError):
            unified_backtest._scenario({**request, 'special_settings': {
                'SPECIAL2': {'enabled': True, 'trigger': 'unknown', 'time_filters': None}}})

    def test_backtest_confirmation_and_stop_use_existing_run_contract(self):
        import pytest
        from verification.test_backtest_jobs67 import make_job, record
        from lab import backtest_jobs
        with tempfile.TemporaryDirectory() as folder:
            with pytest.MonkeyPatch.context() as monkeypatch:
                owner, context = make_job(Path(folder), monkeypatch, phase='planning')
                actions = []
                def complete(action, approved=None):
                    actions.append((action, approved))
                    owner.last_result = ({'record': [{'start': '2025-09-01'}], 'approval_token': 'confirmed'}
                                         if action == 'plan' else {'status': 'COMPLETE'})
                    return 0
                with patch.object(owner, 'execute_action', side_effect=complete):
                    owner.execute_plan()
                    self.assertEqual(record(owner)['phase'], 'confirm')
                    owner.execute_run(record(owner)['plan']['approval_token'])
                self.assertEqual(record(owner)['phase'], 'complete')
                self.assertEqual(actions, [('plan', None), ('run', 'confirmed')])
                owner.update(phase='run')
                with patch.object(backtest_jobs, '_context', return_value=(context['warehouse'],
                                  context['project_root'], context['python_executable'])):
                    unified_backtest.stop(owner.id)
                self.assertEqual((owner.folder / 'stop.request').read_text('ascii'), 'STOP\n')

    def test_web_page_and_authenticated_routes(self):
        host = server.LabServer(0)
        worker = threading.Thread(target=host.serve_forever, daemon=True)
        worker.start()
        try:
            def fetch(path, token=None):
                client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
                client.request('GET', path, headers={'X-Lab-Token': token or ''})
                response = client.getresponse()
                data = response.read()
                status = response.status
                client.close()
                return status, data

            status, page = fetch('/')
            self.assertEqual(status, 200)
            for label in ('라이브 감시', '백테스트', 'AI 전략연구', '설정'):
                self.assertIn(label.encode(), page)
            self.assertEqual(fetch('/api/mo/settings')[0], 403)
            with patch.object(unified_live, 'status', return_value={'modules': {}}):
                status, body = fetch('/api/mo/live/status', host.token)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['modules'], {})
        finally:
            host.shutdown()
            host.server_close()
            worker.join(timeout=5)

    def test_settings_http_round_trip_validates_before_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.txt'
            path.write_bytes(b'TELEGRAM_TOKEN=private-test-value\r\nWONBI_SIGMA=3.0\r\n'
                             b'STAFF_ALLOWED_TIMEFRAMES=1m,5m\r\n')
            with patch.object(unified_settings, 'LIVE_CONFIG', path):
                host = server.LabServer(0)
                worker = threading.Thread(target=host.serve_forever, daemon=True)
                worker.start()
                try:
                    def request(method, payload):
                        client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
                        client.request(method, '/api/mo/settings', body=json.dumps(payload) if payload else None,
                                       headers={'X-Lab-Token': host.token,
                                                'Content-Type': 'application/json'})
                        response = client.getresponse()
                        status, body = response.status, response.read()
                        client.close()
                        return status, json.loads(body)

                    status, response = request('GET', None)
                    self.assertEqual(status, 200)
                    self.assertNotIn('private-test-value', json.dumps(response))
                    status, _ = request('POST', {'group': 'live',
                                                  'changes': {'STAFF_ALLOWED_TIMEFRAMES': '99m'}})
                    self.assertEqual(status, 400)
                    self.assertIn(b'WONBI_SIGMA=3.0', path.read_bytes())
                    status, _ = request('POST', {'group': 'live', 'changes': {'WONBI_SIGMA': '2.5'}})
                    self.assertEqual(status, 200)
                    self.assertIn(b'WONBI_SIGMA=2.5', path.read_bytes())
                finally:
                    host.shutdown()
                    host.server_close()
                    worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
