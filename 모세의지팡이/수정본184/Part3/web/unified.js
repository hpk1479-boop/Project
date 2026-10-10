'use strict';
/* MOSES navigation and transport only. Existing Part3 builder stays in app.js. */
const mo = s => document.querySelector(s);
const moState = { view: '', livePage: 'main', backtestPage: 'setup', liveModule: 'ALL', job: null, resultShown: false, settings: null, specials: null, navigationRevision: 0, viewRevision: 0, jobSelectionRevision: 0 };
const moBacktestPoll = { starting: false, status: null, result: null, timer: null, phase: '', done: false, planRevision: null, request: null,
    resultPending: false, resultFailures: 0 };
let moBacktestLogPending = null;
const moBacktestCancellations = new Map();
let moLiveActionPending = false;
let moLiveActionTimer = null;
const moLiveActionRecords = [];
let moWindowClosing = false;
let moSettingsLoadRevision = 0;
const moConfirmRequests = new Map();
const moStrategyPopup = { mode: null, draft: null, metadata: null, busy: false, saving: false, revision: 0 };
let moStrategyEditor = null, moStrategyEditorTimer = null;
let moBacktestSpecialOptions = null;
let moVirtualEntryDraft = null;
// The target's strategy conditions for this test: its recipe's on the base frame, unless edited.
let moVirtualStrategyDraft = null;
// The draft belongs to one target (a strategy, or WATCH by its OZ flag); a new target gets its defaults.
let moVirtualEntryKey = null, moVirtualSaved = null;
// Other base frames tested together with the chosen one: one replay, one result each (수정본161).
const moVirtualCompare = new Set();
const moLiveSpecialStates = {};
const moModuleIcons = Object.freeze({ STAFF: '▤', ENGINE: '⚙', OZ: '◇', SWEEP: '≋',
    FVG: '▥', INDICATOR: '▴', WATCH: '◉', KIM: '▣' });
