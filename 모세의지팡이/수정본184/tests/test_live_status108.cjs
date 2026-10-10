/* Actual live DOM/handlers in Edge, synthetic APIs and a controlled UI timer only. */
'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '..'), proof = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'live_status108');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const checks = [], errors = [], pass = name => checks.push(name);
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 1000}});
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
      route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
    await page.goto('http://127.0.0.1:8763/');
    await page.addStyleTag({content: fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8')});
    await page.evaluate(() => {
      const names = {STAFF: 'STAFF', ENGINE: 'EVENT', OZ: 'OZ', SWEEP: 'SWEEP', FVG: 'FVG',
        INDICATOR: 'INDICATOR', WATCH: 'WATCH', KIM: '김비서'};
      window.makeStatus = (state = '연결중', pipe = true) => ({modules: Object.fromEntries(
        Object.entries(names).map(([id, name]) => [id, {name, state, preset: false, errors: 0}])),
        lines: ['[ENGINE] 합성 상태 기록'], error: null, pipe_connected: pipe, enabled_specials: []});
      window.liveStatus = makeStatus(); window.calls = []; window.startReplies = [];
      window.stopReplies = []; window.pendingReplies = []; window.statusError = '';
      window.confirmations = []; window.confirmAnswers = []; window.pollCallbacks = [];
      window.uiTime = 0; window.uiTimerId = 0; window.uiTimers = new Map();
      window.setTimeout = (fn, delay) => {
        const id = ++uiTimerId; uiTimers.set(id, {at: uiTime + Number(delay || 0), fn}); return id;
      };
      window.clearTimeout = id => uiTimers.delete(id);
      window.setInterval = (callback, delay) => {pollCallbacks.push({callback, delay}); return pollCallbacks.length;};
      window.clearInterval = () => {};
      window.advanceTimers = async delta => {
        const until = uiTime + delta;
        while (true) {
          const next = [...uiTimers.entries()].filter(([, row]) => row.at <= until)
            .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
          if (!next) break;
          uiTime = next[1].at; uiTimers.delete(next[0]); next[1].fn(); await Promise.resolve();
        }
        uiTime = until; await Promise.resolve();
      };
      window.confirm = text => {confirmations.push(text); return confirmAnswers.shift() ?? true;};
      window.part3InvalidateAIRecipe = () => {};
      window.fetch = async () => ({ok: true, json: async () => ({settings: {provider: 'disabled'}})});
      const reply = (queue, action) => {
        const next = queue.shift() || {ok: true, message: '합성 완료', warnings: []};
        if (next.defer) return new Promise((resolve, reject) => pendingReplies.push({action, resolve, reject}));
        if (next.error) throw Error(next.error);
        return next;
      };
      window.api = async (name, body) => {
        calls.push({name, body});
        if (name.startsWith('mo/live/status?')) {
          if (statusError) throw Error(statusError);
          return JSON.parse(JSON.stringify(liveStatus));
        }
        if (name === 'mo/live/start') return reply(startReplies, 'start');
        if (name === 'mo/live/stop') return reply(stopReplies, 'stop');
        if (name === 'mo/live/specials') return body ?
          {ok: true, message: String.raw`전략 저장 · program\event_host.py 시작 완료`, warnings: []} :
          {items: {}, trigger_choices: [], needs_recovery: false};
        return {modules: {}, lines: []};
      };
      window.resolveAction = response => pendingReplies.shift().resolve(response);
      window.rejectAction = message => pendingReplies.shift().reject(Error(message));
    });
    for (const file of ['ai_chat.js', 'unified.js']) await page.addScriptTag({
      content: fs.readFileSync(path.join(root, 'Part3/web', file), 'utf8')});
    await page.evaluate(async () => {document.dispatchEvent(new Event('DOMContentLoaded')); await moView('live');});
    const action = page.locator('#live-action-message'), idle = page.locator('#live-message');
    const main = page.locator('#live-main-page .moses-live-status'), log = page.locator('#live-log');
    const text = () => main.innerText();
    const update = () => page.evaluate(async () => {await moLiveStatus();});
    const advance = ms => page.evaluate(async ms => {await advanceTimers(ms);}, ms);
    const mainPage = () => page.evaluate(() => moLivePage('main'));
    const selectLog = async name => {
      await page.evaluate(name => {moState.liveModule = name; mo('#live-log-module').value = name; moLivePage('log');}, name);
      await update();
    };
    const actionCalls = () => page.evaluate(() => calls.filter(row => /mo\/live\/(start|stop)$/.test(row.name)));
    assert.equal(await text(), '라이브 감시 연결중');
    assert.equal(await action.isVisible(), false); assert.equal(await idle.isVisible(), true);
    assert.equal(await page.locator('#live-modules .moses-module').count(), 8);
    assert.deepEqual(await page.locator('#live-modules .moses-module strong').allTextContents(),
      ['STAFF', 'EVENT', 'OZ', 'SWEEP', 'FVG', 'INDICATOR', 'WATCH', '김비서']);
    assert.deepEqual(await page.locator('#live-modules .moses-module-icon').allTextContents(), ['▤', '⚙', '◇', '≋', '▥', '▴', '◉', '▣']);
    pass('평상시 연결중 한 줄, 기존 8개 카드·이름·아이콘 유지');
    for (const scenario of ['pipe', 'STAFF', 'ENGINE']) {
      await page.evaluate(scenario => {liveStatus = makeStatus();
        if (scenario === 'pipe') liveStatus.pipe_connected = false; else liveStatus.modules[scenario].state = '연결대기';}, scenario);
      await update(); assert.equal(await text(), '라이브 감시 연결대기');
    }
    await page.evaluate(() => {liveStatus = makeStatus(); liveStatus.modules.WATCH.state = '연결대기'; liveStatus.modules.KIM.state = '연결대기';});
    await update(); assert.equal(await text(), '라이브 감시 연결중');
    pass('STAFF·EVENT·MT5 조건으로 연결중/연결대기 판정, 개별 대기 카드 유지');
    await page.evaluate(() => {liveStatus.modules.OZ.state = '오류';}); await update();
    assert.equal(await text(), '라이브 감시 오류 · 모듈 로그를 확인해 주세요.');
    assert.equal(await page.locator('[data-module="OZ"] .state').innerText(), '● 오류');
    assert.equal(await page.locator('[data-module="OZ"] .state').evaluate(node => node.classList.contains('error')), true);
    await page.evaluate(() => {liveStatus = makeStatus(); liveStatus.modules.SPECIAL_SYNTHETIC = {name: '합성 전략', state: '오류', preset: true};});
    await update(); assert.equal(await text(), '라이브 감시 연결중');
    assert.equal(await page.locator('#live-modules .moses-module').count(), 8);
    pass('실제 카드 오류만 짧게 안내, 전략 상태는 8개 카드에 섞이지 않음');
    await page.evaluate(() => {liveStatus = makeStatus(); liveStatus.error = String.raw`상태 응답 실패 program\event_host.py`;});
    await update(); assert.equal(await text(), '라이브 감시 상태 확인 대기');
    await selectLog('ENGINE'); assert.match(await page.locator('#live-log-error').innerText(), /program\\event_host\.py/);
    await mainPage(); await page.evaluate(() => {liveStatus = makeStatus();}); await update();
    pass('상태 응답 오류는 메인 짧은 안내, 모듈 로그에는 원문 보존');
    await page.evaluate(async () => {
      statusError = String.raw`초기 상태 실패 program\event_host.py`;
      await moView('live');
    });
    assert.equal(await text(), '라이브 감시 상태 확인 대기');
    assert.doesNotMatch(await text(), /program|event_host/);
    await page.evaluate(() => {statusError = '';}); await selectLog('ENGINE');
    assert.match(await log.innerText(), /초기 상태 실패 program\\event_host\.py/);
    await mainPage(); await update();
    pass('탭 진입 상태조회 실패도 내부 경로를 메인에 노출하지 않음');
    await page.evaluate(() => {statusError = String.raw`주기 상태 실패 program\event_host.py`; pollCallbacks.find(row => row.delay === 1500).callback();});
    await page.waitForFunction(() => document.querySelector('#live-log-error').textContent.includes('주기 상태 실패'));
    assert.equal(await text(), '라이브 감시 상태 확인 대기');
    assert.match(await page.locator('#live-log-error').textContent(), /program\\event_host\.py/);
    await page.evaluate(() => {statusError = '';}); await update();
    pass('주기 상태조회 실패도 메인 짧은 안내와 원문 로그 분리');
    await page.evaluate(() => {startReplies.push({defer: true});}); await page.locator('#live-start').click();
    await page.waitForFunction(() => pendingReplies.length === 1);
    assert.equal(await idle.isVisible(), false); assert.equal(await action.isVisible(), true);
    assert.equal(await page.locator('#live-start').isDisabled(), true); assert.equal(await page.locator('#live-stop').isDisabled(), true);
    const pendingCount = (await actionCalls()).length;
    await page.evaluate(async () => {await moLiveAction('start'); await moLiveAction('stop');});
    assert.equal((await actionCalls()).length, pendingCount);
    const progress = await action.innerText(); await advance(10000);
    assert.equal(await text(), progress); assert.doesNotMatch(await text(), /라이브 감시 연결중/);
    pass('진행 안내만 한 줄 표시·자동삭제 없음·중복 시작/종료 차단 유지');
    const rawStart = String.raw`라이브 엔진 시작 완료 · program\event_host.py · PID 1234`;
    await page.evaluate(raw => resolveAction({ok: true, message: raw, warnings: []}), rawStart);
    await page.waitForFunction(() => !document.querySelector('#live-start').disabled);
    assert.equal(await text(), '라이브 감시 시작 완료'); assert.equal(await idle.isVisible(), false);
    await advance(2999); assert.equal(await text(), '라이브 감시 시작 완료');
    await advance(1); assert.equal(await text(), '라이브 감시 연결중');
    assert.equal(await action.textContent(), ''); assert.equal(await action.isVisible(), false);
    assert.deepEqual((await actionCalls()).at(-1), {name: 'mo/live/start', body: {}});
    await selectLog('ALL'); assert.ok((await log.innerText()).includes('[라이브 제어] ' + rawStart));
    await selectLog('ENGINE'); assert.ok((await log.innerText()).includes(rawStart));
    await selectLog('OZ'); assert.doesNotMatch(await log.innerText(), /라이브 제어|event_host/); await mainPage();
    pass('시작 성공 안내 2999ms 유지·3000ms 삭제, 원문은 ALL/ENGINE에만 기록');
    await page.evaluate(() => {stopReplies.push({defer: true});}); await page.locator('#live-stop').click();
    await page.waitForFunction(() => pendingReplies.length === 1); const stopping = await text();
    await advance(6000); assert.equal(await text(), stopping); assert.equal(await idle.isVisible(), false);
    await page.evaluate(() => {liveStatus = makeStatus('연결대기', false); resolveAction({ok: true, message: '합성 엔진 종료 완료', warnings: []});});
    await page.waitForFunction(() => !document.querySelector('#live-stop').disabled); await update();
    assert.equal(await text(), '라이브 감시 종료 완료'); await advance(3000);
    assert.equal(await text(), '라이브 감시 연결대기');
    assert.deepEqual((await actionCalls()).at(-1), {name: 'mo/live/stop', body: {}});
    pass('종료 진행·성공도 단일 안내, 완료 3초 뒤 최신 연결대기 복귀');
    await page.evaluate(() => {
      moLiveActionMessage('성공 임시 안내', false, true);
    }); await advance(1500);
    await page.evaluate(() => moLiveActionMessage('새 진행 안내')); await advance(10000);
    assert.equal(await text(), '새 진행 안내');
    await page.evaluate(() => moLiveActionMessage('두 번째 성공', false, true)); await advance(1500);
    await page.evaluate(() => moLiveActionMessage('새 경고 안내', true)); await advance(10000);
    assert.equal(await text(), '새 경고 안내'); assert.equal(await action.evaluate(node => node.classList.contains('moses-warning')), true);
    await page.evaluate(() => moLiveActionMessage('')); await update();
    pass('이전 성공 타이머가 새 진행·경고 안내를 삭제하지 않음');
    const warning = String.raw`기존 엔진 program\event_host.py 일부를 종료하지 못했습니다.`;
    await page.evaluate(warning => startReplies.push({ok: true, message: '합성 시작 완료 · ' + warning, warnings: [warning]}), warning);
    await page.locator('#live-start').click(); await page.waitForFunction(() => !document.querySelector('#live-start').disabled);
    assert.equal(await text(), '라이브 감시 시작 완료 · 주의 사항은 모듈 로그를 확인해 주세요.'); await advance(20000);
    assert.equal(await text(), '라이브 감시 시작 완료 · 주의 사항은 모듈 로그를 확인해 주세요.');
    assert.doesNotMatch(await text(), /program|event_host/); assert.equal(await idle.isVisible(), false);
    await selectLog('ENGINE'); const warningLog = await log.innerText();
    assert.equal(warningLog.split('\n').at(-1), '[라이브 제어] 합성 시작 완료 · ' + warning); await mainPage();
    pass('경고는 자동삭제하지 않고 원문 기록의 중복 경고 제거');
    const rawFailure = String.raw`합성 실행 실패: program\event_host.py 응답 없음`;
    await page.evaluate(raw => startReplies.push({error: raw}), rawFailure);
    await page.locator('#live-start').click(); await page.waitForFunction(() => !document.querySelector('#live-start').disabled);
    assert.equal(await text(), '라이브 감시 시작 실패 · 모듈 로그를 확인해 주세요.');
    await advance(10000); assert.equal(await text(), '라이브 감시 시작 실패 · 모듈 로그를 확인해 주세요.');
    assert.doesNotMatch(await text(), /program|event_host/); await selectLog('ENGINE'); assert.ok((await log.innerText()).includes(rawFailure));
    await mainPage(); fs.mkdirSync(proof, {recursive: true});
    await main.screenshot({path: path.join(proof, 'status_error.png')});
    pass('실행 실패는 짧은 오류로 유지, 내부 경로 원문은 모듈 로그 보존');
    const engines = [{copy_name: '합성 복사본', pid: 4321, created: 123.5, supports_shutdown: true}];
    await page.evaluate(engines => {
      startReplies.push({confirmation_required: true, engines}, {ok: true, message: '합성 재시작 완료', warnings: []}); confirmAnswers.push(true);
    }, engines);
    const beforeRestart = (await actionCalls()).length; await page.locator('#live-start').click();
    await page.waitForFunction(() => !document.querySelector('#live-start').disabled);
    assert.deepEqual((await actionCalls()).slice(beforeRestart), [{name: 'mo/live/start', body: {}},
      {name: 'mo/live/start', body: {restart: true, expected_engines: [{pid: 4321, created: 123.5}]}}]);
    assert.match(await page.evaluate(() => confirmations.at(-1)), /합성 복사본 · PID 4321/);
    assert.equal(await text(), '라이브 감시 시작 완료'); await advance(3000);
    pass('기존 엔진 재시작 확인과 pid/created 요청 의미 유지');
    await page.evaluate(engines => {startReplies.push({confirmation_required: true, engines}); confirmAnswers.push(false);}, engines);
    const beforeCancel = (await actionCalls()).length; await page.locator('#live-start').click();
    await page.waitForFunction(() => !document.querySelector('#live-start').disabled);
    assert.equal((await actionCalls()).length, beforeCancel + 1); await advance(10000);
    assert.equal(await text(), '재시작을 취소했습니다. 실행 중인 엔진을 유지합니다.');
    pass('재시작 취소는 엔진을 유지하고 안내 자동삭제 없음');
    const closingText = '기록 저장과 엔진 종료를 확인하고 있습니다.';
    const beginClosingRace = async kind => {
      await page.evaluate(() => {
        window.mosesWindowClosing('', true); moLiveActionMessage('');
        mo('#window-close-message').classList.add('hidden');
      });
      await page.evaluate(kind => (kind === 'stop' ? stopReplies : startReplies).push({defer: true}), kind);
      await page.locator(kind === 'stop' ? '#live-stop' : '#live-start').click();
      await page.waitForFunction(() => pendingReplies.length === 1);
      await page.evaluate(message => window.mosesWindowClosing(message), closingText);
      assert.equal(await text(), closingText);
    };
    const verifyClosingRace = async raw => {
      await page.waitForFunction(() => !moLiveActionPending);
      assert.equal(await text(), closingText); assert.equal(await action.isVisible(), true);
      assert.equal(await idle.isVisible(), false);
      assert.equal(await page.locator('#live-start').isDisabled(), true);
      assert.equal(await page.locator('#live-stop').isDisabled(), true);
      await advance(10000); assert.equal(await text(), closingText);
      await selectLog('ENGINE'); assert.ok((await log.innerText()).includes(raw)); await mainPage();
    };
    await beginClosingRace('start');
    const lateSuccess = String.raw`창 종료 중 늦은 시작 완료 program\event_host.py`;
    await page.evaluate(message => resolveAction({ok: true, message, warnings: []}), lateSuccess);
    await verifyClosingRace(lateSuccess);
    pass('창 종료 진행 중 늦은 성공 응답은 문구·버튼·자동삭제 상태를 덮지 않음');
    await beginClosingRace('stop');
    const lateFailure = String.raw`창 종료 중 늦은 종료 실패 program\event_host.py`;
    await page.evaluate(message => rejectAction(message), lateFailure); await verifyClosingRace(lateFailure);
    pass('창 종료 진행 중 늦은 오류 응답은 원문만 기록하고 진행 안내 유지');
    await beginClosingRace('start');
    const confirmationsBeforeClosing = await page.evaluate(() => confirmations.length);
    const closingCalls = (await actionCalls()).length;
    const lateConflict = String.raw`창 종료 중 재시작 확인 필요 program\event_host.py`;
    await page.evaluate(({message, engines}) => resolveAction({confirmation_required: true, engines, message}),
      {message: lateConflict, engines});
    await verifyClosingRace(lateConflict);
    assert.equal(await page.evaluate(() => confirmations.length), confirmationsBeforeClosing);
    assert.equal((await actionCalls()).length, closingCalls);
    await main.screenshot({path: path.join(proof, 'status_closing.png')});
    await page.evaluate(() => {
      window.mosesWindowClosing('', true); moLiveActionMessage('');
      mo('#window-close-message').classList.add('hidden');
    });
    pass('창 종료 중 늦은 충돌 응답은 재시작 대화/요청을 열지 않고 원문 보존');
    await page.evaluate(async () => {
      moLiveActionMessage(''); liveStatus = makeStatus(); await moLiveStatus();
      await moSaveSpecials({SYNTHETIC: {enabled: false, profile: 'synthetic', trigger: 'OZ'}});
    });
    assert.equal(await text(), '전략 설정 저장 완료'); await advance(3000);
    assert.equal(await text(), '라이브 감시 연결중'); assert.doesNotMatch(await text(), /program|event_host/);
    assert.deepEqual(await page.evaluate(() => calls.filter(row => row.name === 'mo/live/specials' && row.body).at(-1).body),
      {items: {SYNTHETIC: {enabled: false, profile: 'synthetic', trigger: 'OZ'}}});
    await selectLog('ALL'); assert.match(await log.innerText(), /전략 저장 · program\\event_host\.py/); await mainPage();
    pass('전략 저장 성공도 3초 뒤 상태 복귀, 기존 저장 payload 유지');
    await selectLog('ALL'); const frozen = await log.innerText(); await page.locator('#live-log-pause').check();
    await page.evaluate(() => {moRecordLiveAction('일시정지 중 합성 기록'); liveStatus.lines = ['[ENGINE] 갱신된 합성 기록'];}); await update();
    assert.equal(await log.innerText(), frozen); await page.locator('#live-log-pause').uncheck(); await update();
    assert.match(await log.innerText(), /일시정지 중 합성 기록/); assert.match(await log.innerText(), /갱신된 합성 기록/);
    await mainPage(); await page.locator('[data-module="OZ"]').click();
    assert.equal(await page.locator('#live-log-module').inputValue(), 'OZ');
    assert.ok((await page.evaluate(() => calls.at(-1).name)).startsWith('mo/live/status?module=OZ&detail=false'));
    pass('로그 일시정지·재개와 개별 카드 로그 이동 유지');
    await page.evaluate(() => {for (let n = 0; n < 35; n++) moRecordLiveAction('최대 기록 ' + n);});
    await selectLog('ENGINE'); const bounded = (await log.innerText()).split('\n').filter(line => line.startsWith('[라이브 제어]'));
    assert.equal(bounded.length, 30); assert.equal(bounded[0], '[라이브 제어] 최대 기록 5');
    assert.equal(bounded.at(-1), '[라이브 제어] 최대 기록 34');
    pass('UI 작업 원문 기록은 최신 30개로 제한');
    await mainPage(); await page.evaluate(() => moLiveActionMessage('')); await update();
    await page.locator('#live-main-page').screenshot({path: path.join(proof, 'live_connected.png')});
    await main.screenshot({path: path.join(proof, 'status_connected.png')});
    await page.evaluate(() => moLiveActionMessage('라이브 감시 시작 완료', false, true));
    await main.screenshot({path: path.join(proof, 'status_success.png')});
    await advance(3000); await page.setViewportSize({width: 640, height: 1000});
    await main.screenshot({path: path.join(proof, 'status_narrow.png')});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator('#live-modules .moses-module').count(), 8);
    pass('성공·일반 상태줄 스크린샷, 좁은 화면 넘침 없음');
    assert.deepEqual(errors, []);
    const report = {passed: checks.length, checks, errors};
    fs.writeFileSync(path.join(proof, 'ui_report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
