'use strict';
/* Presentation only: every performance value and series comes from Part2 (result_analysis). */
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
    const percent2 = value => numeric(value) ? (value * 100).toFixed(2) + '%' : '—';
    const r = value => numeric(value) ? number(value) + ' R' : '—';
    // An average R per trade is often a few hundredths: three decimals.
    const r3 = value => numeric(value) ? value.toLocaleString('ko-KR', {maximumFractionDigits: 3, minimumFractionDigits: 3}) + ' R' : '—';
    const money = value => numeric(value) ? '$' + Math.round(value).toLocaleString('ko-KR') : '—';
    // Korean time, as every period, month and hour of the result (수정본165).
    const kst = value => {
        if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '—';
        const date = new Date(Number(value) + 9 * 3600 * 1000);
        return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 16).replace('T', ' ') : '—';
    };
    const tone = value => !numeric(value) || value === 0 ? '' : value > 0 ? 'bt-positive' : 'bt-negative';
    const direction = value => ({LONG: '매수 (LONG)', SHORT: '매도 (SHORT)'})[value] || value || '—';
    const resultLabel = value => ({WIN: '승', LOSS: '패', UNCLOSED: '미청산', WAITING: '진입 대기',
        UNCERTAIN: '순서 미확정', BLOCKED: '진입 제한으로 제외', EXPIRED: '알림 후 N봉 지나 제외',
        PASS_ENV: '환경조건이 깨져 제외', PASS_RISK: '손절 폭으로 제외'})[value] || value || '—';
    const empty = text => el('p', text || '표시할 데이터가 없습니다.', 'bt-empty');
    const gradeLabel = {poor: '나쁨', weak: '약함', fair: '보통', good: '좋음', excellent: '우수'};
    // The RR table's indices: [label, key, format, a larger value is better].
    const ranked = [
        ['합계 R', 'total_r', r, true], ['평균 R', 'average_r', r3, true], ['승률', 'win_rate', percent, true],
        ['PF', 'profit_factor', number, true], ['회복지수', 'recovery_factor', number, true],
        ['SQN', 'sqn', number, true], ['샤프', 'sharpe', number, true], ['소르티노', 'sortino', number, true],
        ['최대 낙폭', 'max_drawdown_r', r, false], ['최대 연속 손실', 'max_consecutive_losses', count, false],
        ['켈리', 'kelly', percent2, true]];
    const metrics = [
        ['총 거래 수', 'total_trades', count, '미청산·순서 미확정 진입 포함'],
        ['승률', 'win_rate', percent, '청산된 거래 기준'],
        ['합계 R', 'total_r', r, '누적 손익'],
        ['평균 R', 'average_r', r3, '거래당 기대 R'],
        ['PF', 'profit_factor', number, '총이익 ÷ 총손실'],
        ['회복지수', 'recovery_factor', number, '합계 R ÷ 최대 낙폭'],
        ['최대 낙폭', 'max_drawdown_r', r, '최고점 대비'],
        ['최대 연속 손실', 'max_consecutive_losses', count, '청산 순서 기준'],
    ];
    const columns = [
        ['거래 수', 'total_trades', count], ['승률', 'win_rate', percent],
        ['합계 R', 'total_r', r], ['평균 R', 'average_r', r3],
        ['PF', 'profit_factor', number],
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
        const label = point => options.label ? options.label(point) : kst(point.exit_time_ms);
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
                [['시각 (한국)', 'time_ms', kst], ['전략', 'strategy'], ['종목', 'symbol'], ['TF', 'tf'],
                    ['방향', 'direction', direction], ['알림', 'message'], ['수신자', 'recipient']], '알림 원본 미리보기'));
        }
    }
    // [start, end) hours, end exclusive and joined over midnight by Part2: 22–02시 is 22:00 to 02:00.
    const hourText = ranges => ranges.filter(Array.isArray).map(([start, end]) =>
        String(start).padStart(2, '0') + '–' + String(end % 24 === 0 && end ? 24 : end % 24).padStart(2, '0') + '시').join(', ');
    // Sorted RR rows for one index; the first row the average R tells apart from chance is chosen.
    function sortedRows(rows, key) {
        const better = (ranked.find(item => item[1] === key) || ranked[0])[3];
        return rows.slice().sort((a, b) => {
            const left = object(a.values)[key], right = object(b.values)[key];
            if (!numeric(left) || !numeric(right)) return numeric(left) ? -1 : numeric(right) ? 1 : 0;
            return better ? right - left : left - right;
        });
    }
    const topRow = sorted => (sorted.find(row => row.significant) || sorted[0] || {}).rr;
    function render(container, source, job) {
        const data = object(source);
        let view = object(data.analysis), sortKey = 'total_r', activeTab = 'monthly', generation = 0, request = 0;
        container.replaceChildren();
        const dashboard = el('div', undefined, 'bt-dashboard');
        const header = el('div', undefined, 'bt-dashboard-head');
        const heading = el('div'); heading.append(el('h3', '성과 대시보드'), el('p', data.status || '완료', 'bt-status'));
        const controls = el('div', undefined, 'bt-period-picker');
        const yearBox = el('label', '기간 '), year = el('select'), monthBox = el('label', '월 '), month = el('select');
        year.id = 'bt-period-year'; month.id = 'bt-period-month';
        year.setAttribute('aria-label', '기간'); month.setAttribute('aria-label', '월');
        yearBox.append(year); monthBox.append(month); controls.append(yearBox, monthBox);
        header.append(heading, controls); dashboard.append(header);
        const basis = el('p', '한국시간 · 알림 시각 기준', 'bt-basis'); dashboard.append(basis);
        if (Array.isArray(data.compared_runs) && data.compared_runs.length) {
            // Base frames tested in the same request are saved as their own runs (수정본161).
            const frame = tf => tf ? (typeof moFrameText === 'function' ? moFrameText(tf) : tf) : '레시피';
            dashboard.append(el('p', (data.base_frame ? '기준 프레임 ' + frame(data.base_frame) + ' · ' : '') + '함께 시험: ' +
                data.compared_runs.map(row => frame(row.base_frame)).join(' · ') + ' (실행 목록에서 따로 확인)', 'bt-basis'));
        }
        const status = el('p', '', 'bt-analysis-notice'); status.setAttribute('role', 'status');
        const compare = el('section', undefined, 'bt-rr-compare');
        const compareHead = el('div', undefined, 'bt-rr-head'), sortBox = el('label', '정렬 '), sort = el('select');
        sort.id = 'bt-rr-sort'; sort.setAttribute('aria-label', 'RR 정렬 지수');
        ranked.forEach(([label, key]) => {const option = el('option', label); option.value = key; sort.append(option);});
        sortBox.append(sort); compareHead.append(el('h4', 'RR 비교'), sortBox);
        const rrTable = el('div', undefined, 'bt-rr-table-box');
        compare.append(compareHead, rrTable, el('p', '색: 흔히 쓰는 등급 기준 · 흐린 줄: 평균 R이 우연과 구별되지 않음(t<2)', 'bt-basis'));
        const cards = el('div', undefined, 'bt-metric-grid');
        const charts = el('div', undefined, 'bt-chart-grid');
        const slots = el('section', undefined, 'bt-slots');
        const accounts = el('section', undefined, 'bt-accounts');
        const tabs = el('div', undefined, 'bt-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', '백테스트 상세 결과');
        const body = el('div', undefined, 'bt-tab-body'); body.id = 'bt-dashboard-panel'; body.setAttribute('role', 'tabpanel');
        const buttons = [];
        [['monthly', '월별'], ['hourly', '시간대별'], ['timeframe', 'TF별'], ['groups', '전략·종목별'], ['trades', '거래내역']]
            .forEach(([key, label]) => {
                const button = el('button', label); button.type = 'button'; button.dataset.btTab = key;
                button.id = 'bt-tab-' + key; button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', body.id);
                button.onclick = () => {activeTab = key; drawTab();}; buttons.push(button); tabs.append(button);
            });
        tabs.onkeydown = event => {
            const index = buttons.indexOf(document.activeElement);
            if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 :
                (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].click(); buttons[next].focus();
        };
        dashboard.append(status, compare, cards, charts, slots, accounts, tabs, body);
        if (Array.isArray(data.warnings) && data.warnings.length) dashboard.append(el('p', data.warnings.join('\n'), 'moses-warning'));
        originals(dashboard, data, job); container.append(dashboard);
        if (!array(view.rr_table).length) {
            // No trade rows: the run's own alert statistics and virtual-entry summary.
            [controls, compare, cards, charts, slots, accounts, tabs].forEach(node => node.classList.add('hidden'));
            status.textContent = data.analytics_message || '이 실행에는 성과 분석이 없습니다.';
            fallback(body, data); return;
        }
        const currentRow = () => array(view.rr_table).find(row => row.rr === view.rr) || {};
        function periodOptions() {
            const periods = object(view.periods), years = Array.isArray(periods.years) ? periods.years : [];
            const months = Array.isArray(periods.months) ? periods.months : [];
            const chosen = String(view.period || 'all'), chosenYear = chosen === 'all' ? 'all' : chosen.slice(0, 4);
            year.replaceChildren(); month.replaceChildren();
            const all = el('option', '전체'); all.value = 'all'; year.append(all);
            years.forEach(value => {const option = el('option', value + '년'); option.value = value; year.append(option);});
            year.value = chosenYear;
            const whole = el('option', chosenYear === 'all' ? '—' : '연간'); whole.value = chosenYear; month.append(whole);
            months.filter(value => chosenYear !== 'all' && value.startsWith(chosenYear + '-')).forEach(value => {
                const option = el('option', value.slice(5) + '월'); option.value = value; month.append(option);});
            month.value = chosen.length === 7 ? chosen : chosenYear;
            month.disabled = chosenYear === 'all';
        }
        function drawTable() {
            const sorted = sortedRows(array(view.rr_table), sortKey);
            const scroll = el('div', undefined, 'bt-table-scroll'); scroll.tabIndex = 0; scroll.setAttribute('aria-label', 'RR 비교');
            const node = el('table', undefined, 'bt-table bt-rr-table'), head = el('tr');
            [['RR'], ['거래'], ...ranked].forEach(([label, key]) => {
                const th = el('th', label); th.scope = 'col';
                if (key) {th.dataset.sort = key; th.classList.toggle('active', key === sortKey);
                    th.onclick = () => {sortKey = key; sort.value = key; choose(topRow(sortedRows(array(view.rr_table), key)));};}
                head.append(th);
            });
            const thead = el('thead'); thead.append(head); node.append(el('caption', 'RR 비교'), thead);
            const tbody = el('tbody');
            sorted.forEach(row => {
                const line = el('tr'); line.dataset.rr = row.rr; line.tabIndex = 0;
                line.classList.toggle('bt-rr-selected', row.rr === view.rr);
                line.classList.toggle('bt-rr-chance', !row.significant);
                line.title = row.significant ? 'RR ' + row.rr + ' 보기' : '평균 R이 우연과 구별되지 않습니다';
                line.append(el('td', row.rr), el('td', count(row.trades)));
                ranked.forEach(([, key, format]) => {
                    const cell = el('td', format(object(row.values)[key]));
                    const grade = object(row.grades)[key];
                    if (grade) {cell.classList.add('bt-grade-' + grade); cell.title = gradeLabel[grade] || grade;}
                    if (key === 'total_r' || key === 'average_r') cell.classList.add(tone(object(row.values)[key]) || 'bt-neutral');
                    if (key === 'sqn' && row.suspect) cell.title = '7 이상: 과최적화 의심';
                    line.append(cell);
                });
                line.onclick = () => choose(row.rr);
                line.onkeydown = event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); choose(row.rr);}};
                tbody.append(line);
            });
            node.append(tbody); scroll.append(node); rrTable.replaceChildren(scroll);
        }
        function drawCards() {
            const row = currentRow(), summary = object(row.summary), values = {...summary, ...object(row.values)};
            cards.replaceChildren();
            metrics.forEach(([label, key, format, help]) => {
                const card = el('section', undefined, 'bt-metric'); card.dataset.metric = key;
                const value = el('strong', format(values[key]), ['total_r', 'average_r'].includes(key) ? tone(values[key]) : '');
                const grade = object(row.grades)[key];
                if (grade) value.classList.add('bt-grade-' + grade);
                card.append(el('p', label, 'bt-metric-label'), value, el('small', help)); cards.append(card);
            });
            let notice = summary.total_trades === 0 ? '진입한 거래가 없습니다. 비율 지표는 —로 표시됩니다.' :
                numeric(summary.unclosed) && summary.unclosed > 0 ? '미청산 ' + count(summary.unclosed) + '건 · 승률·평균 R은 청산된 거래만 반영합니다.' : '';
            if (numeric(summary.uncertain) && summary.uncertain > 0)
                notice += (notice ? ' ' : '') + '순서 미확정 ' + count(summary.uncertain) +
                    '건 · 진입한 1분봉의 가격 순서를 확인할 수 없어 승률·평균 R에서 제외합니다.';
            status.textContent = notice; status.classList.toggle('hidden', !notice);
        }
        function drawCharts() {
            const detail = object(view.detail);
            const points = (pairs, field) => (Array.isArray(pairs) ? pairs : []).filter(Array.isArray)
                .map(([time, value]) => ({exit_time_ms: time, [field]: value}));
            charts.replaceChildren(
                chart('누적 R', 'RR ' + view.rr + ' · 청산 순서', points(detail.equity, 'equity_r'), 'equity_r'),
                chart('낙폭', '최고점에서 내려간 R', points(detail.drawdown, 'drawdown_r'), 'drawdown_r', {drawdown: true}),
                chart('월별 성과', '합계 R', detail.monthly, 'total_r', {bars: true, label: point => point.month || '—'}));
        }
        function drawSlots() {
            const shown = object(view.slots), check = object(shown.check), all = object(shown.all), picked = object(shown.chosen);
            slots.replaceChildren(el('h4', '시간대 선정'));
            const chosen = hourText(Array.isArray(shown.ranges) ? shown.ranges : []);
            slots.append(el('p', chosen ? '평균 R > 0 · ' + count(shown.min_trades) + '건 이상: ' + chosen :
                '평균 R이 0보다 크고 ' + count(shown.min_trades) + '건 이상인 시간대가 없습니다.', 'bt-slot-hours'));
            const fields = [['구분', 'name'], ['거래 수', 'trades', count], ['승률', 'win_rate', percent],
                ['평균 R', 'average_r', r3], ['합계 R', 'total_r', r]];
            slots.append(table([{name: '24시간', ...all}, {name: '선정 시간대', ...picked}], fields, '24시간과 선정 시간대'));
            if (Object.keys(check).length) {
                const early = hourText(Array.isArray(check.ranges) ? check.ranges : []);
                const late = object(check.chosen), whole = object(check.all);
                slots.append(el('p', '확인: 앞 70% 기간으로 고른 시간대(' + (early || '없음') + ')를 뒤 30% 기간에 적용 → 평균 R ' +
                    r3(late.average_r) + ' (' + count(late.trades) + '건) · 같은 기간 24시간 ' + r3(whole.average_r) +
                    ' (' + count(whole.trades) + '건)', 'bt-slot-check'));
            }
        }
        function drawAccounts() {
            const shown = object(view.accounts), start = numeric(view.start_balance) ? view.start_balance : 10000;
            accounts.replaceChildren(el('h4', money(start) + ' 복리 계좌'));
            const rows = [];
            for (const [key, name] of [['all', '24시간'], ['chosen', '선정 시간대']]) {
                const item = object(shown[key]);
                for (const [kind, label] of [['one_percent', '1%'], ['quarter_kelly', '¼ 켈리']]) {
                    const account = object(item[kind]), missing = !Object.keys(account).length;
                    rows.push({...account, name: name + ' · ' + label + (numeric(account.fraction) && kind === 'quarter_kelly' ?
                        ' (' + (account.fraction * 100).toFixed(2) + '%)' : '') +
                        (missing ? (kind === 'quarter_kelly' && Object.keys(object(item.one_percent)).length ? ' · 우위 없음' : ' · 거래 없음') : '')});
                }
            }
            accounts.append(table(rows, [['계좌', 'name'], ['최종 잔액', 'final', money], ['손익', 'profit', money],
                ['수익률', 'return', percent], ['최대 낙폭', 'max_drawdown', percent], ['최대 동시 보유', 'max_open', count]],
                '계좌별 결과'));
            accounts.append(el('p', '진입할 때 잔액의 1%(또는 ¼ 켈리)를 1R로 걸고 청산 때 반영', 'bt-basis'));
        }
        async function showTrades(offset, version) {
            body.replaceChildren(empty('거래내역을 읽고 있습니다…'));
            const tradeFields = [['알림 (한국)', 'alert_time', kst], ['전략', 'strategy'], ['종목', 'symbol'], ['TF', 'tf'],
                ['방향', 'direction', direction], ['RR', 'rr'], ['결과', 'result', resultLabel], ['R', 'r'],
                ['진입 (한국)', 'entry_time', kst], ['청산 (한국)', 'exit_time', kst], ['진입가', 'entry_price'], ['손절가', 'stop_price']];
            try {
                const page = await api('mo/backtest/trades?id=' + encodeURIComponent(job) + '&rr=' + encodeURIComponent(view.rr) +
                    '&period=' + encodeURIComponent(view.period || 'all') + '&offset=' + offset + '&limit=100');
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
        function drawTab() {
            const version = ++generation, detail = object(view.detail);
            buttons.forEach(button => {const active = button.dataset.btTab === activeTab;
                button.classList.toggle('active', active); button.setAttribute('aria-selected', active); button.tabIndex = active ? 0 : -1;});
            body.setAttribute('aria-labelledby', 'bt-tab-' + activeTab); body.replaceChildren();
            if (activeTab === 'trades') {showTrades(0, version); return;}
            if (activeTab === 'monthly') body.append(table(array(detail.monthly), [['월', 'month'], ...columns], '월별 성과'));
            else if (activeTab === 'hourly') body.append(table(array(detail.hourly),
                [['시', 'hour', value => numeric(value) ? String(value).padStart(2, '0') + '시' : '—'], ...columns], '시간대별 성과'));
            else if (activeTab === 'timeframe') body.append(table(grouped(detail.by_timeframe), [['TF', 'name'], ...columns], 'TF별 성과'));
            else if (activeTab === 'groups') body.append(el('h4', '전략별'), table(grouped(detail.by_strategy), [['전략', 'name'], ...columns], '전략별 성과'),
                el('h4', '종목별'), table(grouped(detail.by_symbol), [['종목', 'name'], ...columns], '종목별 성과'),
                el('h4', '방향별'), table(grouped(detail.by_direction).map(row => ({...row, name: direction(row.name)})),
                    [['방향', 'name'], ...columns], '방향별 성과'));
        }
        function draw() {periodOptions(); drawTable(); drawCards(); drawCharts(); drawSlots(); drawAccounts(); drawTab();}
        async function load(period, rr) {
            const ticket = ++request;
            [year, month, sort].forEach(node => {node.disabled = true;});
            const address = 'mo/backtest/analysis?id=' + encodeURIComponent(job) + '&period=' + encodeURIComponent(period);
            try {
                if (!rr) {
                    // Another period: its RR table first, then the top RR of the current ranking is computed once (수정본170).
                    const ranked = await api(address + '&table=1');
                    if (ticket !== request || !dashboard.isConnected) return;
                    rr = topRow(sortedRows(array(object(ranked).rr_table), sortKey)) || null;
                }
                const next = await api(address + (rr ? '&rr=' + encodeURIComponent(rr) : ''));
                if (ticket !== request || !dashboard.isConnected) return;
                view = object(next);
                draw();
            } catch (error) {
                if (ticket !== request || !dashboard.isConnected) return;
                status.textContent = '결과를 다시 계산하지 못했습니다: ' + error.message; status.classList.remove('hidden');
            } finally {
                if (ticket === request) {[year, sort].forEach(node => {node.disabled = false;}); month.disabled = year.value === 'all';}
            }
        }
        function choose(rr) {if (rr && rr !== view.rr) load(view.period || 'all', rr); else drawTable();}
        year.onchange = () => load(year.value, null);
        month.onchange = () => load(month.value, null);
        sort.onchange = () => {sortKey = sort.value; choose(topRow(sortedRows(array(view.rr_table), sortKey)));};
        sort.value = sortKey; draw();
    }
    return {render};
})();
