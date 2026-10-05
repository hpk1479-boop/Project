'use strict';
/* Presentation only: every performance value and series comes from Part2 JSON. */
window.MosesBacktestDashboard = (() => {
    const el = (tag, text, cls) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = String(text ?? '');
        if (cls) node.className = cls;
        return node;
    };
    const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const array = value => Array.isArray(value) ? value.filter(v => v && typeof v === 'object') : [];
    const numeric = value => typeof value === 'number' && Number.isFinite(value);
    const number = value => numeric(value) ? value.toLocaleString('ko-KR', {maximumFractionDigits: 2, minimumFractionDigits: 2}) : '—';
    const count = value => numeric(value) ? value.toLocaleString('ko-KR') : '—';
    const percent = value => numeric(value) ? (value * 100).toFixed(1) + '%' : '—';
    const r = value => numeric(value) ? number(value) + ' R' : '—';
    const utc = value => {
        if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
        const date = new Date(Number(value));
        return Number.isFinite(date.getTime()) ? date.toISOString().replace('T', ' ').replace('.000Z', ' UTC') : '—';
    };
    const tone = value => !numeric(value) || value === 0 ? '' : value > 0 ? 'bt-positive' : 'bt-negative';
    const direction = value => ({LONG: '매수 (LONG)', SHORT: '매도 (SHORT)'})[value] || value || '—';
    const resultLabel = value => ({WIN: '승', LOSS: '패', UNCLOSED: '미청산', WAITING: '진입 대기',
        UNCERTAIN: '순서 미확정', BLOCKED: '진입 제한으로 제외', EXPIRED: '올존 유효기간 종료',
        PASS_RISK: '손절 폭으로 제외'})[value] || value || '—';
    const empty = text => el('p', text || '표시할 데이터가 없습니다.', 'bt-empty');
    const metrics = [
        ['총 거래 수', 'total_trades', count, '미청산·순서 미확정 진입 포함'],
        ['승률', 'win_rate', percent, '청산된 거래 기준'],
        ['Total R', 'total_r', r, '누적 손익'],
        ['Avg R', 'average_r', r, '청산 거래의 평균 R'],
        ['Profit Factor', 'profit_factor', number, '손실이 없으면 —'],
        ['Expectancy', 'expectancy_r', r, '거래당 기대 R'],
        ['Max Drawdown', 'max_drawdown_r', r, '최고점 대비 최대 낙폭'],
        ['최대 연패', 'max_consecutive_losses', count, '청산 순서 기준'],
    ];
    const columns = [
        ['거래 수', 'total_trades', count], ['승률', 'win_rate', percent],
        ['Total R', 'total_r', r], ['Avg R', 'average_r', r],
        ['Profit Factor', 'profit_factor', number], ['기대 R', 'expectancy_r', r],
        ['승', 'wins', count], ['패', 'losses', count], ['미청산', 'unclosed', count],
        ['순서 미확정', 'uncertain', count],
    ];
    function table(rows, fields, caption) {
        if (!rows.length) return empty();
        const scroll = el('div', undefined, 'bt-table-scroll');
        scroll.tabIndex = 0; scroll.setAttribute('aria-label', caption);
        const node = el('table', undefined, 'bt-table');
        node.append(el('caption', caption));
        const head = el('thead'), header = el('tr');
        fields.forEach(([label]) => {const th = el('th', label); th.scope = 'col'; header.append(th);});
        head.append(header); node.append(head);
        const body = el('tbody');
        rows.forEach(row => {
            const line = el('tr');
            fields.forEach(([, key, format]) => {
                const value = key === 'exit_time' && row.result === 'UNCERTAIN' ? null : row[key];
                const cell = el('td', format ? format(value) : value === '' ? '—' : value ?? '—');
                if (key === 'total_r' || key === 'r') cell.className = tone(row[key]);
                line.append(cell);
            });
            body.append(line);
        });
        node.append(body); scroll.append(node); return scroll;
    }
    const grouped = value => Object.entries(object(value)).map(([name, row]) => ({...object(row), name}));
    const svgNode = (tag, attrs, text) => {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
        if (text !== undefined) node.textContent = text;
        return node;
    };
    function chart(title, subtitle, values, field, options = {}) {
        const card = el('section', undefined, 'bt-chart-card');
        card.append(el('h4', title), el('p', subtitle, 'bt-chart-subtitle'));
        const points = array(values).filter(point => numeric(point[field]));
        if (!points.length) {card.append(empty('아직 표시할 성과 데이터가 없습니다.')); return card;}
        // Scaling to pixels is presentation, never an equity/return calculation.
        const width = 640, height = 254, left = 65, right = 24, top = 22, bottom = 38;
        const plotWidth = width - left - right, plotHeight = height - top - bottom;
        let min = 0, max = 0;
        points.forEach(point => {min = Math.min(min, point[field]); max = Math.max(max, point[field]);});
        if (min === max) max = min + 1;
        const y = value => options.drawdown ? top + (value - min) / (max - min) * plotHeight :
            top + (max - value) / (max - min) * plotHeight;
        const x = index => left + (options.bars ? (index + .5) / points.length :
            points.length === 1 ? .5 : index / (points.length - 1)) * plotWidth;
        const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img',
            'aria-label': title, class: 'bt-chart-svg'});
        svg.append(svgNode('title', {}, title), svgNode('desc', {}, subtitle));
        for (let i = 0; i <= 4; i++) {
            const value = min + (max - min) * i / 4, position = y(value);
            svg.append(svgNode('line', {x1: left, x2: width - right, y1: position, y2: position, class: 'bt-grid-line'}));
            svg.append(svgNode('text', {x: left - 10, y: position + 4, 'text-anchor': 'end', class: 'bt-axis'}, number(value)));
        }
        svg.append(svgNode('line', {x1: left, x2: width - right, y1: y(0), y2: y(0), class: 'bt-zero-line'}));
        const label = point => options.label ? options.label(point) : utc(point.exit_time_ms);
        const tooltip = el('p', '차트에 마우스를 올리거나 방향키로 값을 확인하세요.', 'bt-chart-tooltip');
        if (options.bars) {
            const barWidth = Math.min(72, plotWidth / points.length * .65);
            points.forEach((point, index) => {
                const value = point[field], zero = y(0);
                const bar = svgNode('rect', {x: x(index) - barWidth / 2, y: Math.min(y(value), zero),
                    width: barWidth, height: Math.max(1, Math.abs(y(value) - zero)),
                    class: value < 0 ? 'bt-bar-negative' : 'bt-bar-positive'});
                bar.append(svgNode('title', {}, label(point) + ' · ' + r(value))); svg.append(bar);
                if (points.length <= 12 || index % Math.ceil(points.length / 8) === 0)
                    svg.append(svgNode('text', {x: x(index), y: height - 10, 'text-anchor': 'middle', class: 'bt-axis'}, label(point)));
            });
        } else {
            const coordinates = points.map((point, index) => `${x(index).toFixed(2)},${y(point[field]).toFixed(2)}`);
            svg.append(svgNode('polyline', {points: coordinates.join(' '),
                class: options.drawdown ? 'bt-line-drawdown' : 'bt-line-equity'}));
            if (points.length === 1) svg.append(svgNode('circle', {cx: x(0), cy: y(points[0][field]), r: 4, class: 'bt-point'}));
            svg.append(svgNode('text', {x: left, y: height - 10, class: 'bt-axis'}, label(points[0]).slice(0, 10)));
            svg.append(svgNode('text', {x: width - right, y: height - 10, 'text-anchor': 'end', class: 'bt-axis'}, label(points[points.length - 1]).slice(0, 10)));
        }
        const cursor = svgNode('circle', {r: 5, cx: x(0), cy: y(points[0][field]), class: 'bt-cursor'});
        svg.append(cursor);
        const overlay = svgNode('rect', {x: left, y: top, width: plotWidth, height: plotHeight,
            class: 'bt-chart-hit', tabindex: '0', role: 'slider', 'aria-label': title + ' 기록 탐색',
            'aria-valuemin': 1, 'aria-valuemax': points.length, 'aria-valuenow': 1});
        let active = 0;
        const show = index => {
            active = Math.max(0, Math.min(points.length - 1, index));
            const point = points[active], text = label(point) + ' · ' + r(point[field]);
            cursor.setAttribute('cx', x(active)); cursor.setAttribute('cy', y(point[field]));
            tooltip.textContent = text;
            overlay.setAttribute('aria-valuenow', active + 1); overlay.setAttribute('aria-valuetext', text);
        };
        overlay.onpointermove = event => {
            const rect = svg.getBoundingClientRect();
            const position = (event.clientX - rect.left) / rect.width * width;
            show(options.bars ? Math.floor((position - left) / plotWidth * points.length) :
                Math.round((position - left) / plotWidth * (points.length - 1)));
        };
        overlay.onkeydown = event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            show(event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : active + (event.key === 'ArrowRight' ? 1 : -1));
        };
        overlay.onfocus = () => show(active);
        show(0); svg.append(overlay); card.append(svg, tooltip); return card;
    }
    async function download(job, file, button, message) {
        button.disabled = true; message.textContent = '';
        try {
            const response = await fetch('/api/mo/backtest/download?id=' + encodeURIComponent(job) + '&kind=' + encodeURIComponent(file.kind),
                {headers: {'X-Lab-Token': typeof token === 'undefined' ? '' : token}});
            if (!response.ok) {const error = await response.json(); throw new Error(error.error || '원본 다운로드 실패');}
            const url = URL.createObjectURL(await response.blob());
            const link = el('a'); link.href = url; link.download = file.path.split('/').pop();
            document.body.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {message.textContent = error.message;}
        finally {button.disabled = false;}
    }
    function originals(container, data, job) {
        const detail = el('details', undefined, 'bt-originals');
        detail.append(el('summary', '원본 파일 · 경로 · 실행 기록'));
        const labels = {result: '결과 JSON', analytics: '분석 JSON', alerts: '알림 CSV', summary: '성과 CSV', trades: '거래내역 CSV'};
        const files = array(data.files);
        if (files.length) files.forEach(file => {
            const row = el('div', undefined, 'bt-file-row');
            const info = el('div'); info.append(el('strong', labels[file.kind] || file.kind), el('code', file.path));
            const button = el('button', file.available ? '원본 다운로드' : '파일 없음'); button.type = 'button';
            button.disabled = !file.available;
            const message = el('p', '', 'moses-warning'); message.setAttribute('role', 'status');
            button.onclick = () => download(job, file, button, message);
            row.append(info, button, message); detail.append(row);
        });
        else for (const [label, path] of [['알림 CSV', data.alerts_csv], ['성과 CSV', data.summary_csv], ['거래내역 CSV', data.trades_csv]])
            if (path) detail.append(el('p', label + ': ' + path));
        detail.append(el('p', '실행 ID: ' + (data.run_id || job), 'bt-run-id'));
        if (data.period_adjustment) detail.append(el('p', data.period_adjustment.message));
        for (const period of array(data.excluded_periods)) detail.append(el('p',
            period.start + ' ~ ' + period.end + ': ' + period.message));
        if (array(data.processed_periods).length) detail.append(el('p', '실제 처리 구간 (UTC): ' + data.processed_periods.map(p =>
            (p.start || '—') + ' ~ ' + (p.last_observation || p.end || '—')).join(' · ')));
        container.append(detail);
    }
    function fallback(body, data) {
        const stats = object(data.alert_statistics);
        if (Object.keys(stats).length) body.append(el('p', '총 알림 ' + count(stats.total) + '건 · 일평균 ' + number(stats.daily_average) +
            ' · 주평균 ' + number(stats.weekly_average) + ' · 월평균 ' + number(stats.monthly_average)));
        const summaries = array(object(data.virtual_entry).summary);
        if (summaries.length) {
            body.append(el('h4', '저장된 가상 진입 요약'), table(summaries,
                [['전략', 'strategy'], ['RR', 'rr'], ['알림', 'alerts'], ['진입', 'entries'],
                    ['승', 'wins'], ['패', 'losses'], ['미청산', 'unclosed'], ['순서 미확정', 'uncertain'],
                    ['승률', 'win_rate'], ['Avg R', 'average_r'], ['Total R', 'total_r']], '기존 가상 진입 요약'));
            if (summaries.some(row => numeric(row.uncertain) && row.uncertain > 0))
                body.append(el('p', '진입한 1분봉의 가격 순서를 확인할 수 없는 거래는 순서 미확정으로 표시하고 승률·평균 R에서 제외합니다.', 'bt-basis'));
        } else if (array(data.pieces).length) {
            body.append(el('h4', '데이터 조각'), table(data.pieces,
                [['종목', 'symbol'], ['시작', 'start'], ['종료', 'end'], ['상태', 'status']], '데이터 구축 결과'));
        } else {
            body.append(el('h4', '알림 (첫 200건)'), table(array(data.alerts_preview),
                [['시각 (UTC)', 'time_ms', utc], ['전략', 'strategy'], ['종목', 'symbol'], ['TF', 'tf'],
                    ['방향', 'direction', direction], ['알림', 'message'], ['수신자', 'recipient']], '알림 원본 미리보기'));
        }
    }
    function render(container, source, job) {
        const data = object(source), results = object(object(data.analytics).rr_results);
        const keys = Object.keys(results).filter(key => Object.keys(object(results[key])).length).sort((a, b) => Number(a) - Number(b));
        let selected = keys[0] || '', activeTab = 'summary', generation = 0;
        container.replaceChildren();
        const dashboard = el('div', undefined, 'bt-dashboard');
        const header = el('div', undefined, 'bt-dashboard-head');
        const heading = el('div'); heading.append(el('h3', '성과 대시보드'), el('p', data.status || '완료', 'bt-status'));
        header.append(heading);
        const picker = el('label', '손익비 (RR) ', 'bt-rr-picker'), select = el('select');
        select.setAttribute('aria-label', '손익비별 결과 선택'); select.id = 'bt-dashboard-rr';
        keys.forEach(key => {const option = el('option', 'RR ' + key); option.value = key; select.append(option);});
        if (!keys.length) {select.append(el('option', '분석 없음')); select.disabled = true;}
        picker.append(select); header.append(picker); dashboard.append(header);
        dashboard.append(el('p', 'RR별 독립 결과 · 누적 곡선은 청산 순서 · 월·시간대 집계는 알림 시각 UTC 기준', 'bt-basis'));
        const cards = el('div', undefined, 'bt-metric-grid'); dashboard.append(cards);
        const warning = el('p', undefined, 'bt-analysis-notice'); dashboard.append(warning);
        const tabs = el('div', undefined, 'bt-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', '백테스트 상세 결과');
        const body = el('div', undefined, 'bt-tab-body'); body.id = 'bt-dashboard-panel'; body.setAttribute('role', 'tabpanel');
        const buttons = [];
        [['summary', '요약'], ['monthly', '월별'], ['hourly', '시간대별'], ['timeframe', 'TF별'],
            ['groups', '전략·종목별'], ['trades', '거래내역']].forEach(([key, label]) => {
            const button = el('button', label); button.type = 'button'; button.dataset.btTab = key;
            button.id = 'bt-tab-' + key; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', body.id);
            button.onclick = () => {activeTab = key; draw();}; buttons.push(button); tabs.append(button);
        });
        tabs.onkeydown = event => {
            const index = buttons.indexOf(document.activeElement);
            if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 :
                (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].click(); buttons[next].focus();
        };
        dashboard.append(tabs, body);
        if (Array.isArray(data.warnings) && data.warnings.length) dashboard.append(el('p', data.warnings.join('\n'), 'moses-warning'));
        originals(dashboard, data, job); container.append(dashboard);
        const tradeFields = [['알림 (UTC)', 'alert_time', utc], ['전략', 'strategy'], ['종목', 'symbol'], ['TF', 'tf'],
            ['방향', 'direction', direction], ['RR', 'rr'], ['결과', 'result', resultLabel], ['R', 'r'],
            ['진입 (UTC)', 'entry_time', utc], ['청산 (UTC)', 'exit_time', utc], ['진입가', 'entry_price'], ['손절가', 'stop_price']];
        async function showTrades(offset, version) {
            body.replaceChildren(empty('거래내역을 읽고 있습니다…'));
            try {
                const page = await api('mo/backtest/trades?id=' + encodeURIComponent(job) +
                    '&rr=' + encodeURIComponent(selected) + '&offset=' + offset + '&limit=100');
                if (version !== generation || !dashboard.isConnected) return;
                body.replaceChildren();
                if (!array(page.rows).length) body.append(empty(page.message || '해당 RR의 거래내역이 없습니다.'));
                else body.append(table(page.rows, tradeFields, '거래내역 원본 · 한 페이지 최대 100건'));
                const pager = el('div', undefined, 'bt-pager');
                const prev = el('button', '‹ 이전'), next = el('button', '다음 ›'); prev.type = next.type = 'button';
                prev.disabled = offset === 0; next.disabled = page.next_offset == null;
                prev.onclick = () => showTrades(Math.max(0, offset - 100), version);
                next.onclick = () => showTrades(page.next_offset, version);
                pager.append(prev, el('span', page.rows?.length ? `${offset + 1}–${offset + page.rows.length}건` : '0건'), next); body.append(pager);
            } catch (error) {
                if (version !== generation || !dashboard.isConnected) return;
                body.replaceChildren(empty('거래내역을 읽지 못했습니다: ' + error.message));
                const retry = el('button', '다시 읽기'); retry.onclick = () => showTrades(offset, version); body.append(retry);
            }
        }
        function draw() {
            const version = ++generation, current = object(results[selected]);
            const values = {...object(current.summary), ...object(current.drawdown), ...object(current.streaks)};
            cards.replaceChildren();
            metrics.forEach(([label, key, format, help]) => {
                const card = el('section', undefined, 'bt-metric'); card.dataset.metric = key;
                card.append(el('p', label, 'bt-metric-label'), el('strong', format(values[key]),
                    ['total_r', 'average_r', 'expectancy_r'].includes(key) ? tone(values[key]) : ''), el('small', help)); cards.append(card);
            });
            warning.textContent = keys.length ? (values.total_trades === 0 ? '진입한 거래가 없습니다. 비율 지표는 —로 표시됩니다.' :
                numeric(values.unclosed) && values.unclosed > 0 ? '미청산 ' + count(values.unclosed) + '건 · 승률·평균 R은 청산된 거래만 반영합니다.' : '') :
                (data.analytics_message || '이 실행에는 성과 분석이 없습니다.');
            if (numeric(values.uncertain) && values.uncertain > 0)
                warning.textContent += (warning.textContent ? ' ' : '') + '순서 미확정 ' + count(values.uncertain) +
                    '건 · 진입한 1분봉의 가격 순서를 확인할 수 없어 승률·평균 R에서 제외합니다.';
            warning.classList.toggle('hidden', !warning.textContent);
            buttons.forEach(button => {const active = button.dataset.btTab === activeTab;
                button.classList.toggle('active', active); button.setAttribute('aria-selected', active); button.tabIndex = active ? 0 : -1;});
            body.setAttribute('aria-labelledby', 'bt-tab-' + activeTab); body.replaceChildren();
            if (activeTab === 'trades') {showTrades(0, version); return;}
            if (activeTab === 'summary' && !keys.length) {fallback(body, data); return;}
            if (activeTab === 'summary') {
                const grid = el('div', undefined, 'bt-chart-grid');
                grid.append(chart('누적 Equity R', '청산 순서 · UTC · 단위 R', current.equity_r_curve, 'equity_r'),
                    chart('Drawdown', '최고점에서의 낙폭 · 단위 R', current.drawdown_curve, 'drawdown_r', {drawdown: true}),
                    chart('월별 성과', '알림 시각 UTC · Total R', current.monthly, 'total_r', {bars: true, label: point => point.month || '—'}));
                const sides = ['LONG', 'SHORT'].map(name => ({...object(object(current.by_direction)[name]), name}));
                const comparison = chart('매수 / 매도 성과', '방향별 Total R · 해당 방향이 없으면 데이터 없음', sides, 'total_r',
                    {bars: true, label: point => direction(point.name)});
                const brief = el('div', undefined, 'bt-direction-brief');
                sides.forEach(side => brief.append(el('span', direction(side.name) + ' · ' +
                    (Object.hasOwn(object(current.by_direction), side.name) ? count(side.total_trades) + '건 · 승률 ' + percent(side.win_rate) : '거래 없음'))));
                comparison.append(brief); grid.append(comparison); body.append(grid);
            } else if (activeTab === 'monthly') {
                body.append(chart('월별 성과', '알림 시각 UTC · Total R', current.monthly, 'total_r', {bars: true, label: point => point.month || '—'}),
                    table(array(current.monthly), [['월 (UTC)', 'month'], ...columns], '월별 성과'));
            } else if (activeTab === 'hourly') {
                body.append(el('p', '알림 발생 시각의 UTC 시간대입니다. 한국 시간으로 변환하지 않습니다.', 'bt-basis'),
                    table(array(current.hourly), [['시간 (UTC)', 'hour_utc', value => value == null ? '—' : value + '시'], ...columns], '시간대별 성과'));
            } else if (activeTab === 'timeframe') {
                body.append(table(grouped(current.by_timeframe), [['TF', 'name'], ...columns], 'TF별 성과'));
            } else if (activeTab === 'groups') {
                body.append(el('h4', '전략별'), table(grouped(current.by_strategy), [['전략', 'name'], ...columns], '전략별 성과'),
                    el('h4', '종목별'), table(grouped(current.by_symbol), [['종목', 'name'], ...columns], '종목별 성과'));
            }
        }
        select.onchange = () => {selected = select.value; draw();}; draw();
    }
    return {render};
})();