function moElement(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text ?? '');
    if (className) el.className = className;
    return el;
}
function moMessage(text, error = false) {
    const selector = moState.view === 'settings' ? '#settings-message' :
        moState.livePage === 'settings' ? '#live-settings-message' : '#live-message';
    const target = mo(selector);
    if (target) { target.textContent = text; target.classList.toggle('moses-warning', error); }
    if (target && selector === '#settings-message') target.classList.toggle('hidden', !text);
}
function moUpdateBackButton() {
    const withinLive = moState.view === 'live' && moState.livePage !== 'main';
    const withinBacktest = moState.view === 'backtest' && moState.backtestPage !== 'setup';
    const available = withinLive || withinBacktest;
    mo('#mo-global-back').classList.toggle('hidden', !available);
    mo('#mo-global-back').disabled = !available;
}
function moLivePage(page) {
    moState.livePage = page;
    for (const name of ['main', 'settings', 'log'])
        mo('#live-' + name + '-page').classList.toggle('hidden', name !== page);
    moUpdateBackButton();
}
function moBacktestPage(page) {
    if (page === 'progress' && !moState.job) return;
    if (page === 'result' && !moState.resultShown) return;
    if (moState.backtestPage !== page) moState.navigationRevision += 1;
    moState.backtestPage = page;
    for (const name of ['setup', 'specials', 'progress', 'result'])
        mo('#mo-bt-' + name + '-page').classList.toggle('hidden', name !== page);
    moUpdateBackButton();
}
// Views whose data has been drawn once; until then a first opening shows one loading line (수정본163).
const moViewsDrawn = new Set();
function moViewDrawn(name) {
    moViewsDrawn.add(name);
    mo('#view-' + name)?.classList.remove('moses-view-loading');
}
async function moView(name) {
    if (!['live', 'backtest', 'strategy', 'settings'].includes(name)) return;
    if (moStrategyPopup.busy && !moStrategyPopup.saving && !mo('#strategy-settings-dialog').open)
        moCloseStrategyPopup();
    const revision = ++moState.viewRevision;
    moState.navigationRevision += 1;
    const current = () => revision === moState.viewRevision && moState.view === name;
    const leavingLive = moState.view === 'live' && name !== 'live';
    moState.view = name;
    if (name === 'settings') moCollapseSettingsDetails();
    if (name !== 'backtest') moStopBacktestPolling();
    document.body.dataset.mosesView = name;
    moUpdateBackButton();
    for (const id of ['live', 'backtest', 'strategy', 'settings'])
        mo('#view-' + id).classList.toggle('hidden', id !== name);
    if ((name === 'backtest' || name === 'settings') && !moViewsDrawn.has(name))
        mo('#view-' + name).classList.add('moses-view-loading');
    document.querySelectorAll('.moses-nav button[data-moses-view]').forEach(button => {
        const active = button.dataset.mosesView === name;
        button.classList.toggle('primary', active);
        button.setAttribute('aria-current', active ? 'page' : 'false');
    });
    if (name === 'live') moLivePage('main');
    if (name === 'backtest') moBacktestPage('setup');
    if (leavingLive && mo('#live-detail').checked) {
        mo('#live-detail').checked = false;
        api('mo/live/status?module=ENGINE&detail=false').catch(() => {});
    }
    try {
        if (name === 'live') await moLiveStatus();
        if (name === 'backtest') {
            const pending = [moBacktestOptions(current)];
            if (moState.job) pending.push(moBacktestStatus());
            if (window.MosesBacktestJobs) pending.push(window.MosesBacktestJobs.refresh());
            await Promise.all(pending);
        }
        if (name === 'settings') await moSettingsLoad();
    } catch (error) {
        if (!current()) return;
        if (name === 'backtest') {
            mo('#mo-bt-setup-error').textContent = error.message;
            mo('#mo-bt-setup-error').classList.remove('hidden');
        } else if (name === 'live') {
            moRecordLiveAction(error.message); moMessage('라이브 감시 상태 확인 대기');
        } else moMessage(error.message, true);
    } finally {
        // An error is shown in the view itself.
        if (name === 'backtest' || name === 'settings') mo('#view-' + name).classList.remove('moses-view-loading');
    }
}
async function moBack() {
    if (moState.view === 'live' && moState.livePage !== 'main') {
        moLivePage('main');
    } else if (moState.view === 'backtest' && moState.backtestPage === 'specials') {
        moBacktestPage('setup');
    } else if (moState.view === 'backtest' && moState.backtestPage === 'result') {
        moBacktestPage('progress');
    } else if (moState.view === 'backtest' && moState.backtestPage === 'progress') {
        moBacktestPage('setup');
    }
    moUpdateBackButton();
}
function moLiveCard(name, row) {
    const button = moElement('button', undefined, 'moses-module');
    button.type = 'button'; button.dataset.module = name;
    button.classList.toggle('active', moState.liveModule === name);
    button.append(moElement('span', moModuleIcons[name] || '◇', 'moses-module-icon'));
    const copy = moElement('span', undefined, 'moses-module-copy');
    copy.append(moElement('strong', row.name));
    const state = moElement('span', '● ' + row.state, 'state ' +
        (row.state === '연결중' ? 'connected' : row.state === '오류' ? 'error' : ''));
    copy.append(state);
    button.append(copy, moElement('span', '›', 'moses-module-chevron'));
    button.onclick = async () => {
        moState.liveModule = name; mo('#live-log-module').value = name;
        moLivePage('log');
        await moLiveStatus();
    };
    return button;
}
// One status request at a time (수정본170): a timer tick while one is on its way is skipped; a change the
// user made (fresh) is read once more after it.
let moLiveStatusPending = null, moLiveStatusAgain = false, moLiveCardsKey = '';
function moLiveStatus({fresh = true} = {}) {
    if (moState.view !== 'live') return Promise.resolve();
    if (moLiveStatusPending) {
        if (fresh) moLiveStatusAgain = true;
        return moLiveStatusPending;
    }
    moLiveStatusPending = (async () => {
        try {
            do {moLiveStatusAgain = false; await moReadLiveStatus();}
            while (moLiveStatusAgain && moState.view === 'live');
        } finally {moLiveStatusPending = null;}
    })();
    return moLiveStatusPending;
}
async function moReadLiveStatus() {
    const module = encodeURIComponent(moState.liveModule);
    const detail = mo('#live-detail').checked ? 'true' : 'false';
    // The log lines only while the log page is on screen.
    const showLog = moState.livePage === 'log';
    const data = await api('mo/live/status?module=' + module + '&detail=' + detail + (showLog ? '' : '&lines=false'));
    for (const [name, row] of Object.entries(data.modules)) {
        if (row.preset) moLiveSpecialStates[name] = row.state;
    }
    moStrategyStatusBadges();
    const grid = mo('#live-modules');
    const select = mo('#live-log-module');
    const moduleOptions = ['ALL', ...Object.keys(data.modules)];
    if (Array.from(select.options).map(option => option.value).join('\n') !== moduleOptions.join('\n')) {
        select.replaceChildren(new Option('전체 로그', 'ALL'));
        for (const [name, row] of Object.entries(data.modules))
            select.append(new Option(row.name, name));
        if (!moduleOptions.includes(moState.liveModule)) moState.liveModule = 'ALL';
        select.value = moState.liveModule;
    }
    // The cards are made again only when a module, its state or the chosen module changed.
    const cards = Object.entries(data.modules).filter(([, row]) => !row.preset);
    const cardsKey = JSON.stringify([moState.liveModule, cards.map(([name, row]) => [name, row.name, row.state])]);
    if (cardsKey !== moLiveCardsKey) {
        grid.replaceChildren();
        for (const [name, row] of cards) grid.append(moLiveCard(name, row));
        moLiveCardsKey = cardsKey;
    }
    if (showLog && !mo('#live-log-pause')?.checked) {
        const log = mo('#live-log');
        const lines = moState.liveModule === 'ALL' || moState.liveModule === 'ENGINE' ?
            [...data.lines, ...moLiveActionRecords] : data.lines;
        const text = lines.join('\n') || '기록된 핵심 로그가 없습니다.';
        if (log.textContent !== text) {
            log.textContent = text;
            if (mo('#live-log-scroll')?.checked) log.scrollTop = log.scrollHeight;
        }
    }
    mo('#live-log-error').textContent = data.error || '';
    mo('#live-log-error').classList.toggle('hidden', !data.error);
    const moduleError = Object.entries(data.modules).some(([name, row]) => name in moModuleIcons && row.state === '오류');
    const connected = data.pipe_connected && data.modules.STAFF?.state === '연결중' && data.modules.ENGINE?.state === '연결중';
    mo('#live-message').textContent = data.error ? '라이브 감시 상태 확인 대기' :
        moduleError ? '라이브 감시 오류 · 모듈 로그를 확인해 주세요.' :
        connected ? '라이브 감시 연결중' : '라이브 감시 연결대기';
}
function moLiveActionMessage(text, warning = false, temporary = false) {
    clearTimeout(moLiveActionTimer); moLiveActionTimer = null;
    const target = mo('#live-action-message');
    target.textContent = text;
    target.classList.toggle('hidden', !text);
    target.classList.toggle('moses-warning', warning);
    if (temporary && !warning && text) moLiveActionTimer = setTimeout(() => {
        target.textContent = ''; target.classList.add('hidden'); moLiveActionTimer = null;
    }, 3000);
}
function moRecordLiveAction(text) {
    if (!text) return;
    moLiveActionRecords.push('[라이브 제어] ' + text);
    if (moLiveActionRecords.length > 30) moLiveActionRecords.shift();
}
window.mosesWindowClosing = (message, failed = false) => {
    moWindowClosing = !failed;
    const notice = mo('#window-close-message');
    notice.textContent = message;
    notice.classList.remove('hidden');
    notice.classList.toggle('failed', failed);
    moLiveActionMessage(message || (failed ? '창 닫기를 완료하지 못했습니다. 다시 시도해 주세요.' :
        '기록 저장과 엔진 종료를 확인한 뒤 창을 닫습니다.'), failed);
    mo('#live-start').disabled = mo('#live-stop').disabled = moWindowClosing || moLiveActionPending;
};
function moLiveResultMessage(result, summary = '라이브 감시 작업 완료') {
    const warnings = result.warnings || [];
    const extra = warnings.filter(text => !String(result.message).includes(text));
    moRecordLiveAction([result.message, ...extra].filter(Boolean).join(' · '));
    if (moWindowClosing) return;
    moLiveActionMessage(summary + (warnings.length ? ' · 주의 사항은 모듈 로그를 확인해 주세요.' : ''),
        Boolean(warnings.length), !warnings.length);
}
async function moLiveAction(action) {
    if (moLiveActionPending || moWindowClosing) return;
    moLiveActionPending = true;
    const start = mo('#live-start'), stop = mo('#live-stop');
    start.disabled = stop.disabled = true;
    try {
        if (action === 'stop') {
            moLiveActionMessage('엔진 종료를 요청했습니다. 기록 저장과 종료를 확인하고 있습니다.');
            const result = await api('mo/live/stop', {});
            moLiveResultMessage(result, '라이브 감시 종료 완료');
            return;
        }
        let request = {};
        for (let attempt = 0; attempt < 3; attempt++) {
            moLiveActionMessage(request.restart ? '이전 엔진의 기록 저장·종료 후 새 엔진을 준비하고 있습니다.' :
                '실행 중인 엔진을 확인하고 새 엔진의 준비 상태를 기다리고 있습니다.');
            const result = await api('mo/live/start', request);
            if (moWindowClosing) { moLiveResultMessage(result); return; }
            if (!result.confirmation_required) {
                if (!result.ok) throw new Error(result.message || '라이브 실행을 완료하지 못했습니다.');
                moLiveResultMessage(result, '라이브 감시 시작 완료');
                return;
            }
            const engines = result.engines || [];
            const names = engines.map(row => `${row.copy_name} · PID ${row.pid}`).join('\n');
            const legacy = engines.some(row => !row.supports_shutdown) ?
                '\n이전 엔진 중 정상 종료를 지원하지 않는 버전은 제한 시간 후 강제 종료하며, 기록 저장을 확인할 수 없습니다.' : '';
            const changed = attempt ? '실행 중인 엔진 목록이 변경되었습니다. 다시 확인해 주세요.\n' : '';
            if (!window.confirm(changed + '이전에 작동중인 엔진을 종료하고 다시 시작하시겠습니까?\n\n' + names + legacy)) {
                moLiveActionMessage('재시작을 취소했습니다. 실행 중인 엔진을 유지합니다.');
                return;
            }
            request = {restart: true, expected_engines: engines.map(row => ({pid: row.pid, created: row.created}))};
        }
        throw new Error('엔진 목록이 계속 변경되고 있습니다. 상태를 확인한 뒤 다시 시작하세요.');
    } catch (error) {
        moRecordLiveAction(error.message);
        if (!moWindowClosing) moLiveActionMessage('라이브 감시 ' + (action === 'stop' ? '종료' : '시작') + ' 실패 · 모듈 로그를 확인해 주세요.', true);
    } finally {
        moLiveActionPending = false;
        start.disabled = stop.disabled = moWindowClosing;
    }
}
function moSpecialCard(name, data, choices) {
    const box = moElement('div', undefined, 'moses-special'); box.dataset.special = name;
    box.dataset.displayName = data.name || name;
    const head = moElement('div', undefined, 'moses-special-head');
    const label = moElement('label'); const enabled = document.createElement('input');
    enabled.type = 'checkbox'; enabled.className = 'mo-special-enabled'; enabled.checked = data.enabled;
    label.append(enabled, document.createTextNode(' ' + (data.name || name))); head.append(label);
    box.append(head);
    const options = moElement('div', undefined, 'moses-special-options');
    const triggerLabel = moElement('label', '최종 알림 조건');
    const trigger = document.createElement('select'); trigger.className = 'mo-special-trigger';
    trigger.append(new Option('전략 기본 설정 · ' + (data.default_trigger || '올존'), ''));
    choices.forEach(value => trigger.append(new Option(value, value)));
    if (data.default_trigger === null) {
        trigger.replaceChildren(new Option('조건 충족 알림 · 전략 기본 설정', ''));
        trigger.disabled = true;
    }
    trigger.value = data.trigger || '';
    if (data.load_error) {
        box.dataset.loadError = data.load_error;
        trigger.insertBefore(new Option('저장 설정 불러오기 제외 — 프로필 선택 필요', '__unresolved__'), trigger.firstChild);
        trigger.value = '__unresolved__';
        options.append(moElement('div', data.load_error, 'error'));
        trigger.addEventListener('change', () => {
            if (trigger.value !== '__unresolved__') {
                delete box.dataset.loadError;
                trigger.querySelector('option[value="__unresolved__"]')?.remove();
            }
        });
    }
    triggerLabel.append(trigger); options.append(triggerLabel);
    const defaultLabel = moElement('label', undefined, 'inline');
    const defaultTime = document.createElement('input'); defaultTime.type = 'checkbox';
    defaultTime.className = 'mo-special-time-default'; defaultTime.checked = data.time_filters == null;
    defaultLabel.append(defaultTime, document.createTextNode(' 기본 거래시간 사용'));
    options.append(defaultLabel);
    const sessions = moElement('div', undefined, 'moses-special-sessions');
    for (const key of ['MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK']) {
        const current = (data.time_filters || {})[key] || {};
        const row = moElement('div', undefined, 'moses-row'); row.dataset.session = key;
        const check = document.createElement('input'); check.type = 'checkbox';
        check.className = 'mo-time-enabled'; check.checked = !!current.enabled;
        const sessionTitle = {MAIN_ASIA:'아시아', MAIN_LONDON:'런던', MAIN_NEWYORK:'뉴욕'}[key];
        row.append(moElement('span', sessionTitle), check);
        check.setAttribute('aria-label', sessionTitle + ' 거래시간 사용');
        for (const field of ['start', 'end']) {
            const input = document.createElement('input'); input.type = 'text';
            input.className = 'mo-time-' + field; input.maxLength = 5;
            input.placeholder = field === 'start' ? '시작 (예: 0900)' : '종료 (예: 1800)';
            input.setAttribute('aria-label', sessionTitle + (field === 'start' ? ' 시작 시각' : ' 종료 시각'));
            input.value = current[field] || '';
            row.append(input);
            if (field === 'start') row.append(moElement('span', '–'));
        }
        sessions.append(row);
    }
    const toggle = () => sessions.classList.toggle('hidden', defaultTime.checked);
    defaultTime.onchange = toggle; toggle(); options.append(sessions); box.append(options);
    return box;
}
async function moLiveSpecials() {
    const data = await api('mo/live/specials'); moState.specials = data;
    const container = mo('#live-specials'); container.replaceChildren();
    for (const [name, row] of Object.entries(data.items))
        container.append(moSpecialCard(name, row, data.trigger_choices));
    mo('#live-specials-save').textContent = data.needs_recovery ? '선택한 전략으로 복구 저장' : '전략 설정 저장';
    const message = mo('#live-settings-message');
    message.textContent = data.settings_error ? data.settings_error +
        ' 안전을 위해 라이브 시작을 차단했습니다. 원래 파일을 복구하거나, 아래에서 사용할 전략을 직접 선택한 뒤 복구 저장하세요.' :
        '저장을 누르면 변경된 설정을 적용하기 위해 라이브 엔진이 자동으로 재시작됩니다.';
    message.classList.toggle('moses-warning', !!data.settings_error);
}
async function moSaveSpecials(items = moReadSpecialCards('#live-specials')) {
    const recovering = !!moState.specials?.needs_recovery;
    if (recovering && !window.confirm('손상된 전략 설정 파일을 지금 선택한 전략·조건·거래시간으로 덮어써 복구하시겠습니까?')) return false;
    if (moLiveActionPending || moWindowClosing) throw new Error('라이브 엔진 제어가 끝난 뒤 다시 저장해 주세요.');
    moLiveActionPending = true;
    mo('#live-start').disabled = mo('#live-stop').disabled = true;
    try {
        moStrategyPopupMessage('설정을 저장하고 라이브 엔진에 적용하고 있습니다.');
        const result = await api('mo/live/specials', recovering ? { items, recover: true } : { items });
        await moLiveSpecials();
        if (moState.livePage === 'settings') moMessage(result.message);
        moLiveResultMessage(result, '전략 설정 저장 완료');
        return true;
    } finally {
        moLiveActionPending = false;
        mo('#live-start').disabled = mo('#live-stop').disabled = moWindowClosing;
    }
}
function moReadSpecialCards(selector) {
    const items = {};
    document.querySelectorAll(selector + ' [data-special]').forEach(box => {
        if (box.dataset.loadError) throw new Error(box.dataset.special + ': 현재 프로필을 선택하세요.');
        let times = null;
        if (!box.querySelector('.mo-special-time-default').checked) {
            times = {};
            box.querySelectorAll('[data-session]').forEach(row => {
                const entry = { enabled: row.querySelector('.mo-time-enabled').checked };
                for (const name of ['start', 'end']) {
                    const text = row.querySelector('.mo-time-' + name).value.trim();
                    if (text) entry[name] = text;
                }
                times[row.dataset.session] = entry;
            });
        }
        items[box.dataset.special] = { enabled: box.querySelector('.mo-special-enabled').checked,
            trigger: box.querySelector('.mo-special-trigger').value || null, time_filters: times };
    });
    return items;
}
function moStrategyCopy(value) { return JSON.parse(JSON.stringify(value ?? {})); }
function moStrategyCommittedRows(selector, metadata) {
    const items = {};
    document.querySelectorAll(selector + ' [data-special]').forEach(box => {
        const name = box.dataset.special;
        const row = moStrategyCopy(metadata.items?.[name] || metadata.special_settings?.[name]);
        row.name = row.name || metadata.strategy_names?.[name] || name;
        row.enabled = box.querySelector('.mo-special-enabled').checked;
        const trigger = box.querySelector('.mo-special-trigger').value;
        row.trigger = trigger === '__unresolved__' ? row.trigger : trigger || null;
        if (box.dataset.loadError) row.load_error = box.dataset.loadError;
        else delete row.load_error;
        row.time_filters = null;
        if (!box.querySelector('.mo-special-time-default').checked) {
            row.time_filters = {};
            box.querySelectorAll('[data-session]').forEach(session => {
                const entry = { enabled: session.querySelector('.mo-time-enabled').checked };
                for (const field of ['start', 'end']) {
                    const text = session.querySelector('.mo-time-' + field).value.trim();
                    if (text) entry[field] = text;
                }
                row.time_filters[session.dataset.session] = entry;
            });
        }
        row.default_trigger = row.default_trigger || metadata.default_triggers?.[name];
        if (metadata.default_time_filters?.[name] !== undefined)
            row.default_time_filters = moStrategyCopy(metadata.default_time_filters[name]);
        items[name] = row;
    });
    return items;
}
function moStrategyPopupMessage(text) {
    const message = mo('#strategy-settings-message'); message.textContent = text || '';
    message.classList.toggle('hidden', !text);
}
function moStrategyTimeFormat(text) {
    const raw = String(text || '').replace(/:/g, '');
    return raw.length === 4 ? raw.slice(0, 2) + ':' + raw.slice(2) : raw;
}
function moStrategyTimeSplit(range) {
    const parts = String(range || '').replace(/:/g, '').split('-');
    return {start: parts[0] || '', end: parts[1] || ''};
}
// Sessions as they apply now: the saved choice when there is one, otherwise the strategy's own times.
function moStrategyTimeSessions(row, labels, config, saved = row.time_filters) {
    const defaults = row.default_time_filters;
    const ranges = defaults && typeof defaults === 'object' && !Array.isArray(defaults) ? defaults : {};
    const names = Array.isArray(defaults) ? defaults : typeof defaults === 'string' ? [defaults.trim()] : [];
    return Object.entries(labels).map(([key, label]) => {
        const own = saved?.[key] || {};
        const fallback = moStrategyTimeSplit(Object.hasOwn(ranges, key) ? ranges[key] : config[key]);
        return {key, label,
            enabled: saved != null ? !!own.enabled : names.includes(key) || Object.hasOwn(ranges, key),
            start: (saved != null && own.start) || fallback.start, end: (saved != null && own.end) || fallback.end};
    });
}
function moStrategyTimeSummary(row, labels, config) {
    const enabled = moStrategyTimeSessions(row, labels, config).filter(item => item.enabled);
    const summary = moElement('span', undefined, 'moses-strategy-time-summary');
    if (!enabled.length) summary.textContent = '24시간';
    enabled.forEach((item, index) => {
        if (index) summary.append(' · ');
        summary.append(moElement('span', item.label + (item.start && item.end ?
            ' ' + moStrategyTimeFormat(item.start) + '–' + moStrategyTimeFormat(item.end) : ''), 'moses-strategy-time-item'));
    });
    return summary;
}
function moStrategyStatusBadges() {
    document.querySelectorAll('[data-strategy-state]').forEach(badge => {
        const text = moLiveSpecialStates[badge.dataset.strategyState] || '연결 대기';
        badge.textContent = '● ' + text;
        badge.classList.toggle('connected', text === '연결중');
        badge.classList.toggle('error', text === '오류');
    });
}
function moStrategyPopupSingle() {
    return moStrategyPopup.mode === 'backtest';
}
function moStrategySelectAllState() {
    const rows = Object.values(moStrategyPopup.draft || {}), button = mo('#strategy-list-select-all');
    button.textContent = rows.length && rows.every(row => row.enabled) ? '전체해제' : '전체선택';
    button.disabled = !rows.length || moStrategyPopup.busy || moStrategyPopup.saving || moStrategyPopupSingle();
}
function moStrategySelectAll() {
    const popup = moStrategyPopup;
    if (!popup.mode || !popup.draft || popup.busy || popup.saving || moStrategyPopupSingle()) return;
    const rows = Object.values(popup.draft), enabled = !rows.every(row => row.enabled);
    for (const row of rows) row.enabled = enabled;
    document.querySelectorAll('#strategy-settings-cards .moses-strategy-toggle input[type=checkbox]')
        .forEach(check => { check.checked = enabled; });
    moStrategySelectAllState();
}
function moStrategyPopupRender() {
    const popup = moStrategyPopup, grid = mo('#strategy-settings-cards'); grid.replaceChildren();
    const labels = popup.metadata.session_labels || {MAIN_ASIA:'아시아', MAIN_LONDON:'런던', MAIN_NEWYORK:'뉴욕'};
    for (const [name, row] of Object.entries(popup.draft)) {
        const card = moElement('section', undefined, 'moses-strategy-popup-card'); card.dataset.strategy = name;
        const label = moElement('label', undefined, 'moses-strategy-toggle');
        const check = document.createElement('input'); check.type = 'checkbox'; check.checked = !!row.enabled;
        check.setAttribute('aria-label', (row.name || name) + ' 선택');
        check.onchange = () => {
            row.enabled = check.checked;
            // Every backtest selects one strategy: choosing one clears the others.
            if (check.checked && moStrategyPopupSingle()) {
                for (const [other, value] of Object.entries(popup.draft)) if (other !== name) value.enabled = false;
                grid.querySelectorAll('.moses-strategy-toggle input[type=checkbox]')
                    .forEach(box => { if (box !== check) box.checked = false; });
            }
            moStrategySelectAllState();
        };
        label.append(check, document.createTextNode(row.name || name));
        const head = moElement('div', undefined, 'moses-strategy-card-head'); head.append(label);
        if (popup.mode === 'live') {
            const status = moElement('span', undefined, 'moses-strategy-state');
            status.dataset.strategyState = name; head.append(status);
        }
        card.append(head);
        const profile = moElement('button', row.load_error ? '설정 불러오기 제외 · 프로필 재선택 필요' :
            row.trigger || row.default_trigger || (row.default_trigger === null ? '조건 충족 알림' : '최종 알림 조건 선택'), 'moses-strategy-profile');
        profile.disabled = row.default_trigger === null;
        profile.type = 'button'; profile.setAttribute('aria-label', name + ' 최종 알림 조건');
        profile.classList.toggle('moses-strategy-profile-needs-selection', !!row.load_error);
        profile.onclick = () => moStrategyProfileEditor(name); card.append(profile);
        const actions = moElement('div', undefined, 'moses-strategy-time-row');
        actions.append(moStrategyTimeSummary(row, labels, popup.metadata.session_times || {}));
        const times = moElement('button', '◷ 거래시간', 'moses-strategy-time'); times.type = 'button';
        times.setAttribute('aria-label', name + ' 거래시간'); times.onclick = () => moStrategyTimeEditor(name);
        actions.append(times); card.append(actions); grid.append(card);
    }
    moStrategyStatusBadges();
    moStrategySelectAllState();
}
async function moOpenStrategyPopup(mode) {
    const popup = moStrategyPopup;
    if (popup.mode || popup.busy) return;
    popup.mode = mode; popup.busy = true;
    moStrategySelectAllState();
    const revision = ++popup.revision;
    const viewRevision = moState.viewRevision;
    const live = mode === 'live';
    mo('#strategy-settings-title').textContent = live ? '라이브 전략 설정' : '백테스트 전략 설정';
    mo('#strategy-settings-subtitle').textContent = live ? '라이브 감시 조건과 거래시간' : '백테스트 조건과 거래시간';
    mo('#strategy-settings-note').textContent = live ? '저장을 누르면 변경된 설정을 적용하기 위해 라이브 엔진이 자동으로 재시작됩니다.' :
        '적용된 설정은 다음 백테스트부터 사용됩니다.';
    mo('#strategy-settings-apply').textContent = live ? '✓ 저장' : '✓ 적용';
    mo('#strategy-settings-apply').disabled = true;
    mo('#strategy-settings-cards').replaceChildren();
    moStrategyPopupMessage('전략 설정을 불러오는 중입니다.');
    try {
        if (live) await moLiveSpecials();
        else await moBacktestOptions();
        if (popup.revision !== revision || !popup.mode || moState.viewRevision !== viewRevision) return;
        popup.metadata = live ? moState.specials : moBacktestSpecialOptions;
        popup.draft = moStrategyCommittedRows(live ? '#live-specials' : '#mo-bt-specials', popup.metadata);
        moStrategyPopupRender();
        moStrategyPopupMessage(popup.metadata.settings_error ? popup.metadata.settings_error +
            ' 안전을 위해 라이브 시작을 차단했습니다. 사용할 전략과 프로필을 선택한 뒤 복구 저장하세요.' :
            (popup.metadata.catalog_errors || []).join('\n'));
        mo('#strategy-settings-apply').textContent = live && popup.metadata.needs_recovery ? '✓ 복구 저장' : live ? '✓ 저장' : '✓ 적용';
        mo('#strategy-settings-apply').disabled = false;
    } catch (error) {
        if (popup.revision === revision && moState.viewRevision === viewRevision) moStrategyPopupMessage(error.message);
    } finally {
        if (popup.revision === revision) {
            popup.busy = false;
            moStrategySelectAllState();
            if (moState.viewRevision !== viewRevision) moCloseStrategyPopup();
            else if (popup.mode) mo('#strategy-settings-dialog').showModal();
        }
    }
}
function moCloseStrategyEditor() {
    moStrategyEditor = null;
    if (mo('#strategy-editor-dialog').open) mo('#strategy-editor-dialog').close();
}
async function moChangeStrategyList(action) {
    const popup = moStrategyPopup;
    if (!popup.mode || popup.busy || popup.saving) return;
    const ids = Array.from(document.querySelectorAll('#strategy-settings-cards [data-strategy]'))
        .filter(card => card.querySelector('.moses-strategy-toggle input[type=checkbox]')?.checked)
        .map(card => card.dataset.strategy);
    if (action === 'delete' && !ids.length) { moStrategyPopupMessage('삭제할 전략의 기본 체크박스에 체크하세요.'); return; }
    const part = popup.mode === 'live' ? 'Part1' : 'Part2';
    const label = popup.mode === 'live' ? '파트1 라이브 감시' : '파트2 백테스트';
    const message = action === 'delete' ? '선택한 전략을 ' + label + ' 전략설정에서 삭제합니다. 다른 파트의 등록과 파트3 전략 원본은 유지됩니다. 계속하시겠습니까?' :
        label + ' 전략설정을 초기화합니다. 기본 스페셜만 복구하고 새로 만든 전략의 등록은 해제합니다. 다른 파트의 등록과 파트3 전략 원본은 유지됩니다. 계속하시겠습니까?';
    if (!window.confirm(message)) return;
    popup.busy = popup.saving = true;
    moStrategySelectAllState();
    for (const id of ['strategy-list-delete', 'strategy-list-reset', 'strategy-settings-apply']) mo('#' + id).disabled = true;
    try {
        const result = await api('mo/strategies', {part, action, ids});
        await moLiveSpecials(); await moBacktestOptions();
        popup.metadata = popup.mode === 'live' ? moState.specials : moBacktestSpecialOptions;
        popup.draft = moStrategyCommittedRows(popup.mode === 'live' ? '#live-specials' : '#mo-bt-specials', popup.metadata);
        moStrategyPopupRender(); moStrategyPopupMessage(result.message); moBacktestMode();
        window.dispatchEvent(new CustomEvent('moses-strategies-changed', {detail: {hostRefreshed: true}}));
    } catch (error) { moStrategyPopupMessage(error.message); }
    finally {
        popup.busy = popup.saving = false;
        moStrategySelectAllState();
        for (const id of ['strategy-list-delete', 'strategy-list-reset', 'strategy-settings-apply']) mo('#' + id).disabled = false;
    }
}
function moCloseStrategyPopup() {
    if (moStrategyPopup.saving) return;
    moCloseStrategyEditor(); moStrategyPopup.revision++;
    moStrategyPopup.mode = null; moStrategyPopup.draft = null; moStrategyPopup.metadata = null;
    moStrategyPopup.busy = false;
    moStrategySelectAllState();
    if (mo('#strategy-settings-dialog').open) mo('#strategy-settings-dialog').close();
}
function moOpenStrategyEditor(title, save, reset, options = {}) {
    mo('#strategy-editor-title').textContent = title;
    mo('#strategy-editor-error').textContent = ''; mo('#strategy-editor-error').classList.add('hidden');
    clearTimeout(moStrategyEditorTimer);
    mo('#strategy-editor-body').replaceChildren(); moStrategyEditor = {save, reset, ...options};
    if (!mo('#strategy-editor-dialog').open) mo('#strategy-editor-dialog').showModal();
}
function moStrategyProfileEditor(name) {
    const row = moStrategyPopup.draft[name], choices = moStrategyPopup.metadata.trigger_choices || [];
    let selected = row.load_error ? '' : row.trigger || row.default_trigger || '';
    const radios = [];
    const setSelection = value => { selected = value; radios.forEach(input => { input.checked = input.value === value; }); };
    moOpenStrategyEditor('최종 OZ 트리거 — 1개만 선택 가능', () => {
        if (!choices.includes(selected)) throw new Error('최종 알림 조건을 선택하세요.');
        row.trigger = selected === row.default_trigger ? null : selected;
        delete row.load_error; delete row.original;
    }, () => { setSelection(row.default_trigger || ''); });
    const grid = moElement('div', undefined, 'moses-strategy-profile-choices');
    for (const value of choices) {
        const label = moElement('label'), input = document.createElement('input');
        input.type = 'radio'; input.name = 'strategy-trigger'; input.value = value; input.checked = value === selected;
        input.onchange = () => { if (input.checked) setSelection(value); }; radios.push(input);
        label.append(input, document.createTextNode(value)); grid.append(label);
    }
    mo('#strategy-editor-body').append(grid);
    if (row.load_error) mo('#strategy-editor-body').append(moElement('p', row.load_error, 'muted'));
}
function moStrategyHHMM(value) {
    const text = String(value || '').trim().replace(/:/g, '');
    if (!text) return null;
    if (!/^\d{4}$/.test(text) || (text !== '2400' && (+text.slice(0,2) > 23 || +text.slice(2) > 59)))
        throw new Error('시각은 00:00~24:00 범위로 입력하세요.');
    return text;
}
function moStrategyTimeEditor(name) {
    const popup = moStrategyPopup, row = popup.draft[name];
    const labels = popup.metadata.session_labels || {MAIN_ASIA:'아시아', MAIN_LONDON:'런던', MAIN_NEWYORK:'뉴욕'};
    const config = popup.metadata.session_times || {}, fields = [];
    const defaultStates = moStrategyTimeSessions({default_time_filters: row.default_time_filters}, labels, config, null);
    const helperText = 'KST · 시작/종료 포함 · 자정 통과 지원', helper = moElement('small', helperText);
    const notice = text => {
        clearTimeout(moStrategyEditorTimer);
        helper.textContent = text;
        moStrategyEditorTimer = setTimeout(() => { helper.textContent = helperText; }, 2500);
    };
    moOpenStrategyEditor((row.name || name) + ' · 최종 알림 시간', () => {
        const result = {};
        let isDefault = true;
        for (const field of fields) {
            const target = defaultStates.find(item => item.key === field.key);
            const value = {enabled: field.enabled.checked}, compact = {};
            for (const part of ['start', 'end']) {
                compact[part] = moStrategyHHMM(field[part].value);
                if (value.enabled && !compact[part])
                    throw new Error(labels[field.key] + (part === 'start' ? ' 시작 시각' : ' 종료 시각') + '을 입력하세요.');
                // The times on screen become the strategy's own; they never follow 주요 거래시간 later.
                if (compact[part]) value[part] = compact[part];
            }
            result[field.key] = value;
            if (value.enabled !== target.enabled || (value.enabled &&
                ((compact.start || '') !== target.start || (compact.end || '') !== target.end))) isDefault = false;
        }
        // What is on screen equals the strategy's own times: keep following them instead of storing a copy.
        row.time_filters = isDefault ? null : result;
    }, () => {
        for (const field of fields) {
            const target = defaultStates.find(item => item.key === field.key);
            field.enabled.checked = target.enabled;
            field.start.value = moStrategyTimeFormat(target.start); field.end.value = moStrategyTimeFormat(target.end);
        }
        notice('기본값을 불러왔습니다 · 저장하면 적용됩니다');
    }, {keepOpen: true, saved: () => notice('설정되었습니다')});
    const grid = moElement('div', undefined, 'moses-strategy-time-editor');
    const shown = moStrategyTimeSessions(row, labels, config);
    for (const item of shown) {
        const field = {key: item.key};
        const line = moElement('label'), title = moElement('span');
        field.enabled = document.createElement('input'); field.enabled.type = 'checkbox'; field.enabled.checked = item.enabled;
        title.append(field.enabled, document.createTextNode(item.label)); line.append(title);
        for (const [index, part] of ['start', 'end'].entries()) {
            const input = document.createElement('input'); input.type = 'text'; input.maxLength = 5;
            input.value = moStrategyTimeFormat(item[part]); input.placeholder = part === 'start' ? '시작' : '종료';
            input.setAttribute('aria-label', item.label + (part === 'start' ? ' 시작 시각' : ' 종료 시각'));
            field[part] = input; line.append(input); if (index === 0) line.append(moElement('span', '–'));
        }
        fields.push(field); grid.append(line);
    }
    grid.append(helper);
    mo('#strategy-editor-body').append(grid);
}
async function moApplyStrategyPopup() {
    const popup = moStrategyPopup;
    if (popup.busy || !popup.draft) return;
    const items = {};
    try {
        for (const [name, row] of Object.entries(popup.draft)) {
            if (row.load_error) throw new Error(name + ': 현재 프로필을 선택하세요.');
            items[name] = {enabled: !!row.enabled, trigger: row.trigger || null, time_filters: moStrategyCopy(row.time_filters)};
            if (row.time_filters == null) items[name].time_filters = null;
        }
        popup.busy = popup.saving = true; mo('#strategy-settings-apply').disabled = true;
        moStrategySelectAllState();
        mo('#strategy-settings-close').disabled = mo('#strategy-settings-cancel').disabled = true;
        if (popup.mode === 'live') { if (!await moSaveSpecials(items)) return; }
        else {
            const checks = mo('#mo-bt-specials'); checks.replaceChildren();
            for (const [name, row] of Object.entries(items)) checks.append(moSpecialCard(name,
                {...popup.draft[name], ...row}, popup.metadata.trigger_choices || []));
            moBacktestMode();
        }
        popup.busy = popup.saving = false; moCloseStrategyPopup();
    } catch (error) { moStrategyPopupMessage(error.message); }
    finally {
        popup.busy = popup.saving = false; mo('#strategy-settings-apply').disabled = false;
        moStrategySelectAllState();
        mo('#strategy-settings-close').disabled = mo('#strategy-settings-cancel').disabled = false;
    }
}
async function moBacktestOptions(current = () => true) {
    const value = await api('mo/backtest/options');
    if (!current()) return;
    moBacktestSpecialOptions = value;
    // The last executed policy returns only for its own target, once.
    if (moVirtualEntryKey === null && moVirtualSaved === null)
        moVirtualSaved = {policy: value.virtual_entry, target: value.virtual_entry_target,
            strategy: value.virtual_entry_strategy};
    for (const key of ['symbol', 'start', 'end', 'mode']) {
        const input = mo('#mo-bt-' + key);
        if (!input.dataset.initialized) { input.value = value[key]; input.dataset.initialized = '1'; }
    }
    const symbols = mo('#mo-bt-symbols'); symbols.replaceChildren();
    value.symbols.forEach(symbol => symbols.append(new Option(symbol, symbol)));
    const symbolChoice = mo('#mo-bt-symbol-select');
    symbolChoice.replaceChildren();
    value.symbols.forEach(symbol => symbolChoice.append(new Option(symbol, symbol)));
    symbolChoice.value = mo('#mo-bt-symbol').value; symbolChoice.disabled = !value.symbols.length;
    symbolChoice.title = value.symbols.length ? '창고에 저장된 종목 선택' : '창고에 저장된 종목이 없습니다. 직접 입력할 수 있습니다.';
    const checks = mo('#mo-bt-specials');
    const renderedNames = Array.from(checks.children).map(box => [box.dataset.special, box.dataset.displayName]);
    const expectedNames = value.specials.map(name => [name, value.strategy_names?.[name] || name]);
    if (JSON.stringify(renderedNames) !== JSON.stringify(expectedNames)) {
      const currentRows = moStrategyCommittedRows('#mo-bt-specials', value);
      checks.replaceChildren();
      for (const name of value.specials) {
        const saved = currentRows[name] || value.special_settings?.[name] || {};
        checks.append(moSpecialCard(name, { name: value.strategy_names?.[name] || name, enabled: !!saved.enabled,
            trigger: saved.trigger, time_filters: saved.time_filters, load_error: saved.load_error,
            default_trigger: value.default_triggers?.[name] }, value.trigger_choices || []));
      }
    }
    if (!mo('#mo-bt-watch').dataset.initialized) {
        mo('#mo-bt-watch').value = value.watch_text || '';
        mo('#mo-bt-watch').dataset.initialized = '1';
    }
    const chat = mo('#mo-bt-watch-chat');
    if (chat && !chat.dataset.initialized) {
        chat.value = value.watch_chat_id || 'BACKTEST'; chat.dataset.initialized = '1';
    }
    if (!mo('#mo-bt-spread').dataset.initialized) {
        mo('#mo-bt-spread').value = value.spread_points?.[mo('#mo-bt-symbol').value] ?? 0;
        mo('#mo-bt-spread').dataset.initialized = '1';
    }
    mo('#mo-bt-data-state').textContent = value.warehouse_set ? '데이터 창고 연결됨' : '데이터 창고 미설정';
    moBacktestMode();
    moViewDrawn('backtest');
}
function moFrameText(tf) {
    const found = /^(\d+)([mhdw])$/.exec(tf || '');
    return found ? found[1] + {m: '분', h: '시간', d: '일', w: '주'}[found[2]] : tf;
}
// The selected virtual-entry target: exactly one strategy. A WATCH command is alert-only.
function moVirtualTarget() {
    if (mo('#mo-bt-target').value === 'WATCH') return null;
    const names = Array.from(mo('#mo-bt-specials').querySelectorAll('[data-special]'))
        .filter(box => box.querySelector('.mo-special-enabled').checked).map(box => box.dataset.special);
    const profile = names.length === 1 ? moBacktestSpecialOptions?.virtual_profiles?.[names[0]] : null;
    return profile && {key: names[0], name: names[0], profile};
}
function moVirtualSync() {
    const target = moVirtualTarget();
    if (target && target.key !== moVirtualEntryKey) {
        const saved = moVirtualSaved;
        moVirtualSaved = {};
        // The strategy's own recipe values, unless its last run used other ones.
        const own = saved?.policy && saved.target === target.name;
        moVirtualEntryDraft = moStrategyCopy(own ? saved.policy : target.profile.default);
        moVirtualStrategyDraft = own && saved.strategy ? moStrategyCopy(saved.strategy)
            : moVirtualRecipeOnBase(target.profile, moVirtualEntryDraft.tf).strategy;
        moVirtualEntryKey = target.key;
        moVirtualCompare.clear();
        // The strategy's own limits are shown, not folded away.
        mo('#mo-bt-virtual-filter-details').open = moVirtualEntryDraft.filters.length > 0;
    }
    moVirtualEntryRender(target);
}
// A frame keeps its level (기준·중위·상위·최상위) when the base frame changes; others stay as they are.
function moVirtualRebase(tf, from, to) {
    const ladder = moBacktestSpecialOptions?.virtual_ladder || {};
    const level = (ladder[from] || []).indexOf(tf);
    return level < 0 || !ladder[to]?.[level] ? tf : ladder[to][level];
}
// Every frame written in a recipe part (tf/tfs/price_tf), moved to the same level under another base frame.
function moVirtualMoveFrames(value, from, to) {
    if (Array.isArray(value)) return value.map(item => moVirtualMoveFrames(item, from, to));
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
        ['tf', 'price_tf'].includes(key) && typeof item === 'string' ? moVirtualRebase(item, from, to)
            : key === 'tfs' && Array.isArray(item) ? item.map(tf => moVirtualRebase(tf, from, to))
            : moVirtualMoveFrames(item, from, to)]));
}
// The recipe as if it had been written on base frame tf: its entry policy and its strategy conditions.
// Choosing a base frame always refills every value from here; values edited before are dropped.
function moVirtualRecipeOnBase(profile, tf) {
    const base = tf === 'SIGNAL' ? profile.base : tf, moves = !!profile.base && base !== profile.base;
    const policy = moStrategyCopy(profile.default);
    if (moves) {
        for (const row of [policy.atr, policy.stop, ...policy.conditions, ...policy.filters])
            if (row.tf) row.tf = moVirtualRebase(row.tf, profile.base, base);
        policy.tf = tf;
    }
    const strategy = profile.strategy ? (moves ? moVirtualMoveFrames(profile.strategy, profile.base, base)
        : moStrategyCopy(profile.strategy)) : null;
    return {policy, strategy};
}
// The base frame (기준 프레임) of the test: the strategy's own frames, or another frame the whole test moves to.
function moVirtualBases(profile) {
    const frames = profile.timeframes || [];
    return [['SIGNAL', frames.length ? frames.map(moFrameText).join('·') : '전략 시간봉'],
        ...(profile.bases || []).filter(tf => tf !== profile.base).map(tf => [tf, moFrameText(tf)])];
}
// The real frame of a base frame choice: SIGNAL is the recipe's own frame.
function moVirtualRealBase(profile, tf) {
    return tf === 'SIGNAL' ? profile.base : tf;
}
// The other base frames to test together with the chosen one; none when the strategy's frames cannot move.
function moVirtualCompareField(profile, bases, tf) {
    if (!profile.base || bases.length < 2) { moVirtualCompare.clear(); return null; }
    const current = moVirtualRealBase(profile, tf);
    moVirtualCompare.delete(current);
    const box = moElement('div', undefined, 'moses-virtual-compare');
    box.append(moElement('span', '함께 비교', 'muted'));
    for (const [value] of bases) {
        const frame = moVirtualRealBase(profile, value);
        if (frame === current) continue;
        const item = moElement('label', undefined, 'moses-virtual-compare-item');
        const check = moElement('input'); check.type = 'checkbox';
        check.checked = moVirtualCompare.has(frame); check.dataset.compareBase = frame;
        check.onchange = () => {
            if (check.checked) moVirtualCompare.add(frame); else moVirtualCompare.delete(frame);
            moVirtualEntryRender();
        };
        item.append(check, document.createTextNode(moFrameText(frame)));
        box.append(item);
    }
    if (moVirtualCompare.size)
        box.append(moElement('p', '기준 프레임마다 그 기준의 레시피 값으로 시험해 결과를 따로 저장합니다.', 'muted'));
    return box;
}
// The base frames of a request: the chosen one first, then the compared ones; none when not comparing.
function moVirtualBaseList(profile, tf) {
    return moVirtualCompare.size ? [moVirtualRealBase(profile, tf), ...moVirtualCompare] : [];
}
// The strategy's own and environment frames on the chosen base frame.
function moVirtualOnBase(profile, tf) {
    if (tf === 'SIGNAL' || !profile.base || tf === profile.base) return profile;
    return {...profile, timeframes: [tf],
        env_timeframes: (profile.env_timeframes || []).map(frame => moVirtualRebase(frame, profile.base, tf))};
}
// A field's frame as shown on the base frame: SIGNAL is each alert's own frame and ENV the frame its
// first condition matched on, both as the target's real frames (e.g. 1분·2분·3분); a recipe frame as it is.
function moVirtualFrameText(shown, tf) {
    const named = frames => frames.map(moFrameText).join('·');
    if (tf === 'SIGNAL') return (shown.timeframes || []).length ? named(shown.timeframes) : '전략 시간봉';
    if (tf === 'ENV') return '환경 프레임 (' + named(shown.env_timeframes || []) + ')';
    return moFrameText(tf);
}
// Frames come only from the base frame: a frame field shows its frame and is never chosen.
function moReadonlyField(label, text, key) {
    const box = moElement('label', label), input = moElement('input');
    input.type = 'text'; input.value = text; input.disabled = true;
    if (key) input.dataset.virtualField = key;
    box.append(input);
    return box;
}
function moVirtualField(label, row, key, choices, changed, optional = false) {
    const box = moElement('label', label);
    const input = moElement(choices ? 'select' : 'input');
    input.dataset.virtualField = key;
    if (choices) choices.forEach(([value, text]) => input.append(new Option(text, value)));
    else {
        const whole = ['period', 'bars', 'fast', 'slow', 'lookback', 'slow_period', 'fast_period'].includes(key);
        input.type = 'number'; input.min = whole ? '1' : '0';
        input.step = whole ? '1' : 'any';
        if (whole) input.max = '500';
        if (optional) input.placeholder = '제한 없음';
    }
    input.value = row[key] ?? '';
    input.addEventListener(choices ? 'change' : 'input', () => {
        row[key] = choices ? input.value : input.value === '' ? null : Number(input.value);
        changed?.();
    });
    box.append(input);
    return box;
}
// 수정본183: "돌파" only for crossing; "위·아래" for a state, named by the price it reads (종가·시가).
const MO_VIRTUAL_CONDITIONS = {CANDLE_CLOSE: '양봉·음봉', CANDLE_SHAPE: '망치형·역망치형',
    MA_POSITION: '이평 종가 위·아래', MA_OPEN_POSITION: '이평 시가 위·아래', MA_CROSS: '이평 종가 돌파',
    MA_TOUCH_CANDLE: '이평 터치', MA_TOUCH: '이평 터치 이후', ENGULFING: '인걸핑', NECKLINE_BREAK: '넥라인 종가 돌파'};
