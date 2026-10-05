/* Real HTTP/UI integration. Mutable settings and strategies live in the Python fixture. */
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const fixture = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
fs.mkdirSync(fixture.evidence, {recursive:true});
(async () => {
    const browser = await chromium.launch({channel:'msedge',headless:true});
    const checks = [], errors = [], failedResponses = [];
    try {
        const page = await browser.newPage({viewport:{width:1280,height:900}}), responseCodes = {};
        page.on('pageerror', error => errors.push(error.message));
        page.on('dialog', dialog => dialog.accept());
        page.on('response', response => {
            const route = new URL(response.url()).pathname;
            responseCodes[route] = response.status();
            if (response.status() >= 400) failedResponses.push({route,status:response.status()});
        });
        await page.route('**/*', route => route.request().url().startsWith(fixture.url) ? route.continue() : route.abort());
        await page.addInitScript(() => {
            window.strategyPopupDisplays = [];
            const show = HTMLDialogElement.prototype.showModal;
            HTMLDialogElement.prototype.showModal = function () {
                const result = show.call(this);
                if (this.id === 'strategy-settings-dialog') {
                    const rect = this.getBoundingClientRect();
                    strategyPopupDisplays.push({cards:this.querySelectorAll('[data-strategy]').length,
                        left:rect.left,top:rect.top,width:rect.width,height:rect.height});
                }
                return result;
            };
        });
        await page.goto(fixture.url + '/#token=' + encodeURIComponent(fixture.token));
        const openList = async id => {
            await page.locator('.moses-nav button[data-moses-view="strategy"]').click();
            await page.locator('#ai-preset-open').click();
            await page.waitForFunction(() => document.querySelectorAll('#ai-preset-list > details').length === 2 && !document.querySelector('#ai-preset-open').disabled);
            if (id) await page.locator('[data-preset-id="'+id+'"]').click();
        };
        const openBacktest = async () => {
            await page.locator('#strategy-settings-apply').click();
            await page.waitForFunction(() => !document.querySelector('#strategy-settings-dialog').open);
            await page.locator('.moses-nav button[data-moses-view="backtest"]').click();
            await page.locator('#mo-bt-edit-specials').click();
            await page.waitForFunction(() => document.querySelector('#strategy-settings-dialog').open && !moStrategyPopup.busy);
        };
        await openList();
        assert.equal(responseCodes['/strategy_library.js'], 200);
        assert.equal(responseCodes['/api/ai/presets'], 200);
        assert.match(await page.locator('#ai-preset-list').innerText(), /생성한 전략이 없습니다/);
        assert.equal(await page.locator('#strategy-settings-other-part').count(), 0);
        checks.push('현재 목록 JS와 인증 API 200·빈 생성 목록·삭제한 이동 버튼 없음');
        await page.locator('#ai-preset-close').click();
        await page.evaluate(async recipe => {await part3ApplyAIRecipe(recipe);}, fixture.recipe);
        await page.locator('#generate-button').click();
        await page.waitForFunction(() => state.lastGenerated?.endsWith('.py'));
        await openList();
        const id = await page.locator('#ai-preset-list [data-preset-id^="TEST_SPECIAL"]').getAttribute('data-preset-id');
        await page.locator('[data-preset-id="'+id+'"]').click();
        assert.equal(await page.evaluate(id => moState.specials?.items?.[id] !== undefined, id), false);
        await page.locator('#strategy-library-promote').click();
        await page.waitForSelector('#strategy-settings-dialog[open] [data-strategy="'+id+'"]');
        const layout = await page.evaluate(async () => {
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
            const rect = document.querySelector('#strategy-settings-dialog').getBoundingClientRect();
            return {shown:strategyPopupDisplays,current:{cards:document.querySelectorAll('#strategy-settings-cards [data-strategy]').length,
                left:rect.left,top:rect.top,width:rect.width,height:rect.height}};
        });
        assert.equal(layout.shown.length, 1);
        assert.ok(layout.shown[0].cards > 0);
        assert.deepEqual(layout.shown[0], layout.current, 'the first visible dialog already has its final position and dimensions');
        assert.equal(await page.locator('[data-strategy="'+id+'"] .moses-strategy-toggle input').isChecked(), false);
        await openBacktest();
        assert.equal(await page.locator('[data-strategy="'+id+'"] .moses-strategy-toggle input').isChecked(), false);
        checks.push('실제 생성·양파트 승급·기본 실행 꺼짐·각 파트 저장과 직접 메뉴 이동');
        await page.locator('[data-strategy="'+id+'"] .moses-strategy-toggle input').check();
        await page.locator('#strategy-list-delete').click();
        await page.waitForFunction(id => !moStrategyPopup.busy && !moStrategyPopup.draft[id], id);
        await page.locator('#strategy-settings-cancel').click();
        await openList(id);
        const onePart = await page.evaluate(async () => ({library:await api('strategies'),live:await api('mo/live/specials'),backtest:await api('mo/backtest/options')}));
        assert.ok(onePart.library.items.some(item => item.id===id));
        assert.ok(onePart.live.items[id]);
        assert.equal(onePart.backtest.specials.includes(id), false);
        await page.locator('#strategy-library-rename').click();
        await page.locator('#strategy-library-name').fill('실제 이름 변경 완료');
        await page.locator('#strategy-library-rename-save').click();
        await page.waitForFunction(id => document.querySelector('[data-preset-id="'+id+'"]')?.textContent === '실제 이름 변경 완료', id);
        await page.locator('#strategy-library-promote').click();
        await page.waitForSelector('#strategy-settings-dialog[open] [data-strategy="'+id+'"]');
        assert.match(await page.locator('[data-strategy="'+id+'"] .moses-strategy-toggle').innerText(), /실제 이름 변경 완료/);
        await openBacktest();
        assert.match(await page.locator('[data-strategy="'+id+'"] .moses-strategy-toggle').innerText(), /실제 이름 변경 완료/);
        await page.locator('#strategy-settings-cancel').click();
        await openList(id);
        const promoted = await page.evaluate(async id => (await api('strategies')).items.find(item => item.id===id), id);
        assert.deepEqual(promoted.registrations.sort(), ['Part1','Part2']);
        checks.push('Part2 선택삭제는 Part1·Part3 보존·이름변경·재승급 양파트 표시명 유지');
        await page.locator('#strategy-library-delete').click();
        await page.waitForFunction(id => !document.querySelector('[data-preset-id="'+id+'"]'), id);
        const remaining = await page.evaluate(async () => ({library:await api('strategies'),live:await api('mo/live/specials'),backtest:await api('mo/backtest/options')}));
        assert.equal(remaining.library.items.length, 0);
        assert.equal(remaining.live.items[id], undefined);
        assert.equal(remaining.backtest.specials.includes(id), false);
        checks.push('Part3 삭제는 파일·메타데이터·양파트 등록 모두 제거');
        await page.locator('#ai-preset-close').click();
        await page.evaluate(async recipe => {await part3GenerateAIRecipe({...recipe,name:'실제 AI 확인 전략'});}, fixture.recipe);
        await openList();
        assert.match(await page.locator('#ai-preset-list').innerText(), /실제 AI 확인 전략/);
        const generated = await page.evaluate(async () => (await api('strategies')).items);
        assert.equal(generated.length, 1);
        assert.deepEqual(generated[0].registrations, []);
        checks.push('AI 확인 생성도 미승급 상태로 목록에 저장');
        assert.deepEqual(errors, []);
        assert.deepEqual(failedResponses, []);
        await page.screenshot({path:path.join(fixture.evidence,'http_library.png'),fullPage:true});
        fs.writeFileSync(path.join(fixture.evidence,'http_ui_checks.json'),JSON.stringify({passed:true,checks,errors,failedResponses,
            environment:'real localhost/static web/authenticated APIs; temporary mutable state; live engines and jobs isolated'},null,2)+'\n');
        console.log(JSON.stringify({passed:true,checks:checks.length}));
    } finally {await browser.close();}
})().catch(error => {console.error(error.stack); process.exitCode=1;});
