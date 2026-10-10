/* Run with: node test_backtest_polling56.cjs <installed playwright module>.
 * Current HTML/JS in isolated Edge, mocked APIs, no engine/MT5/network calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const js = fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8');
const tests = [];
const add = (name, fn) => tests.push({name, fn});

async function fixture(browser, fakeTime = true) {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}});
    await page.route('**/*', route => route.abort());
    await page.setContent(html);
    await page.addStyleTag({content: css});
    await page.evaluate(fake => {
        window.timers = new Map(); let next = 0;
        if (fake) {
            window.setTimeout = (fn, delay) => { const id = ++next; timers.set(id, {fn, delay}); return id; };
            window.clearTimeout = id => timers.delete(id);
        }
        window.setInterval = () => 0;
        window.api = async () => ({modules: {STAFF: {name: 'STAFF', state: '대기'},
                                            ENGINE: {name: 'ENGINE', state: '대기'}}, lines: []});
        window.flush = async () => {for (let i = 0; i < 12; i++) await Promise.resolve();};
        window.tick = async () => {
            const [id, item] = [...timers][0]; timers.delete(id); item.fn(); await flush();
        };
    }, fakeTime);
    await page.addScriptTag({content: js});
    await page.evaluate(async () => {
        await flush(); moState.view = 'backtest';
        document.querySelector('#view-live').classList.add('hidden');
        document.querySelector('#view-backtest').classList.remove('hidden');
    });
    return page;
}

add('start locks before reply, deduplicates clicks, and queries immediately', async page => {
    const r = await page.evaluate(async () => {
        let starts = 0, statuses = 0, accept;
        window.api = route => route.includes('/start') ? (starts++, new Promise(r => accept = r)) :
            (statuses++, Promise.resolve({phase: 'planning'}));
        const first = moBacktestStart(), second = moBacktestStart();
        const locked = mo('#mo-bt-start-button').disabled;
        accept({job_id: 'a'.repeat(32)}); await Promise.all([first, second]); await flush();
        await moBacktestStart();
        return {starts, statuses, locked, next: [...timers.values()].map(t => t.delay)};
    });
    assert.deepEqual(r, {starts: 1, statuses: 1, locked: true, next: [300]});
});

add('failed start unlocks and allows a fresh request', async page => {
    const r = await page.evaluate(async () => {
        window.api = async () => {throw new Error('start failed');};
        try {await moBacktestStart();} catch (_) {}
        const unlocked = !mo('#mo-bt-start-button').disabled;
        window.api = async route => route.includes('/start') ? {job_id: 'a'.repeat(32)} : {phase: 'plan'};
        await moBacktestStart(); await flush();
        return {unlocked, active: moState.job === 'a'.repeat(32), locked: mo('#mo-bt-start-button').disabled};
    });
    assert.deepEqual(r, {unlocked: true, active: true, locked: true});
});

add('invalid local input unlocks without sending a start request', async page => {
    const r = await page.evaluate(async () => {
        const card = document.createElement('div');
        card.dataset.special = 'SPECIAL1'; card.dataset.loadError = 'profile must be selected';
        mo('#mo-bt-specials').append(card); let calls = 0;
        window.api = async () => {calls++; return {};};
        let rejected = false;
        try {await moBacktestStart();} catch (_) {rejected = true;}
        return {calls, rejected, unlocked: !mo('#mo-bt-start-button').disabled, job: moState.job};
    });
    assert.deepEqual(r, {calls: 0, rejected: true, unlocked: true, job: null});
});

add('a new run after completion resets the result and resumes fast polling', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); moState.resultShown = true; moBacktestPoll.done = true;
        mo('#mo-bt-result').textContent = 'old result'; let starts = 0, ids = [];
        window.api = async route => route.includes('/start') ? (starts++, {job_id: 'b'.repeat(32)}) :
            (ids.push(route.split('id=')[1]), {phase: 'planning'});
        await moBacktestStart(); await flush();
        return {starts, ids, resultCleared: mo('#mo-bt-result').textContent === '',
                shown: moState.resultShown, next: [...timers.values()].map(t => t.delay)};
    });
    assert.deepEqual(r, {starts: 1, ids: ['b'.repeat(32)], resultCleared: true, shown: false, next: [300]});
});

add('slow status has one request and schedules only after it settles', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); let calls = 0, accept;
        window.api = () => (calls++, new Promise(r => accept = r));
        const a = moBacktestStatus(), b = moBacktestStatus();
        const timersWhileWaiting = timers.size;
        accept({phase: 'run', result_ready: false}); await Promise.all([a, b]);
        return {calls, timersWhileWaiting, next: [...timers.values()].map(t => t.delay)};
    });
    assert.deepEqual(r, {calls: 1, timersWhileWaiting: 0, next: [1500]});
});