const MO_VIRTUAL_SHAPES = [['HAMMER', '망치형'], ['INVERTED_HAMMER', '역망치형']];
function moVirtualItem(title, remove) {
    const item = moElement('div', undefined, 'moses-virtual-item');
    const heading = moElement('div', undefined, 'moses-virtual-heading');
    const button = moElement('button', '삭제'); button.type = 'button';
    button.setAttribute('aria-label', title + ' 삭제'); button.onclick = remove;
    heading.append(moElement('strong', title), button); item.append(heading);
    return item;
}
const MO_VIRTUAL_FAMILIES = ['SMA', 'EMA', 'WMA', 'HMA'].map(x => [x, x]);
const MO_VIRTUAL_MODES = [['IMMEDIATE', '즉시 진입'], ['CONFIRM', '조건 진입']];
const MO_VIRTUAL_ATR_FILTERS = ['CANDLE_ATR', 'MA_DISTANCE_ATR'];
const MO_VIRTUAL_FILTERS = {CANDLE_ATR: '확인봉 크기', MA_DISTANCE_ATR: '시가와 이평 거리',
    ENVIRONMENT: '환경조건', MAX_BARS: '알림 후 N봉'};
const MO_VIRTUAL_ENVIRONMENTS = {TREND: '추세', MA_STATE: '이평 정배열', MA_SLOPE_STATE: '이평 기울기',
    MA_PRICE_STATE: '가격과 이평'};
function moVirtualMAFields(row, frameField) {
    const fields = moElement('div', undefined, 'moses-virtual-grid');
    fields.append(moVirtualField('이평', row, 'family', MO_VIRTUAL_FAMILIES),
        moVirtualField('기간', row, 'period'), frameField('시간봉', row, 'tf'));
    return fields;
}
// A market state that must hold while the entry waits; the side follows each alert.
function moVirtualEnvironmentFields(row, frameField) {
    const fields = moElement('div', undefined, 'moses-virtual-grid');
    fields.append(moVirtualField('조건', row, 'condition', Object.entries(MO_VIRTUAL_ENVIRONMENTS), () => {
        for (const key of ['family', 'fast', 'slow', 'period', 'lookback']) delete row[key];
        if (row.condition === 'MA_STATE') Object.assign(row, {family: 'HMA', fast: 6, slow: 17});
        else if (row.condition !== 'TREND') Object.assign(row, {family: 'HMA', period: 50});
        if (row.condition === 'MA_SLOPE_STATE') row.lookback = 2;
        moVirtualEntryRender();
    }));
    if (row.condition === 'MA_STATE')
        fields.append(moVirtualField('이평', row, 'family', MO_VIRTUAL_FAMILIES),
            moVirtualField('빠른 기간', row, 'fast'), moVirtualField('느린 기간', row, 'slow'));
    else if (row.condition !== 'TREND')
        fields.append(moVirtualField('이평', row, 'family', MO_VIRTUAL_FAMILIES), moVirtualField('기간', row, 'period'));
    if (row.condition === 'MA_SLOPE_STATE') fields.append(moVirtualField('비교 봉 수', row, 'lookback'));
    fields.append(frameField('시간봉', row, 'tf'),
        moVirtualField('봉 기준', row, 'bar_state', [['CLOSED', '확정봉'], ['FORMING', '진행봉']]));
    return fields;
}
// The recipe's strategy conditions (folded by default). Only their values are shown and edited;
// the conditions themselves, their order and their frames (the base frame's) stay the recipe's.
const MO_STRATEGY_KINDS = {TREND: '추세', TREND_METRIC: '추세 지표', CANDLE_STATE: '양봉·음봉', CANDLE_SHAPE: '망치형·역망치형',
    MA_STATE: '이평 정배열', MA_PRICE_STATE: '가격과 이평', MA_SLOPE_STATE: '이평 기울기', MA_CROSS: '이평 크로스',
    MA_PRICE_CROSS: '이평 종가 돌파', MA_PRICE_TOUCH: '이평 터치', FVG_STATE: 'FVG 영역', FVG_NEW: 'FVG 생성',
    FVG_TOUCH: 'FVG 터치', WONBI_TOUCH: '원비 터치', EXTERNAL_LIQUIDITY_TOUCH: '외부유동성 터치', SESSION_START: '세션 시작',
    BAR_CLOSE: '봉 마감', OZ_ALERT: '올존', REGIME_BAND: '레짐 밴드', PERCENTILE_OUT: '퍼센타일 이탈',
    PERCENTILE_OUT_IN: '퍼센타일 이탈 후 복귀', PRICE_LEVEL: '가격 레벨', LIQUIDITY_LEVEL: '유동성 레벨'};
