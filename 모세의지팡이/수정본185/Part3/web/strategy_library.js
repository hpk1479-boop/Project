"use strict";
/* Generated strategy actions live inside the shared strategy list dialog. */
(() => {
    const select = selector => document.querySelector(selector);
    let selected = null, busy = false, researchBusy = false, renameId = null;
    window.mosesStrategyListBusy = () => busy;
    function message(text, error = false) {
        const output = select('#strategy-library-message');
        output.textContent = text || ''; output.classList.toggle('error', error);
    }
    function controls() {
        const generated = selected?.source === 'generated';
        select('#strategy-library-actions').classList.toggle('hidden', !generated);
        for (const id of ['strategy-library-promote', 'strategy-library-rename', 'strategy-library-delete'])
            select('#' + id).disabled = busy || researchBusy || !generated;
        for (const id of ['strategy-library-rename-save', 'strategy-library-rename-cancel', 'strategy-library-rename-close', 'strategy-library-name'])
            select('#' + id).disabled = busy;
        select('#ai-preset-load').disabled = busy || researchBusy || !selected;
        select('#ai-preset-list').querySelectorAll('button').forEach(button => { button.disabled = busy || researchBusy; });
        for (const id of ['ai-preset-open', 'ai-preset-close', 'ai-preset-cancel']) select('#' + id).disabled = busy || researchBusy;
    }
    window.addEventListener('moses-strategy-list-selected', event => {selected = event.detail?.item || null; controls();});
    window.addEventListener('moses-strategy-list-busy', event => {researchBusy = !!event.detail?.busy; controls();});
    async function openHost() {
        window.mosesCloseStrategyList(true);
        if (moStrategyPopup.mode) moCloseStrategyPopup();
        await moView('live'); await moOpenStrategyPopup('live');
    }
    async function mutate(payload, successText, promote = false) {
        if (busy || researchBusy) return false;
        busy = true; controls(); message('변경 내용을 저장하는 중입니다.');
        try {
            const result = await api('strategies', payload);
            const updated = result.items?.find(item => item.id === selected?.id);
            selected = updated ? {...selected, ...updated, source: 'generated'} : null;
            message(successText + ((result.errors || []).length ? '\n' + result.errors.join('\n') : ''), !!result.errors?.length);
            window.dispatchEvent(new CustomEvent('moses-strategies-changed', {detail: {library: result}}));
            if (promote) {toast(successText); await openHost();}
            return true;
        } catch (error) {
            message(error.message, true);
            if (payload.action === 'rename') select('#strategy-library-rename-error').textContent = error.message;
            return false;
        } finally {busy = false; controls();}
    }
    select('#strategy-library-promote').addEventListener('click', async () => {
        if (selected?.source !== 'generated') return;
        await mutate({action: 'promote', ids: [selected.id]},
            '파트1·파트2에 등록했습니다. 각 전략설정에서 사용할 전략을 선택해 주세요.', true);
    });
    select('#strategy-library-delete').addEventListener('click', async () => {
        if (selected?.source !== 'generated') return;
        const item = selected, registered = (item.registrations || []).filter(part => ['Part1', 'Part2'].includes(part));
        const warning = registered.length ? '\n\n이 전략은 파트1 또는 파트2에 등록되어 있습니다. 삭제하면 파트1·파트2 등록과 파트3 전략 원본이 함께 삭제됩니다.' : '\n\n파트3의 전략 원본을 삭제합니다.';
        if (!window.confirm(item.name + warning + '\n복구할 수 없습니다. 삭제하시겠습니까?')) return;
        await mutate({action: 'delete', ids: [item.id], confirmed: true}, '선택한 전략을 삭제했습니다.');
    });
    function closeRename() {
        if (busy) return;
        renameId = null; select('#strategy-library-rename-dialog').close();
    }
    select('#strategy-library-rename').addEventListener('click', () => {
        if (selected?.source !== 'generated' || busy || researchBusy) return;
        renameId = selected.id; select('#strategy-library-name').value = selected.name || '';
        select('#strategy-library-rename-error').textContent = '';
        select('#strategy-library-rename-dialog').showModal();
        select('#strategy-library-name').focus(); select('#strategy-library-name').select();
    });
    select('#strategy-library-rename-form').addEventListener('submit', async event => {
        event.preventDefault();
        const name = select('#strategy-library-name').value.trim();
        if (!name || !renameId || busy || researchBusy) return;
        if (await mutate({action: 'rename', id: renameId, name}, '전략 이름을 변경했습니다.')) closeRename();
    });
    for (const id of ['strategy-library-rename-cancel', 'strategy-library-rename-close']) select('#' + id).addEventListener('click', closeRename);
    select('#strategy-library-rename-dialog').addEventListener('cancel', event => {event.preventDefault(); closeRename();});
    select('#ai-preset-dialog').addEventListener('cancel', event => {if (busy) event.preventDefault();});
    controls();
})();
