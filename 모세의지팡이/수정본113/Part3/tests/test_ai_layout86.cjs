/* Layout-only regression: local HTML/CSS, no app server or AI requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'Part3/web/index.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const proof = path.join(root, '검증결과/AI레이아웃86');
(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    const results = [];
    try {
        const page = await browser.newPage();
        await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:8763/' ?
            route.fulfill({contentType: 'text/html; charset=utf-8', body: html}) : route.abort());
        await page.goto('http://127.0.0.1:8763/');
        await page.addStyleTag({content: fs.readFileSync(path.join(root, 'Part3/web/style.css'), 'utf8')});
        for (const [width, height] of [[1280,720], [1920,1080], [1280,500], [960,540], [375,812]]) {
            await page.setViewportSize({width, height});
            for (const expanded of [false, true]) {
                await page.evaluate(expanded => {
                    document.querySelector('#ai-pending').classList.toggle('hidden', !expanded);
                    document.querySelector('#ai-text').style.height = expanded ? '210px' : '';
                    const messages = document.querySelector('#ai-messages');
                    messages.replaceChildren();
                    for (let i = 0; i < (expanded ? 50 : 0); i++) {
                        const message = document.createElement('div');
                        message.className = 'ai-msg ai-assistant';
                        message.textContent = '전략 조건 해석 결과를 확인합니다. '.repeat(8);
                        messages.append(message);
                    }
                }, expanded);
                const bounds = await page.evaluate(() => {
                    const rect = selector => {
                        const r = document.querySelector(selector).getBoundingClientRect();
                        return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};
                    };
                    return {panel:rect('.ai-panel'),input:rect('.ai-input'),send:rect('#ai-send'),
                        reset:rect('#ai-reset'),log:rect('.ai-main .log-panel'),
                        overflow:document.documentElement.scrollWidth > innerWidth + 1,
                        messageHeight:document.querySelector('#ai-messages').clientHeight};
                });
                const name = `${width}x${height} ${expanded ? 'expanded' : 'initial'}`;
                assert.ok(bounds.input.bottom <= bounds.panel.bottom - 1, name + ': input escapes panel');
                assert.ok(bounds.send.bottom <= bounds.panel.bottom - 1, name + ': send button escapes panel');
                assert.ok(bounds.reset.bottom <= bounds.panel.bottom - 1, name + ': reset button escapes panel');
                assert.equal(await page.locator('#ai-reset').innerText(), '초기화');
                assert.ok(bounds.panel.bottom <= bounds.log.top, name + ': panel overlaps log');
                assert.ok(!bounds.overflow, name + ': horizontal overflow');
                assert.ok(bounds.messageHeight <= Math.max(240,height*.48) + 2, name + ': history is unbounded');
                results.push({name,pass:true});
            }
        }
        fs.mkdirSync(proof, {recursive:true});
        fs.writeFileSync(path.join(proof, 'results.json'), JSON.stringify({results},null,2)+'\n');
        await page.setViewportSize({width:1280,height:720});
        await page.evaluate(() => {
            document.querySelector('#ai-pending').classList.add('hidden');
            document.querySelector('#ai-text').style.height = '';
            document.querySelector('#ai-messages').replaceChildren();
        });
        await page.screenshot({path:path.join(proof,'layout.png'),fullPage:true});
        console.log('PASS '+results.length+' AI layout scenarios');
    } finally { await browser.close(); }
})().catch(error => {console.error(error.message);process.exitCode=1;});