const MO_STRATEGY_SIDES = {CANDLE_STATE: ['봉', [['BULL', '양봉'], ['BEAR', '음봉']]],
    MA_STATE: ['배열', [['ABOVE', '정배열'], ['BELOW', '역배열']]], MA_PRICE_STATE: ['가격 위치', [['ABOVE', '이평 위'], ['BELOW', '이평 아래']]],
    MA_SLOPE_STATE: ['기울기', [['UP', '상승'], ['DOWN', '하락']]], WONBI_TOUCH: ['밴드', [['LOWER', '하단'], ['UPPER', '상단']]],
    FVG_STATE: ['방향', [['BULL', '상승'], ['BEAR', '하락']]], FVG_NEW: ['방향', [['BULL', '상승'], ['BEAR', '하락']]],
    FVG_TOUCH: ['방향', [['BULL', '상승'], ['BEAR', '하락']]], EXTERNAL_LIQUIDITY_TOUCH: ['유동성', [['LOW', '저점'], ['HIGH', '고점']]],
    PERCENTILE_OUT: ['방향', [['LOWER', '하단'], ['UPPER', '상단']]], PERCENTILE_OUT_IN: ['방향', [['LOWER', '하단'], ['UPPER', '상단']]]};
const MO_STRATEGY_LISTS = [['steps', '조건'], ['cancel_conditions', '취소 조건'], ['final_conditions', '최종 확인 조건'],
    ['after_conditions', '이후 조건']];
function moStrategyFrames(tfs) {
    return tfs.map(tf => ({SOURCE: '선행 조건 시간봉', FINAL: '최종 감시 시간봉'})[tf] || moFrameText(tf)).join('·');
}
// An average written as a name (HMA17): its family and its period.
function moStrategyAverage(step, key, label) {
    const found = /^(SMA|EMA|WMA|HMA)(\d+)$/.exec(step[key]);
    if (!found) return [moReadonlyField(label, step[key])];
    const holder = {family: found[1], period: Number(found[2])};
    const update = () => { step[key] = holder.family + (holder.period ?? ''); };
    return [moVirtualField(label, holder, 'family', MO_VIRTUAL_FAMILIES, update),
        moVirtualField(label + ' 기간', holder, 'period', null, update)];
}
// A window after an event (수정본173): '30분', '6시간' or '1시간봉 6개'.
function moStrategyWindow(window) {
    if (Number.isInteger(window.bars)) return moFrameText(window.tf) + '봉 ' + window.bars + '개';
    const seconds = window.seconds;
    return seconds % 3600 === 0 ? seconds / 3600 + '시간' : seconds % 60 === 0 ? seconds / 60 + '분' : seconds + '초';
}
function moStrategyStepFields(step) {
    const fields = moElement('div', undefined, 'moses-virtual-grid');
    if (Array.isArray(step.tfs)) fields.append(moReadonlyField('시간봉', moStrategyFrames(step.tfs), 'tf'));
    if (step.recent && typeof step.recent === 'object')
        fields.append(moReadonlyField(step.negated ? '최근 기간 (없을 때)' : '최근 기간', moStrategyWindow(step.recent), 'recent'));
    if (typeof step.price_tf === 'string') fields.append(moReadonlyField('가격 시간봉', moStrategyFrames([step.price_tf]), 'price_tf'));
    for (const [key, label] of [['ma_left', '이평 1'], ['ma_right', '이평 2']])
        if (typeof step[key] === 'string') fields.append(...moStrategyAverage(step, key, label));
    if (typeof step.ma_family === 'string') fields.append(moVirtualField('이평', step, 'ma_family', MO_VIRTUAL_FAMILIES));
    if (typeof step.fast_period === 'number') fields.append(moVirtualField('빠른 기간', step, 'fast_period'));
    if (typeof step.slow_period === 'number')
        fields.append(moVirtualField(typeof step.fast_period === 'number' ? '느린 기간' : '기간', step, 'slow_period'));
    if (typeof step.lookback === 'number') fields.append(moVirtualField('비교 봉 수', step, 'lookback'));
    if (typeof step.side === 'string') {
        const [label, sides] = MO_STRATEGY_SIDES[step.kind] || ['방향', []];
        fields.append(sides.some(([value]) => value === step.side) ? moVirtualField(label, step, 'side', sides)
            : moReadonlyField(label, step.side));
    }
    if (typeof step.shape === 'string') fields.append(moVirtualField('모양', step, 'shape', MO_VIRTUAL_SHAPES));
    if (['CLOSED', 'FORMING'].includes(step.bar_state))
        fields.append(moVirtualField('봉 기준', step, 'bar_state', [['CLOSED', '확정봉'], ['FORMING', '진행봉']]));
    return fields;
}
function moStrategyTitle(step) {
    if (step.kind === 'MA_CROSS') return {LONG: '골든크로스', SHORT: '데드크로스'}[step.direction] || '이평 크로스';
    return MO_STRATEGY_KINDS[step.kind] || step.kind;
}
function moVirtualStrategyRender() {
    const box = mo('#mo-bt-virtual-strategy'); box.replaceChildren();
    const intent = moVirtualStrategyDraft;
    if (!intent) { box.append(moElement('p', '레시피의 전략 조건이 없습니다.', 'muted')); return; }
    const card = (title, path, fields) => {
        const item = moElement('div', undefined, 'moses-virtual-item');
        const heading = moElement('div', undefined, 'moses-virtual-heading');
        heading.append(moElement('strong', title)); item.append(heading);
        item.dataset.strategyStep = path;
        if (fields.childElementCount) item.append(fields);
        return item;
    };
    const unit = (part, prefix, path) => {
        for (const [key, title] of MO_STRATEGY_LISTS) {
            if (!(part[key] || []).length) continue;
            box.append(moElement('p', prefix + title, 'muted'));
            part[key].forEach((step, index) =>
                box.append(card(moStrategyTitle(step), path + key + '.' + index, moStrategyStepFields(step))));
        }
        if (part.final?.kind === 'OZ' && Array.isArray(part.final.tfs)) {
            const fields = moElement('div', undefined, 'moses-virtual-grid');
            fields.append(moReadonlyField('시간봉', moStrategyFrames(part.final.tfs), 'tf'));
            box.append(card(prefix + '올존 최종 판정', path + 'final', fields));
        }
    };
    unit(intent, '', '');
    (intent.branches || []).forEach((branch, index) => unit(branch,
        '분기 ' + (index + 1) + ({LONG: ' · 매수', SHORT: ' · 매도'}[branch.direction] || '') + ' · ', 'branches.' + index + '.'));
}
function moVirtualEntryRender(target = moVirtualTarget()) {
    const draft = moVirtualEntryDraft, ready = !!(target && draft);
    mo('#mo-bt-virtual-body').classList.toggle('hidden', !ready);
    mo('#mo-bt-virtual-empty').classList.toggle('hidden', ready);
    mo('#mo-bt-virtual-empty').textContent = '전략 1개를 선택하세요.';
    if (!ready) return;
    const profile = target.profile, oz = profile.oz;
    // The base frame is the one frame a test chooses; every other frame follows it.
    const bases = moVirtualBases(profile);
    if (!bases.some(([value]) => value === draft.tf)) draft.tf = 'SIGNAL';
    const baseField = moVirtualField('기준 프레임', draft, 'tf', bases, () => {
        // Everything is the recipe again, as if written on the new base frame; earlier edits are dropped.
        const recipe = moVirtualRecipeOnBase(profile, draft.tf);
        moVirtualEntryDraft = recipe.policy; moVirtualStrategyDraft = recipe.strategy;
        mo('#mo-bt-virtual-filter-details').open = moVirtualEntryDraft.filters.length > 0;
        moVirtualEntryRender();
    });
    baseField.querySelector('select').disabled = bases.length < 2;
    const compareField = moVirtualCompareField(profile, bases, draft.tf);
    const shown = moVirtualOnBase(profile, draft.tf);
    const frameField = (label, row, key) => moReadonlyField(label, moVirtualFrameText(shown, row[key]), key);
    if (!oz) draft.conditions = draft.conditions.filter(row => row.kind !== 'NECKLINE_BREAK');
    const main = mo('#mo-bt-virtual-main'); main.replaceChildren();
    main.append(moVirtualField('진입 방식', draft, 'mode', MO_VIRTUAL_MODES, () => {
        // Conditional entry starts from the strategy's own recipe conditions on this base frame, if it has them.
        const own = moVirtualRecipeOnBase(profile, draft.tf).policy;
        if (moVirtualOpening(draft)) moVirtualOpenArrangement(draft, false, own);
        draft.conditions = draft.mode === 'CONFIRM' && own.mode === 'CONFIRM' ? own.conditions : [];
        if (draft.mode === 'IMMEDIATE') draft.filters = draft.filters.filter(row => row.kind !== 'MAX_BARS');
        moVirtualEntryRender();
    }), baseField);
    if (compareField) main.append(compareField);
    const opening = draft.mode === 'CONFIRM' && moVirtualOpening(draft);
    moVirtualStrategyRender();
    mo('#mo-bt-virtual-confirm').classList.toggle('hidden', draft.mode === 'IMMEDIATE');
    const kind = mo('#mo-bt-virtual-condition-kind'), chosen = kind.value;
    // One 이평 시가 위·아래 at most; the neckline only for an OZ strategy.
    kind.replaceChildren(...Object.entries(MO_VIRTUAL_CONDITIONS)
        .filter(([value]) => (oz || value !== 'NECKLINE_BREAK') && !(opening && value === 'MA_OPEN_POSITION'))
        .map(([value, text]) => new Option(text, value)));
    if (Array.from(kind.options).some(option => option.value === chosen)) kind.value = chosen;
    const conditions = mo('#mo-bt-virtual-conditions'); conditions.replaceChildren();
    draft.conditions.forEach((row, index) => {
        const item = moVirtualItem(MO_VIRTUAL_CONDITIONS[row.kind], () => {
            draft.conditions.splice(index, 1);
            if (row.kind === 'MA_OPEN_POSITION')
                moVirtualOpenArrangement(draft, false, moVirtualRecipeOnBase(profile, draft.tf).policy);
            moVirtualEntryRender();
        });
        item.dataset.virtualCondition = row.kind;
        if (row.kind.startsWith('MA_')) item.append(moVirtualMAFields(row, frameField));
        if (row.kind === 'MA_OPEN_POSITION') {
            // The open's distance from its average, in the policy's ATR, which is shown here.
            const fields = moElement('div', undefined, 'moses-virtual-grid');
            fields.append(moVirtualField('거리 ATR 배수 이하', row, 'max_atr'),
                frameField('ATR 시간봉', draft.atr, 'tf'), moVirtualField('ATR 기간', draft.atr, 'period'));
            item.append(fields);
        }
        if (row.kind === 'CANDLE_SHAPE') {
            const fields = moElement('div', undefined, 'moses-virtual-grid');
            fields.append(moVirtualField('모양', row, 'shape', MO_VIRTUAL_SHAPES));
            item.append(fields);
        }
        conditions.append(item);
    });
    if (!draft.conditions.length) conditions.append(moElement('p', '조건을 추가하세요.', 'muted'));
    const filterKind = mo('#mo-bt-virtual-filter-kind'), chosenFilter = filterKind.value;
    // One N-bar limit, and only while an entry waits (a conditional entry).
    filterKind.replaceChildren(...Object.entries(MO_VIRTUAL_FILTERS).filter(([value]) => value !== 'MAX_BARS'
        || (draft.mode !== 'IMMEDIATE' && !draft.filters.some(row => row.kind === 'MAX_BARS')))
        .map(([value, text]) => new Option(text, value)));
    if (Array.from(filterKind.options).some(option => option.value === chosenFilter)) filterKind.value = chosenFilter;
    const filters = mo('#mo-bt-virtual-filters'); filters.replaceChildren();
    // With 이평 시가 위·아래 the ATR is shown in that condition instead.
    if (!opening && draft.filters.some(row => MO_VIRTUAL_ATR_FILTERS.includes(row.kind))) {
        const atr = moElement('div', undefined, 'moses-virtual-grid');
        atr.append(frameField('ATR 시간봉', draft.atr, 'tf'), moVirtualField('ATR 기간', draft.atr, 'period'));
        filters.append(atr);
    }
    draft.filters.forEach((row, index) => {
        const item = moVirtualItem(MO_VIRTUAL_FILTERS[row.kind], () => { draft.filters.splice(index, 1); moVirtualEntryRender(); });
        item.dataset.virtualFilter = row.kind;
        let fields;
        if (row.kind === 'ENVIRONMENT') fields = moVirtualEnvironmentFields(row, frameField);
        else if (row.kind === 'MAX_BARS') {
            fields = moElement('div', undefined, 'moses-virtual-grid');
            fields.append(moVirtualField('봉 수', row, 'bars'));
        } else {
            fields = row.kind === 'CANDLE_ATR' ? moElement('div', undefined, 'moses-virtual-grid')
                : moVirtualMAFields(row, frameField);
            if (row.kind === 'CANDLE_ATR')
                fields.append(moVirtualField('봉 크기', row, 'measure', [['BODY', '몸통'], ['RANGE', '고가~저가']]));
            fields.append(moVirtualField('ATR 배수 이상', row, 'min', null, null, true),
                moVirtualField('ATR 배수 미만', row, 'max', null, null, true));
        }
        item.append(fields); filters.append(item);
    });
    const stops = [...(oz ? [['OZ_B0', '올존 B0']] : []), ['RECENT_EXTREME', '직전 N봉 저점·고점'], ['ATR', 'ATR']];
    if (!stops.some(([value]) => value === draft.stop.kind)) draft.stop.kind = 'ATR';
    const stop = mo('#mo-bt-virtual-stop'); stop.replaceChildren();
    stop.append(moVirtualField('손절 방식', draft.stop, 'kind', stops, () => moVirtualEntryRender()));
    if (draft.stop.kind === 'RECENT_EXTREME')
        stop.append(frameField('시간봉', draft.stop, 'tf'), moVirtualField('봉 수', draft.stop, 'bars'));
    if (draft.stop.kind === 'ATR')
        stop.append(frameField('시간봉', draft.stop, 'tf'), moVirtualField('ATR 기간', draft.stop, 'period'),
            moVirtualField('배수', draft.stop, 'multiplier'));
}
// 이평 시가 위·아래: the one candle whose open reaches its average is judged (수정본183).
function moVirtualOpening(draft) {
    return draft.conditions.some(row => row.kind === 'MA_OPEN_POSITION');
}
// With 이평 시가 위·아래 the alert frame's MA arrangement is read at the open, so an inverse cross at the
// open passes the alert: adding it sets those limits to the forming candle, removing it gives them the
// recipe's candle basis again (an added limit's own default, the closed candle).
function moVirtualOpenArrangement(draft, opening, recipe) {
    for (const row of draft.filters) {
        if (row.kind !== 'ENVIRONMENT' || row.condition !== 'MA_STATE' || row.tf !== 'SIGNAL') continue;
        const own = Number.isInteger(row.recipe_index) ? recipe?.filters[row.recipe_index] : null;
        row.bar_state = opening ? 'FORMING' : own?.bar_state || 'CLOSED';
    }
}
function moVirtualEntryValue() {
    const value = moStrategyCopy(moVirtualEntryDraft);
    if (value.mode === 'IMMEDIATE') value.conditions = [];
    return value;
}
function moVirtualEntryAdd(kind, filter = false) {
    const draft = moVirtualEntryDraft;
    if (!draft) return;
    const row = {kind};
    if (kind === 'ENVIRONMENT')
        Object.assign(row, {condition: 'MA_STATE', family: 'HMA', fast: 6, slow: 17, tf: 'SIGNAL', bar_state: 'CLOSED'});
    else if (kind === 'MAX_BARS') row.bars = 3;
    else {
        if (kind.startsWith('MA_')) Object.assign(row, {family: 'HMA', period: 6, tf: 'SIGNAL'});
        if (kind === 'MA_OPEN_POSITION') row.max_atr = 0.3;
        if (kind === 'CANDLE_SHAPE') row.shape = 'HAMMER';
        if (filter) Object.assign(row, {min: null, max: kind === 'CANDLE_ATR' ? 2 : 1});
        if (kind === 'CANDLE_ATR') row.measure = 'BODY';
    }
    draft[filter ? 'filters' : 'conditions'].push(row);
    if (kind === 'MA_OPEN_POSITION') moVirtualOpenArrangement(draft, true);
    if (filter) mo('#mo-bt-virtual-filter-details').open = true;
    moVirtualEntryRender();
}
function moBacktestSingleSelection(preferred = null) {
    const selected = Array.from(mo('#mo-bt-specials').querySelectorAll('.mo-special-enabled:checked'));
    // Old saved lists have no click history: retain their last enabled row until the user chooses one.
    const keep = selected.includes(preferred) ? preferred : selected.at(-1);
    for (const check of selected) if (check !== keep) check.checked = false;
}
// OZ triggers tested together with the chosen one (수정본171): one run each, saved as its own run like the
// base frames compared together. Only for the one selected strategy when it has OZ triggers.
const moTriggerCompare = new Set();
let moTriggerCompareKey = null;
function moTriggerTarget() {
    if (mo('#mo-bt-target').value === 'WATCH' || mo('#mo-bt-build-only').checked) return null;
    const cards = Array.from(mo('#mo-bt-specials').querySelectorAll('[data-special]'))
        .filter(card => card.querySelector('.mo-special-enabled').checked);
    if (cards.length !== 1 || cards[0].dataset.loadError) return null;
    const name = cards[0].dataset.special, fallback = moBacktestSpecialOptions?.default_triggers?.[name];
    if (!fallback) return null;
    return {name, trigger: cards[0].querySelector('.mo-special-trigger').value || fallback};
}
function moTriggerCompareRender() {
    const box = mo('#mo-bt-trigger-compare');
    if (!box) return;
    const target = moTriggerTarget();
    if (!target || target.name !== moTriggerCompareKey) moTriggerCompare.clear();
    moTriggerCompareKey = target ? target.name : null;
    box.replaceChildren();
    box.classList.toggle('hidden', !target);
    if (!target) return;
    moTriggerCompare.delete(target.trigger);
    box.append(moElement('span', '트리거 함께 비교', 'muted'));
    for (const value of moBacktestSpecialOptions?.trigger_choices || []) {
        if (value === target.trigger) continue;
        const item = moElement('label'), check = moElement('input');
        check.type = 'checkbox'; check.checked = moTriggerCompare.has(value); check.dataset.compareTrigger = value;
        check.onchange = () => {
            if (check.checked) moTriggerCompare.add(value); else moTriggerCompare.delete(value);
            moTriggerCompareRender();
        };
        item.append(check, document.createTextNode(value));
        box.append(item);
    }
    if (moTriggerCompare.size) box.append(moElement('p', '트리거마다 결과를 따로 저장합니다.', 'muted'));
}
// The triggers of a request: the chosen one first, then the compared ones in the list's order; none when
// not comparing.
function moTriggerList() {
    const target = moTriggerTarget();
    if (!target || !moTriggerCompare.size) return [];
    return [target.trigger, ...(moBacktestSpecialOptions?.trigger_choices || []).filter(value => moTriggerCompare.has(value))];
}
function moBacktestMode(event) {
    moBacktestSingleSelection(event?.target);
    const watch = mo('#mo-bt-target').value === 'WATCH';
    // A WATCH command is alert-only.
    if (watch && mo('#mo-bt-result-mode').value === 'VIRTUAL_ENTRY') mo('#mo-bt-result-mode').value = 'ALERT_ONLY';
    mo('#mo-bt-special-summary').classList.toggle('hidden', watch);
    mo('#mo-bt-watch-box').classList.toggle('hidden', !watch);
    const build = mo('#mo-bt-build-only').checked;
    mo('#mo-bt-spread-box').classList.toggle('hidden', build || mo('#mo-bt-result-mode').value !== 'VIRTUAL_ENTRY');
    mo('#mo-bt-virtual-panel').classList.toggle('hidden', build || mo('#mo-bt-result-mode').value !== 'VIRTUAL_ENTRY');
    mo('#mo-bt-rebuild').closest('label').classList.toggle('active', build);
    mo('#mo-bt-start-button').textContent = build ? '▶ 데이터 구축' : '▶ 백테스트 실행';
    mo('#mo-bt-rebuild').disabled = !build;
    document.querySelectorAll('[data-bt-target]').forEach(button => {
        const active = button.dataset.btTarget === mo('#mo-bt-target').value;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-bt-result]').forEach(button => {
        const active = button.dataset.btResult === (build ? 'BUILD_ONLY' : mo('#mo-bt-result-mode').value);
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
        if (button.dataset.btResult === 'VIRTUAL_ENTRY') button.disabled = watch;
    });
    const selected = mo('#mo-bt-specials').querySelectorAll('.mo-special-enabled:checked').length;
    const virtual = moBacktestVirtual();
    mo('#mo-bt-special-count').textContent = selected ? selected + '개 전략 선택됨' : '선택된 전략이 없습니다.';
    if (virtual) moVirtualSync();
    moTriggerCompareRender();
}
function moBacktestVirtual() {
    return !mo('#mo-bt-build-only').checked && mo('#mo-bt-result-mode').value === 'VIRTUAL_ENTRY';
}
function moBacktestRequest() {
    moBacktestSingleSelection();
    const specialSettings = moReadSpecialCards('#mo-bt-specials');
    const request = { symbol: mo('#mo-bt-symbol').value.trim(), start: mo('#mo-bt-start').value,
        end: mo('#mo-bt-end').value, mode: mo('#mo-bt-mode').value,
        target_mode: mo('#mo-bt-target').value,
        specials: Object.entries(specialSettings).filter(([, value]) => value.enabled).map(([name]) => name),
        special_settings: specialSettings,
        watch_text: mo('#mo-bt-watch').value, result_mode: mo('#mo-bt-result-mode').value,
        watch_chat_id: mo('#mo-bt-watch-chat')?.value || 'BACKTEST',
        spread_points: Number(mo('#mo-bt-spread').value),
        build_only: mo('#mo-bt-build-only').checked,
        rebuild: mo('#mo-bt-build-only').checked && mo('#mo-bt-rebuild').checked };
    if (moBacktestVirtual() && moVirtualEntryDraft && moVirtualTarget()) {
        request.virtual_entry = moVirtualEntryValue();
        request.virtual_strategy = moVirtualStrategyDraft ? moStrategyCopy(moVirtualStrategyDraft) : null;
        const bases = moVirtualBaseList(moVirtualTarget().profile, moVirtualEntryDraft.tf);
        if (bases.length) request.virtual_bases = bases;
    }
    const triggers = moTriggerList();
    if (triggers.length) request.trigger_variants = triggers;
    return request;
}
async function moBacktestStart() {
    if (moBacktestPoll.starting || (moState.job && !moBacktestPoll.done)) return;
    return moSubmitBacktest(moBacktestRequest());
}
function moBacktestRecovery() {
    const busy = moBacktestPoll.starting;
    const finished = moState.job && moBacktestPoll.done;
    const recoverable = finished && ['error', 'cancelled', 'interrupted'].includes(moBacktestPoll.phase);
    mo('#mo-bt-start-button').disabled = busy || (!!moState.job && !moBacktestPoll.done);
    mo('#mo-bt-stop-button').disabled = busy || !moState.job || !!finished || moBacktestPoll.resultPending;
    mo('#mo-bt-recovery-card').classList.toggle('hidden', !recoverable);
    mo('#mo-bt-retry').disabled = busy || !recoverable || !moBacktestPoll.request;
    mo('#mo-bt-retry').textContent = busy ? '다시 시도 요청 중…' : '같은 조건으로 다시 시도';
    mo('#mo-bt-recovery-help').textContent = moBacktestPoll.request
        ? 'MT5와 연결 상태를 확인한 뒤 같은 종목·기간·조건으로 다시 시도할 수 있습니다.'
        : '원래 실행 조건이 이 창에 남아 있지 않습니다. 설정 화면에서 종목·기간·조건을 확인한 뒤 새로 실행해 주세요.';
}
async function moBacktestRetry() {
    if (!moBacktestPoll.done || !['error', 'cancelled', 'interrupted'].includes(moBacktestPoll.phase) || !moBacktestPoll.request) return;
    return moSubmitBacktest(moBacktestPoll.request);
}
async function moSubmitBacktest(request) {
    if (moBacktestPoll.starting || (moState.job && !moBacktestPoll.done)) return;
    const snapshot = JSON.parse(JSON.stringify(request));
    const navigationRevision = moState.navigationRevision;
    const selectionRevision = ++moState.jobSelectionRevision;
    moBacktestPoll.starting = true;
    moBacktestRecovery();
    try {
        const data = await api('mo/backtest/start', snapshot);
        await moAdoptBacktestJob(data, {request: snapshot, navigationRevision, selectionRevision});
    } finally {
        moBacktestPoll.starting = false;
        moBacktestRecovery();
    }
}
async function moAdoptBacktestJob(data, {request = null, navigationRevision = moState.navigationRevision, selectionRevision = moState.jobSelectionRevision, openProgress = true} = {}) {
    if (!/^[a-f0-9]{32}$/.test(data.job_id || '')) throw Error('백테스트 작업 ID를 확인하세요.');
    if (selectionRevision !== moState.jobSelectionRevision) return;
    const showProgress = openProgress && navigationRevision === moState.navigationRevision;
    if (showProgress && moState.view !== 'backtest') moView('backtest');
    moStopBacktestPolling();
    const retryRequest = request || (moState.job === data.job_id ? moBacktestPoll.request : null);
    moState.job = data.job_id; moState.resultShown = false;
    moBacktestPoll.status = null; moBacktestPoll.result = null;
    moBacktestPoll.phase = data.phase || 'planning'; moBacktestPoll.done = false;
    moBacktestPoll.resultPending = false; moBacktestPoll.resultFailures = 0;
    moBacktestPoll.request = retryRequest ? JSON.parse(JSON.stringify(retryRequest)) : null;
    moBacktestPoll.planRevision = null;
    if (mo('#mo-bt-detail-log')) mo('#mo-bt-detail-log').textContent = '';
    mo('#mo-bt-log').textContent = ''; mo('#mo-bt-warnings').textContent = '';
    mo('#mo-bt-build-progress').value = 0; mo('#mo-bt-replay-progress').value = 0;
    mo('#mo-bt-remaining').textContent = '';
    mo('#mo-bt-setup-error').classList.add('hidden');
    mo('#mo-bt-open-progress').disabled = false;
    for (const id of ['#mo-bt-open-result', '#mo-bt-progress-result']) mo(id).disabled = true;
    mo('#mo-bt-confirm-card').classList.add('hidden');
    mo('#mo-bt-phase').textContent = '작업 상태 확인 중';
    mo('#mo-bt-progress-summary').textContent = '저장된 작업과 진행 상태를 확인하고 있습니다.';
    mo('#mo-bt-result').replaceChildren();
    if (showProgress) moBacktestPage('progress');
    moBacktestRecovery();
    moBacktestStatus().catch(moBacktestPollError);
}
function moStopBacktestPolling() {
    clearTimeout(moBacktestPoll.timer);
    moBacktestPoll.timer = null;
}
function moForgetBacktestJob(deleted) {
    deleted.forEach(job => moBacktestCancellations.delete(job));
    if (!deleted.includes(moState.job)) return;
    moStopBacktestPolling();
    moState.jobSelectionRevision += 1;
    moState.job = null; moState.resultShown = false;
    moBacktestPoll.status = null; moBacktestPoll.result = null;
    moBacktestPoll.done = true; moBacktestPoll.phase = ''; moBacktestPoll.resultPending = false;
    moBacktestPoll.request = null; moBacktestPoll.planRevision = null;
    for (const id of ['#mo-bt-open-progress', '#mo-bt-open-result', '#mo-bt-progress-result', '#mo-bt-stop-button']) mo(id).disabled = true;
    mo('#mo-bt-result').replaceChildren();
    moBacktestPage('setup');
}
function moBacktestPollError(error) {
    mo('#mo-bt-warnings').textContent = error.message;
}
async function moBacktestCancel() {
    const job = moState.job;
    if (!job || moBacktestPoll.done) return;
    const previous = moBacktestCancellations.get(job);
    if (previous?.forcing) return;
    const force = !!previous;
    if (force && !window.confirm('강제 종료하시겠습니까?')) return;
    const request = {forcing: force};
    moBacktestCancellations.set(job, request);
    mo('#mo-bt-phase').textContent = force ? '강제 종료 중입니다.' : '종료 중입니다. 진행 내용을 저장하고 있습니다.';
    mo('#mo-bt-progress-summary').textContent = mo('#mo-bt-phase').textContent;
    try {
        const result = await api('mo/backtest/stop', {job_id: job, force});
        if (job !== moState.job || moBacktestCancellations.get(job) !== request) return;
        request.forcing = false;
        mo('#mo-bt-confirm-card').classList.add('hidden');
        mo('#mo-bt-phase').textContent = result.message || '종료 중입니다. 진행 내용을 저장하고 있습니다.';
        mo('#mo-bt-progress-summary').textContent = mo('#mo-bt-phase').textContent;
        moScheduleBacktestStatus(0);
    } catch (error) {
        if (moBacktestCancellations.get(job) !== request) return;
        if (!force) moBacktestCancellations.delete(job);
        else request.forcing = false;
        if (job === moState.job) moBacktestPollError(error);
    }
}
function moScheduleBacktestStatus(delay) {
    moStopBacktestPolling();
    if (!moState.job || moState.view !== 'backtest' || moBacktestPoll.done) return;
    moBacktestPoll.timer = setTimeout(() => {
        moBacktestPoll.timer = null;
        moBacktestStatus().catch(moBacktestPollError);
    }, delay);
}
function moPlan(data) {
    moBacktestPoll.planRevision = data.plan_revision || null;
    const card = mo('#mo-bt-confirm-card');
    if (!['confirm', 'planned'].includes(data.phase) || !data.plan) { card.classList.add('hidden'); return; }
    card.classList.remove('hidden');
    mo('#mo-bt-confirm-button').classList.toggle('hidden', data.phase === 'planned');
    mo('#mo-bt-cancel-button').classList.toggle('hidden', data.phase === 'planned');
    const pieces = data.plan.record || [], estimate = data.plan.estimate || {};
    mo('#mo-bt-confirm-text').textContent = (data.plan.period_adjustment?.message || '') + ' ' +
        (data.plan.excluded_periods || []).map(p => p.start + ' ~ ' + p.end + ': ' + p.message).join(' · ') + ' ' +
        '새 녹화 ' + pieces.length + '조각: ' +
        pieces.map(p => p.start + ' ~ ' + p.end).join(', ') +
        ' · 예상 녹화 ' + Math.round((estimate.recording_seconds || 0) / 60) + '분' +
        ' · 임시 데이터 ' + ((estimate.temporary_msp3_bytes || 0) / 1e9).toFixed(2) + 'GB. ' +
        '완료된 달은 월 조각 전체를 녹화합니다.' +
        (data.phase === 'planned' ? ' 계획만 조회했습니다. 실행하려면 설정 화면에서 백테스트를 시작하세요.' : '');
}
function moTable(rows, columns) {
    const scroll = moElement('div', undefined, 'moses-result-scroll');
    const table = moElement('table', undefined, 'moses-result-table');
    const head = document.createElement('thead'), tr = document.createElement('tr');
    columns.forEach(column => tr.append(moElement('th', column)));
    head.append(tr); table.append(head);
    const body = document.createElement('tbody');
    rows.forEach(row => {
        const line = document.createElement('tr');
        columns.forEach(column => line.append(moElement('td', row[column] ?? '—')));
        body.append(line);
    });
    table.append(body); scroll.append(table); return scroll;
}
async function moResult() {
    if (!moState.job || moState.resultShown) return;
    if (moBacktestPoll.result) return moBacktestPoll.result.promise;
    const pending = { job: moState.job, promise: null };
    moBacktestPoll.result = pending;
    try {
        pending.promise = moReadResult(pending.job);
        return await pending.promise;
    } finally {
        if (moBacktestPoll.result === pending) moBacktestPoll.result = null;
    }
}
async function moReadResult(job) {
    const data = await api('mo/backtest/result?id=' + encodeURIComponent(job));
    if (job !== moState.job) return;
    for (const id of ['#mo-bt-open-result', '#mo-bt-progress-result']) mo(id).disabled = false;
    const box = mo('#mo-bt-result'); box.replaceChildren();
    if (window.MosesBacktestDashboard) {
        window.MosesBacktestDashboard.render(box, data, job);
        moState.resultShown = true;
        return;
    }
    box.append(moElement('p', '실행 ID: ' + data.run_id + ' · ' + data.status));
    if (data.period_adjustment) box.append(moElement('p', data.period_adjustment.message));
    if (data.excluded_periods?.length) box.append(moTable(data.excluded_periods, ['start', 'end', 'message']));
    if (data.processed_periods?.length)
        box.append(moElement('p', '실제 처리 구간 (UTC): ' + data.processed_periods.map(p =>
            p.start + ' ~ ' + (p.last_observation || p.end)).join(' · ')));
    if (data.alert_statistics) {
        const s = data.alert_statistics;
        const average = value => value == null ? '—' : Number(value).toFixed(2);
        box.append(moElement('p', '총 알림 ' + s.total + '건 · 일평균 ' + average(s.daily_average) +
            ' · 주평균 ' + average(s.weekly_average) + ' · 월평균 ' + average(s.monthly_average)));
        if (s.months?.length) box.append(moTable(s.months, ['month', 'alerts']));
    }
    if (data.virtual_entry?.summary?.length) {
        box.append(moElement('h3', '가상 진입 성과'));
        box.append(moTable(data.virtual_entry.summary,
            ['strategy', 'rr', 'alerts', 'entries', 'passes', 'pass_blocked', 'expired', 'pass_env', 'pass_condition', 'pass_distance', 'pass_risk',
                'waiting', 'wins', 'losses', 'unclosed', 'uncertain', 'win_rate', 'average_r', 'total_r']));
    } else if (data.pieces?.length) {
        box.append(moElement('h3', '데이터 조각'));
        box.append(moTable(data.pieces, ['symbol', 'start', 'end', 'status']));
    } else {
        box.append(moElement('h3', '알림 (첫 200건)'));
        const alerts = (data.alerts_preview || []).map(row => ({ ...row,
            time: Number.isFinite(Number(row.time_ms)) ?
                new Date(Number(row.time_ms)).toISOString().replace('T', ' ').replace('Z', ' UTC') : '—' }));
        box.append(moTable(alerts, ['time', 'strategy', 'symbol', 'tf', 'direction', 'message', 'recipient']));
    }
    if (data.warnings?.length) box.append(moElement('p', data.warnings.join('\n'), 'moses-warning'));
    if (data.compared_runs?.length) {
        // The other runs of the request: other base frames (수정본161) or other OZ triggers (수정본171).
        const frame = tf => tf ? moFrameText(tf) : '레시피';
        // Strategies × triggers replayed together (수정본184) name each run by both.
        const run = row => [row.strategy, row.trigger].filter(Boolean).join(' ') || '—';
        const byStrategy = data.compared_runs.some(row => (row.strategy ?? null) !== (data.strategy ?? null));
        const byTrigger = data.compared_runs.some(row => (row.trigger ?? null) !== (data.trigger ?? null));
        box.append(moElement('p', (byStrategy ? '실행 ' + run(data) + ' · 함께 시험: ' + data.compared_runs.map(run).join(' · ') :
            byTrigger ? '트리거 ' + (data.trigger || '—') + ' · 함께 시험: ' +
            data.compared_runs.map(row => row.trigger || '—').join(' · ') :
            (data.base_frame ? '기준 프레임 ' + frame(data.base_frame) + ' · ' : '') + '함께 시험: ' +
            data.compared_runs.map(row => frame(row.base_frame)).join(' · ')) + ' (실행 목록에서 따로 확인)', 'muted'));
    }
    for (const [label, path] of [['알림 CSV', data.alerts_csv], ['성과 CSV', data.summary_csv],
                                  ['거래별 CSV', data.trades_csv]])
        if (path) box.append(moElement('p', label + ': ' + path, 'muted'));
    moState.resultShown = true;
}
async function moBacktestStatus() {
    if (!moState.job || moState.view !== 'backtest' || moBacktestPoll.done) return;
    if (moBacktestPoll.status) return moBacktestPoll.status.promise;
    moStopBacktestPolling();
    const pending = { job: moState.job, promise: null };
    moBacktestPoll.status = pending;
    try {
        pending.promise = moReadBacktestStatus(pending.job);
        return await pending.promise;
    } finally {
        if (moBacktestPoll.status === pending) moBacktestPoll.status = null;
        const delay = pending.job !== moState.job ? 0 :
            ['planning', 'plan', 'starting'].includes(moBacktestPoll.phase) ? 300 : 1500;
        moScheduleBacktestStatus(delay);
    }
}
async function moReadBacktestStatus(job) {
    const navigationRevision = moState.navigationRevision;
    const data = await api('mo/backtest/status?id=' + encodeURIComponent(job));
    if (job !== moState.job) return;
    moBacktestPoll.phase = data.phase;
    moBacktestDetailLog().catch(() => {});
    moPlan(data);
    const progress = data.progress;
    const finished = (['complete', 'cancelled', 'error', 'planned'].includes(data.phase) && !data.active) ||
        (data.phase === 'interrupted' && !data.active);
    if (finished) moBacktestCancellations.delete(job);
    else if (data.cancel_requested && !moBacktestCancellations.has(job))
        moBacktestCancellations.set(job, {forcing: false});
    const cancelling = !finished && moBacktestCancellations.has(job);
    if (cancelling) mo('#mo-bt-confirm-card').classList.add('hidden');
    mo('#mo-bt-phase').textContent = cancelling ?
        (moBacktestCancellations.get(job).forcing ? '강제 종료 중입니다.' : '종료 중입니다. 진행 내용을 저장하고 있습니다.') : progress?.phase || data.message ||
        ({ planning: '데이터 계획 확인 중', confirm: '녹화 확인 대기', planned: '계획 조회 완료', starting: '백테스트 시작 중',
            complete: '완료', cancelled: '중단됨', interrupted: '관리 연결 중단', error: '오류' }[data.phase] || data.phase);
    mo('#mo-bt-progress-summary').textContent = cancelling ? mo('#mo-bt-phase').textContent : data.message || mo('#mo-bt-phase').textContent;
    if (progress) {
        mo('#mo-bt-build-progress').value = progress.build;
        mo('#mo-bt-replay-progress').value = progress.virtual > 0 ? progress.virtual : progress.replay;
        mo('#mo-bt-remaining').textContent = progress.remaining;
        mo('#mo-bt-warnings').textContent = progress.warnings.join('\n');
        mo('#mo-bt-log').textContent = progress.lines.join('\n');
    }
    if (data.phase === 'error') mo('#mo-bt-warnings').textContent = data.message;
    // A completed job's result is read before polling ends. A failed read is tried again at the next
    // status check, three reads at most; then polling ends with the error shown (수정본181).
    moBacktestPoll.resultPending = finished && data.phase === 'complete' && !!data.result_ready && !moState.resultShown;
    moBacktestPoll.done = finished && !moBacktestPoll.resultPending;
    moBacktestRecovery();
    if (data.result_ready) {
        const onProgress = moState.backtestPage === 'progress';
        const firstResult = !moState.resultShown;
        try {
            await moResult();
        } catch (error) {
            if (job === moState.job && moBacktestPoll.resultPending && ++moBacktestPoll.resultFailures >= 3) {
                moBacktestPoll.resultPending = false; moBacktestPoll.done = true; moBacktestRecovery();
            }
            throw error;
        }
        if (job !== moState.job) return;
        if (moBacktestPoll.resultPending) {
            moBacktestPoll.resultPending = false; moBacktestPoll.done = true; moBacktestRecovery();
        }
        if (firstResult && onProgress && finished && data.phase !== 'error' &&
            moState.view === 'backtest' && navigationRevision === moState.navigationRevision) moBacktestPage('result');
    }
}
async function moBacktestDetailLog() {
    const output = mo('#mo-bt-detail-log');
    if (!output || !mo('#mo-bt-detail')?.checked || !moState.job) return;
    if (moBacktestLogPending) return moBacktestLogPending.promise;
    const pending = { job: moState.job, promise: null };
    moBacktestLogPending = pending;
    pending.promise = (async () => {
        try {
            const data = await api('mo/backtest/log?id=' + encodeURIComponent(pending.job));
            if (pending.job !== moState.job) return;
            const text = data.text || (data.available ? '아직 기록이 없습니다.' : '아직 로그 파일이 없습니다.');
            if (output.textContent !== text) {
                output.textContent = text;
                if (mo('#mo-bt-log-scroll')?.checked) output.scrollTop = output.scrollHeight;
            }
        } catch (error) {
            if (pending.job === moState.job) output.textContent = '로그 조회 실패: ' + error.message;
        } finally {
            if (moBacktestLogPending === pending) moBacktestLogPending = null;
        }
    })();
    return pending.promise;
}
// Labels only: what each setting is for is explained in the user manual.
const moSettingText = {
    SYMBOLS: '감시 종목',
    TELEGRAM_TOKEN: '봇 토큰',
    TELEGRAM_CHAT_ID: '알림방 (그룹·채널 번호)',
    TELEGRAM_COMMAND_CHAT_IDS: '명령방 (내 텔레그램 번호)',
    PRIVATE_LIVE_ALERTS_ENABLED: '명령방으로도 라이브 알림 받기',
    PRIVATE_ECONOMY_ALERTS_ENABLED: '명령방으로도 경제지표 알림 받기',
    ECONOMY_ENABLED: '경제지표 알림',
    LIVE_RECORD_ENABLED: '라이브 알림 기록',
    STAFF_PIPE_NAME: 'MT5 연결 통로',
    STAFF_STALE_SEC: '데이터 끊김 판단(초)',
    SERVER_UTC_OFFSET: '브로커 서버 시간 (서머타임 아닐 때)', SERVER_DST: '브로커 서머타임',
    WONBI_SIGMA: '원비 밴드 배수',
    ASIA: '아시아 세션', LONDON: '런던 세션', NEWYORK: '뉴욕 세션',
    MAIN_ASIA: '아시아 주요 거래시간', MAIN_LONDON: '런던 주요 거래시간', MAIN_NEWYORK: '뉴욕 주요 거래시간',
    OPENING_ASIA: '아시아 장 초반', OPENING_LONDON: '런던 장 초반', OPENING_NEWYORK: '뉴욕 장 초반',
    ECONOMY_FETCH_SEC: '경제지표 일정 갱신(초)', ECONOMY_POLL_SEC: '경제지표 발표 확인(초)',
    TRACE_ENABLED: '진단 기록', TRACE_RING_LINES: '진단 기록 줄 수',
    TRACE_WARNING_BYTES: '경고 기록 파일 크기', TRACE_WARNING_BACKUPS: '경고 기록 파일 개수',
    cores: '동시 작업 수', overlap_trading_days: '앞 구간 겹쳐 읽기(거래일)',
    capture_start: '녹화 읽기 시작', oz_evaluation: '올존 확인 시간봉', broker_symbols: 'MT5 종목명 연결',
    warehouse: '데이터 창고 폴더', python_executable: '파이썬 실행 파일',
    provider: 'AI', model: 'Ollama 모델', base_url: 'Ollama 주소', gemini_model: 'Gemini 모델',
    gemini_api_key: 'Gemini API 키', watch_enabled: 'WATCH 명령 AI 보조 해석', timeout: 'AI 응답 대기(초)',
    gguf_model_path: 'GGUF 모델 파일', llama_server_path: 'GGUF 실행기', gguf_context_size: '대화 기억 공간(토큰)',
    gguf_gpu_layers: 'GPU 처리 층 수', gguf_threads: 'CPU 스레드 수', gguf_chat_template_path: '대화 형식 파일',
    max_new_tokens: '응답 최대 길이(토큰)'
};
// Each live setting the server sends belongs to exactly one group.
const moLiveSettingGroups = [
    {id: 'basic', title: '기본', keys: ['SYMBOLS', 'ECONOMY_ENABLED', 'LIVE_RECORD_ENABLED']},
    {id: 'telegram', title: '텔레그램', keys: ['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS']},
    {id: 'privatealerts', title: '명령방 알림', keys: ['PRIVATE_LIVE_ALERTS_ENABLED', 'PRIVATE_ECONOMY_ALERTS_ENABLED']},
    {id: 'connection', title: 'MT5 연결', advanced: true, keys: ['STAFF_PIPE_NAME', 'STAFF_STALE_SEC', 'SERVER_UTC_OFFSET', 'SERVER_DST']},
    {id: 'indicators', title: '지표', advanced: true, keys: ['WONBI_SIGMA'], prefix: ['POINT_']},
    {id: 'sessions', title: '세션 시간', advanced: true,
        keys: ['ASIA', 'LONDON', 'NEWYORK', 'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK', 'OPENING_ASIA', 'OPENING_LONDON', 'OPENING_NEWYORK']},
    {id: 'operation', title: '갱신 주기', advanced: true, keys: ['ECONOMY_FETCH_SEC', 'ECONOMY_POLL_SEC']},
    {id: 'diagnostics', title: '진단', advanced: true,
        keys: ['TRACE_ENABLED', 'TRACE_RING_LINES', 'TRACE_WARNING_BYTES', 'TRACE_WARNING_BACKUPS']}
];
function moCollapseSettingsDetails() {
    document.querySelectorAll('#settings-live-fields details, #settings-part2-fields details, #settings-ai-fields details').forEach(detail => {detail.open = false;});
}
function moRenderLiveSettings(parent, rows) {
    parent.replaceChildren();
    const groups = new Map(moLiveSettingGroups.map(group => [group.id, []]));
    for (const row of rows) {
        const group = moLiveSettingGroups.find(item => item.keys.includes(row.key) ||
            item.prefix?.some(prefix => row.key.startsWith(prefix)));
        if (group) groups.get(group.id).push(row);
    }
    const details = moElement('details', undefined, 'moses-settings-details');
    details.id = 'settings-live-details'; details.open = false;
    details.append(moElement('summary', '상세 설정'));
    for (const definition of moLiveSettingGroups) {
        const items = groups.get(definition.id);
        if (!items.length) continue;
        const section = moElement('fieldset', undefined, 'moses-settings-group');
        section.dataset.settingsGroup = definition.id;
        section.append(moElement('legend', definition.title));
        const fields = moElement('div', undefined, 'moses-settings-grid');
        fields.id = parent.id + '-' + definition.id;
        const ordered = [...items].sort((a, b) => {
            const position = key => {const index = definition.keys.indexOf(key); return index < 0 ? definition.keys.length : index;};
            return position(a.key) - position(b.key);
        });
        for (const row of ordered) {
            const input = moField(fields, row.key, row.secret ? row.configured : row.value,
                row.secret ? 'password' : 'text', row.secret);
            input.dataset.original = row.secret ? '' : row.value;
            if (row.secret) input.autocomplete = 'new-password';
        }
        section.append(fields);
        if (definition.id === 'telegram') section.append(moTelegramChatsButton());
        (definition.advanced ? details : parent).append(section);
    }
    if (details.querySelector('[data-setting]')) parent.append(details);
}
const moSettingChoices = {
    provider: [['gemini', 'Gemini'], ['ollama', 'Ollama'], ['local_gguf', 'GGUF'], ['disabled', '사용 안 함']],
    watch_enabled: [['true', '켜기'], ['false', '끄기']],
    LIVE_RECORD_ENABLED: [['true', '사용'], ['false', '사용 안 함']],
    PRIVATE_LIVE_ALERTS_ENABLED: [['false', '받지 않음'], ['true', '받기']],
    PRIVATE_ECONOMY_ALERTS_ENABLED: [['false', '받지 않음'], ['true', '받기']],
    capture_start: [['keyframe', '가까운 복원 지점부터'], ['beginning', '녹화 파일 처음부터']],
    oz_evaluation: [['selected', '선택한 시간봉만'], ['all', '모든 시간봉']],
    // MT5 bar times are the broker's server clock; Moses reads them in Korean time (수정본172).
    SERVER_UTC_OFFSET: Array.from({length: 27}, (_, i) => i - 12).map(hour => [String(hour), 'UTC' + (hour < 0 ? '' : '+') + hour]),
    SERVER_DST: [['US', '미국 서머타임 (3~11월 +1시간)'], ['EU', '유럽 서머타임 (3~10월 +1시간)'], ['NONE', '서머타임 없음']]
};
function moSettingLabel(name) {
    if (name.startsWith('POINT_')) return name.slice(6) + ' 1포인트 가격';
    return moSettingText[name] || name;
}
// Inside the input: an example of the format, or the real value used when the field is empty.
const moSettingPlaceholders = {SYMBOLS: '예: XAUUSD+,NAS100', STAFF_PIPE_NAME: '\\\\.\\pipe\\StaffOfMoses_v1',
    llama_server_path: 'runtime/llama.cpp/llama-server.exe', model: '예: qwen3.5:4b', gguf_model_path: '예: models/gguf/my_strategy.gguf'};
