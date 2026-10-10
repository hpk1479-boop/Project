'use strict';
/* One job view for button, generated strategy, and AI command entry points. */
(() => {
    const node = id => document.getElementById(id);
    const phases = { planning: '계획 확인 중', plan: '계획 확인 중', starting: '시작 중',
        run: '실행 중', confirm: '구축 승인 대기', planned: '계획 조회 완료',
        complete: '완료', cancelled: '중단됨', interrupted: '관리 연결 중단', error: '오류' };
    const terminal = new Set(['complete', 'cancelled', 'error', 'interrupted', 'planned']);
    let recentPending = null;
    const selected = new Set(), checks = new Map();
    let displayed = [], deleting = false, leftovers = [];
    const deletable = item => terminal.has(item.phase) && !item.active;
    function selectionButtons() {
        node('bt-jobs-delete').disabled = deleting || !selected.size;
        node('bt-jobs-select-all').disabled = deleting || !displayed.some(deletable);
        const eligible = displayed.filter(deletable);
        node('bt-jobs-select-all').textContent = eligible.length && eligible.every(item => selected.has(item.job_id || item.id))
            ? '전체해제' : '전체선택';
        node('bt-jobs-refresh').disabled = deleting;
        // Run folders left without a record or a result (수정본163): one button, not one warning each.
        const tidy = node('bt-jobs-leftovers');
        tidy.hidden = !leftovers.length;
        tidy.disabled = deleting;
        tidy.textContent = '결과 없는 폴더 ' + leftovers.length + '개 정리';
        for (const [id, value] of checks) {
            value.check.checked = selected.has(id);
            value.check.disabled = deleting || !deletable(value.item);
        }
    }
    function jobText(item) {
        const target = item.filename || item.scenario?.strategies?.join(', ') || item.strategies?.join(', ') || (item.kind === 'generated' ? '생성 전략' : '백테스트');
        const frame = item.base_frame ? (typeof moFrameText === 'function' ? moFrameText(item.base_frame) : item.base_frame) + ' 기준' : '';
        // An OZ trigger other than the strategy's own (수정본171), as a base frame other than the recipe's.
        const trigger = item.trigger || '';
        const symbol = item.symbol || item.scenario?.symbol || '';
        const start = item.start || item.scenario?.start || '', end = item.end || item.scenario?.end || '';
        return [target, frame, trigger, symbol, start && end ? start + ' ~ ' + end + ' (종료일 미포함)' : '',
            phases[item.phase] || item.phase || '상태 확인 필요', item.job_id || item.id].filter(Boolean).join(' · ');
    }
    // What a run tested, as it was when it started (수정본169), read when first opened.
    const modeNames = {BAR: 'BAR', TICK: 'TIMER / 틱', EVENT: '이벤트'};
    const recipeNow = {changed: '지금 레시피와 다름', missing: '지금은 없는 전략'};
    function testedLine(lines, label, value) {
        const line = moElement('div', undefined, 'moses-tested-line');
        line.append(moElement('span', label, 'moses-tested-label'),
            typeof value === 'string' ? moElement('span', value) : value);
        lines.push(line);
    }
    function testedText(texts) {
        const box = moElement('div', undefined, 'moses-tested-text');
        for (const text of texts) box.append(moElement('p', text));
        return box;
    }
    function testedView(data) {
        const frame = tf => typeof moFrameText === 'function' ? moFrameText(tf) : tf;
        const label = key => typeof moSettingLabel === 'function' ? moSettingLabel(key) : key;
        const choices = typeof moSettingChoices === 'object' ? moSettingChoices : {};
        const display = window.part3IntentDisplay, lines = [];
        for (const row of data.strategies || []) {
            const head = moElement('div', [row.name, row.title, row.folder].filter(Boolean).join(' · '), 'moses-tested-head');
            for (const note of [row.edited && '가상진입에서 고친 조건', recipeNow[row.now]].filter(Boolean))
                head.append(moElement('span', note, 'moses-tested-note'));
            lines.push(head);
            if (row.base_frame) testedLine(lines, '기준 프레임', frame(row.base_frame));
            if (row.trigger) testedLine(lines, 'OZ 트리거', row.trigger);
            if (row.times) testedLine(lines, '거래 시간', row.times);
            // Conditions the explanation cannot read are shown as saved.
            let texts = [JSON.stringify(row.conditions)];
            if (row.conditions && display) {
                try {texts = display.explain(row.conditions);} catch {}
            }
            testedLine(lines, '조건', row.conditions ? testedText(texts) : '기록 없음');
        }
        for (const text of data.commands || []) testedLine(lines, 'WATCH', text);
        if (data.result_mode === 'VIRTUAL_ENTRY') {
            // A policy of an older form than the panel reads is shown as saved.
            let policy = JSON.stringify(data.virtual_entry);
            if (display && data.virtual_entry_current) {
                try {policy = display.virtualEntry(data.virtual_entry, data.strategies?.[0]?.frames);} catch {}
            }
            testedLine(lines, '가상진입', testedText(String(policy).split('\n')));
            if (data.spread_points != null) testedLine(lines, '스프레드', data.spread_points + '포인트');
        }
        if (data.mode) testedLine(lines, '재생 모드', modeNames[data.mode] || data.mode);
        if (data.server_time) testedLine(lines, '브로커 서버 시간', data.server_time);
        if (data.oz_evaluation) testedLine(lines, label('oz_evaluation'),
            (choices.oz_evaluation || []).find(([value]) => value === data.oz_evaluation)?.[1] || data.oz_evaluation);
        if (data.overlap_trading_days != null) testedLine(lines, label('overlap_trading_days'), String(data.overlap_trading_days));
        for (const [key, value] of Object.entries(data.config || {})) testedLine(lines, label(key), String(value));
        return lines;
    }
    function testedDetails(id) {
        const details = moElement('details', undefined, 'moses-job-tested');
        const body = moElement('div', undefined, 'moses-tested');
        details.append(moElement('summary', '당시 설정'), body);
        let loaded = false;
        details.ontoggle = async () => {
            if (!details.open || loaded) return;
            loaded = true;
            body.replaceChildren(moElement('p', '불러오는 중', 'muted'));
            try {body.replaceChildren(...testedView(await api('mo/backtest/tested?id=' + encodeURIComponent(id))));}
            catch (error) {
                body.replaceChildren(moElement('p', '당시 설정: ' + error.message, 'moses-tested-error'));
                loaded = false;  // read again when opened again
            }
        };
        return details;
    }
    async function reconnect(id) {
        const navigationRevision = moState.navigationRevision;
        const selectionRevision = ++moState.jobSelectionRevision;
        const status = await api('mo/backtest/reconnect', {job_id: id});
        await moAdoptBacktestJob(status, {navigationRevision, selectionRevision});
    }
    async function refresh() {
        if (recentPending) return recentPending;
        recentPending = (async () => {
            const box = node('bt-jobs-list'), message = node('bt-jobs-message');
            try {
                const data = await api('mo/backtest/recent'), items = Array.isArray(data) ? data : data.items || [];
                displayed = items;
                leftovers = Array.isArray(data.leftovers) ? data.leftovers : [];
                const eligible = new Set(items.filter(deletable).map(item => item.job_id || item.id));
                for (const id of selected) if (!eligible.has(id)) selected.delete(id);
                checks.clear();
                box.replaceChildren();
                for (const item of items) {
                    const id = item.job_id || item.id;
                    const row = moElement('div', undefined, 'moses-job-row'); row.setAttribute('role', 'listitem');
                    const check = moElement('input', undefined, 'moses-job-check'); check.type = 'checkbox';
                    check.setAttribute('aria-label', jobText(item) + ' 선택');
                    check.title = deletable(item) ? '삭제할 백테스트 선택' : '실행 중이거나 종료를 확인하지 못한 작업';
                    check.onchange = () => {
                        if (deleting || !deletable(item)) return;
                        if (check.checked) selected.add(id); else selected.delete(id);
                        selectionButtons();
                    };
                    checks.set(id, {check, item}); row.append(check);
                    row.append(moElement('span', jobText(item)));
                    const open = moElement('button', '진행 / 결과 보기'); open.type = 'button';
                    open.onclick = async () => {
                        open.disabled = true;
                        try {await reconnect(item.job_id || item.id);}
                        catch (error) {message.textContent = error.message;}
                        finally {open.disabled = false;}
                    };
                    row.append(open);
                    if (!item.scenario?.build_only) row.append(testedDetails(id));
                    box.append(row);
                }
                selectionButtons();
                message.textContent = data.warnings?.join('\n') || (items.length ? items.length + '개 백테스트 · 화면을 다시 열어도 실행 중 작업을 조회할 수 있습니다.' : '아직 저장된 작업이 없습니다.');
                const active = items.filter(item => item.active || !terminal.has(item.phase));
                if (!moState.job && active.length === 1 && moState.view === 'backtest') await moAdoptBacktestJob(active[0], {openProgress: false});
                if (!moState.job && active.length > 1) message.textContent += ' 실행 중인 작업이 여러 개입니다. 조회할 작업을 선택하세요.';
            } catch (error) {message.textContent = '작업 목록 조회: ' + error.message;}
        })();
        try {return await recentPending;} finally {recentPending = null;}
    }
    node('bt-jobs-refresh').onclick = refresh;
    node('bt-jobs-select-all').onclick = () => {
        if (deleting) return;
        const eligible = displayed.filter(deletable);
        if (eligible.every(item => selected.has(item.job_id || item.id))) selected.clear();
        else for (const item of eligible) selected.add(item.job_id || item.id);
        selectionButtons();
    };
    node('bt-jobs-delete').onclick = async () => {
        if (deleting || !selected.size) return;
        const ids = Array.from(selected), message = node('bt-jobs-message');
        if (!window.confirm('선택한 백테스트 ' + ids.length + '개의 기록과 결과를 삭제하시겠습니까?\n원본 captures 데이터는 삭제하지 않습니다.')) return;
        deleting = true; selectionButtons();
        try {
            if (recentPending) await refresh();
            const result = await api('mo/backtest/delete', {job_ids: ids});
            for (const id of result.deleted || []) selected.delete(id);
            if (typeof moForgetBacktestJob === 'function') moForgetBacktestJob(result.deleted || []);
            await refresh();
            message.textContent = (result.deleted || []).length + '개 백테스트 기록·결과를 삭제했습니다.'
                + ((result.errors || []).length ? '\n' + result.errors.map(item => item.job_id + ': ' + item.message).join('\n') : '');
        } catch (error) {message.textContent = '백테스트 삭제: ' + error.message;}
        finally {deleting = false; selectionButtons();}
    };
    node('bt-jobs-leftovers').onclick = async () => {
        if (deleting || !leftovers.length) return;
        const ids = leftovers.slice(), message = node('bt-jobs-message');
        if (!window.confirm('결과 없이 남은 실행 폴더 ' + ids.length + '개를 지우시겠습니까?')) return;
        deleting = true; selectionButtons();
        try {
            if (recentPending) await refresh();
            const result = await api('mo/backtest/leftovers', {job_ids: ids});
            await refresh();
            message.textContent = (result.deleted || []).length + '개 폴더를 정리했습니다.'
                + ((result.errors || []).length ? '\n' + result.errors.map(item => item.job_id + ': ' + item.message).join('\n') : '');
        } catch (error) {message.textContent = '폴더 정리: ' + error.message;}
        finally {deleting = false; selectionButtons();}
    };
    window.MosesBacktestJobs = {refresh, reconnect};
})();
