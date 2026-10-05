'use strict';
/* MOSES navigation and transport only. Existing Part3 builder stays in app.js. */
const mo = s => document.querySelector(s);
const moState = { view: '', livePage: 'main', backtestPage: 'setup', liveModule: 'ALL', job: null, resultShown: false, settings: null, specials: null, navigationRevision: 0, viewRevision: 0, jobSelectionRevision: 0 };
const moBacktestPoll = { starting: false, status: null, result: null, timer: null, phase: '', done: false, planRevision: null, request: null };
let moBacktestLogPending = null;
const moBacktestCancellations = new Map();
let moLiveActionPending = false;
let moLiveActionTimer = null;
const moLiveActionRecords = [];
let moWindowClosing = false;
let moSettingsLoadRevision = 0;
const moTelegramRequests = new Map();
const moStrategyPopup = { mode: null, draft: null, metadata: null, busy: false, saving: false, revision: 0 };
let moStrategyEditor = null;
let moBacktestSpecialOptions = null;
let moVirtualEntryDraft = null;
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
async function moLiveStatus() {
    if (moState.view !== 'live') return;
    const module = encodeURIComponent(moState.liveModule);
    const detail = mo('#live-detail').checked ? 'true' : 'false';
    const data = await api('mo/live/status?module=' + module + '&detail=' + detail);
    for (const [name, row] of Object.entries(data.modules)) {
        if (row.preset) moLiveSpecialStates[name] = row.state;
    }
    moStrategyStatusBadges();
    const grid = mo('#live-modules'); grid.replaceChildren();
    const select = mo('#live-log-module');
    const moduleOptions = ['ALL', ...Object.keys(data.modules)];
    if (Array.from(select.options).map(option => option.value).join('\n') !== moduleOptions.join('\n')) {
        select.replaceChildren(new Option('전체 로그', 'ALL'));
        for (const [name, row] of Object.entries(data.modules))
            select.append(new Option(row.name, name));
        if (!moduleOptions.includes(moState.liveModule)) moState.liveModule = 'ALL';
        select.value = moState.liveModule;
    }
    for (const [name, row] of Object.entries(data.modules)) {
        if (row.preset) continue;
        grid.append(moLiveCard(name, row));
    }
    if (!mo('#live-log-pause')?.checked) {
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
function moStrategyTimeSummary(value, labels) {
    const enabled = Object.entries(labels).filter(([key]) => value[key]?.enabled).map(([, label]) => label);
    return enabled.length ? enabled.join(' · ') : '알림 꺼짐 · 선택한 세션 없음';
}
function moStrategyStatusBadges() {
    document.querySelectorAll('[data-strategy-state]').forEach(badge => {
        const text = moLiveSpecialStates[badge.dataset.strategyState] || '연결 대기';
        badge.textContent = '● ' + text;
        badge.classList.toggle('connected', text === '연결중');
        badge.classList.toggle('error', text === '오류');
    });
}
function moStrategySelectAllState() {
    const rows = Object.values(moStrategyPopup.draft || {}), button = mo('#strategy-list-select-all');
    button.textContent = rows.length && rows.every(row => row.enabled) ? '전체해제' : '전체선택';
    button.disabled = !rows.length || moStrategyPopup.busy || moStrategyPopup.saving;
}
function moStrategySelectAll() {
    const popup = moStrategyPopup;
    if (!popup.mode || !popup.draft || popup.busy || popup.saving) return;
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
        check.onchange = () => { row.enabled = check.checked; moStrategySelectAllState(); };
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
        if (row.time_filters != null)
            actions.append(moElement('span', moStrategyTimeSummary(row.time_filters, labels), 'moses-strategy-time-summary'));
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
function moOpenStrategyEditor(title, save, reset) {
    mo('#strategy-editor-title').textContent = title;
    mo('#strategy-editor-error').textContent = ''; mo('#strategy-editor-error').classList.add('hidden');
    mo('#strategy-editor-body').replaceChildren(); moStrategyEditor = {save, reset};
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
    const defaults = row.default_time_filters;
    const defaultRanges = defaults && typeof defaults === 'object' && !Array.isArray(defaults) ? defaults : null;
    const defaultSessions = Array.isArray(defaults) ? defaults : typeof defaults === 'string' ? [defaults.trim()] : [];
    const config = popup.metadata.session_times || {}, fields = [];
    const format = text => { const raw = String(text || '').replace(/:/g, ''); return raw.length === 4 ? raw.slice(0,2) + ':' + raw.slice(2) : raw; };
    moOpenStrategyEditor(name + ' · 최종 알림 시간', () => {
        const result = {};
        for (const field of fields) {
            const value = {enabled: field.enabled.checked};
            for (const part of ['start', 'end']) {
                const compact = moStrategyHHMM(field[part].value);
                if (value.enabled && !compact && field.expected[part])
                    throw new Error(labels[field.key] + (part === 'start' ? ' 시작 시각' : ' 종료 시각') + '을 입력하세요.');
                if (compact && (field.current[part] != null || compact !== field.base[part] ||
                    defaultRanges)) value[part] = compact;
            }
            result[field.key] = value;
        }
        row.time_filters = result;
    }, () => { row.time_filters = null; moStrategyPopupRender(); moCloseStrategyEditor(); });
    const grid = moElement('div', undefined, 'moses-strategy-time-editor');
    for (const [key, label] of Object.entries(labels)) {
        const base = String(config[key] || '').split('-');
        const displayed = String(defaultRanges?.[key] || config[key] || '').split('-');
        const current = row.time_filters?.[key] || {};
        const field = {key, current, base: {start: base[0] || '', end: base[1] || ''},
            expected: {start: !!(current.start || displayed[0] || base[0]), end: !!(current.end || displayed[1] || base[1])}};
        const line = moElement('label'), title = moElement('span');
        field.enabled = document.createElement('input'); field.enabled.type = 'checkbox';
        field.enabled.checked = row.time_filters != null ? !!current.enabled :
            defaults === 0 || defaultSessions.includes(key) || !!(defaultRanges && Object.hasOwn(defaultRanges, key));
        title.append(field.enabled, document.createTextNode(label)); line.append(title);
        for (const [index, part] of ['start', 'end'].entries()) {
            const input = document.createElement('input'); input.type = 'text'; input.maxLength = 5;
            input.value = format(current[part] || displayed[index]); input.placeholder = part === 'start' ? '시작' : '종료';
            input.setAttribute('aria-label', label + (part === 'start' ? ' 시작 시각' : ' 종료 시각'));
            field[part] = input; line.append(input); if (index === 0) line.append(moElement('span', '–'));
        }
        fields.push(field); grid.append(line);
    }
    grid.append(moElement('small', 'KST · 시작/종료 포함 · 자정 통과 지원. 모든 세션을 해제하면 최종 알림이 없습니다. 기본값은 전략의 기존 거래시간을 복원합니다.'));
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
    if (!moVirtualEntryDraft) {
        moVirtualEntryDraft = JSON.parse(JSON.stringify(value.virtual_entry || moVirtualEntryDefault()));
        moVirtualEntryRender();
    }
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
}
function moVirtualEntryDefault() {
    return {schema: 1, mode: 'AUTO', tf: 'SIGNAL', conditions: [],
        atr: {tf: 'SIGNAL', period: 14}, filters: [],
        stop: {kind: 'AUTO', tf: 'SIGNAL', bars: 5, multiplier: 1}};
}
function moVirtualTimeframes() {
    return [['SIGNAL', '신호 프레임 · 자동'], ...(moBacktestSpecialOptions?.virtual_timeframes ||
        ['1m', '2m', '3m', '4m', '5m', '6m', '10m', '12m', '15m', '20m', '30m',
            '1h', '2h', '3h', '4h', '6h', '8h', '12h', '1d']).map(tf => [tf, tf])];
}
function moVirtualField(label, row, key, choices, changed, optional = false) {
    const box = moElement('label', label);
    const input = moElement(choices ? 'select' : 'input');
    input.dataset.virtualField = key;
    if (choices) choices.forEach(([value, text]) => input.append(new Option(text, value)));
    else {
        input.type = 'number'; input.min = ['period', 'bars'].includes(key) ? '1' : '0';
        input.step = ['period', 'bars'].includes(key) ? '1' : 'any';
        if (['period', 'bars'].includes(key)) input.max = '500';
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
function moVirtualMAFields(box, row) {
    box.append(moVirtualField('이평 종류', row, 'family', ['SMA', 'EMA', 'WMA', 'HMA'].map(x => [x, x])),
        moVirtualField('이평 기간', row, 'period'),
        moVirtualField('이평 프레임', row, 'tf', moVirtualTimeframes()));
}
function moVirtualEntryRender() {
    const draft = moVirtualEntryDraft;
    if (!draft) return;
    const main = mo('#mo-bt-virtual-main'); main.replaceChildren();
    main.append(moVirtualField('진입 방식', draft, 'mode',
        [['AUTO', '자동'], ['IMMEDIATE', '즉시 진입'], ['CONFIRM', '확인 진입']], moVirtualEntryRender));
    if (draft.mode !== 'IMMEDIATE')
        main.append(moVirtualField('확인봉 프레임', draft, 'tf', moVirtualTimeframes()));
    mo('#mo-bt-virtual-mode-help').textContent = draft.mode === 'AUTO'
        ? '자동: 올존은 HMA6 위·아래 확인 진입, 그 외 알림은 즉시 진입합니다. 확인 조건을 직접 지정하려면 확인 진입을 선택하세요.'
        : draft.mode === 'CONFIRM'
            ? '알림 이후 확정봉과 선택한 조건을 확인합니다. 올존은 해당 패턴의 유효기간 안에서만 진입합니다.'
            : '알림이 발생한 가격으로 진입합니다. ATR 제한과 손절 설정을 함께 적용합니다.';
    mo('#mo-bt-virtual-confirm').classList.toggle('hidden', draft.mode !== 'CONFIRM');
    const conditions = mo('#mo-bt-virtual-conditions'); conditions.replaceChildren();
    const names = {MA_POSITION: '이평 위·아래', MA_CROSS: '이평 종가 돌파',
        MA_TOUCH: '이평 터치 후 확인', ENGULFING: '인걸핑', NECKLINE_BREAK: '올존 넥라인 종가 돌파'};
    draft.conditions.forEach((row, index) => {
        const item = moElement('div', undefined, 'moses-virtual-item');
        item.dataset.virtualCondition = row.kind;
        const heading = moElement('div', undefined, 'moses-virtual-heading');
        heading.append(moElement('strong', names[row.kind] || row.kind));
        const remove = moElement('button', '삭제'); remove.type = 'button';
        remove.setAttribute('aria-label', (names[row.kind] || row.kind) + ' 조건 삭제');
        remove.onclick = () => {draft.conditions.splice(index, 1); moVirtualEntryRender();};
        heading.append(remove); item.append(heading);
        if (row.kind.startsWith('MA_')) {
            const fields = moElement('div', undefined, 'moses-virtual-grid');
            moVirtualMAFields(fields, row); item.append(fields);
        }
        const help = {MA_POSITION: '현재 봉 시가가 매수는 이평 위, 매도는 이평 아래여야 합니다.',
            MA_CROSS: '확인봉 종가가 이평을 매수는 상향, 매도는 하향 돌파해야 합니다.',
            MA_TOUCH: '알림 이후 이평에 닿은 확정봉 다음에 확인합니다.',
            ENGULFING: '확인봉 몸통이 이전 봉의 반대 방향 몸통을 감싸야 합니다.',
            NECKLINE_BREAK: '알림을 발생시킨 올존의 넥라인을 확인봉 종가로 돌파해야 합니다.'};
        item.append(moElement('p', help[row.kind], 'muted'));
        conditions.append(item);
    });
    if (!draft.conditions.length) conditions.append(moElement('p',
        draft.mode === 'AUTO' ? '자동 기본 확인 조건을 사용합니다.' : '양봉·음봉 마감만 확인합니다.', 'muted'));
    const filters = mo('#mo-bt-virtual-filters'); filters.replaceChildren();
    draft.filters.forEach((row, index) => {
        const item = moElement('div', undefined, 'moses-virtual-item');
        item.dataset.virtualFilter = row.kind;
        const title = row.kind === 'CANDLE_ATR' ? '확인봉 크기' : '현재 시가와 이평 거리';
        const heading = moElement('div', undefined, 'moses-virtual-heading');
        heading.append(moElement('strong', title));
        const remove = moElement('button', '삭제'); remove.type = 'button';
        remove.setAttribute('aria-label', title + ' 제한 삭제');
        remove.onclick = () => {draft.filters.splice(index, 1); moVirtualEntryRender();};
        heading.append(remove); item.append(heading);
        const fields = moElement('div', undefined, 'moses-virtual-grid');
        if (row.kind === 'CANDLE_ATR')
            fields.append(moVirtualField('봉 크기 기준', row, 'measure', [['BODY', '몸통'], ['RANGE', '고가~저가']]));
        else moVirtualMAFields(fields, row);
        fields.append(moVirtualField('최소 ATR 배수', row, 'min', null, null, true),
            moVirtualField('최대 ATR 배수', row, 'max', null, null, true));
        item.append(fields); filters.append(item);
    });
    if (!draft.filters.length) filters.append(moElement('p', 'ATR 진입 제한 없음', 'muted'));
    const atr = mo('#mo-bt-virtual-atr'); atr.replaceChildren();
    atr.append(moVirtualField('ATR 프레임', draft.atr, 'tf', moVirtualTimeframes()),
        moVirtualField('ATR 기간', draft.atr, 'period'));
    const stop = mo('#mo-bt-virtual-stop'); stop.replaceChildren();
    stop.append(moVirtualField('손절 방식', draft.stop, 'kind',
        [['AUTO', '자동 · 올존 B0 / 그 외 ATR'], ['OZ_B0', '올존 B0 저점·고점'],
            ['RECENT_EXTREME', '직전 N봉 저점·고점'], ['ATR', 'ATR 배수']], moVirtualEntryRender));
    if (['RECENT_EXTREME', 'ATR'].includes(draft.stop.kind))
        stop.append(moVirtualField('손절 프레임', draft.stop, 'tf', moVirtualTimeframes()));
    if (draft.stop.kind === 'RECENT_EXTREME') stop.append(moVirtualField('직전 봉 수 N', draft.stop, 'bars'));
    if (draft.stop.kind === 'ATR') stop.append(moVirtualField('손절 ATR 배수', draft.stop, 'multiplier'));
}
function moVirtualEntryValue() {
    const value = JSON.parse(JSON.stringify(moVirtualEntryDraft || moVirtualEntryDefault()));
    if (value.mode !== 'CONFIRM') value.conditions = [];
    if (value.stop.kind !== 'RECENT_EXTREME') value.stop.bars = 5;
    if (value.stop.kind !== 'ATR') value.stop.multiplier = 1;
    if (['AUTO', 'OZ_B0'].includes(value.stop.kind)) value.stop.tf = 'SIGNAL';
    return value;
}
function moVirtualEntryAdd(kind, filter = false) {
    if (!moVirtualEntryDraft) moVirtualEntryDraft = moVirtualEntryDefault();
    const row = {kind};
    if (kind.startsWith('MA_')) Object.assign(row, {family: 'HMA', period: 6, tf: 'SIGNAL'});
    if (filter) Object.assign(row, {min: null, max: kind === 'CANDLE_ATR' ? 2 : 1});
    if (kind === 'CANDLE_ATR') row.measure = 'BODY';
    moVirtualEntryDraft[filter ? 'filters' : 'conditions'].push(row);
    if (filter) mo('#mo-bt-virtual-filter-details').open = true;
    moVirtualEntryRender();
}
function moBacktestMode() {
    const watch = mo('#mo-bt-target').value === 'WATCH';
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
    });
    const selected = mo('#mo-bt-specials').querySelectorAll('.mo-special-enabled:checked').length;
    mo('#mo-bt-special-count').textContent = selected ? selected + '개 전략 선택됨' : '선택된 전략이 없습니다.';
}
function moBacktestRequest() {
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
    if (!request.build_only && request.result_mode === 'VIRTUAL_ENTRY')
        request.virtual_entry = moVirtualEntryValue();
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
    mo('#mo-bt-stop-button').disabled = busy || !moState.job || !!finished;
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
    moBacktestPoll.done = true; moBacktestPoll.phase = '';
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
            ['strategy', 'rr', 'alerts', 'entries', 'passes', 'pass_blocked', 'pass_risk',
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
    moBacktestPoll.done = finished;
    moBacktestRecovery();
    if (data.result_ready) {
        const onProgress = moState.backtestPage === 'progress';
        const firstResult = !moState.resultShown;
        await moResult();
        if (job !== moState.job) return;
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
const moSettingText = {
    live_records: ['LIVE 기록 폴더', '이미 만들어 둔 프로젝트 밖의 폴더를 입력하세요. 이 컴퓨터의 설정에만 저장되며 다음 라이브 시작부터 적용됩니다.'],
    TELEGRAM_TOKEN: ['텔레그램 봇 연결 키', '봇을 연결할 때 사용하는 키입니다. 저장된 키는 화면에 표시하지 않습니다.'],
    TELEGRAM_CHAT_ID: ['알림을 받을 채팅방', '매매 알림을 보낼 텔레그램 채팅방 번호 또는 채널 이름입니다.'],
    TELEGRAM_COMMAND_CHAT_IDS: ['명령을 보낼 수 있는 채팅방', '개인 감시 명령을 받을 채팅방 번호입니다. 여러 개는 쉼표로 구분합니다.'],
    STAFF_BIND_ENDPOINT: ['데이터 수신 프로그램의 연결 주소', '시장 데이터를 받아 전달하는 프로그램이 사용하는 주소입니다.'],
    STAFF_ENDPOINT: ['시장 데이터 연결 주소', '다른 감시 프로그램이 시장 데이터를 받을 때 사용하는 주소입니다.'],
    MANAGER_ALERT_ENDPOINT: ['알림 전달 연결 주소', '감시 결과를 알림 담당 프로그램에 전달하는 주소입니다.'],
    ZMQ_TIMEOUT_MS: ['내부 통신 대기 시간(밀리초)', '프로그램 간 응답을 기다리는 시간입니다. 1,000밀리초는 1초입니다.'],
    OZ_COMMAND_FILE: ['감시 명령 저장 파일', '공용 감시 명령을 전달하는 파일입니다. 프로젝트 안의 상대 위치를 입력합니다.'],
    STAFF_PIPE_NAME: ['MT5 데이터 연결 통로', 'MT5와 데이터 수신 프로그램이 함께 사용하는 Windows 연결 통로 이름입니다.'],
    STAFF_BARS: ['보관할 과거 봉 개수', '데이터 수신 프로그램에서 보관할 봉 개수입니다.'],
    STAFF_STALE_SEC: ['데이터 지연 판정 시간(초)', '이 시간 동안 새 데이터가 없으면 수신 상태를 지연으로 판단합니다.'],
    SYMBOLS: ['기본 종목 목록', '라이브 감시·텔레그램 명령·상태 표시에서 함께 참고할 종목입니다. 여러 종목은 쉼표로 구분합니다. MT5 수신 종목을 제한하지 않습니다.'],
    STAFF_ALLOWED_TIMEFRAMES: ['참고 시간봉 목록', '예: 1m, 15m, 1h. 현재 라이브 수신 시간봉을 제한하는 항목은 아닙니다.'],
    WONBI_SIGMA: ['원비 밴드 폭 배수', '원비 밴드 폭을 정하는 배수입니다. 기간과 가격 기준은 기존 방식대로 유지됩니다.'],
    ASIA: ['아시아 장 시간', '시간대 판독 범위입니다. 예: 0900-1800은 09:00부터 18:00까지입니다.'],
    LONDON: ['런던 장 시간', '런던 장으로 판독할 시간 범위입니다. 네 자리 시각 두 개를 하이픈으로 구분합니다.'],
    NEWYORK: ['뉴욕 장 시간', '뉴욕 장으로 판독할 시간 범위입니다. 네 자리 시각 두 개를 하이픈으로 구분합니다.'],
    MAIN_ASIA: ['아시아 주요 거래 시간', '아시아 주요 시간 필터에 사용할 범위입니다. 예: 0900-1200.'],
    MAIN_LONDON: ['런던 주요 거래 시간', '런던 주요 시간 필터에 사용할 범위입니다. 예: 1600-1900.'],
    MAIN_NEWYORK: ['뉴욕 주요 거래 시간', '뉴욕 주요 시간 필터에 사용할 범위입니다. 예: 2200-2400.'],
    OPENING_ASIA: ['아시아 장 초반 시간', '아시아 장 초반 필터에 사용할 시간 범위입니다.'],
    OPENING_LONDON: ['런던 장 초반 시간', '런던 장 초반 필터에 사용할 시간 범위입니다.'],
    OPENING_NEWYORK: ['뉴욕 장 초반 시간', '뉴욕 장 초반 필터에 사용할 시간 범위입니다.'],
    CONFIG_RELOAD_SEC: ['전략 설정 변경 확인 간격(초)', '전략 설정 파일의 변경 여부를 확인하는 간격입니다.'],
    COMPOSER_POLL_SEC: ['감시 조건 확인 간격(초)', '감시 조건을 주기적으로 확인할 때 사용하는 간격입니다.'],
    ENGINE_SUBSCRIPTION_REFRESH_SEC: ['감시 목록 갱신 간격(초)', '공용 감시 목록을 다시 맞추는 간격입니다.'],
    ECONOMY_ENABLED: ['경제지표 알림 사용', '경제지표 발표 알림을 사용할지 선택합니다.'],
    ECONOMY_FETCH_SEC: ['경제지표 일정 갱신 간격(초)', '경제지표 발표 일정을 다시 가져오는 간격입니다.'],
    ECONOMY_POLL_SEC: ['경제지표 발표 확인 간격(초)', '경제지표 발표와 알림 대상 여부를 확인하는 간격입니다.'],
    ECONOMY_ALERTED_FILE: ['경제지표 알림 이력 파일', '이미 보낸 알림의 기록을 저장하는 파일입니다. 프로젝트 안의 상대 위치를 입력합니다.'],
    ECONOMY_BRIEFING_FILE: ['경제지표 요약 저장 파일', '경제지표 요약을 저장하는 파일입니다. 프로젝트 안의 상대 위치를 입력합니다.'],
    cores: ['동시에 사용할 작업 수', '비워 두면 이 컴퓨터의 물리 코어 수로 자동 설정합니다. 직접 조정할 때만 1~256을 입력하세요.'],
    overlap_trading_days: ['앞 구간을 함께 읽을 기간(거래일)', '구간 시작 전 데이터를 추가로 읽는 기간입니다. 주말을 제외한 0~30일을 입력합니다.'],
    work_size: ['백테스트 작업 구간 크기', '자동은 기간과 작업 수에 맞춰 월·주·일 단위로 나눕니다. 데이터 구축의 저장 조각 크기는 변경하지 않습니다.'],
    capture_start: ['녹화 데이터 읽기 시작 위치', '가까운 복원 지점부터 읽거나, 녹화 파일 처음부터 읽도록 선택합니다.'],
    oz_evaluation: ['최종 신호를 확인할 시간봉 범위', '선택한 시간봉만 확인할지, 모든 시간봉을 확인할지 선택합니다.'],
    broker_symbols: ['프로그램과 MT5의 종목명 연결', '같은 종목의 이름이 다를 때만 등록합니다. 목록에 없으면 같은 이름을 사용합니다.'],
    warehouse: ['데이터 보관 폴더', '녹화 데이터와 백테스트 결과를 보관할 폴더입니다.'],
    python_executable: ['실행에 사용할 파이썬 프로그램', '필요할 때만 실행 파일을 지정합니다. 비워 두면 기존 자동 선택 방식을 사용합니다.'],
    provider: ['AI 실행방식', '사용할 AI를 선택하세요. 사용 안 함은 모든 AI 호출을 중지합니다. 저장하면 다음 요청부터 적용됩니다.'],
    model: ['Ollama 모델', '이 컴퓨터에 설치된 Ollama 모델을 선택합니다. 모델명은 설치된 이름 그대로 사용합니다.'],
    gemini_model: ['Gemini 모델', '사용할 Gemini 모델명을 입력합니다. 라이브 감시 명령과 AI 전략연구에 같은 모델을 사용합니다.'],
    gemini_api_key: ['Gemini 연결 키', '새 키를 입력하면 기존 키를 교체합니다. 저장 전에 연결을 확인하며, 저장한 키는 화면에 다시 표시하지 않습니다. 삭제만 하려면 입력란을 비우고 저장값 삭제를 선택하세요.'],
    watch_enabled: ['WATCH 명령 AI 보조 해석', '켜면 기존 라이브 명령 해석으로 처리하지 못한 문장을 아래에서 선택한 공통 AI로 보조 해석합니다. AI 전략연구의 사용 여부에는 영향을 주지 않습니다.'],
    base_model: ['LoRA의 기본 모델', '학습할 때 사용한 기본 모델 이름 또는 프로젝트 안의 모델 폴더를 입력합니다.'],
    adapter_path: ['LoRA 학습 파일 폴더', '이 프로젝트 안의 상대 위치를 입력합니다. 다른 컴퓨터로 옮길 때 이 폴더도 함께 복사하세요.'],
    load_in_4bit: ['메모리를 줄이는 4비트 로딩', '켜면 모델을 4비트로 로드합니다. 지원하는 GPU와 관련 패키지가 필요합니다.'],
    max_new_tokens: ['응답 최대 길이(토큰)', '생성할 응답의 최대 길이입니다. 양의 정수를 입력하거나 비워 두면 실행 환경의 기본 제한을 사용합니다.'],
    gguf_model_path: ['GGUF 모델 파일', '이 프로젝트 안의 GGUF 파일 위치를 입력합니다. 다른 컴퓨터로 옮길 때 이 파일도 함께 복사하세요.'],
    llama_server_path: ['GGUF AI 실행기(변경할 때만)', '프로젝트에 포함된 runtime/llama.cpp/llama-server.exe를 기본으로 사용합니다. 다른 실행기를 사용할 때만 프로젝트 안의 상대 경로를 지정하세요. 비우면 기본 실행기로 돌아갑니다.'],
    gguf_context_size: ['대화 기억 공간(토큰)', 'AI가 한 번에 읽을 수 있는 대화와 전략 계약의 길이입니다. 기본값은 16384이며, 크게 설정할수록 메모리가 더 필요합니다.'],
    gguf_gpu_layers: ['GPU로 처리할 모델 층 수', '0이면 CPU로 실행합니다. 양의 정수는 지정한 층 수만큼, -1은 가능한 모든 층을 GPU로 처리합니다. GPU를 지원하는 실행기가 필요합니다.'],
    gguf_threads: ['CPU 작업 스레드 수', '양의 정수를 입력하거나 비워 두면 실행기가 자동으로 선택합니다.'],
    gguf_chat_template_path: ['대화 형식 파일(선택)', '모델에 들어 있는 대화 형식을 기본으로 사용합니다. 따로 준비한 형식이 있을 때만 프로젝트 안의 파일 위치를 입력하세요.'],
    base_url: ['AI 프로그램 연결 주소', '이 컴퓨터에서 실행 중인 Ollama의 연결 주소입니다.'],
    timeout: ['AI 응답 대기 시간(초)', 'AI 응답을 기다리는 최대 시간입니다. 10~300초를 입력합니다.']
};
const moLiveSettingGroups = [
    {id: 'monitoring', title: '라이브 감시', help: '개인 명령과 화면에서 참고할 기본 종목을 설정합니다.',
        keys: ['SYMBOLS']},
    {id: 'telegram', title: '텔레그램 알림', help: '값을 입력한 뒤 각 항목 옆의 확인을 눌러 연결을 확인하고 저장하세요. 봇 연결 키를 먼저 확인한 뒤 채팅방을 설정합니다. 저장된 값은 표시하지 않습니다.',
        keys: ['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_COMMAND_CHAT_IDS']},
    {id: 'economy', title: '경제지표 알림', help: '경제지표 발표 알림의 사용 여부를 선택합니다.', keys: ['ECONOMY_ENABLED']},
    {id: 'connection', title: 'MT5 · 프로그램 연결', advanced: true,
        help: 'MT5 데이터 통로와 프로그램 사이의 연결 설정입니다.',
        keys: ['STAFF_PIPE_NAME', 'STAFF_BIND_ENDPOINT', 'STAFF_ENDPOINT', 'MANAGER_ALERT_ENDPOINT', 'ZMQ_TIMEOUT_MS', 'OZ_COMMAND_FILE']},
    {id: 'indicators', title: '감시 데이터 · 지표 기준', advanced: true,
        help: '과거 봉 보관, 지연 판정, 참고 시간봉과 지표 기준을 설정합니다.',
        keys: ['STAFF_BARS', 'STAFF_STALE_SEC', 'STAFF_ALLOWED_TIMEFRAMES', 'WONBI_SIGMA'], prefix: ['POINT_']},
    {id: 'sessions', title: '거래 시간대', advanced: true,
        help: '장·주요 시간·장 초반의 공통 시간 범위입니다. 시간 기준은 기존 감시 프로그램을 따릅니다.',
        keys: ['ASIA', 'LONDON', 'NEWYORK', 'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK', 'OPENING_ASIA', 'OPENING_LONDON', 'OPENING_NEWYORK']},
    {id: 'operation', title: '갱신 주기 · 알림 기록', advanced: true,
        help: '설정 확인과 경제지표 갱신 간격, 알림 기록 위치를 설정합니다.',
        keys: ['CONFIG_RELOAD_SEC', 'COMPOSER_POLL_SEC', 'ENGINE_SUBSCRIPTION_REFRESH_SEC', 'ECONOMY_FETCH_SEC', 'ECONOMY_POLL_SEC', 'ECONOMY_ALERTED_FILE', 'ECONOMY_BRIEFING_FILE']},
    {id: 'diagnostics', title: '진단 · 로그', advanced: true,
        help: '동작을 점검할 때 사용하는 진단 설정입니다.', keys: ['TRACE_ENABLED'], prefix: ['TRACE_', 'LOG_']},
    {id: 'other', title: '기타 상세 설정', advanced: true,
        help: '현재 설정 파일에 있는 추가 항목을 표시합니다.', keys: []}
];
function moCollapseSettingsDetails() {
    document.querySelectorAll('#settings-live-fields details, #settings-part2-fields details, #settings-ai-fields details').forEach(detail => {detail.open = false;});
}
function moRenderLiveSettings(parent, rows) {
    parent.replaceChildren();
    parent.append(moElement('p', 'WATCH 명령의 AI 보조 해석과 모델은 아래 공통 AI 설정에서 관리합니다.', 'moses-setting-help'));
    const groups = new Map(moLiveSettingGroups.map(group => [group.id, []]));
    for (const row of rows) {
        const group = moLiveSettingGroups.find(item => item.keys.includes(row.key) ||
            item.prefix?.some(prefix => row.key.startsWith(prefix))) || moLiveSettingGroups.at(-1);
        groups.get(group.id).push(row);
    }
    const details = moElement('details', undefined, 'moses-settings-details');
    details.id = 'settings-live-details'; details.open = false;
    const summary = moElement('summary');
    summary.append(moElement('span', '상세 설정'), moElement('small', 'MT5 연결 · 감시 기준 · 시간대 · 갱신 및 진단'));
    details.append(summary, moElement('p', '필요한 항목만 펼쳐 변경하세요. 기본 설정과 함께 아래 저장 버튼으로 저장합니다.', 'moses-setting-help'));
    for (const definition of moLiveSettingGroups) {
        const items = groups.get(definition.id);
        if (!items.length) continue;
        const section = moElement('fieldset', undefined, 'moses-settings-group');
        section.dataset.settingsGroup = definition.id;
        section.append(moElement('legend', definition.title), moElement('p', definition.help, 'moses-setting-help'));
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
        (definition.advanced ? details : parent).append(section);
    }
    if (details.querySelector('[data-setting]')) parent.append(details);
}
const moSettingChoices = {
    provider: [['gemini', 'Gemini'], ['ollama', 'Ollama'], ['local_gguf', 'GGUF'], ['local_lora', '로컬 LoRA'], ['disabled', '사용 안 함']],
    watch_enabled: [['true', '켜기'], ['false', '끄기']],
    load_in_4bit: [['false', '끄기'], ['true', '켜기']],
    work_size: [['AUTO', '자동'], ['MONTH', '한 달씩'], ['FORTNIGHT', '2주씩'], ['WEEK', '1주씩'], ['DAY', '하루씩']],
    capture_start: [['keyframe', '가까운 복원 지점부터'], ['beginning', '녹화 파일 처음부터']],
    oz_evaluation: [['selected', '선택한 시간봉만'], ['all', '모든 시간봉']]
};
function moSettingInfo(name) {
    if (name.startsWith('POINT_')) return [name.slice(6) + '의 1포인트 가격 단위', '가상 진입에서 사용하는 1포인트의 가격 크기입니다.'];
    return moSettingText[name] || ['추가 설정', '현재 저장된 값을 그대로 표시합니다.'];
}
function moSettingsError(error) {
    let text = error.message || String(error);
    for (const key of Object.keys(moSettingText).sort((a,b) => b.length-a.length))
        text = text.replace(new RegExp('\\b' + key + '\\b', 'g'), moSettingText[key][0]);
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
function moTelegramField(input, configured) {
    const name = input.dataset.setting, label = input.parentElement;
    const field = moElement('span', undefined, 'moses-telegram-field');
    const button = moElement('button', '확인', 'moses-telegram-confirm hidden'); button.type = 'button';
    button.setAttribute('aria-label', moSettingInfo(name)[0] + ' 확인');
    input.before(field); field.append(input, button);
    const status = moElement('small', undefined, 'moses-telegram-status');
    status.id = label.querySelector('.moses-setting-help').id + '-status';
    status.setAttribute('role', 'status'); field.after(status);
    input.setAttribute('aria-describedby', input.getAttribute('aria-describedby') + ' ' + status.id);
    let clear = label.querySelector('.mo-secret-clear');
    if (!clear) {
        const holder = moElement('span', undefined, 'moses-secret-clear-label');
        clear = document.createElement('input'); clear.type = 'checkbox'; clear.className = 'mo-secret-clear';
        holder.append(clear, document.createTextNode(' 저장값 삭제')); label.append(holder);
    }
    let revision = 0;
    input.dataset.configured = String(configured);
    input.placeholder = configured ? '설정됨 · 변경할 때만 입력' : '미설정';
    function feedback(message, phase) {
        status.textContent = message; status.dataset.phase = phase;
        status.setAttribute('role', phase === 'error' ? 'alert' : 'status');
        status.classList.toggle('moses-telegram-error', phase === 'error');
    }
    function refresh() {
        const pending = moTelegramRequests.has(name);
        button.disabled = pending;
        button.textContent = pending ? '확인 중' : '확인';
        button.classList.toggle('hidden', !input.value.trim() && !clear.checked && input.dataset.configured !== 'true');
        clear.parentElement.classList.toggle('hidden', input.dataset.configured !== 'true');
        if (!pending && status.dataset.phase === 'pending') feedback('', '');
    }
    input.telegramRefresh = refresh;
    function changed() {revision++; feedback('', ''); refresh();}
    input.addEventListener('input', () => {if (input.value.trim()) clear.checked = false; changed();});
    clear.addEventListener('change', changed);
    async function confirm() {
        if (moTelegramRequests.has(name)) return;
        const entered = input.value.trim(), deleting = clear.checked;
        if (!entered && !deleting && input.dataset.configured !== 'true') return;
        const tokenInput = mo('#settings-live-fields [data-setting="TELEGRAM_TOKEN"]');
        const candidateToken = tokenInput?.value.trim() || '';
        const request = {revision, value: deleting ? null : entered};
        const payload = {key: name, value: request.value};
        if (name !== 'TELEGRAM_TOKEN' && candidateToken && !tokenInput.parentElement.parentElement.querySelector('.mo-secret-clear')?.checked)
            payload.token = candidateToken;
        const privateValues = [entered, candidateToken];
        moTelegramRequests.set(name, request); refresh(); feedback(deleting ? '저장값을 삭제하는 중입니다.' : '연결을 확인하고 저장하는 중입니다.', 'pending');
        const current = () => input.isConnected && revision === request.revision && moTelegramRequests.get(name) === request;
        try {
            const result = await api('mo/settings/telegram', payload);
            if (result.ok !== true || result.key !== name || typeof result.configured !== 'boolean')
                throw Error('확인 결과를 받지 못했습니다. 다시 확인해 주세요.');
            moSettingsLoadRevision++;
            if (!current()) return;
            input.value = ''; input.dataset.original = ''; clear.checked = false;
            input.dataset.configured = String(result.configured);
            input.placeholder = result.configured ? '설정됨 · 변경할 때만 입력' : '미설정';
            const row = moState.settings?.live?.find(row => row.key === name);
            if (row) row.configured = result.configured;
            feedback(result.message || (result.configured ? '확인하고 저장했습니다.' : '저장값을 삭제했습니다.'), 'success');
        } catch (error) {
            if (current()) feedback(moTelegramError(error, privateValues), 'error');
        } finally {
            if (moTelegramRequests.get(name) === request) moTelegramRequests.delete(name);
            refresh();
            mo(`#settings-live-fields [data-setting="${name}"]`)?.telegramRefresh?.();
        }
    }
    button.addEventListener('click', confirm);
    input.addEventListener('keydown', event => {if (event.key === 'Enter') {event.preventDefault(); confirm();}});
    refresh();
}
function moTelegramDrafts() {
    return Array.from(document.querySelectorAll('#settings-live-fields [data-setting]')).filter(input => moTelegramKeys.has(input.dataset.setting))
        .map(input => ({key: input.dataset.setting, value: input.value,
            deleting: !!input.parentElement.parentElement.querySelector('.mo-secret-clear')?.checked}));
}
function moField(parent, name, value, type = 'text', secret = false) {
    const [title, hint] = moSettingInfo(name);
    const label = moElement('label', title, secret ? 'secret' : undefined);
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
        if (name === 'cores') {input.min = '1'; input.max = '256'; input.step = '1'; input.placeholder = '비워 두면 자동 선택';}
        if (name === 'overlap_trading_days') {input.min = '0'; input.max = '30'; input.step = '1';}
        if (name === 'timeout') {input.min = '10'; input.max = '300'; input.step = '1';}
        if (name === 'max_new_tokens') {input.type = 'number'; input.min = '1'; input.step = '1'; input.placeholder = '비워 두면 기본 제한 사용';}
        if (['gguf_context_size', 'gguf_threads'].includes(name)) {input.type = 'number'; input.min = '1'; input.step = '1';}
        if (name === 'gguf_threads') input.placeholder = '비워 두면 자동 선택';
        if (name === 'gguf_gpu_layers') {input.type = 'number'; input.min = '-1'; input.step = '1';}
    }
    input.value = raw;
    if (secret) { input.value = ''; input.placeholder = value ? '•••••••• · 변경할 때만 입력' : '미설정'; }
    label.append(input);
    if (choices && !secret) moSelectArrow(input);
    const help = moElement('small', hint, 'moses-setting-help');
    help.id = parent.id + '-' + name.replace(/[^\w-]/g, '-') + '-help';
    input.setAttribute('aria-describedby', help.id); label.append(help);
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
    box.setAttribute('aria-label', moSettingText.broker_symbols[0]);
    box.append(moElement('h3', moSettingText.broker_symbols[0]), moElement('p', moSettingText.broker_symbols[1], 'moses-setting-help'));
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
async function moSettingsLoad() {
    const revision = ++moSettingsLoadRevision;
    const data = await api('mo/settings');
    if (revision !== moSettingsLoadRevision) return;
    const telegramDrafts = moTelegramDrafts(); moState.settings = data;
    mo('#settings-version').textContent = Number.isInteger(data.app_revision) && data.app_revision > 0
        ? `버전 ${data.app_revision}` : '버전 정보 없음';
    moRenderLiveSettings(mo('#settings-live-fields'), data.live);
    for (const draft of telegramDrafts) {
        const input = mo(`#settings-live-fields [data-setting="${draft.key}"]`);
        if (!input) continue;
        input.value = draft.value;
        input.parentElement.parentElement.querySelector('.mo-secret-clear').checked = draft.deleting && input.dataset.configured === 'true';
        input.telegramRefresh();
    }
    const part2 = mo('#settings-part2-fields'); part2.replaceChildren();
    const part2Details = moElement('details', undefined, 'moses-settings-details');
    part2Details.id = 'settings-part2-details'; part2Details.open = false;
    part2Details.append(moElement('summary', '상세 설정'), moElement('p', '동시 작업 수와 작업 구간은 기본적으로 자동입니다. 직접 조정할 때만 펼쳐 변경하세요.', 'moses-setting-help'));
    const part2Advanced = moElement('div', undefined, 'moses-settings-grid');
    part2Advanced.id = 'settings-part2-advanced-fields'; part2Details.append(part2Advanced);
    for (const [key, value] of Object.entries(data.part2)) {
        if (key === 'warehouse') continue;
        const target = ['cores', 'work_size'].includes(key) ? part2Advanced : part2;
        const input = key === 'broker_symbols' ? moSymbolMappingField(part2, value) :
            moField(target, key, typeof value === 'object' && value !== null ? JSON.stringify(value) : value ?? '');
        input.dataset.original = input.value;
    }
    if (part2Advanced.querySelector('[data-setting]')) part2.append(part2Details);
    const connections = mo('#settings-connection-fields'); connections.replaceChildren();
    for (const key of ['warehouse', 'live_records', 'python_executable']) {
        const input = moField(connections, key, data.connections[key]); input.dataset.original = input.value;
    }
    await moRenderAISettings(mo('#settings-ai-fields'), data.ai || {});
}
async function moRenderAISettings(ai, settings) {
    ai.replaceChildren();
    const intro = ai.parentElement.querySelector('p.moses-setting-help');
    if (intro) intro.textContent = '라이브 감시 명령과 AI 전략연구가 공유할 AI 실행 방식과 모델을 선택하세요. 저장하면 현재 AI 대화가 초기화되고 다음 요청부터 적용됩니다.';
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
    model.dataset.original = model.value; model.placeholder = '예: qwen3.5:4b';
    const gemini = modelSlot('ai-gemini-fields');
    const geminiModel = moField(gemini, 'gemini_model', settings.gemini_model);
    geminiModel.dataset.original = geminiModel.value;
    const gguf = modelSlot('ai-local-gguf-fields');
    const ggufModel = moField(gguf, 'gguf_model_path', settings.gguf_model_path);
    ggufModel.dataset.original = ggufModel.value; ggufModel.placeholder = '예: models/gguf/my_strategy.gguf';
    const lora = modelSlot('ai-lora-model-fields');
    const baseModel = moField(lora, 'base_model', settings.base_model);
    baseModel.dataset.original = baseModel.value; baseModel.placeholder = '예: Qwen/Qwen3.5-4B';
    const credential = modelSlot('ai-credential-fields');
    const keyConfigured = settings.gemini_api_key_configured === true;
    const geminiKey = moField(credential, 'gemini_api_key', keyConfigured, 'password', true);
    geminiKey.dataset.original = ''; geminiKey.dataset.configured = String(keyConfigured);
    geminiKey.autocomplete = 'new-password';
    const geminiNote = moElement('p', 'Gemini를 선택하면 입력한 명령과 전략 내용이 외부 AI 서비스로 전달됩니다. 다른 실행 방식에서 오류가 나도 Gemini로 자동 전환하지 않습니다.', 'moses-setting-help ai-provider-note');
    ai.append(geminiNote);
    const disabledNote = moElement('p', 'AI 사용 안 함: 라이브 감시 명령의 AI 보조 해석과 AI 전략연구의 AI 요청을 보내지 않습니다.', 'moses-setting-help ai-provider-note');
    disabledNote.id = 'ai-disabled-status'; disabledNote.setAttribute('role', 'status'); ai.append(disabledNote);
    const common = moElement('div', undefined, 'moses-settings-grid ai-provider-fields');
    common.id = 'ai-common-fields'; ai.append(common);
    const watch = moField(common, 'watch_enabled', settings.watch_enabled ?? true);
    watch.dataset.original = watch.value;
    const timeout = moField(common, 'timeout', settings.timeout ?? 90, 'number'); timeout.dataset.original = timeout.value;
    const ollamaOptions = moElement('div', undefined, 'moses-settings-grid ai-provider-fields');
    ollamaOptions.id = 'ai-ollama-advanced-fields'; ai.append(ollamaOptions);
    const endpoint = moField(ollamaOptions, 'base_url', settings.base_url); endpoint.readOnly = true;
    const local = moElement('div', undefined, 'moses-settings-grid ai-provider-fields');
    local.id = 'ai-local-lora-fields'; ai.append(local);
    for (const key of ['adapter_path', 'load_in_4bit']) {
        const input = moField(local, key, key === 'load_in_4bit' ? settings[key] === true : settings[key]);
        input.dataset.original = input.value;
        if (key === 'adapter_path') input.placeholder = '예: models/my_strategy_lora';
    }
    local.append(moElement('p', '기본 모델과 LoRA 학습 파일은 자동으로 선택하지 않습니다. 모델은 처음 사용할 때 로드하고 재사용하며, 모델 전환 시 기존 모델을 해제합니다.', 'moses-setting-help ai-provider-note'));
    const ggufGroup = moElement('fieldset', undefined, 'moses-settings-group ai-provider-fields');
    ggufGroup.id = 'ai-gguf-advanced-group';
    ggufGroup.append(moElement('legend', 'GGUF 실행기 · 처리 방식'));
    const advanced = moElement('div', undefined, 'moses-settings-grid'); advanced.id = 'ai-gguf-advanced-fields';
    ggufGroup.append(advanced); ai.append(ggufGroup);
    const ggufServer = moField(advanced, 'llama_server_path', settings.llama_server_path);
    ggufServer.dataset.original = ggufServer.value; ggufServer.required = false;
    ggufServer.placeholder = '기본: runtime/llama.cpp/llama-server.exe';
    for (const key of ['gguf_context_size', 'gguf_gpu_layers', 'gguf_threads', 'gguf_chat_template_path']) {
        const fallback = key === 'gguf_context_size' ? 16384 : key === 'gguf_gpu_layers' ? 0 : null;
        const input = moField(advanced, key, settings[key] ?? fallback); input.dataset.original = input.value;
        if (key === 'gguf_chat_template_path') input.placeholder = '비워 두면 모델의 대화 형식 사용';
    }
    advanced.append(moElement('p', '모델 목록은 폴더에서 읽기만 하며 모델을 로드하지 않습니다. 저장하면 다음 AI 요청부터 선택한 모델 하나만 준비하고 재사용합니다.', 'moses-setting-help ai-provider-note'));
    const maximum = moField(local, 'max_new_tokens', settings.max_new_tokens);
    maximum.dataset.original = maximum.value;
    let choicesReady = false, ggufChoicesReady = false, geminiChoicesReady = false;
    async function displayProvider() {
        const active = moSelectedAIProvider(ai);
        const isOllama = active === 'ollama', isLoRA = active === 'local_lora', isGGUF = active === 'local_gguf', isGemini = active === 'gemini', isDisabled = active === 'disabled';
        ollama.classList.toggle('hidden', !isOllama); gemini.classList.toggle('hidden', !isGemini);
        gguf.classList.toggle('hidden', !isGGUF); lora.classList.toggle('hidden', !isLoRA);
        credential.classList.toggle('hidden', !isGemini); geminiNote.classList.toggle('hidden', !isGemini);
        local.classList.toggle('hidden', !isLoRA); ggufGroup.classList.toggle('hidden', !isGGUF);
        ollamaOptions.classList.toggle('hidden', !isOllama); common.classList.toggle('hidden', isDisabled);
        disabledNote.classList.toggle('hidden', !isDisabled);
        model.required = isOllama; baseModel.required = isLoRA; ggufModel.required = isGGUF;
        geminiModel.required = isGemini; geminiKey.required = isGemini && !keyConfigured;
        local.querySelector('[data-setting="adapter_path"]').required = isLoRA;
        (isGGUF ? advanced : local).append(maximum.parentElement);
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
    provider.addEventListener('change', () => displayProvider().catch(error => moMessage(error.message)));
    await displayProvider();
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
            select.replaceChildren();
            status.textContent = 'API 키를 입력하면 모델 목록을 불러옵니다. 모델명을 직접 입력할 수도 있습니다.';
            return;
        }
        try {
            const result = await api('ai/' + provider + '-models', enteredKey ? {[provider + '_api_key']: enteredKey} : undefined);
            if (current !== revision || !field.isConnected) return;
            const names = Array.isArray(result.models) ? result.models.filter(name => typeof name === 'string' && name) : [];
            select.replaceChildren();
            if (result.available) names.forEach(name => select.append(new Option(name, name)));
            select.value = model.value; select.disabled = !select.options.length;
            status.textContent = select.disabled ? (result.error || '사용 가능한 모델이 없습니다. 모델명을 직접 입력하세요.') :
                '모델명을 클릭해 직접 수정하거나 오른쪽 화살표로 선택하세요. 저장하면 적용됩니다.';
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
            status.textContent = models.length ? `모델 ${models.length}개 · 직접 수정하거나 화살표로 선택한 뒤 저장하세요.` :
                (result.error || '사용 가능한 모델이 없습니다. 모델명 또는 파일 위치를 직접 입력하세요.');
            if (model.value && !models.some(item => item.path === model.value))
                status.textContent += ' 현재 설정한 파일은 목록에 없습니다. 입력한 값을 확인하세요.';
        } catch (_) {
            if (!parent.isConnected) return;
            choices([]); status.textContent = '모델 목록 조회에 실패했습니다. 모델명 또는 파일 위치를 직접 입력하세요.';
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
        if (group === 'live' && moTelegramKeys.has(input.dataset.setting)) return;
        if (group === 'ai' && input.dataset.setting === 'provider') return;
        const replacingAIKey = group === 'ai' && input.dataset.setting === 'gemini_api_key' && input.value.trim() !== '';
        if (input.parentElement.querySelector('.mo-secret-clear')?.checked && !replacingAIKey) {
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
            if (['load_in_4bit', 'watch_enabled'].includes(input.dataset.setting)) value = value === 'true';
        }
        changes[input.dataset.setting] = value;
    });
    if (group === 'ai') {
        const provider = moSelectedAIProvider(folder);
        if (provider !== folder.querySelector('[data-setting="provider"]').dataset.original) changes.provider = provider;
    }
    if (!Object.keys(changes).length) { moMessage('변경한 값이 없습니다.'); return; }
    if (group === 'ai') {
        const read = key => key === 'provider' ? moSelectedAIProvider(folder) : folder.querySelector(`[data-setting="${key}"]`).value.trim();
        if (read('provider') === 'local_lora') {
            if (!read('base_model')) throw Error('LoRA의 기본 모델을 입력하세요.');
            const adapter = read('adapter_path');
            if (!adapter) throw Error('LoRA 학습 파일 폴더를 입력하세요.');
            if (/^(?:[a-z]:|[\\/])/i.test(adapter) || adapter.split(/[\\/]/).includes('..'))
                throw Error('LoRA 학습 파일 폴더는 프로젝트 안의 상대 위치로 입력하세요.');
            const maximum = read('max_new_tokens');
            if (maximum && (!Number.isSafeInteger(Number(maximum)) || Number(maximum) <= 0))
                throw Error('응답 최대 길이는 양의 정수를 입력하거나 비워 두세요.');
        } else if (read('provider') === 'local_gguf') {
            for (const key of ['gguf_model_path', 'llama_server_path', 'gguf_chat_template_path']) {
                const path = read(key), name = moSettingInfo(key)[0];
                if (!path && key === 'gguf_model_path') throw Error(name + ' 위치를 입력하세요.');
                if (path && (/^(?:[a-z]:|[\\/])/i.test(path) || path.split(/[\\/]/).includes('..')))
                    throw Error(name + '은 프로젝트 안의 상대 위치로 입력하세요.');
            }
            for (const key of ['gguf_context_size', 'gguf_threads', 'max_new_tokens']) {
                const value = read(key);
                if ((!value && key === 'gguf_context_size') || (value && (!Number.isSafeInteger(Number(value)) || Number(value) <= 0)))
                    throw Error(moSettingInfo(key)[0] + '은 양의 정수를 입력하세요.');
            }
            const layers = read('gguf_gpu_layers');
            if (!layers || !Number.isSafeInteger(Number(layers)) || Number(layers) < -1)
                throw Error('GPU로 처리할 모델 층 수는 -1 또는 0 이상의 정수를 입력하세요.');
        } else if (read('provider') === 'gemini') {
            if (!read('gemini_model')) throw Error('Gemini 모델명을 입력하세요.');
            const key = folder.querySelector('[data-setting="gemini_api_key"]');
            if (!read('gemini_api_key') && key.dataset.configured !== 'true' && changes.gemini_api_key !== null)
                throw Error('Gemini 연결 키를 입력하세요.');
        } else if (read('provider') === 'ollama' && !read('model')) throw Error('Ollama 모델명을 입력하세요.');
    }
    let result;
    try { result = await api('mo/settings', { group, changes }); }
    catch (error) { throw moSettingsError(error); }
    moMessage(result.message); await moSettingsLoad();
    if (group === 'ai') window.dispatchEvent(new Event('part3-ai-settings-changed'));
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
mo('#strategy-editor-default').onclick = () => moStrategyEditor?.reset();
mo('#strategy-editor-save').onclick = () => {
    try { moStrategyEditor?.save(); moStrategyPopupRender(); moCloseStrategyEditor(); }
    catch (error) {
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
    button.onclick = () => moSettingsSave(button.dataset.saveSettings).catch(e => moMessage(e.message, true)));
setInterval(() => {
    if (moState.view === 'live') moLiveStatus().catch(e => {
        mo('#live-message').textContent = '라이브 감시 상태 확인 대기';
        mo('#live-log-error').textContent = e.message;
        mo('#live-log-error').classList.remove('hidden');
    });
}, 1500);
moView('live');
