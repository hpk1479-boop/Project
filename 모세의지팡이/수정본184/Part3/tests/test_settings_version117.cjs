/* Current settings DOM/CSS/scripts with synthetic API values: the version label sits at the lower right. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..'), evidence = process.argv[3];
fs.mkdirSync(evidence, {recursive: true});
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const measure = page => page.evaluate(() => {
    const view = document.querySelector('#view-settings'), label = document.querySelector('#settings-version');
    const rect = element => { const box = element.getBoundingClientRect(); return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}; };
    const last = view.children[view.children.length - 2];
    const inner = rect(view), padding = parseFloat(getComputedStyle(view).paddingRight);
    return {view: inner, label: rect(label), previous: rect(last), padding, align: getComputedStyle(label).textAlign,
            text: label.textContent, display: getComputedStyle(view).display};
});
async function open(browser, width, height, name) {
    const page = await browser.newPage({viewport: {width, height}});
    await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
        route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
    await page.goto('http://127.0.0.1:8763/');
    await page.addStyleTag({content: fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8')});
    await page.evaluate(() => {
        window.setInterval = () => 0;
        window.fetch = async () => ({ok: true, json: async () => ({settings: {provider: 'disabled'}})});
        window.api = async route => route === 'mo/settings' ? {
            app_revision: 117, live: [], part2: {}, connections: {}, ai: {provider: 'disabled'}} : {modules: {}, lines: []};
    });
    await page.addScriptTag({content: fs.readFileSync(path.join(root, 'Part3/web/unified.js'), 'utf8')});
    await page.evaluate(async () => { await moView('settings'); });
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#toast')).opacity === '0');
    return page;
}
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const checks = [];
    try {
        // A long page: the label is the last thing, under every panel, at the right edge.
        const page = await open(browser, 1280, 900);
        let box = await measure(page);
        assert.equal(box.text, '버전 117');
        assert.equal(box.align, 'right');
        assert.equal(box.display, 'flex');
        assert.ok(box.label.right <= box.view.right + 0.5 && box.label.right >= box.view.right - box.padding - 0.5,
            'right edge ' + JSON.stringify(box));
        assert.ok(box.label.top >= box.previous.bottom - 0.5, 'below the last panel');
        await page.locator('#settings-version').scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(evidence, 'settings_version_long_page.png'), animations: 'disabled'});
        checks.push('긴 설정 화면: 모든 패널 아래, 오른쪽 끝에 버전 표시');
        await page.close();

        // A tall window: the label still sits at the bottom of the view, not right under the last panel.
        const tall = await open(browser, 1280, 3200);
        box = await measure(tall);
        assert.ok(box.view.bottom - box.label.bottom <= 40, 'bottom gap ' + JSON.stringify(box));
        assert.ok(box.label.right >= box.view.right - box.padding - 0.5);
        await tall.screenshot({path: path.join(evidence, 'settings_version_tall_window.png'), animations: 'disabled'});
        checks.push('화면이 큰 창: 설정 화면 맨 아래 오른쪽');
        await tall.close();

        // A phone-width window keeps it right aligned under the panels.
        const narrow = await open(browser, 360, 800);
        box = await measure(narrow);
        assert.equal(box.align, 'right');
        assert.ok(box.label.top >= box.previous.bottom - 0.5);
        assert.ok(Math.abs(box.label.right - (box.view.right - box.padding)) <= 1);
        checks.push('좁은 창에서도 오른쪽 정렬');
        await narrow.close();

        // The other views are untouched by the settings layout.
        const home = await open(browser, 1280, 900);
        await home.evaluate(async () => { await moView('live'); });
        assert.equal(await home.locator('#view-settings').evaluate(view => getComputedStyle(view).display), 'none');
        checks.push('설정이 아닌 화면에서는 설정 화면이 숨겨진 상태 유지');
        await home.close();
        fs.writeFileSync(path.join(evidence, 'settings_version_checks.json'), JSON.stringify({passed: true, checks}, null, 2) + '\n');
        console.log('PASS ' + checks.length + ' browser scenarios');
    } finally { await browser.close(); }
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
