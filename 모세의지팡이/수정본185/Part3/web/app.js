'use strict';
// AI intent is interpreted by the model; only the trusted Part3 server creates Recipe and code.
// The existing unified live/backtest views consume the same api() transport below.
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.get('token')) {
    sessionStorage.setItem('part3-token', fragment.get('token'));
    history.replaceState(null, '', location.pathname);
}
const token = sessionStorage.getItem('part3-token') || '';
const state = {recipe: null, code: '', meta: null, lastGenerated: null, previewMode: 'code', valid: false, busy: false};
let toastTimer = null, modalApply = null;
async function api(path, data) {
    const response = await fetch('/api/' + path, {
        method: data === undefined ? 'GET' : 'POST',
        headers: {'X-Lab-Token': token, 'Content-Type': 'application/json'},
        body: data === undefined ? undefined : JSON.stringify(data)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || '요청에 실패했습니다.');
    return body;
}
function log(message, error = false) {
    const entry = document.createElement('div');
    entry.className = 'log-line' + (error ? ' error' : '');
    entry.textContent = '[' + new Date().toLocaleTimeString('ko-KR', {hour12: false}) + '] ' + message;
    $('#log').append(entry);
    while ($('#log').children.length > 100) $('#log').firstChild.remove();
    $('#log').scrollTop = $('#log').scrollHeight;
}
function toast(message) {
    $('#toast').textContent = message;
    $('#toast').classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('#toast').classList.remove('show'), 3500);
}
function fail(error) { const message = error.message || String(error); log(message, true); toast(message); }
function showPreview() {
    const output = $('#code-preview');
    output.textContent = state.previewMode === 'summary' && state.recipe
        ? JSON.stringify(state.recipe, null, 2)
        : state.code;
}
async function updatePreview() {
    if (!state.recipe) return;
    state.valid = false;
    $('#generate-button').disabled = true;
    $('#preview-status').textContent = 'Part3가 전략을 검증하고 있습니다.';
    try {
        const response = await api('preview', {recipe: state.recipe});
        state.code = response.code;
        state.valid = true;
        $('#preview-status').textContent = (state.recipe.name || '새 전략') + ' · 생성 준비 완료';
        $('#preview-status').classList.remove('error');
        $('#generate-button').disabled = false;
        showPreview();
        await api('draft', {recipe: state.recipe});
    } catch (error) {
        $('#preview-status').textContent = error.message;
        $('#preview-status').classList.add('error');
        throw error;
    }
}
window.part3ApplyAIRecipe = async function (recipe) {
    state.recipe = recipe;
    state.lastGenerated = null;
    await updatePreview();
    log('확인한 AI 전략 의도를 Part3가 검증했습니다. 파일은 아직 생성하지 않았습니다.');
};
window.part3InvalidateAIRecipe = function () {
    state.recipe = null;
    state.code = '';
    state.valid = false;
    state.lastGenerated = null;
    $('#generate-button').disabled = true;
    $('#preview-status').textContent = '최신 해석을 확인하고 적용하면 미리보기가 표시됩니다.';
    $('#preview-status').classList.remove('error');
    showPreview();
};
function modal(title, html, apply = null) {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = html;
    modalApply = apply;
    $('#modal-save').classList.toggle('hidden', !apply);
    if (!$('#modal').open) $('#modal').showModal();
}
function closeModal() {
    $('#modal').close(); modalApply = null;
}
async function generate() {
    if (state.busy || !state.recipe || !state.valid) return;
    state.busy = true;
    $('#generate-button').disabled = true;
    try {
        const result = await api('generate', {recipe: state.recipe});
        state.code = result.code;
        state.lastGenerated = result.filename;
        window.dispatchEvent(new Event('moses-strategies-changed'));
        showPreview();
        const strategyName = state.recipe.name || '새 전략';
        log(strategyName + ' 생성 완료 · 테스트 전략 목록에 보관했습니다.' + (result.warning ? ' · ' + result.warning : ''));
        toast(strategyName + ' 생성 완료 · 메인전략 승급 후 라이브·백테스트에 등록됩니다.');
        return result;
    } finally {
        state.busy = false;
        $('#generate-button').disabled = false;
    }
}
window.part3GenerateAIRecipe = async function (recipe) {
    await window.part3ApplyAIRecipe(recipe);
    return await generate();
};
document.addEventListener('click', async event => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    try {
        switch (button.dataset.action) {
            case 'generate': await generate(); break;
            case 'preview-tab':
                state.previewMode = button.dataset.mode;
                $$('.preview-tabs button').forEach(item => item.classList.toggle('active', item === button));
                showPreview();
                break;
            case 'full-code': modal('생성될 Python 코드', `<pre class="large-code">${esc(state.code)}</pre>`); break;
            case 'reopen': {
                const result = await api('reopen', {filename: button.dataset.filename});
                // Existing strategies are view-only here; manual editing does not return via AI UI.
                state.recipe = null;
                state.code = result.code;
                state.lastGenerated = result.filename;
                state.valid = false;
                $('#generate-button').disabled = true;
                $('#preview-status').textContent = result.filename + ' · 기존 생성 파일 보기';
                showPreview();
                break;
            }
            case 'clear-log': $('#log').textContent = ''; break;
            case 'close-modal': closeModal(); break;
        }
    } catch (error) { fail(error); }
});
$('#modal-save').addEventListener('click', async () => {
    try { if (modalApply) await modalApply(); closeModal(); } catch (error) { fail(error); }
});
async function init() {
    try {
        state.meta = await api('init');
        if (state.meta.draft_warning) log(state.meta.draft_warning, true);
        log('AI 전략연구 화면을 시작했습니다.');
    } catch (error) { $('#preview-status').textContent = error.message; fail(error); }
}
init();