for (const key of ['ASIA', 'LONDON', 'NEWYORK', 'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK', 'OPENING_ASIA', 'OPENING_LONDON', 'OPENING_NEWYORK'])
    moSettingPlaceholders[key] = '예: 0900-1800';
function moSettingsError(error) {
    let text = error.message || String(error);
    for (const key of Object.keys(moSettingText).sort((a,b) => b.length-a.length))
        text = text.replace(new RegExp('\\b' + key + '\\b', 'g'), moSettingText[key]);
    return Error(text.replace(/true 또는 false/g, '켜기 또는 끄기').replace(/Windows Named Pipe/g, 'Windows 연결 통로')
        .replace(/HHMM-HHMM/g, '0900-1800처럼 네 자리 시각 두 개').replace(/\bTF\b/g, '시간봉'));
}
function moSelectArrow(select) {
    const field = moElement('span', undefined, 'moses-select-field');
    select.before(field);
    const arrow = moElement('span', '▼', 'moses-bt-symbol-arrow');
    arrow.setAttribute('aria-hidden', 'true');
    field.append(select, arrow);
}
const moTelegramKeys = new Set(['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS']);
function moTelegramError(error, privateValues) {
    let message = error?.message || '확인하지 못했습니다. 연결 상태를 확인하고 다시 눌러 주세요.';
    for (const value of privateValues.filter(value => typeof value === 'string' && value))
        message = message.split(value).join('[숨김]');
    return message.replace(/https?:\/\/api\.telegram\.org\/\S+/gi, '텔레그램 연결')
        .replace(/bot\d+:[A-Za-z0-9_-]+/g, 'bot[숨김]');
}
// A secret saved by its own [확인]: the bot token and chat fields, and the Gemini key (수정본177). A typed
// value is checked and saved, the delete box deletes, and an empty field checks the saved value again.
// The group's save button leaves the field out, and a typed value survives a redraw of the settings.
// request(value, entered) gives the request body and the typed values an error message must hide.
function moConfirmField(input, configured, {route, example, statusId, buttonId, selector, request: prepare, error, saved}) {
    const name = input.dataset.setting, label = input.parentElement;
    const field = moElement('span', undefined, 'moses-telegram-field');
    const button = moElement('button', '확인', 'moses-telegram-confirm hidden'); button.type = 'button';
    if (buttonId) button.id = buttonId;
    button.setAttribute('aria-label', moSettingLabel(name) + ' 확인');
    input.before(field); field.append(input, button);
    const status = moElement('small', undefined, 'moses-telegram-status');
    status.id = statusId;
    status.setAttribute('role', 'status'); field.after(status);
    input.setAttribute('aria-describedby', status.id);
    input.dataset.confirmField = 'true';
    let clear = label.querySelector('.mo-secret-clear');
    if (!clear) {
        const holder = moElement('span', undefined, 'moses-secret-clear-label');
        clear = document.createElement('input'); clear.type = 'checkbox'; clear.className = 'mo-secret-clear';
        holder.append(clear, document.createTextNode(' 저장값 삭제')); label.append(holder);
    }
    let revision = 0;
    const placeholder = state => state ? '설정됨 · 변경할 때만 입력' : '미설정 · ' + example;
    input.dataset.configured = String(configured);
    input.placeholder = placeholder(configured);
    function feedback(message, phase) {
        status.textContent = message; status.dataset.phase = phase;
        status.setAttribute('role', phase === 'error' ? 'alert' : 'status');
        status.classList.toggle('moses-telegram-error', phase === 'error');
    }
    function refresh() {
        const pending = moConfirmRequests.has(name);
        button.disabled = pending;
        button.textContent = pending ? '확인 중' : '확인';
        button.classList.toggle('hidden', !input.value.trim() && !clear.checked && input.dataset.configured !== 'true');
        clear.parentElement.classList.toggle('hidden', input.dataset.configured !== 'true');
        if (!pending && status.dataset.phase === 'pending') feedback('', '');
    }
    input.confirmRefresh = refresh;
    input.confirmSaved = (result, value, unchanged) => {
        if (unchanged) {input.value = ''; input.dataset.original = ''; clear.checked = false;}
        // Saving finished even if the draft changed or the settings were redrawn in the meantime.
        // Preserve a newer draft, but always reflect the server's actual saved state (수정본178).
        input.dataset.configured = String(result.configured);
        input.placeholder = placeholder(result.configured);
        saved(result, input, value);
        if (unchanged) feedback(result.message || (result.configured ? '확인하고 저장했습니다.' : '저장값을 삭제했습니다.'), 'success');
        else feedback('', '');  // A previous reply must not describe a newer draft as saved.
    };
    function changed() {revision++; feedback('', ''); refresh();}
    input.addEventListener('input', () => {if (input.value.trim()) clear.checked = false; changed();});
    clear.addEventListener('change', changed);
    async function confirm() {
        if (moConfirmRequests.has(name)) return;
        const entered = input.value.trim(), deleting = clear.checked;
        if (!entered && !deleting && input.dataset.configured !== 'true') return;
        const request = {revision, value: deleting ? null : entered};
        const {body, hidden} = prepare(request.value, entered);
        moConfirmRequests.set(name, request); refresh(); feedback(deleting ? '저장값을 삭제하는 중입니다.' : '연결을 확인하고 저장하는 중입니다.', 'pending');
        const current = () => input.isConnected && revision === request.revision && moConfirmRequests.get(name) === request;
        try {
            const result = await api(route, body);
            if (result.ok !== true || result.key !== name || typeof result.configured !== 'boolean')
                throw Error('확인 결과를 받지 못했습니다. 다시 확인해 주세요.');
            moSettingsLoadRevision++;
            const active = mo(selector) || input;
            const unchanged = active === input ? current() : active.value.trim() === entered &&
                !!active.closest('label').querySelector('.mo-secret-clear')?.checked === deleting;
            active.confirmSaved(result, request.value, unchanged);
        } catch (failure) {
            if (current()) feedback(error(failure, hidden), 'error');
        } finally {
            if (moConfirmRequests.get(name) === request) moConfirmRequests.delete(name);
            refresh();
            mo(selector)?.confirmRefresh?.();
        }
    }
    button.addEventListener('click', confirm);
    input.addEventListener('keydown', event => {if (event.key === 'Enter') {event.preventDefault(); confirm();}});
    input.confirmSave = confirm;
    refresh();
}
function moTelegramField(input, configured) {
    const name = input.dataset.setting;
    moConfirmField(input, configured, {
        route: 'mo/settings/telegram', statusId: 'telegram-status-' + name,
        selector: `#settings-live-fields [data-setting="${name}"]`,
        example: {TELEGRAM_TOKEN: '예: 123456789:AAH…', TELEGRAM_CHAT_ID: '예: -1001234567890 또는 @채널이름',
            TELEGRAM_COMMAND_CHAT_IDS: '예: 123456789'}[name],
        request(value, entered) {
            const tokenInput = mo('#settings-live-fields [data-setting="TELEGRAM_TOKEN"]');
            const candidateToken = tokenInput?.value.trim() || '';
            const body = {key: name, value};
            if (name !== 'TELEGRAM_TOKEN' && candidateToken && !tokenInput.parentElement.parentElement.querySelector('.mo-secret-clear')?.checked)
                body.token = candidateToken;
            return {body, hidden: [entered, candidateToken]};
        },
        error: moTelegramError,
        saved(result) {
            const row = moState.settings?.live?.find(row => row.key === name);
            if (row) row.configured = result.configured;
        }
    });
}
// Chats the bot already sees: 1:1 chats that sent /start, groups and channels it was invited to.
function moTelegramChatsButton() {
    const box = moElement('div', undefined, 'moses-telegram-load');
    const button = moElement('button', '대화방 불러오기'); button.type = 'button'; button.id = 'telegram-chats-load';
    const status = moElement('small', undefined, 'moses-telegram-status'); status.setAttribute('role', 'status');
    const list = moElement('div', undefined, 'moses-telegram-chats');
    box.append(button, status, list);
    const say = (text, error = false) => {status.textContent = text; status.classList.toggle('moses-telegram-error', error);};
    async function load(pause = false) {
        const token = mo('#settings-live-fields [data-setting="TELEGRAM_TOKEN"]')?.value.trim() || '';
        const request = pause ? {pause_live: true} : {};
        if (token) request.token = token;
        button.disabled = true; list.replaceChildren(); say(pause ? '라이브를 멈추고 불러오는 중입니다.' : '불러오는 중입니다.');
        try {
            const result = await api('mo/settings/telegram_chats', request);
            if (result.needs_live_pause) {
                if (window.confirm('불러오려면 라이브 감시를 잠시 멈춰야 합니다. 멈추고 불러올까요?')) return await load(true);
                say(''); return;
            }
            const chats = Array.isArray(result.chats) ? result.chats : [];
            // A running live engine reads and clears the bot's messages first.
            say(chats.length ? (result.live_restarted ? '라이브를 다시 시작했습니다' : '') : result.live_restarted ?
                '찾은 대화방이 없습니다 · 라이브 전체 종료 후 /start를 보내거나 그룹에 초대하고 다시 불러오세요' :
                '찾은 대화방이 없습니다 · 봇에게 /start를 보내거나 그룹·채널에 초대한 뒤 다시 불러오세요');
            for (const chat of chats) {
                const kind = {private: '개인', group: '그룹', channel: '채널'}[chat.type] || '';
                const row = moElement('div', undefined, 'moses-telegram-chat'); row.dataset.chat = chat.id;
                const name = [chat.name, chat.username ? '@' + chat.username : ''].filter(Boolean).join(' ');
                row.append(moElement('span', [kind, name, chat.id].filter(Boolean).join(' · ')));
                for (const [key, title] of [['TELEGRAM_CHAT_ID', '알림방으로'], ['TELEGRAM_COMMAND_CHAT_IDS', '명령방으로']]) {
                    if (key === 'TELEGRAM_COMMAND_CHAT_IDS' && chat.type !== 'private') continue;
                    const use = moElement('button', title); use.type = 'button'; use.dataset.useAs = key;
                    use.onclick = () => moTelegramUse(key, chat.id);
                    row.append(use);
                }
                list.append(row);
            }
        } catch (error) { say(moTelegramError(error, [token]), true); }
        finally { button.disabled = false; }
    }
    button.onclick = () => load().catch(error => say(error.message, true));
    return box;
}
function moTelegramUse(key, id) {
    const input = mo(`#settings-live-fields [data-setting="${key}"]`);
    if (!input) return;
    input.value = id; input.dispatchEvent(new Event('input'));
    input.confirmSave?.();
}
// What is typed in, or marked for deletion in, the fields with their own [확인], kept across a redraw.
function moConfirmDrafts() {
    return Array.from(document.querySelectorAll('[data-confirm-field]')).map(input => ({key: input.dataset.setting,
        value: input.value, deleting: !!input.closest('label')?.querySelector('.mo-secret-clear')?.checked}));
}
function moRestoreConfirmDrafts(drafts) {
    for (const draft of drafts) {
        const input = document.querySelector(`[data-confirm-field][data-setting="${draft.key}"]`);
        if (!input) continue;
        input.value = draft.value;
        input.closest('label').querySelector('.mo-secret-clear').checked = draft.deleting && input.dataset.configured === 'true';
        input.confirmRefresh();
    }
}
function moField(parent, name, value, type = 'text', secret = false) {
    const label = moElement('label', moSettingLabel(name), secret ? 'secret' : undefined);
    const choices = moSettingChoices[name] || (name.endsWith('_ENABLED') ? [['true','켜기'],['false','끄기']] : null);
    const input = document.createElement(choices && !secret ? 'select' : 'input'); input.dataset.setting = name;
    const raw = value == null ? '' : String(value);
    if (choices && !secret) {
        for (const [key, text] of choices) { const option = moElement('option', text); option.value = key; input.append(option); }
        if (!choices.some(([key]) => key === raw)) {
            const known = choices.find(([key]) => key === raw.toLowerCase());
            const option = moElement('option', known ? known[1] : '현재 저장된 값'); option.value = raw; input.prepend(option);
        }
    } else {
        input.type = type;
        if (!secret && (name.endsWith('_SEC') || name.endsWith('_MS') || name.endsWith('_BARS') ||
                name.startsWith('POINT_') || ['WONBI_SIGMA','cores','overlap_trading_days','timeout'].includes(name))) {
            input.type = 'number'; input.step = 'any';
        }
        if (name === 'cores') {input.min = '1'; input.max = '256'; input.step = '1'; input.placeholder = String(moState.settings?.part2_cores ?? '');}
        if (name === 'overlap_trading_days') {input.min = '0'; input.max = '30'; input.step = '1';}
        if (name === 'timeout') {input.min = '10'; input.max = '300'; input.step = '1';}
        if (name === 'max_new_tokens') {input.type = 'number'; input.min = '1'; input.step = '1';}
        if (['gguf_context_size', 'gguf_threads'].includes(name)) {input.type = 'number'; input.min = '1'; input.step = '1';}
        if (name === 'gguf_gpu_layers') {input.type = 'number'; input.min = '-1'; input.step = '1';}
    }
    input.value = raw;
    const example = moSettingPlaceholders[name] || (name.startsWith('POINT_') ? '예: 0.01' : '');
    if (!secret && example) input.placeholder = example;
    if (secret) { input.value = ''; input.placeholder = value ? '•••••••• · 변경할 때만 입력' : '미설정'; }
    label.append(input);
    if (choices && !secret) moSelectArrow(input);
    if (secret && value) {
        const clear = moElement('span', undefined, 'moses-secret-clear-label');
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
        checkbox.className = 'mo-secret-clear';
        clear.append(checkbox, document.createTextNode(' 저장값 삭제'));
        label.append(clear);
    }
    parent.append(label);
    if (moTelegramKeys.has(name)) moTelegramField(input, !!value);
    return input;
}
function moSymbolMappingField(parent, value) {
    const box = moElement('section', undefined, 'moses-symbol-mapping');
    box.setAttribute('aria-label', moSettingText.broker_symbols);
    box.append(moElement('h3', moSettingText.broker_symbols));
    const input = document.createElement('input'); input.type = 'hidden'; input.dataset.setting = 'broker_symbols';
    input.value = JSON.stringify(value); input.dataset.original = input.value;
    const rows = moElement('div', undefined, 'moses-symbol-rows');
    const add = moElement('button', '종목 연결 추가'); add.type = 'button';
    let dirty = false;
    function row(logical = '', broker = '') {
        const line = moElement('div', undefined, 'moses-symbol-row');
        for (const [title, text, placeholder] of [['프로그램 종목명', logical, '예: XAUUSD+'], ['MT5 종목명', broker, '예: GOLD']]) {
            const label = moElement('label', title), field = document.createElement('input');
            field.value = text; field.placeholder = placeholder; field.setAttribute('aria-label', title);
            field.addEventListener('input', () => {dirty = true;}); label.append(field); line.append(label);
        }
        const remove = moElement('button', '삭제'); remove.type = 'button'; remove.setAttribute('aria-label', '이 종목 연결 삭제');
        remove.onclick = () => {line.remove(); dirty = true;}; line.append(remove); rows.append(line);
    }
    Object.entries(value || {}).forEach(([logical, broker]) => row(logical, broker));
    add.onclick = () => {row(); dirty = true; rows.lastElementChild.querySelector('input').focus();};
    input.readMapping = () => {
        if (!dirty) return;
        const mapping = {};
        for (const line of rows.children) {
            const fields = [...line.querySelectorAll('input')], [logical, broker] = fields.map(field => field.value.trim());
            if (!logical && !broker) continue;
            if (!logical || !broker) throw Error('종목 연결의 프로그램 종목명과 MT5 종목명을 모두 입력하세요.');
            if (Object.hasOwn(mapping, logical)) throw Error('같은 프로그램 종목명을 두 번 연결할 수 없습니다.');
            Object.defineProperty(mapping, logical, {value:broker,enumerable:true,configurable:true});
        }
        input.value = JSON.stringify(mapping);
    };
    box.append(input, rows, add); parent.append(box); return input;
}
// This PC's fastest worker count, measured on request and kept per PC (수정본167).
function moTuningText(tuned) {
    if (!tuned) return '이 PC는 최적화 전';
    const rows = (tuned.rows || []).filter(row => row.bundles_per_second)
        .map(row => `${row.workers}개 ${Math.round(row.bundles_per_second)}`).join(', ');
    return `이 PC 최적화: ${tuned.workers}개` + (rows ? ` (${rows} 묶음/초)` : '');
}
function moTuningRow(after, tuned) {
    const row = moElement('div', undefined, 'moses-tuning-row');
    const button = moElement('button', '최적화'); button.type = 'button'; button.id = 'settings-tune';
    const text = moElement('span', moTuningText(tuned)); text.id = 'settings-tune-status';
    row.append(button, text); after.after(row);
    button.onclick = async () => {
        if (!window.confirm('다른 프로그램을 모두 닫은 뒤 측정하세요. 약 3분 걸리고, 그동안 PC가 바빠집니다.')) return;
        button.disabled = true;
        try {
            let state = await api('mo/backtest/tune', {});
            while (state.running) {
                text.textContent = state.steps ? `측정 중 · ${state.workers}개 동시 (${state.step}/${state.steps})` : '측정 준비 중';
                await new Promise(resolve => setTimeout(resolve, 1000));
                state = await api('mo/backtest/tune');
            }
            if (state.error) {text.textContent = state.error; return;}
            await moSettingsLoad();
        } catch (error) {text.textContent = error.message;}
        finally {button.disabled = false;}
    };
}
async function moSettingsLoad() {
    const revision = ++moSettingsLoadRevision;
    const data = await api('mo/settings');
    if (revision !== moSettingsLoadRevision) return;
    const drafts = moConfirmDrafts(); moState.settings = data;
    mo('#settings-version').textContent = Number.isInteger(data.app_revision) && data.app_revision > 0
        ? `버전 ${data.app_revision}` : '버전 정보 없음';
    moRenderLiveSettings(mo('#settings-live-fields'), data.live);
    const part2 = mo('#settings-part2-fields'); part2.replaceChildren();
    const part2Details = moElement('details', undefined, 'moses-settings-details');
    part2Details.id = 'settings-part2-details'; part2Details.open = false;
    part2Details.append(moElement('summary', '상세 설정'));
    const part2Advanced = moElement('div', undefined, 'moses-settings-grid');
    part2Advanced.id = 'settings-part2-advanced-fields'; part2Details.append(part2Advanced);
    for (const [key, value] of Object.entries(data.part2)) {
        if (key === 'warehouse') continue;
        const target = key === 'cores' ? part2Advanced : part2;
        const input = key === 'broker_symbols' ? moSymbolMappingField(part2, value) :
            moField(target, key, typeof value === 'object' && value !== null ? JSON.stringify(value) : value ?? '');
        input.dataset.original = input.value;
    }
    const cores = part2Advanced.querySelector('[data-setting="cores"]');
    if (cores) moTuningRow(cores.closest('label'), data.part2_tuned);
    if (part2Advanced.querySelector('[data-setting]')) part2.append(part2Details);
    const connections = mo('#settings-connection-fields'); connections.replaceChildren();
    for (const key of ['warehouse', 'python_executable']) {
        const input = moField(connections, key, data.connections[key]); input.dataset.original = input.value;
    }
    const rendering = moRenderAISettings(mo('#settings-ai-fields'), data.ai || {});
    moRestoreConfirmDrafts(drafts);  // the AI fields are drawn before its first wait
    moViewDrawn('settings');  // every field is drawn; a model list may still be on its way
    await rendering;
}
async function moRenderAISettings(ai, settings) {
    ai.replaceChildren();
    const row = moElement('div', undefined, 'moses-settings-grid ai-settings-main-row');
    row.id = 'ai-settings-main-row'; ai.append(row);
    const selectedProvider = settings.provider || 'gemini';
    const provider = moField(row, 'provider', selectedProvider);
    provider.dataset.original = selectedProvider; provider.id = 'ai-provider-select';
    const modelSlot = id => {
        const slot = moElement('div', undefined, 'ai-model-slot'); slot.id = id; row.append(slot); return slot;
    };
    const ollama = modelSlot('ai-ollama-fields');
    const model = moField(ollama, 'model', settings.model);
    model.dataset.original = model.value;
    const gemini = modelSlot('ai-gemini-fields');
    const geminiModel = moField(gemini, 'gemini_model', settings.gemini_model);
    geminiModel.dataset.original = geminiModel.value;
    const gguf = modelSlot('ai-local-gguf-fields');
    const ggufModel = moField(gguf, 'gguf_model_path', settings.gguf_model_path);
    ggufModel.dataset.original = ggufModel.value;
    const credential = modelSlot('ai-credential-fields');
    const keyConfigured = settings.gemini_api_key_configured === true;
    const geminiKey = moField(credential, 'gemini_api_key', keyConfigured, 'password', true);
    geminiKey.dataset.original = ''; geminiKey.autocomplete = 'new-password';
    moGeminiKeyConfirm(geminiKey, keyConfigured);
    const common = moElement('div', undefined, 'moses-settings-grid ai-provider-fields');
    common.id = 'ai-common-fields'; ai.append(common);
    const watch = moField(common, 'watch_enabled', settings.watch_enabled ?? true);
    watch.dataset.original = watch.value;
    const timeout = moField(common, 'timeout', settings.timeout ?? 90, 'number'); timeout.dataset.original = timeout.value;
    const ollamaOptions = moElement('div', undefined, 'moses-settings-grid ai-provider-fields');
    ollamaOptions.id = 'ai-ollama-advanced-fields'; ai.append(ollamaOptions);
    const endpoint = moField(ollamaOptions, 'base_url', settings.base_url); endpoint.readOnly = true;
    const ggufGroup = moElement('fieldset', undefined, 'moses-settings-group ai-provider-fields');
    ggufGroup.id = 'ai-gguf-advanced-group';
    ggufGroup.append(moElement('legend', 'GGUF 실행기 · 처리 방식'));
    const advanced = moElement('div', undefined, 'moses-settings-grid'); advanced.id = 'ai-gguf-advanced-fields';
    ggufGroup.append(advanced); ai.append(ggufGroup);
    const ggufServer = moField(advanced, 'llama_server_path', settings.llama_server_path);
    ggufServer.dataset.original = ggufServer.value; ggufServer.required = false;
    for (const key of ['gguf_context_size', 'gguf_gpu_layers', 'gguf_threads', 'gguf_chat_template_path']) {
        const fallback = key === 'gguf_context_size' ? 16384 : key === 'gguf_gpu_layers' ? 0 : null;
        const input = moField(advanced, key, settings[key] ?? fallback); input.dataset.original = input.value;
    }
    const maximum = moField(advanced, 'max_new_tokens', settings.max_new_tokens);
    maximum.dataset.original = maximum.value;
    let choicesReady = false, ggufChoicesReady = false, geminiChoicesReady = false;
    async function displayProvider() {
        const active = moSelectedAIProvider(ai);
        const isOllama = active === 'ollama', isGGUF = active === 'local_gguf', isGemini = active === 'gemini', isDisabled = active === 'disabled';
        ollama.classList.toggle('hidden', !isOllama); gemini.classList.toggle('hidden', !isGemini);
        gguf.classList.toggle('hidden', !isGGUF); credential.classList.toggle('hidden', !isGemini);
        ggufGroup.classList.toggle('hidden', !isGGUF);
        ollamaOptions.classList.toggle('hidden', !isOllama); common.classList.toggle('hidden', isDisabled);
        model.required = isOllama; ggufModel.required = isGGUF;
        geminiModel.required = isGemini; geminiKey.required = isGemini && geminiKey.dataset.configured !== 'true';
        if (isOllama && !choicesReady) {
            choicesReady = true;
            await moAIModelChoices(ollama, model);
        }
        if (isGGUF && !ggufChoicesReady) {
            ggufChoicesReady = true;
            await moGGUFModelChoices(gguf, ggufModel);
        }
        if (isGemini && !geminiChoicesReady) {
            geminiChoicesReady = true;
            await moGeminiModelChoices(geminiModel, geminiKey);
        }
    }
    provider.addEventListener('change', () => displayProvider().catch(error => moSettingsStatus('ai', error.message, true)));
    await displayProvider();
}
// The same field as the bot token (수정본177): its own [확인] checks a typed key with Gemini and saves it,
// deletes the saved key, or checks the saved key again.
function moGeminiKeyConfirm(input, configured) {
    moConfirmField(input, configured, {
        route: 'mo/settings/gemini_key', statusId: 'ai-gemini-key-status', buttonId: 'ai-gemini-key-confirm',
        selector: '#settings-ai-fields [data-setting="gemini_api_key"]', example: '예: AIza…',
        request: (value, entered) => ({body: {key: 'gemini_api_key', value}, hidden: [entered]}),
        error(failure, hidden) {
            let message = moSettingsError(failure).message;
            for (const value of hidden.filter(Boolean)) message = message.split(value).join('[숨김]');
            return message;
        },
        saved(result, field, value) {
            if (moState.settings?.ai) moState.settings.ai.gemini_api_key_configured = result.configured;
            if (value === '') return;  // A saved-key recheck changes neither settings nor open AI work.
            // The model list reads the saved key again, and the AI screens read the settings again.
            field.dispatchEvent(new Event('change'));
            window.dispatchEvent(new Event('part3-ai-settings-changed'));
        }
    });
}
async function moGeminiModelChoices(model, key) {
    return moExternalModelChoices(model, key, 'gemini', 'Gemini');
}
async function moExternalModelChoices(model, key, provider, title) {
    const field = moElement('span', undefined, 'moses-editable-choice');
    model.before(field); field.append(model);
    const select = document.createElement('select'); select.id = 'ai-' + provider + '-model-select';
    select.className = 'moses-choice-select'; select.disabled = true;
    select.setAttribute('aria-label', '사용할 ' + title + ' 모델 선택');
    const arrow = moElement('span', '▼', 'moses-bt-symbol-arrow'); arrow.setAttribute('aria-hidden', 'true');
    field.append(select, arrow);
    const status = moElement('small', undefined, 'moses-setting-help');
    status.id = 'ai-' + provider + '-list-status'; status.setAttribute('role', 'status'); field.after(status);
    model.addEventListener('input', () => {select.value = model.value;});
    select.onchange = () => {if (select.value) model.value = select.value;};
    let revision = 0;
    async function load() {
        const current = ++revision, enteredKey = key.value.trim();
        select.disabled = true;
        status.textContent = title + ' 모델 목록을 확인하는 중입니다.';
        if (!enteredKey && key.dataset.configured !== 'true') {
            select.replaceChildren(); status.textContent = '';
            return;
        }
        try {
            const result = await api('ai/' + provider + '-models', enteredKey ? {[provider + '_api_key']: enteredKey} : undefined);
            if (current !== revision || !field.isConnected) return;
            const names = Array.isArray(result.models) ? result.models.filter(name => typeof name === 'string' && name) : [];
            select.replaceChildren();
            if (result.available) names.forEach(name => select.append(new Option(name, name)));
            select.value = model.value; select.disabled = !select.options.length;
            status.textContent = select.disabled ? (result.error || '사용 가능한 모델이 없습니다') : '';
        } catch (_) {
            if (current !== revision || !field.isConnected) return;
            select.replaceChildren();
            status.textContent = title + ' 모델 목록을 불러오지 못했습니다. 모델명을 직접 입력하세요.';
        }
    }
    key.addEventListener('change', () => {load();});
    await load();
}
function moSelectedAIProvider(folder) {
    return folder.querySelector('[data-setting="provider"]').value;
}
async function moGGUFModelChoices(parent, model) {
    return moLocalModelChoices(parent, model, 'gguf', 'ai/gguf-models');
}
async function moAIModelChoices(parent, model) {
    return moLocalModelChoices(parent, model, 'ollama', 'ai/models');
}
async function moLocalModelChoices(parent, model, provider, route) {
    const isGGUF = provider === 'gguf';
    const field = moElement('span', undefined, 'moses-editable-choice');
    model.before(field); field.append(model);
    const select = document.createElement('select'); select.id = isGGUF ? 'ai-gguf-model-select' : 'ai-model-select';
    select.className = 'moses-choice-select'; select.setAttribute('aria-label', isGGUF ? '사용할 GGUF 모델' : '설치된 Ollama 모델');
    const arrow = moElement('span', '▼', 'moses-bt-symbol-arrow'); arrow.setAttribute('aria-hidden', 'true');
    field.append(select, arrow);
    const controls = moElement('div', undefined, 'ai-model-controls');
    const status = moElement('span', undefined, 'moses-setting-help');
    status.id = isGGUF ? 'ai-gguf-list-status' : 'ai-model-list-status'; status.setAttribute('role', 'status');
    const refresh = moElement('button', '모델 목록 새로고침'); refresh.type = 'button';
    refresh.id = isGGUF ? 'ai-gguf-model-refresh' : 'ai-model-refresh';
    controls.append(status, refresh); parent.append(controls);
    model.addEventListener('input', () => {select.value = model.value;});
    select.onchange = () => {if (select.value && !select.selectedOptions[0]?.disabled) model.value = select.value;};
    const option = (value, title) => {
        const item = moElement('option', title); item.value = value; select.append(item); return item;
    };
    function choices(models) {
        select.replaceChildren();
        const placeholder = isGGUF ? '__unconfigured_gguf__' : '__unconfigured_model__';
        if (!model.value) option(placeholder, '모델을 선택하세요').disabled = true;
        for (const item of models) option(item.path, item.name);
        if (model.value && !models.some(item => item.path === model.value))
            option(model.value, isGGUF ? model.value.split(/[\\/]/).at(-1) : model.value + ' (현재 설정 · 목록에 없음)');
        select.value = model.value || placeholder;
        select.disabled = !models.length;
    }
    async function load() {
        select.disabled = true; refresh.disabled = true;
        status.textContent = '모델 목록을 확인하는 중입니다.';
        try {
            const result = await api(route);
            if (!parent.isConnected) return;
            const raw = Array.isArray(result.models) ? result.models : [];
            const models = isGGUF ? raw.filter(item => item && typeof item.name === 'string' && typeof item.path === 'string') :
                (result.available ? raw.filter(name => typeof name === 'string' && name).map(name => ({name, path: name})) : []);
            choices(models);
            status.textContent = models.length ? `모델 ${models.length}개` : (result.error || '사용 가능한 모델이 없습니다');
            if (model.value && !models.some(item => item.path === model.value)) status.textContent += ' · 현재 값은 목록에 없음';
        } catch (_) {
            if (!parent.isConnected) return;
            choices([]); status.textContent = '모델 목록을 불러오지 못했습니다';
        } finally {refresh.disabled = false;}
    }
    refresh.onclick = load;
    await load();
}
async function moSettingsSave(group) {
    const folder = mo('#settings-' + (group === 'connections' ? 'connection' : group) + '-fields');
    if (!folder) throw Error('설정 그룹을 찾지 못했습니다.');
    const changes = {};
    folder.querySelectorAll('[data-setting="broker_symbols"]').forEach(input => input.readMapping());
    folder.querySelectorAll('[data-setting]').forEach(input => {
        // A field with its own [확인] saves only through it (bot token, chat fields, Gemini key).
        if (input.dataset.confirmField === 'true') return;
        if (group === 'ai' && input.dataset.setting === 'provider') return;
        if (input.closest('label')?.querySelector('.mo-secret-clear')?.checked) {
            changes[input.dataset.setting] = null;
            return;
        }
        if (input.readOnly || input.value === input.dataset.original || (input.type === 'password' && !input.value)) return;
        let value = input.value;
        if (group === 'part2') {
            if (['cores', 'overlap_trading_days'].includes(input.dataset.setting))
                value = value === '' && input.dataset.setting === 'cores' ? null : Number(value);
            if (input.dataset.setting === 'broker_symbols') value = JSON.parse(value);
        }
        if (group === 'ai') {
            if (input.dataset.setting === 'timeout') value = Number(value);
            if (['max_new_tokens', 'gguf_threads'].includes(input.dataset.setting)) value = value.trim() === '' ? null : Number(value);
            if (['gguf_context_size', 'gguf_gpu_layers'].includes(input.dataset.setting)) value = Number(value);
            if (input.dataset.setting === 'watch_enabled') value = value === 'true';
        }
        changes[input.dataset.setting] = value;
    });
    if (group === 'ai') {
        const provider = moSelectedAIProvider(folder);
        if (provider !== folder.querySelector('[data-setting="provider"]').dataset.original) changes.provider = provider;
    }
    if (!Object.keys(changes).length) { moSettingsStatus(group, '변경한 값이 없습니다'); return; }
    if (group === 'ai') {
        const read = key => key === 'provider' ? moSelectedAIProvider(folder) : folder.querySelector(`[data-setting="${key}"]`).value.trim();
        if (read('provider') === 'local_gguf') {
            for (const key of ['gguf_model_path', 'llama_server_path', 'gguf_chat_template_path']) {
                const path = read(key), name = moSettingLabel(key);
                if (!path && key === 'gguf_model_path') throw Error(name + ' 위치를 입력하세요.');
                if (path && (/^(?:[a-z]:|[\\/])/i.test(path) || path.split(/[\\/]/).includes('..')))
                    throw Error(name + '은 프로젝트 안의 상대 위치로 입력하세요.');
            }
            for (const key of ['gguf_context_size', 'gguf_threads', 'max_new_tokens']) {
                const value = read(key);
                if ((!value && key === 'gguf_context_size') || (value && (!Number.isSafeInteger(Number(value)) || Number(value) <= 0)))
                    throw Error(moSettingLabel(key) + '은 양의 정수를 입력하세요.');
            }
            const layers = read('gguf_gpu_layers');
            if (!layers || !Number.isSafeInteger(Number(layers)) || Number(layers) < -1)
                throw Error('GPU로 처리할 모델 층 수는 -1 또는 0 이상의 정수를 입력하세요.');
        } else if (read('provider') === 'gemini') {
            if (!read('gemini_model')) throw Error('Gemini 모델명을 입력하세요.');
            if (folder.querySelector('[data-setting="gemini_api_key"]').dataset.configured !== 'true')
                throw Error('Gemini API 키를 넣고 옆의 확인을 눌러 주세요.');
        } else if (read('provider') === 'ollama' && !read('model')) throw Error('Ollama 모델명을 입력하세요.');
    }
    let result;
    try { result = await api('mo/settings', { group, changes }); }
    catch (error) { throw moSettingsError(error); }
    await moSettingsLoad(); moSettingsStatus(group, result.message);
    if (group === 'ai') window.dispatchEvent(new Event('part3-ai-settings-changed'));
}
// The result of a save appears next to its own button, then clears.
const moSettingsStatusTimers = {};
function moSettingsStatus(group, text, error = false) {
    const status = document.querySelector(`[data-save-status="${group}"]`);
    if (!status) return;
    status.textContent = text; status.classList.toggle('error', error);
    clearTimeout(moSettingsStatusTimers[group]);
    if (!error) moSettingsStatusTimers[group] = setTimeout(() => { status.textContent = ''; }, 4000);
}
document.querySelectorAll('.moses-nav button[data-moses-view]').forEach(button =>
    button.addEventListener('click', () => moView(button.dataset.mosesView)));
