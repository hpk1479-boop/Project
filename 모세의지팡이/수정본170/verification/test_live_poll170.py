"""170: the live screen asks for its status one request at a time, reads the log lines only while the log
page is on screen, and makes the module cards again only when one of them changed (shipped scripts)."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
const calls = [];
let ticks = [], hold = null;
let live = {modules: {STAFF: {name: 'STAFF', state: '연결중'}, ENGINE: {name: '엔진', state: '연결중'}}, lines: ['line one'], error: ''};
const context = vm.createContext({console, document, Option, sessionStorage: {getItem: () => null, setItem() {}},
    window: {dispatchEvent() {}, addEventListener() {}},
    setInterval: callback => {ticks.push(callback); return ticks.length;}, setTimeout: () => 1, clearTimeout() {},
    api: async name => {
        calls.push(name);
        if (name.startsWith('mo/live/status')) {
            if (hold) await hold.promise;
            const lines = new URLSearchParams(name.split('?')[1]).get('lines') === 'false' ? [] : live.lines;
            return JSON.parse(JSON.stringify({...live, lines}));
        }
        throw Error('Unexpected API ' + name);
    }});
const run = code => vm.runInContext(code, context), get = id => document.querySelector('#' + id);
const settle = async () => {for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve));};
const statusCalls = () => calls.filter(name => name.startsWith('mo/live/status'));
function deferred() {let resolve; const promise = new Promise(done => {resolve = done;}); return {promise, resolve};}
const tick = () => ticks.forEach(callback => callback());
(async () => {
    run(fs.readFileSync(root + '/unified.js', 'utf8'));
    await settle();
    if (scenario === 'log') {
        // The cards page: no log lines asked for, the log left as it is.
        assert.equal(run('moState.livePage'), 'main');
        assert.match(statusCalls()[0], /&lines=false$/);
        assert.equal(get('live-log').textContent, '');
        run("moLivePage('log')"); await run('moLiveStatus()');
        assert.doesNotMatch(statusCalls().at(-1), /lines=/);
        assert.equal(get('live-log').textContent, 'line one');
        run("moLivePage('main')"); live.lines = ['line two']; tick(); await settle();
        assert.match(statusCalls().at(-1), /&lines=false$/);
        assert.equal(get('live-log').textContent, 'line one');
    } else if (scenario === 'one') {
        const before = statusCalls().length;
        hold = deferred();
        tick(); tick(); tick(); await settle();                 // timer ticks while one request is on its way
        assert.equal(statusCalls().length, before + 1);
        const asked = run('moLiveStatus()');                     // the user changed something meanwhile
        hold.resolve(); hold = null; await asked; await settle();
        assert.equal(statusCalls().length, before + 2);          // read once more after it, no more
        tick(); await settle();
        assert.equal(statusCalls().length, before + 3);
    } else if (scenario === 'cards') {
        const first = get('live-modules').children[0];
        assert.equal(get('live-modules').children.length, 2);
        tick(); await settle();
        assert.strictEqual(get('live-modules').children[0], first);   // the same states: the same cards
        live.modules.ENGINE.state = '오류'; tick(); await settle();
        assert.notStrictEqual(get('live-modules').children[0], first);
        assert.match(get('live-modules').textContent, /오류/);
    }
    console.log('PASS ' + scenario);
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
'''


@pytest.mark.parametrize('scenario', ['log', 'one', 'cards'])
def test_live_status_polling(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout
