/* Current Edge UI -> real local Part3 HTTP routes -> scripted model -> temporary storage. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const evidence = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '수정형대화59');
fs.mkdirSync(evidence, {recursive:true});
const results = [], trace = [];
const pass = name => results.push({name,passed:true});

(async () => {
    const fixture = spawn(process.argv[3], ['-B', path.join(__dirname,'test_ai_revision59.py'), '--ui-server'],
        {windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    const lines=[], pending=[];
    const output=readline.createInterface({input:fixture.stdout});
    output.on('line', line => pending.length ? pending.shift()(JSON.parse(line)) : lines.push(JSON.parse(line)));
    let stderr='';fixture.stderr.on('data', data => {stderr+=data.toString();});
    const next = () => lines.length ? Promise.resolve(lines.shift()) : new Promise((resolve,reject)=> {
        const timer=setTimeout(()=>reject(new Error('fixture timeout '+stderr)),10000);
        pending.push(value=>{clearTimeout(timer);resolve(value);});
    });
    const status = async () => {fixture.stdin.write('status\n');return next();};
    let browser;
    try {
        const {port,token}=await next(), origin=`http://127.0.0.1:${port}`;
        browser=await chromium.launch({channel:'msedge',headless:true});
        const page=await browser.newPage({viewport:{width:1440,height:900}});
        const errors=[], requests=[], chats=[];
        page.on('pageerror', error=>errors.push(error.message));
        page.on('request', req=>{
            const route=new URL(req.url()).pathname;
            if(req.method()==='POST' && route.startsWith('/api/'))requests.push({route,data:req.postDataJSON()});
        });
        page.on('response', async res=>{
            if(new URL(res.url()).pathname==='/api/ai/chat' && res.ok())chats.push(await res.json());
        });
        // Only unrelated live polling is stubbed; interpretation/apply/draft/generate use real server.
        await page.route(origin+'/api/mo/live/status**',route=>route.fulfill({json:{modules:{},lines:[]}}));
        await page.goto(origin+'/#token='+token);
        await page.waitForFunction(()=>!!state.meta);
        await page.evaluate(()=>moView('strategy'));
        async function send(text, count) {
            await page.locator('#ai-text').fill(text);await page.locator('#ai-send').click();
            await page.waitForFunction(()=>!document.querySelector('#ai-send').disabled);
            assert.equal(await page.locator('.ai-interpretation').count(),count);
        }
        await send('15분 상승추세일 때 3분 하단 원비 터치하면 1분 일반 브레이커를 감시해',1);
        const first=await status();trace.push({stage:'최초 해석',...first});
        assert.match(await page.locator('.ai-interpretation').last().innerText(),/3분봉 하단 WONBI 터치/);
        assert.equal(first.intent.interpretation.steps[1].tfs[0],'3m');
        assert.equal(first.generated_count,0);assert.equal(first.draft_exists,false);
        assert.equal(await page.locator('#generate-button').isDisabled(),true);
        assert.equal(requests.some(x=>['/api/ai/apply','/api/draft','/api/generate'].includes(x.route)),false);
        pass('First interpretation remains unapplied and creates no draft or strategy file');

        await send('아니 원비는 15분이야',2);
        const second=await status();trace.push({stage:'수정 요청 → 전체 intent',...second});
        const expected=structuredClone(first.intent);expected.interpretation.steps[1].tfs=['15m'];
        assert.deepEqual(second.intent,expected);assert.deepEqual(second.provider_context,first.intent);
        assert.match(await page.locator('.ai-interpretation').last().innerText(),/15분봉 하단 WONBI 터치/);
        assert.equal(await page.locator('#ai-mode').innerText(),'수정된 전략 의도');
        assert.equal(await page.locator('#ai-pending').isVisible(),true);
        assert.equal(second.generated_count,0);assert.equal(second.draft_exists,false);
        assert.equal(requests.some(x=>['/api/ai/apply','/api/draft','/api/generate'].includes(x.route)),false);
        await page.locator('.ai-interpretation').last().locator('details summary').click();
        const raw=JSON.parse(await page.locator('.ai-interpretation').last().locator('details pre').innerText());
        assert.deepEqual(raw.result,second.intent);
        await page.locator('.ai-interpretation').last().locator('details summary').click();
        await page.screenshot({path:path.join(evidence,'revised_intent.png'),fullPage:true});
        pass('Natural-language revision receives previous canonical intent; only WONBI timeframe changes, full result is shown');

        const stale=await page.evaluate(async session => {
            const res=await fetch('/api/ai/apply',{method:'POST',headers:{'Content-Type':'application/json',
                'X-Lab-Token':sessionStorage.getItem('part3-token')},body:JSON.stringify({session,revision:1})});
            return {status:res.status,body:await res.json()};
        },requests.find(x=>x.route==='/api/ai/chat').data.session);
        assert.equal(stale.status,400);assert.match(stale.body.error,/最新|최신 해석/);
        assert.equal((await status()).draft_exists,false);
        pass('Confirmation of the superseded interpretation is rejected by the actual server');

        await page.locator('#ai-confirm').click();
        await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled && !document.querySelector('#ai-send').disabled);
        const applied=await status();trace.push({stage:'사용자 재확인 → Part3 적용',...applied});
        assert.equal(requests.filter(x=>x.route==='/api/ai/apply').at(-1).data.revision,2);
        assert.deepEqual(await page.evaluate(()=>state.recipe.strategy_intent),second.intent.interpretation);
        assert.equal(applied.draft_exists,true);assert.equal(applied.generated_count,0);
        assert.equal(applied.has_candidate,false);
        await page.screenshot({path:path.join(evidence,'confirmed_preview.png'),fullPage:true});
        pass('Explicit reconfirmation applies revised Recipe v2 and previews it without strategy file generation');

        // Delay a real revision request to inspect the waiting UI and prevent duplicate Ctrl+Enter.
        let release, entered;
        const gate=new Promise(resolve=>{release=resolve;}), arriving=new Promise(resolve=>{entered=resolve;});
        await page.route(origin+'/api/ai/chat',async route=>{entered();await gate;await route.continue();});
        await page.locator('#ai-text').fill('감시 시간만 2분으로 바꿔');await page.locator('#ai-send').click();
        await arriving;
        assert.equal(await page.locator('#generate-button').isDisabled(),true);
        assert.equal(await page.evaluate(()=>state.recipe),null);
        for(const id of ['ai-confirm','ai-cancel','ai-reset'])assert.equal(await page.locator('#'+id).isDisabled(),true);
        await page.locator('#ai-text').fill('중복 요청');await page.locator('#ai-text').press('Control+Enter');
        const count=requests.filter(x=>x.route==='/api/ai/chat').length;assert.equal(count,3);
        release();await page.waitForFunction(()=>!document.querySelector('#ai-send').disabled);
        const third=await status();trace.push({stage:'적용 후 추가 수정 → 재확인 대기',...third});
        const expectedThird=structuredClone(second.intent);expectedThird.interpretation.final_window_sec=120;
        assert.deepEqual(third.intent,expectedThird);assert.deepEqual(third.provider_context,second.intent);
        assert.equal(third.generated_count,0);assert.equal(await page.locator('#generate-button').isDisabled(),true);
        assert.equal(requests.filter(x=>x.route==='/api/draft').length,1);
        pass('Editing an applied interpretation invalidates old preview; waiting UI blocks generation, stale confirmation and duplicate send');

        await page.locator('#ai-confirm').click();
        await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled && !document.querySelector('#ai-send').disabled);
        assert.deepEqual(await page.evaluate(()=>state.recipe.strategy_intent),third.intent.interpretation);
        await page.locator('#generate-button').click();
        await page.waitForFunction(()=>!state.busy && !!state.lastGenerated);
        const generated=await status();trace.push({stage:'별도 생성 버튼',...generated});
        assert.equal(generated.generated_count,1);
        assert.equal(requests.filter(x=>x.route==='/api/generate').length,1);
        assert.deepEqual(requests.find(x=>x.route==='/api/generate').data.recipe.strategy_intent,third.intent.interpretation);
        pass('Strategy file is generated only by a separate click after reconfirming the latest revision');

        assert.equal(await page.locator('#ai-reset').innerText(),'초기화');
        await page.locator('#ai-reset').click();
        await page.waitForFunction(()=>!document.querySelector('#ai-reset').disabled);
        assert.equal(await page.locator('.ai-interpretation').count(),0);
        assert.equal(await page.locator('#generate-button').isDisabled(),true);
        await send('별도 신규 전략',1);
        const reset=await status();assert.equal(reset.revision,1);assert.equal(reset.provider_context,null);
        assert.equal(reset.intent.interpretation.steps[1].tfs[0],'3m');
        assert.equal(chats.at(-1).is_revision,false);
        assert.deepEqual(errors,[]);
        pass('Starting a new conversation clears draft context and confirmation while leaving generated files intact');
        await page.close();
    } finally {
        if(browser)await browser.close();
        fixture.stdin.write('stop\n');fixture.stdin.end();
        await new Promise(resolve=>fixture.once('exit',resolve));output.close();
        fs.writeFileSync(path.join(evidence,'ui_flow.json'),JSON.stringify({tests:results.length,results,trace,
            environment:'Edge -> real authenticated HTTP -> scripted model -> temporary files; live polling stubbed'},null,2)+'\n');
        if(stderr)console.error(stderr);
    }
    console.log(`PASS ${results.length} revision UI/HTTP flow tests`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