mo('#mo-global-back').onclick = () => moBack();
document.querySelectorAll('[data-live-page]').forEach(button =>
    button.addEventListener('click', () => moLivePage(button.dataset.livePage)));
document.querySelectorAll('[data-backtest-page]').forEach(button =>
    button.addEventListener('click', () => moBacktestPage(button.dataset.backtestPage)));
mo('#live-open-settings').onclick = () => moOpenStrategyPopup('live');
mo('#live-open-logs').onclick = () => { moState.liveModule = 'ALL'; mo('#live-log-module').value = 'ALL'; moLivePage('log'); moLiveStatus().catch(() => {}); };
mo('#mo-bt-open-progress').onclick = () => moBacktestPage('progress');
mo('#mo-bt-open-result').onclick = () => moBacktestPage('result');
mo('#mo-bt-progress-result').onclick = () => moBacktestPage('result');
mo('#mo-bt-recovery-setup').onclick = () => moBacktestPage('setup');
mo('#mo-bt-retry').onclick = () => moBacktestRetry().catch(moBacktestPollError);
mo('#mo-bt-edit-specials').onclick = () => moOpenStrategyPopup('backtest');
mo('#strategy-settings-apply').onclick = () => moApplyStrategyPopup();
mo('#strategy-list-select-all').onclick = moStrategySelectAll;
mo('#strategy-list-delete').onclick = () => moChangeStrategyList('delete');
mo('#strategy-list-reset').onclick = () => moChangeStrategyList('reset');
window.addEventListener('moses-strategies-changed', event => {
    if (event.detail?.hostRefreshed) return;
    moBacktestSpecialOptions = null;
    Promise.all([moLiveSpecials(), moBacktestOptions()]).then(() => moBacktestMode()).catch(error => moMessage(error.message, true));
});
mo('#strategy-settings-close').onclick = moCloseStrategyPopup;
mo('#strategy-settings-cancel').onclick = moCloseStrategyPopup;
mo('#strategy-settings-dialog').addEventListener('cancel', event => {
    event.preventDefault(); moCloseStrategyPopup();
});
mo('#strategy-editor-close').onclick = moCloseStrategyEditor;
mo('#strategy-editor-cancel').onclick = moCloseStrategyEditor;
mo('#strategy-editor-dialog').addEventListener('cancel', event => {
    event.preventDefault(); moCloseStrategyEditor();
});
mo('#strategy-editor-default').onclick = () => {
    mo('#strategy-editor-error').classList.add('hidden');
    moStrategyEditor?.reset();
};
mo('#strategy-editor-save').onclick = () => {
    try {
        const editor = moStrategyEditor; mo('#strategy-editor-error').classList.add('hidden');
        editor?.save(); moStrategyPopupRender();
        if (editor?.keepOpen) editor.saved?.(); else moCloseStrategyEditor();
    } catch (error) {
        mo('#strategy-editor-error').textContent = error.message;
        mo('#strategy-editor-error').classList.remove('hidden');
    }
};
mo('#mo-bt-data-settings').onclick = () => moView('settings');
mo('#mo-bt-symbol-select').onchange = () => {
    if (mo('#mo-bt-symbol-select').value) mo('#mo-bt-symbol').value = mo('#mo-bt-symbol-select').value;
    mo('#mo-bt-symbol-select').value = mo('#mo-bt-symbol').value;
};
document.querySelectorAll('[data-bt-target]').forEach(button => button.onclick = () => {
    mo('#mo-bt-target').value = button.dataset.btTarget;
    moBacktestMode();
});
document.querySelectorAll('[data-bt-result]').forEach(button => button.onclick = () => {
    if (button.dataset.btResult === 'BUILD_ONLY') mo('#mo-bt-build-only').checked = true;
    else {
        mo('#mo-bt-build-only').checked = false;
        mo('#mo-bt-result-mode').value = button.dataset.btResult;
    }
    moBacktestMode();
});
mo('#mo-bt-specials').addEventListener('change', moBacktestMode);
mo('#mo-bt-watch').addEventListener('change', moBacktestMode);
mo('#live-start').onclick = () => moLiveAction('start');
mo('#live-stop').onclick = () => moLiveAction('stop');
mo('#live-specials-save').onclick = () => moSaveSpecials().catch(e => moMessage(e.message, true));
if (mo('#live-specials-refresh')) mo('#live-specials-refresh').onclick = () => moLiveSpecials().catch(e => moMessage(e.message, true));
mo('#live-log-module').onchange = () => { moState.liveModule = mo('#live-log-module').value; moLiveStatus().catch(() => {}); };
mo('#live-detail').onchange = () => moLiveStatus().catch(() => {});
if (mo('#live-log-pause')) mo('#live-log-pause').onchange = () => moLiveStatus().catch(() => {});
if (mo('#mo-bt-detail')) mo('#mo-bt-detail').onchange = () => {
    mo('#mo-bt-detail-log').classList.toggle('hidden', !mo('#mo-bt-detail').checked);
    mo('#mo-bt-log')?.classList.toggle('hidden', mo('#mo-bt-detail').checked);
    moBacktestDetailLog().catch(() => {});
};
if (mo('#mo-bt-log-refresh')) mo('#mo-bt-log-refresh').onclick = () => moBacktestDetailLog().catch(() => {});
for (const id of ['#mo-bt-target', '#mo-bt-result-mode', '#mo-bt-build-only']) mo(id).onchange = moBacktestMode;
mo('#mo-bt-virtual-condition-add').onclick = () => moVirtualEntryAdd(mo('#mo-bt-virtual-condition-kind').value);
mo('#mo-bt-virtual-filter-add').onclick = () => moVirtualEntryAdd(mo('#mo-bt-virtual-filter-kind').value, true);
mo('#mo-bt-start-button').onclick = () => moBacktestStart().catch(e => {
    mo('#mo-bt-setup-error').textContent = e.message;
    mo('#mo-bt-setup-error').classList.remove('hidden');
});
mo('#mo-bt-stop-button').onclick = moBacktestCancel;
mo('#mo-bt-confirm-button').onclick = () => api('mo/backtest/confirm', { job_id: moState.job, plan_revision: moBacktestPoll.planRevision }).then(() => {
    mo('#mo-bt-confirm-card').classList.add('hidden'); moScheduleBacktestStatus(0);
}).catch(moBacktestPollError);
mo('#mo-bt-cancel-button').onclick = moBacktestCancel;
document.querySelectorAll('[data-save-settings]').forEach(button =>
    button.onclick = () => moSettingsSave(button.dataset.saveSettings).catch(e => moSettingsStatus(button.dataset.saveSettings, e.message, true)));
setInterval(() => {
    if (moState.view === 'live') moLiveStatus({fresh: false}).catch(e => {
        mo('#live-message').textContent = '라이브 감시 상태 확인 대기';
        mo('#live-log-error').textContent = e.message;
        mo('#live-log-error').classList.remove('hidden');
    });
}, 1500);
moView('live');
