"""Run inside the actual frozen worker in a disposable licensed payload copy."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

GUI_SECONDS = 13.0
GET_ENDPOINTS = (
    '/api/init', '/api/mo/live/status?module=ALL&detail=false',
    '/api/mo/live/specials', '/api/mo/live/engines', '/api/mo/settings',
    '/api/mo/backtest/options', '/api/mo/backtest/recent', '/api/recent',
    '/api/ai/settings', '/api/ping', '/api/strategies',
)
APP_PREFIXES = ('Part1', 'Part2', 'Part3', 'lab', 'common_ai', 'event_backtest',
                'generic_backtest', 'event_host', 'START_MOSES')


class Assets(HTMLParser):
    """Collect local script/link resources from the application's actual HTML."""
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
        if value:
            parsed = urlsplit(value)
            if not parsed.scheme and not parsed.netloc and parsed.path:
                self.paths.add('/' + parsed.path.lstrip('/'))


def import_names(value):
    names = value.get('modules') if isinstance(value, dict) else value
    if not isinstance(names, list) or not names:
        raise ValueError('Nonempty validation import candidates are required.')
    result = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', name):
            raise ValueError('Invalid import candidate.')
        if any(name == prefix or name.startswith(prefix + '.') for prefix in APP_PREFIXES):
            raise ValueError('Application entrypoints are not external import candidates: ' + name)
        if name not in result:
            result.append(name)
    return result


def safe_text(value, root):
    text = str(value)
    for source, replacement in ((str(root), '<payload>'), (str(Path.home()), '<local>')):
        text = text.replace(source.replace('\\', '\\\\'), replacement).replace(source, replacement)
    return re.sub(r'[A-Za-z]:[\\/][^\r\n\s"\'<>|]*', '<path>', text)


def save_report(root, mode, checks, errors):
    report = {'schema': 1, 'mode': mode, 'nonce': os.environ.get('MOSES_VALIDATION_NONCE', ''),
              'passed': bool(checks) and not errors and all(row['passed'] for row in checks),
              'checks': checks, 'errors': [safe_text(error, root) for error in errors],
              'external_mt5_or_ai_services_started': False}
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return safe_text(value, root) if isinstance(value, str) else value
    report = clean(report)
    destination = root / 'runtime' / ('validation_' + mode + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, destination)
    return report


def service_start_command(command):
    parts = [str(item) for item in command] if isinstance(command, (list, tuple)) else [str(command)]
    joined = ' '.join(parts).lower().replace('\\', '/')
    executable = Path(parts[0]).name.lower() if parts else ''
    # Terminal inventory mentions terminal64.exe in a read-only CIM filter.
    # Keep that existing input path; it does not launch or attach to MT5.
    mutations = ('start-process', 'stop-process', 'remove-item', 'invoke-expression')
    if executable in ('powershell.exe', 'pwsh.exe') and any(
            query in joined for query in ('get-ciminstance', 'get-process')) and not any(
            value in joined for value in mutations):
        return False
    return any(marker in joined for marker in ('terminal64.exe', 'terminal.exe', 'metaeditor',
               'ollama', 'llama-server', 'common_ai.service', 'event_host.py',
               'backtest_supervisor.py', '-m event_backtest', *mutations))


def install_boundaries(ports):
    """Allow local UI traffic and read-only OS queries, reject app service starts."""
    def audit(event, args):
        if event == 'socket.connect':
            address = args[1]
            if isinstance(address, tuple):
                if str(address[0]).lower() not in ('127.0.0.1', '::1', 'localhost') or address[1] not in ports:
                    raise PermissionError('Validation cannot contact external or existing services.')
        elif event == 'subprocess.Popen':
            if service_start_command(args[1]):
                raise PermissionError('Validation cannot start MT5, AI or application workers.')
    sys.addaudithook(audit)


