/* Current settings DOM/scripts with synthetic API values. No Telegram/live engines. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1280, height: 900}});
        await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
            route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
        await page.goto('http://127.0.0.1:8763/');
        await page.addStyleTag({content: fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8')});
        await page.evaluate(() => {
            window.sampleRevision = 112;
            window.setInterval = () => 0;
            window.fetch = async () => ({ok: true, json: async () => ({settings: {provider: 'disabled'}})});
            window.api = async route => route === 'mo/settings' ? {
                app_revision: sampleRevision, live: [], part2: {}, connections: {}, ai: {provider: 'disabled'}
            } : {modules: {}, lines: []};
        });
        await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8')});
        await page.evaluate(async () => { await moView('settings'); });
        assert.equal(await page.locator('#settings-version').textContent(), '버전 112');
        assert.equal(await page.locator('#settings-version').count(), 1);
        assert.equal(await page.locator('#view-settings').evaluate(view => view.lastElementChild.id), 'settings-version');
        await page.locator('#settings-version').scrollIntoViewIfNeeded();
        // CSS is injected after the HTML in this fixture; let the existing empty
        // toast's opacity transition finish before taking a product screenshot.
        await page.waitForFunction(() => getComputedStyle(document.querySelector('#toast')).opacity === '0');
        await page.screenshot({path: path.join(evidence, 'settings_version112.png'), animations: 'disabled'});
        await page.evaluate(async () => {sampleRevision = 113; await moSettingsLoad();});
        assert.equal(await page.locator('#settings-version').textContent(), '버전 113');
        await page.evaluate(async () => {sampleRevision = null; await moSettingsLoad();});
        assert.equal(await page.locator('#settings-version').textContent(), '버전 정보 없음');
        console.log('PASS 3 browser scenarios: Settings bottom revision112; next revision113; unknown metadata feedback.');
    } finally { await browser.close(); }
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