add('concurrent result loads share one request and one rendering', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); let calls = 0, accept;
        window.api = () => (calls++, new Promise(r => accept = r));
        const a = moResult(), b = moResult();
        accept({run_id: 'a'.repeat(32), status: '완료', alerts_preview: []});
        await Promise.all([a, b]);
        return {calls, shown: moState.resultShown, headings: mo('#mo-bt-result').querySelectorAll('h3').length};
    });
    assert.deepEqual(r, {calls: 1, shown: true, headings: 1});
});

add('failed result remains retryable', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32);
        window.api = async () => {throw new Error('result failed');};
        try {await moResult();} catch (_) {}
        const failedShown = moState.resultShown;
        window.api = async () => ({run_id: 'a'.repeat(32), status: '완료'});
        await moResult(); return {failedShown, retryShown: moState.resultShown};
    });
    assert.deepEqual(r, {failedShown: false, retryShown: true});
});

add('planning uses 300ms, confirmation and execution use 1500ms', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); moBacktestPoll.phase = 'planning';
        const states = [{phase: 'plan'}, {phase: 'confirm', plan: {record: [
            {start: '2026-09-01', end: '2026-10-01'}], estimate: {}}}, {phase: 'run'}];
        window.api = async () => states.shift();
        await moBacktestStatus(); const planning = [...timers.values()][0].delay;
        await tick(); const confirm = [...timers.values()][0].delay;
        const visible = !mo('#mo-bt-confirm-card').classList.contains('hidden');
        await tick(); const run = [...timers.values()][0].delay;
        return {planning, confirm, visible, run};
    });
    assert.deepEqual(r, {planning: 300, confirm: 1500, visible: true, run: 1500});
});

for (const phase of ['complete', 'cancelled', 'error']) {
    add(`${phase} stops polling after any available result is shown`, async page => {
        const r = await page.evaluate(async phase => {
            moState.job = 'a'.repeat(32); moBacktestPage('progress');
            let statuses = 0, results = 0;
            window.api = async route => route.includes('/status') ?
                (statuses++, {phase, result_ready: phase !== 'error', message: '오류 안내'}) :
                (results++, {run_id: 'a'.repeat(32), status: phase, alerts_preview: [{message: '부분 알림'}]});
            await moBacktestStatus(); await moBacktestStatus();
            moState.view = 'strategy'; moState.view = 'backtest'; await moBacktestStatus();
            return {statuses, results, next: timers.size, startEnabled: !mo('#mo-bt-start-button').disabled,
                    stopDisabled: mo('#mo-bt-stop-button').disabled, shown: moState.resultShown,
                    partialVisible: mo('#mo-bt-result').textContent.includes('부분 알림')};
        }, phase);
        assert.deepEqual(r, {statuses: 1, results: phase === 'error' ? 0 : 1, next: 0,
            startEnabled: true, stopDisabled: true, shown: phase !== 'error', partialVisible: phase !== 'error'});
    });
}

add('terminal result failure retries before stopping polling', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); mo('#mo-bt-start-button').disabled = true;
        let attempts = 0;
        window.api = async route => {
            if (route.includes('/status')) return {phase: 'complete', result_ready: true};
            if (++attempts === 1) throw new Error('temporary failure');
            return {run_id: 'a'.repeat(32), status: '완료'};
        };
        try {await moBacktestStatus();} catch (_) {}
        const pending = {shown: moState.resultShown, locked: mo('#mo-bt-start-button').disabled,
                         stopDisabled: mo('#mo-bt-stop-button').disabled, next: [...timers.values()].map(t => t.delay)};
        await tick();
        return {pending, attempts, shown: moState.resultShown, next: timers.size,
                startEnabled: !mo('#mo-bt-start-button').disabled, stopDisabled: mo('#mo-bt-stop-button').disabled};
    });
    assert.deepEqual(r, {pending: {shown: false, locked: true, stopDisabled: true, next: [1500]}, attempts: 2, shown: true,
                         next: 0, startEnabled: true, stopDisabled: true});
});

// 수정본181: a completed job's result read is tried three times; then polling ends with the error shown,
// and opening the job again reads it again.
add('a result that keeps failing stops after three reads and can be opened again', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32);
        let reads = 0, fail = true;
        window.api = async route => {
            if (route.includes('/status')) return {phase: 'complete', result_ready: true};
            if (route.includes('/result')) {
                reads++;
                if (fail) throw new Error('결과 파일 손상');
                return {run_id: 'a'.repeat(32), status: '완료'};
            }
            return {};
        };
        try {await moBacktestStatus();} catch (_) {}
        await tick(); await tick();
        const stopped = {reads, next: timers.size, done: moBacktestPoll.done, shown: moState.resultShown,
                         startEnabled: !mo('#mo-bt-start-button').disabled, stopDisabled: mo('#mo-bt-stop-button').disabled,
                         warning: mo('#mo-bt-warnings').textContent};
        fail = false;
        await moAdoptBacktestJob({job_id: 'a'.repeat(32), phase: 'complete'}); await flush(); await flush();
        return {stopped, reads, shown: moState.resultShown};
    });
    assert.deepEqual(r, {stopped: {reads: 3, next: 0, done: true, shown: false, startEnabled: true, stopDisabled: true,
                                   warning: '결과 파일 손상'}, reads: 4, shown: true});
});