def probe_imports(root, checks):
    candidates = import_names(json.loads((root / 'runtime/validation_candidates.json').read_text('utf-8')))
    for name in candidates:
        try:
            importlib.import_module(name)
            checks.append({'label': name, 'passed': True})
        except Exception as exc:
            checks.append({'label': name, 'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)})


def request_row(base, token, endpoint, *, post=False):
    headers = {'X-Lab-Token': token}
    if post:
        headers['Content-Type'] = 'application/json'
    request = Request(base + endpoint, headers=headers, data=b'{}' if post else None,
                      method='POST' if post else 'GET')
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        row = {'label': endpoint, 'status': response.status, 'passed': response.status == 200,
               'bytes': len(raw)}
        content_type = response.headers.get('Content-Type', '')
        if content_type.startswith('application/json'):
            data = json.loads(raw)
            if response.status != 200:
                row['error'] = data.get('error', 'Request failed.')
            if endpoint.startswith('/api/mo/live/status'):
                count = sum(not item.get('preset', False) for item in data.get('modules', {}).values())
                row['live_cards'] = count
                row['passed'] = row['passed'] and count == 8
        return row, raw


def checked_folder(expected, target):
    candidate = Path(target).resolve()
    if candidate != Path(expected).resolve() or not candidate.is_dir():
        raise FileNotFoundError('The generated-file directory is missing.')
    return candidate


def probe_http(root, server, checks):
    base = 'http://127.0.0.1:' + str(server.server_port)
    from lab.server import DOCS
    endpoints = [*GET_ENDPOINTS, *('/api/doc?name=' + quote(name, safe='/') for name in DOCS), '/']
    candidates = json.loads((root / 'runtime/validation_candidates.json').read_text('utf-8'))
    for endpoint in candidates.get('http_endpoints', []):
        if not isinstance(endpoint, str) or not endpoint.startswith('/') or endpoint.startswith('//'):
            raise ValueError('Invalid local validation endpoint.')
        if endpoint != '/api/folder' and endpoint not in endpoints:
            endpoints.append(endpoint)
    for endpoint in endpoints:
        try:
            row, raw = request_row(base, server.token, endpoint)
            checks.append(row)
            if endpoint == '/' and row['passed']:
                assets = Assets()
                assets.feed(raw.decode('utf-8'))
                endpoints.extend(sorted(assets.paths - set(endpoints)))
        except Exception as exc:
            checks.append({'label': endpoint, 'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)})
    expected = root / 'Part3/TEST_SPECIAL'
    original_startfile = getattr(os, 'startfile', None)
    opened = []
    def startfile(path, *args, **kwargs):
        opened.append(checked_folder(expected, path))
    os.startfile = startfile
    try:
        row, _ = request_row(base, server.token, '/api/folder', post=True)
        row['folder_exists'] = expected.is_dir()
        row['passed'] = row['passed'] and row['folder_exists'] and len(opened) == 1
        checks.append(row)
    finally:
        if original_startfile is None:
            del os.startfile
        else:
            os.startfile = original_startfile
    probe_user_strategies(root, base, server.token, checks)


def fixture_engine_instances(instances, root):
    """Retain only engines owned by this disposable Part1 directory."""
    part1 = (Path(root) / 'Part1').resolve()
    result = []
    for instance in instances:
        if not isinstance(instance, dict) or not isinstance(instance.get('root'), (str, Path)):
            raise ValueError('Validation engine inventory has no trustworthy root.')
        if Path(instance['root']).resolve() == part1:
            result.append(instance)
    return result


def probe_user_strategies(root, base, token, checks):
    """Exercise the real 101 library/host lifecycle in the disposable fixture."""
    def post(endpoint, data, status=200):
        request = Request(base + endpoint, headers={'X-Lab-Token': token,
            'Content-Type': 'application/json'}, data=json.dumps(data).encode(), method='POST')
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            if response.status != status:
                detail = ''
                try:
                    body = json.loads(response.read(8192))
                    if isinstance(body, dict) and isinstance(body.get('error'), str):
                        detail = ': ' + safe_text(body['error'], root)[:600]
                except (ValueError, UnicodeError):
                    pass
                raise AssertionError('Unexpected lifecycle HTTP status: ' + endpoint + ':' + str(response.status) + detail)
            return json.loads(response.read())
    def get(endpoint):
        row, raw = request_row(base, token, endpoint)
        if not row['passed']:
            raise AssertionError('Strategy lifecycle GET failed: ' + endpoint)
        return json.loads(raw)
    def hosts(name, part1, part2):
        live, backtest = get('/api/mo/live/specials'), get('/api/mo/backtest/options')
        from strategy_recipe import registry
        if ((name in live['items']) != part1 or (name in backtest['specials']) != part2
                or (name in registry.list_presets('Part1')) != part1
                or (name in registry.list_presets('Part2')) != part2):
            raise AssertionError('Host registrations diverged.')
        return live, backtest
    engine_api = original_inventory = None
    try:
        from lab.live_processes import process_api
        engine_api = process_api()
        original_inventory = engine_api.find_engine_instances
        engine_api.find_engine_instances = lambda: fixture_engine_instances(original_inventory(), root)
        from strategy_recipe import registry
        from lab import catalog
        initial = get('/api/strategies')
        if initial['items'] or initial.get('errors'):
            raise AssertionError('Developer test strategies entered the recipient payload.')
        symbols = list(catalog.current_symbols())
        recipe = {'schema_version': 2, 'base': 'AI', 'name': 'Release validation user strategy',
            'description': 'Disposable library validation', 'symbols': symbols,
            'strategy_intent': {'direction': 'LONG', 'symbols': symbols,
                'order_mode': 'SIMULTANEOUS', 'steps': [{'kind': 'MA_PRICE_TOUCH', 'tfs': ['5m'],
                    'ma_family': 'EMA', 'slow_period': 50}],
                'final': {'kind': 'OZ', 'tfs': ['5m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}}
        generated = post('/api/generate', {'recipe': recipe})
        name = generated['strategy_id']
        code = root / 'Part3/TEST_SPECIAL' / generated['filename']
        sidecar = code.with_suffix('.recipe.json')
        if not code.is_file() or not sidecar.is_file():
            raise AssertionError('The test library is not writable.')
        library = get('/api/strategies')
        if len(library['items']) != 1 or library['items'][0]['id'] != name or library['items'][0]['registrations']:
            raise AssertionError('Generation implicitly promoted the strategy.')
        hosts(name, False, False)
        post('/api/strategies', {'action': 'promote', 'ids': [name]})
        live, backtest = hosts(name, True, True)
        if live['items'][name]['enabled'] or name in backtest['selected_specials']:
            raise AssertionError('Promotion implicitly enabled a live/backtest strategy.')
        title = 'Renamed release validation strategy'
        post('/api/strategies', {'action': 'rename', 'id': name, 'name': title})
        live, backtest = hosts(name, True, True)
        if live['items'][name]['name'] != title or backtest['strategy_names'][name] != title:
            raise AssertionError('Renaming did not reach both hosts.')
        for part, flags in (('Part1', (False, True)), ('Part2', (False, False))):
            post('/api/mo/strategies', {'action': 'delete', 'part': part, 'ids': [name]})
            hosts(name, *flags)
            if not code.is_file() or not sidecar.is_file() or len(get('/api/strategies')['items']) != 1:
                raise AssertionError('Host exclusion deleted the Part3 original.')
        post('/api/strategies', {'action': 'promote', 'ids': [name]})
        post('/api/strategies', {'action': 'promote', 'ids': [name]})
        live, backtest = hosts(name, True, True)
        if backtest['specials'].count(name) != 1 or get('/api/strategies')['items'][0]['registrations'] != ['Part1', 'Part2']:
            raise AssertionError('Repeated promotion created duplicate registrations.')
        rejected = post('/api/strategies', {'action': 'delete', 'ids': [name]}, status=400)
        if not rejected.get('error') or not code.exists():
            raise AssertionError('Registered deletion did not require confirmation.')
        post('/api/strategies', {'action': 'delete', 'ids': [name], 'confirmed': True})
        hosts(name, False, False)
        if code.exists() or sidecar.exists() or get('/api/strategies')['items']:
            raise AssertionError('Confirmed deletion did not remove all three registrations/files.')
        first = next(iter(registry.builtin_entries()), None)
        if first:
            for part, flags in (('Part1', (False, True)), ('Part2', (False, False))):
                post('/api/mo/strategies', {'action': 'delete', 'part': part, 'ids': [first]})
                hosts(first, *flags)
            post('/api/mo/strategies', {'action': 'reset', 'part': 'Part1'})
            hosts(first, True, False)
            post('/api/mo/strategies', {'action': 'reset', 'part': 'Part2'})
            hosts(first, True, True)
        if name in registry.list_presets() or get('/api/strategies')['items']:
            raise AssertionError('Reset recovered a deleted test strategy.')
        checks.append({'label': 'generated_strategy_lifecycle101', 'passed': True,
            'fresh_library_empty': True, 'promotion_both_hosts': True, 'independent_exclusion': True,
            'rename_both_hosts': True, 'confirmed_delete_all_parts': True, 'builtin_reset': True})
    except Exception as exc:
        checks.append({'label': 'generated_strategy_lifecycle101', 'passed': False,
                       'error': type(exc).__name__ + ': ' + str(exc)})
    finally:
        if original_inventory is not None:
            engine_api.find_engine_instances = original_inventory


JS_STATE = """(() => {
  window.__mosesValidationErrors ??= [];
  if (!window.__mosesValidationHook) {
    window.__mosesValidationHook = true;
    window.addEventListener('error', e => window.__mosesValidationErrors.push(String(e.message)));
    window.addEventListener('unhandledrejection', e => window.__mosesValidationErrors.push(String(e.reason)));
  }
  return {bootstrap: typeof moView === 'function' && typeof moState === 'object',
    cards: document.querySelector('#live-modules')?.children.length || 0,
    view: typeof moState === 'object' ? moState.view : '',
    backtest: typeof moBacktestSpecialOptions !== 'undefined' && moBacktestSpecialOptions !== null,
    settings: typeof moState === 'object' && moState.settings !== null,
    strategy: typeof window.part3ApplyAIRecipe === 'function'
      && typeof window.part3IntentEditor?.create === 'function'
      && !!document.querySelector('#view-strategy .code-panel #code-preview')
      && !!document.querySelector('#preview-status')
      && document.querySelectorAll('#view-strategy [data-action="preview-tab"]').length === 2
      && !!document.querySelector('#view-strategy [data-action="full-code"]')
      && !!document.querySelector('#generate-button')
      && document.querySelector('#ai-preset-dialog')?.open === true
      && document.querySelector('#ai-preset-open')?.disabled === false
      && document.querySelectorAll('#ai-preset-list > details').length === 2
      && !document.querySelector('#ai-preset-message')?.classList.contains('has-error')
      && ['ai-preset-detail', 'ai-preset-detail-title', 'ai-preset-example', 'ai-preset-load',
          'ai-preset-cancel', 'strategy-library-promote', 'strategy-library-rename',
          'strategy-library-delete', 'strategy-library-rename-dialog'].every(id => document.getElementById(id)),
    setup_error: document.querySelector('#mo-bt-setup-error')?.textContent || '',
    errors: window.__mosesValidationErrors};
})()"""


JS_STRATEGY_DETAIL = """(() => {
  const list = document.querySelector('#ai-preset-list');
  const detail = document.querySelector('#ai-preset-detail');
  const load = document.querySelector('#ai-preset-load');
  const first = list?.querySelector('button[data-preset-id]');
  if (!detail || !load) return false;
  if (!first) return detail.hidden && load.disabled && [...list.querySelectorAll(':scope > details')].every(
    group => !!group.querySelector('p')?.textContent.trim());
  first.click();
  return !detail.hidden && !load.disabled && first.getAttribute('aria-pressed') === 'true'
    && document.querySelector('#ai-preset-detail-title')?.textContent === first.textContent
    && !!document.querySelector('#ai-preset-example')?.textContent.trim();
})()"""


def rendered_checks(window, *, deadline, clock=time.monotonic, sleep=time.sleep):
    """All four views share one deadline; a failed bootstrap cannot hang a loop."""
    rows = []
    for view in ('live', 'backtest', 'settings', 'strategy'):
        if view != 'live':
            window.evaluate_js('moView(' + json.dumps(view) + '); true')
        if view == 'strategy':
            window.evaluate_js("document.querySelector('#ai-preset-open')?.click(); true")
        state = {}
        while clock() < deadline:
            state = window.evaluate_js(JS_STATE) or {}
            if state.get('errors') or (view == 'backtest' and state.get('setup_error')):
                break
            ready = state.get('cards') == 8 if view == 'live' else state.get(view)
            if state.get('bootstrap') and state.get('view') == view and ready:
                break
            sleep(.1)
        ready = state.get('cards') == 8 if view == 'live' else state.get(view)
        if view == 'strategy' and ready:
            state['strategy_detail'] = window.evaluate_js(JS_STRATEGY_DETAIL) is True
            ready = state['strategy_detail']
        passed = bool(state.get('bootstrap') and state.get('view') == view and ready and
                      not state.get('errors') and not (view == 'backtest' and state.get('setup_error')))
        rows.append({'label': view, 'passed': passed, 'state': state,
                     'deadline_reached': clock() >= deadline})
        if not passed:
            break
        if view == 'strategy':
            window.evaluate_js("document.querySelector('#ai-preset-cancel')?.click(); true")
    return rows


def probe_gui(root, server, checks, errors):
    import webview
    deadline = time.monotonic() + GUI_SECONDS
    window = webview.create_window('MOSES release validation',
        'http://127.0.0.1:' + str(server.server_port) + '/#token=' + server.token,
        width=1440, height=900, min_size=(1024, 680), hidden=True,
        background_color='#0b1423')
    completed = threading.Event()
    def inspect_views():
        try:
            if not window.events.loaded.wait(max(0, deadline - time.monotonic())):
                raise TimeoutError('WebView bootstrap did not finish within 13 seconds.')
            checks.extend(rendered_checks(window, deadline=deadline))
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
        finally:
            completed.set()
            # Write failure proof even if an OS renderer cannot close promptly.
            # The outer runner owns the bounded process/tree cleanup.
            save_report(root, 'gui', checks, errors)
            if window.events.shown.is_set():
                window.destroy()
    webview.start(inspect_views, gui='edgechromium', private_mode=True, debug=False)
    if not completed.wait(.5):
        errors.append('The hidden WebView exited before view validation completed.')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[1] not in ('imports', 'http', 'gui'):
        raise ValueError('Validation expects a payload root and imports/http/gui mode.')
    root, mode = Path(args[0]).resolve(), args[1]
    sys.path[:0] = [str(root), str(root / 'Part3'), str(root / 'Part2'), str(root / 'Part1/program')]
    checks, errors, ports = [], [], set()
    server = thread = None
    install_boundaries(ports)
    try:
        if mode == 'imports':
            probe_imports(root, checks)
        else:
            from lab.server import LabServer
            server = LabServer(0)
            ports.add(server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True, name='release-validation-http')
            thread.start()
            if mode == 'http':
                probe_http(root, server, checks)
            else:
                probe_gui(root, server, checks, errors)
    except Exception as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
    report = save_report(root, mode, checks, errors)
    print(json.dumps({'mode': mode, 'passed': report['passed'], 'checks': len(checks)}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
