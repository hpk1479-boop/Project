/* Actual Part3 HTML/JS, isolated browser, no external requests or engines. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const out = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'revision111/backtest_cancel');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const js = fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8');
const tests = [];
const add = (name, fn) => tests.push({name, fn});
async function fixture(browser) {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html);
    await page.addStyleTag({content: css});
    await page.evaluate(() => {
        window.timers = new Map(); let next = 0;
        window.setTimeout = (fn, delay) => {const id = ++next; timers.set(id, {fn, delay}); return id;};
        window.clearTimeout = id => timers.delete(id);
        window.setInterval = () => 0;
        window.api = async () => ({modules: {}, lines: []});
        window.flush = async () => {for (let i = 0; i < 12; i++) await Promise.resolve();};
    });
    await page.addScriptTag({content: js});
    await page.evaluate(async () => {
        await flush(); moState.view = 'backtest'; moState.job = 'a'.repeat(32);
        moBacktestPoll.done = false; moBacktestPoll.phase = 'run';
        moBacktestPage('progress'); moBacktestRecovery();
        mo('#view-live').classList.add('hidden'); mo('#view-backtest').classList.remove('hidden');
    });
    return page;
}

add('first cancel saves normally and stays available for a second click', async page => {
    const result = await page.evaluate(async () => {
        const calls = []; let confirms = 0;
        window.confirm = () => (++confirms, true);
        window.api = async (route, data) => {calls.push({route, data}); return {ok: true};};
        await mo('#mo-bt-stop-button').onclick();
        return {calls, confirms, enabled: !mo('#mo-bt-stop-button').disabled,
                message: mo('#mo-bt-phase').textContent, polling: timers.size};
    });
    assert.deepEqual(result.calls, [{route: 'mo/backtest/stop', data: {job_id: 'a'.repeat(32), force: false}}]);
    assert.equal(result.confirms, 0); assert.equal(result.enabled, true);
    assert.match(result.message, /종료 중/); assert.equal(result.polling, 1);
    await page.screenshot({path: path.join(out, 'stopping.png'), fullPage: true});
});

add('second cancel can force while the first response is still pending', async page => {
    const result = await page.evaluate(async () => {
        let resolveFirst; const calls = [], confirms = [];
        window.confirm = text => (confirms.push(text), true);
        window.api = (route, data) => {
            calls.push({route, data});
            return data.force ? Promise.resolve({ok: true, message: '강제 종료했습니다.'}) : new Promise(resolve => resolveFirst = resolve);
        };
        const first = mo('#mo-bt-stop-button').onclick(); await flush();
        const enabled = !mo('#mo-bt-stop-button').disabled;
        await mo('#mo-bt-stop-button').onclick();
        resolveFirst({ok: true, message: 'normal delayed reply'}); await first;
        return {calls, confirms, enabled, message: mo('#mo-bt-phase').textContent};
    });
    assert.equal(result.enabled, true); assert.deepEqual(result.confirms, ['강제 종료하시겠습니까?']);
    assert.deepEqual(result.calls.map(call => call.data.force), [false, true]);
    assert.equal(result.message, '강제 종료했습니다.');
});

add('declining force continues normal saving without a new stop request', async page => {
    const result = await page.evaluate(async () => {
        const calls = []; window.confirm = () => false;
        window.api = async (_route, data) => {calls.push(data); return {ok: true};};
        await moBacktestCancel(); await moBacktestCancel();
        return {calls, message: mo('#mo-bt-phase').textContent, enabled: !mo('#mo-bt-stop-button').disabled};
    });
    assert.deepEqual(result.calls.map(call => call.force), [false]);
    assert.match(result.message, /종료 중/); assert.equal(result.enabled, true);
});

add('status retains the stopping notice and confirms restored cancellation', async page => {
    const result = await page.evaluate(async () => {
        const calls = [], confirms = [];
        window.confirm = text => (confirms.push(text), true);
        window.api = async (route, data) => route.includes('/status') ?
            {phase: 'run', active: true, cancel_requested: true, progress: {phase: '녹화 중', build: 10, replay: 0, remaining: '', warnings: [], lines: []}} :
            (calls.push(data), {ok: true});
        await moReadBacktestStatus(moState.job);
        const notice = mo('#mo-bt-phase').textContent;
        await moBacktestCancel();
        return {notice, confirms, calls};
    });
    assert.match(result.notice, /종료 중/); assert.deepEqual(result.confirms, ['강제 종료하시겠습니까?']);
    assert.deepEqual(result.calls.map(call => call.force), [true]);
});

add('cancelled metadata with a live worker continues polling and allows escalation', async page => {
    const result = await page.evaluate(async () => {
        window.api = async () => ({phase: 'cancelled', active: true, cancel_requested: true});
        await moReadBacktestStatus(moState.job);
        return {done: moBacktestPoll.done, enabled: !mo('#mo-bt-stop-button').disabled,
                notice: mo('#mo-bt-phase').textContent};
    });
    assert.equal(result.done, false); assert.equal(result.enabled, true); assert.match(result.notice, /종료 중/);
});

add('normal completion removes cancellation state before the next job', async page => {
    const result = await page.evaluate(async () => {
        let calls = 0; window.confirm = () => {throw Error('unexpected force prompt');};
        window.api = async route => route.includes('/status') ?
            {phase: 'cancelled', active: false, cancel_requested: true} : (++calls, {ok: true});
        await moBacktestCancel(); await moReadBacktestStatus(moState.job);
        const clear = !moBacktestCancellations.has(moState.job);
        moState.job = 'b'.repeat(32); moBacktestPoll.done = false;
        await moBacktestCancel();
        return {clear, calls};
    });
    assert.deepEqual(result, {clear: true, calls: 2});
});

add('the plan cancel button uses the same two-click behavior', async page => {
    const result = await page.evaluate(async () => {
        const choices = []; window.confirm = () => true;
        window.api = async (_route, data) => (choices.push(data.force), {ok: true});
        await mo('#mo-bt-cancel-button').onclick();
        await mo('#mo-bt-stop-button').onclick();
        return choices;
    });
    assert.deepEqual(result, [false, true]);
});

add('force failure remains retryable and double clicks do not send duplicate force', async page => {
    const result = await page.evaluate(async () => {
        const choices = []; let rejectForce;
        window.confirm = () => true;
        window.api = (_route, data) => {
            choices.push(data.force);
            return data.force ? new Promise((_resolve, reject) => rejectForce = reject) : Promise.resolve({ok: true});
        };
        await moBacktestCancel(); const firstForce = moBacktestCancel(); await moBacktestCancel();
        rejectForce(Error('force failed')); await firstForce;
        window.api = async (_route, data) => (choices.push(data.force), {ok: true});
        await moBacktestCancel();
        return choices;
    });
    assert.deepEqual(result, [false, true, true]);
});

(async () => {
    fs.mkdirSync(out, {recursive: true});
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        for (const test of tests) {
            const page = await fixture(browser);
            try {await test.fn(page); results.push({name: test.name, passed: true}); console.log('PASS ' + test.name);}
            catch (error) {results.push({name: test.name, passed: false, error: error.message}); console.error('FAIL ' + test.name + ': ' + error.message);}
            finally {await page.close();}
        }
        const output = {tests: results.length, failed: results.filter(item => !item.passed).length,
                        environment: 'isolated Edge, actual UI, mocked APIs', results};
        fs.writeFileSync(path.join(out, 'frontend.json'), JSON.stringify(output, null, 2) + '\n');
        if (output.failed) process.exitCode = 1;
    } finally {await browser.close();}
})().catch(error => {console.error(error.message); process.exitCode = 1;});