add('status network failure keeps retrying without request overlap', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); moBacktestPoll.phase = 'planning'; let calls = 0;
        window.api = async () => {if (++calls === 1) throw new Error('offline'); return {phase: 'run'};};
        try {await moBacktestStatus();} catch (_) {}
        const retry = [...timers.values()][0].delay;
        await tick(); return {calls, retry, next: [...timers.values()][0].delay};
    });
    assert.deepEqual(r, {calls: 2, retry: 300, next: 1500});
});

add('safe stop request keeps polling until the partial result exists', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); mo('#mo-bt-start-button').disabled = true;
        let phase = 'run', stops = 0;
        window.api = async route => {
            if (route.includes('/stop')) {stops++; return {ok: true};}
            if (route.includes('/status')) return {phase, result_ready: phase === 'cancelled'};
            return {run_id: 'a'.repeat(32), status: '중단됨(부분 결과)', alerts_preview: [{message: '보존한 알림'}]};
        };
        await mo('#mo-bt-stop-button').onclick(); await tick();
        const stillLocked = mo('#mo-bt-start-button').disabled;
        phase = 'cancelled'; await tick();
        return {stops, stillLocked, next: timers.size, partial: mo('#mo-bt-result').textContent.includes('보존한 알림')};
    });
    assert.deepEqual(r, {stops: 1, stillLocked: true, next: 0, partial: true});
});

add('leaving cancels polling, returning refreshes an active job', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); let statuses = 0;
        window.api = async route => route.includes('/options') ?
            {symbols: [], specials: [], symbol: 'XAUUSD+', start: '2026-09-01', end: '2026-10-01', mode: 'BAR'} :
            (statuses++, {phase: 'planning'});
        await moBacktestStatus(); await moView('strategy');
        const away = timers.size;
        await moView('backtest');
        return {away, statuses, next: [...timers.values()].map(t => t.delay)};
    });
    assert.deepEqual(r, {away: 0, statuses: 2, next: [300]});
});

add('late old status and result do not overwrite a new job', async page => {
    const r = await page.evaluate(async () => {
        moState.job = 'a'.repeat(32); let accept, resultCalls = 0;
        window.api = route => route.includes('/status') ? new Promise(r => accept = r) : (resultCalls++, Promise.resolve({}));
        const pending = moBacktestStatus(); moState.job = 'b'.repeat(32);
        accept({phase: 'complete', result_ready: true}); await pending;
        const untouchedStatus = !moBacktestPoll.done && resultCalls === 0;
        moStopBacktestPolling();
        moState.job = 'a'.repeat(32); window.api = () => new Promise(r => accept = r);
        const oldResult = moResult(); moState.job = 'b'.repeat(32);
        accept({run_id: 'a'.repeat(32), status: '완료'}); await oldResult;
        return {untouchedStatus, shown: moState.resultShown, text: mo('#mo-bt-result').textContent};
    });
    assert.deepEqual(r, {untouchedStatus: true, shown: false, text: ''});
});

(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        for (const test of tests) {
            const page = await fixture(browser);
            try {await test.fn(page); results.push({name: test.name, passed: true}); console.log('PASS ' + test.name);}
            catch (error) {results.push({name: test.name, passed: false, error: error.message}); console.error('FAIL ' + test.name + ': ' + error.message);}
            finally {await page.close();}
        }
        // Real timer: the plan is already ready when the start reply arrives.
        const page = await fixture(browser, false);
        const readyPlanMs = await page.evaluate(async () => {
            window.api = async route => route.includes('/start') ? {job_id: 'a'.repeat(32)} :
                {phase: 'confirm', plan: {record: [{start: '2026-09-01', end: '2026-10-01'}], estimate: {}}};
            const started = performance.now(); await moBacktestStart(); await flush();
            if (mo('#mo-bt-confirm-card').classList.contains('hidden')) throw new Error('ready plan was not displayed immediately');
            moStopBacktestPolling(); return +(performance.now()-started).toFixed(3);
        });
        await page.close();
        const output = {tests: results.length, failed: results.filter(r => !r.passed).length,
                        ready_plan_display_ms: readyPlanMs, environment: 'isolated Edge, mocked APIs', results};
        const folder = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '웹UI최적화'); fs.mkdirSync(folder, {recursive: true});
        fs.writeFileSync(path.join(folder, 'frontend_tests.json'), JSON.stringify(output, null, 2)+'\n');
        console.log(JSON.stringify({tests: output.tests, failed: output.failed, ready_plan_display_ms: readyPlanMs}));
        if (output.failed) process.exitCode = 1;
    } finally {await browser.close();}
})().catch(error => {console.error(error.message); process.exitCode = 1;});
