/* Current Part3 UI in isolated Edge. All APIs mocked; no strategy files or engines created. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const evidence = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '전략생성화면58');
fs.mkdirSync(evidence, {recursive: true});
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const css = fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8');
const results = [];
const pass = name => results.push({name, passed: true});

(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 900}});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
            route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
        await page.goto('http://127.0.0.1:8763/');
        await page.addStyleTag({content: css});
        await page.evaluate(() => {
            window.requests = [];
            window.setInterval = () => 0;
            window.flush = async () => {for (let i=0; i<30; i++) await Promise.resolve();};
            window.recipeFixture = {name: '화면 시험 전략'};
            window.fetch = async (url, options={}) => {
                const route = url.replace('/api/', '');
                const data = options.body ? JSON.parse(options.body) : undefined;
                requests.push({route, data});
                let body = {ok: true};
                const items = [{filename:'Test_SPECIAL123.py', name:'기존 생성 전략', created_at:'2026-10-01T00:00:00'}];
                if (route === 'init') body = {recent: items, connections: {warehouse: ''}};
                else if (route === 'ai/settings') body = {settings: {model:'qwen3.5:4b'}, base_url:'http://127.0.0.1:11434'};
                else if (route === 'mo/settings') body = {live:[], part2:{}, connections:{}, ai:{model:'qwen3.5:4b',timeout:90}};
                else if (route.startsWith('mo/live/status')) body = {modules:{STAFF:{name:'STAFF',state:'연결 대기'}},lines:[]};
                else if (route === 'ai/chat') body = {actions:[], can_apply:true, result:{supported:true,
                    interpretation:{direction:'LONG',symbols:['XAUUSD+'],steps:[],inherit_base_rules:true,
                        base_special:'SPECIAL1',final:{kind:'OZ',tf:'1m'},persistent:true}}};
                else if (route === 'ai/apply') body = {recipe:recipeFixture};
                else if (route === 'preview') body = {code:'# preview strategy\n',filename:'Test_SPECIAL124.py'};
                else if (route === 'generate') body = {code:'# generated strategy\n',filename:'Test_SPECIAL124.py'};
                else if (route === 'recent') body = {items};
                return {ok:true,status:200,json:async () => body};
            };
        });
        for (const name of ['app.js', 'ai_display.js', 'ai_chat.js', 'unified.js']) {
            await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web', name), 'utf8')});
        }
        await page.evaluate(async () => {
            document.dispatchEvent(new Event('DOMContentLoaded')); await flush(); await moView('strategy');
        });
        assert.equal(await page.locator('#recent-list, .ai-recent').count(), 0);
        assert.equal(await page.getByText('최근 생성한 전략').count(), 0);
        assert.deepEqual(errors, []);
        assert.match(await page.locator('#log').innerText(), /화면을 시작했습니다/);
        assert.equal(await page.evaluate(() => requests.filter(x => x.route === 'recent').length), 0);
        pass('Recent strategies removed; initialization succeeds without a list request');

        const dimensions = [];
        for (const size of [{width:1440,height:900},{width:1280,height:800},{width:900,height:800}]) {
            await page.setViewportSize(size);
            const layout = await page.evaluate(() => {
                const rect = s => {const r=document.querySelector(s).getBoundingClientRect();
                    return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
                return {panel:rect('.ai-panel'),main:rect('.ai-main'),preview:rect('.ai-preview'),
                    input:rect('#ai-text'),messages:rect('#ai-messages'),
                    overflow:document.documentElement.scrollWidth > innerWidth};
            });
            assert.ok(layout.panel.height >= layout.main.height * .70, JSON.stringify(layout));
            assert.ok(layout.input.height >= 110);
            assert.ok(layout.messages.height >= 240);
            assert.equal(layout.overflow, false);
            if (size.width > 980) {
                assert.ok(layout.panel.width >= layout.preview.width * 2);
                assert.ok(layout.preview.x >= layout.panel.x + layout.panel.width);
            } else {
                assert.ok(layout.preview.y >= layout.main.bottom);
            }
            dimensions.push({viewport:size,...layout});
            await page.screenshot({path:path.join(evidence, `strategy_${size.width}.png`),fullPage:true});
            pass(`AI area is larger and controls fit at ${size.width}x${size.height}`);
        }

        await page.setViewportSize({width:1440,height:900});
        await page.locator('#ai-text').fill('상승 FVG 이후 올존을 감시하는 전략');
        await page.locator('#ai-send').click();
        await page.waitForFunction(() => !document.querySelector('#ai-pending').classList.contains('hidden'));
        assert.match(await page.locator('#ai-messages').innerText(), /상승 FVG/);
        assert.match(await page.locator('#ai-messages').innerText(), /AI 해석 결과/);
        pass('AI interpretation still sends input and displays the response');

        await page.locator('#ai-confirm').click();
        await page.waitForFunction(() => !document.querySelector('#generate-button').disabled);
        assert.match(await page.locator('#code-preview').innerText(), /preview strategy/);
        await page.locator('#generate-button').click();
        await page.waitForFunction(() => document.querySelector('#code-preview').textContent.includes('generated strategy'));
        const generated = await page.evaluate(() => ({filename:state.lastGenerated,
            recentRequests:requests.filter(x => x.route === 'recent').length,
            errorLog:document.querySelector('#log .error')?.textContent || ''}));
        assert.deepEqual(generated, {filename:'Test_SPECIAL124.py',recentRequests:0,errorLog:''});
        assert.deepEqual(errors, []);
        pass('Preview and generation succeed without touching removed recent-list elements');

        await page.locator('[data-action="backtest"]').click();
        await page.waitForSelector('#bt-file');
        assert.equal(await page.locator('#bt-file option').first().innerText(), 'Test_SPECIAL123.py');
        assert.equal(await page.evaluate(() => requests.filter(x => x.route === 'recent').length), 1);
        pass('Existing generated-strategy selection remains available in the backtest dialog');
        await page.locator('#modal [data-action="close-modal"]').first().click();

        await page.evaluate(async () => {await moView('settings'); await moView('strategy');});
        assert.equal(await page.locator('#ai-messages .ai-msg').count(), 3);
        assert.equal(await page.locator('#generate-button').isDisabled(), false);
        pass('Navigation retains the AI conversation and generated preview');
        fs.writeFileSync(path.join(evidence,'layout_dimensions.json'),JSON.stringify(dimensions,null,2)+'\n');
        await page.close();
    } finally {
        await browser.close();
        fs.writeFileSync(path.join(evidence,'ui_tests.json'),JSON.stringify({tests:results.length,results,
            environment:'isolated headless Edge; mocked APIs'},null,2)+'\n');
    }
    console.log(`PASS ${results.length} Part3 strategy UI tests`);
})().catch(error => {console.error(error.stack); process.exitCode=1;});
