/* Current display renderer and model settings in isolated Edge. APIs and the editor module are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {chromium} = require(process.argv[2]);
const root = path.resolve(__dirname, '../..');
const evidence = path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), 'AI표시설정58');
fs.mkdirSync(evidence, {recursive: true});
const example = {result:{supported:true,intent:'CREATE_STRATEGY',interpretation:{direction:'LONG',
    symbols:['XAUUSD+'],symbol_source:'CURRENT_DEFAULT',steps:[
        {kind:'TREND',tfs:['15m'],direction:'LONG',bar_state:'CLOSED'},
        {kind:'WONBI_TOUCH',tfs:['3m'],side:'LOWER',direction:'LONG'}],
    order_mode:'SIMULTANEOUS',global_combine:'ALL',within_sec:null,final_window_sec:600,
    final:{kind:'OZ',tfs:['1m'],validation_mode:'NORMAL',trigger_mode:'BREAKER'},persistent:true},
    needs_clarification:false,clarification_question:null,message_ko:''},actions:[],can_apply:true,application_error:null};
const recipe = {schema_version:2,base:'AI',name:'시험 전략',description:'화면 검증',symbols:['XAUUSD+'],
    strategy_intent:example.result.interpretation};
const results=[];
const pass=name=>results.push({name,passed:true});
let aiConfig={provider:'ollama',model:'qwen3:8b',timeout:90,base_url:'http://127.0.0.1:11434'};
let models={available:true,models:['qwen3.5:4b','qwen3:8b'],error:null}, failModels=false;
const requests=[];

(async()=>{
    const browser=await chromium.launch({channel:'msedge',headless:true});
    try {
        const page=await browser.newPage({viewport:{width:1440,height:900}});
        const errors=[];page.on('pageerror',e=>errors.push(e.message));
        await page.route('**/*',route=>route.abort());
        await page.route('http://127.0.0.1:8763/**',async route=>{
            const url=new URL(route.request().url()), name=url.pathname;
            if (!name.startsWith('/api/')) {
                const file=name==='/'?'index.html':name.slice(1);
                // Exercise ai_display's supported path without editable contract fixtures.
                if(file==='ai_editor.js')return route.fulfill({contentType:'application/javascript',body:'window.part3IntentEditor = null;'});
                return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
                    contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
            }
            const data=route.request().postDataJSON();requests.push({path:name,data});
            let body={ok:true};
            if(name==='/api/init')body={recent:[],connections:{warehouse:''}};
            else if(name==='/api/ai/settings')body={settings:{...aiConfig},base_url:aiConfig.base_url};
            else if(name==='/api/ai/models') {
                if(failModels)return route.fulfill({status:500,json:{error:'unavailable'}});
                body=models;
            }
            else if(name==='/api/mo/settings' && data) {
                assert.equal(data.group,'ai');Object.assign(aiConfig,data.changes);body={ok:true,message:'저장 완료'};
            }
            else if(name==='/api/mo/settings')body={live:[],part2:{},connections:{},ai:{...aiConfig}};
            else if(name.startsWith('/api/mo/live/status'))body={modules:{STAFF:{name:'STAFF',state:'연결 대기'}},lines:[]};
            else if(name==='/api/ai/chat')body=example;
            else if(name==='/api/ai/apply')body={recipe};
            else if(name==='/api/preview')body={filename:'Test_SPECIAL123.py',code:'# validated preview'};
            else if(name==='/api/generate')body={filename:'Test_SPECIAL123.py',code:'# generated fixture'};
            return route.fulfill({json:body});
        });
        await page.goto('http://127.0.0.1:8763/#token=isolated-test');
        await page.waitForFunction(()=>!!state.meta);
        assert.equal(await page.locator('#ai-reset').innerText(),'초기화');
        await page.evaluate(()=>moView('settings'));
        assert.deepEqual(await page.locator('#ai-model-select option').allTextContents(),
            ['qwen3.5:4b','qwen3:8b']);
        assert.equal(await page.locator('#ai-model-select').inputValue(),'qwen3:8b');
        assert.equal(await page.locator('#settings-ai-fields [data-setting="model"]').isVisible(),true);
        assert.equal(aiConfig.model,'qwen3:8b');
        pass('Installed models are listed; saved default is preserved without auto-saving');

        await page.locator('#ai-model-select').selectOption('qwen3.5:4b');
        const refreshed=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/ai/settings');
        await page.locator('[data-save-settings="ai"]').click();
        assert.equal((await (await refreshed).json()).settings.model,'qwen3.5:4b');
        await page.waitForFunction(()=>document.querySelector('#ai-model-select')?.value==='qwen3.5:4b');
        assert.equal(aiConfig.model,'qwen3.5:4b');
        assert.deepEqual(requests.filter(x=>x.path==='/api/mo/settings' && x.data).at(-1).data,
            {group:'ai',changes:{model:'qwen3.5:4b'}});
        await page.screenshot({path:path.join(evidence,'model_dropdown.png'),fullPage:true});
        pass('Dropdown selection saves the model string and refreshes AI settings');

        await page.locator('#settings-ai-fields [data-setting="timeout"]').fill('120');
        await page.locator('[data-save-settings="ai"]').click();
        await page.waitForFunction(()=>document.querySelector('#settings-ai-fields [data-setting="timeout"]').dataset.original==='120' &&
            !document.querySelector('#ai-model-select').disabled);
        assert.equal(aiConfig.model,'qwen3.5:4b');
        assert.deepEqual(requests.filter(x=>x.path==='/api/mo/settings' && x.data).at(-1).data,
            {group:'ai',changes:{timeout:120}});
        pass('Timeout-only save keeps the model selection');

        await page.locator('#settings-ai-fields [data-setting="model"]').fill('organization/custom:q4_K_M');
        await page.locator('[data-save-settings="ai"]').click();
        await page.waitForFunction(()=>document.querySelector('#ai-model-select')?.value==='organization/custom:q4_K_M');
        assert.equal(aiConfig.model,'organization/custom:q4_K_M');
        assert.match(await page.locator('#ai-model-select option:checked').innerText(),/現在|현재 설정/);
        pass('Manual names and saved models missing from the installed list remain selectable');

        models={available:false,models:[],error:'Ollama 모델 목록을 불러오지 못했습니다. 모델명을 직접 입력하세요.'};
        await page.evaluate(()=>moSettingsLoad());
        assert.equal(await page.locator('#ai-model-select').isDisabled(),true);
        assert.equal(await page.locator('#settings-ai-fields [data-setting="model"]').isVisible(),true);
        await page.locator('#settings-ai-fields [data-setting="model"]').fill('qwen3.5:4b');
        await page.locator('[data-save-settings="ai"]').click();
        await page.waitForFunction(()=>document.querySelector('#settings-ai-fields [data-setting="model"]').dataset.original==='qwen3.5:4b');
        assert.equal(aiConfig.model,'qwen3.5:4b');
        await page.screenshot({path:path.join(evidence,'model_manual_fallback.png'),fullPage:true});
        pass('Ollama offline exposes manual input; manual save still works');

        failModels=true;await page.evaluate(()=>moSettingsLoad());
        assert.equal(await page.locator('#settings-ai-fields [data-setting="model"]').isVisible(),true);
        assert.match(await page.locator('#ai-model-list-status').innerText(),/조회에 실패/);
        failModels=false;models={available:true,models:[],error:null};await page.evaluate(()=>moSettingsLoad());
        assert.equal(await page.locator('#settings-ai-fields [data-setting="model"]').isVisible(),true);
        assert.match(await page.locator('#ai-model-list-status').innerText(),/사용 가능한 모델이 없습니다/);
        pass('HTTP failure and empty installation also retain manual input');

        models={available:true,models:['qwen3.5:4b','qwen3:8b'],error:null};
        await page.locator('.ai-model-controls button').click();
        await page.waitForFunction(()=>!document.querySelector('#ai-model-select').disabled);
        assert.equal(await page.locator('#ai-model-select').inputValue(),'qwen3.5:4b');
        pass('Refreshing after recovery restores model choices and keeps the saved model');

        await page.evaluate(()=>moView('strategy'));
        await page.locator('#ai-text').fill('15분 상승추세이며 3분 하단 원비에 닿으면 1분 일반 브레이커를 감시해');
        await page.locator('#ai-send').click();await page.waitForSelector('.ai-interpretation');
        const basic=await page.locator('.ai-interpretation').innerText();
        for(const value of ['종목','조건 1','조건 2','조건 관계','시간 제한','최종 행동','방향','감시 모드',
            '15분봉이 상승추세','3분봉 하단 WONBI 터치','모든 조건을 만족','일반 브레이커','매수','반복 감시','10분']) {
            assert.ok(basic.includes(value),value);
        }
        assert.doesNotMatch(basic,/TREND|WONBI_TOUCH|SIMULTANEOUS|\bALL\b|\bLONG\b|\bNORMAL\b|\bBREAKER\b|direction=/);
        assert.equal(await page.locator('.ai-intent-details').evaluate(x=>x.open),false);
        assert.equal(await page.locator('.ai-intent-details pre').isVisible(),false);
        await page.locator('.ai-intent-details summary').click();
        assert.deepEqual(JSON.parse(await page.locator('.ai-intent-details pre').innerText()),example);
        await page.locator('.ai-intent-details summary').click();
        await page.evaluate(()=>{document.querySelector('.ai-interpretation').scrollIntoView({block:'start'});window.scrollTo(0,0);});
        await page.screenshot({path:path.join(evidence,'interpretation_example.png'),fullPage:true});
        fs.writeFileSync(path.join(evidence,'interpretation_example.txt'),basic+'\n');
        pass('Default interpretation is Korean; exact canonical response is available only in closed details');

        const translations=await page.evaluate(()=>{
            const steps=[{kind:'TREND',tfs:['1h'],direction:'SHORT'},
                {kind:'MA_STATE',tfs:['1m'],ma_family:'EMA',fast_period:50,slow_period:200,side:'ABOVE'},
                {kind:'MA_PRICE_STATE',tfs:['3m'],ma_family:'HMA',slow_period:50,side:'BELOW'},
                {kind:'MA_SLOPE_STATE',tfs:['5m'],ma_family:'WMA',slow_period:17,side:'UP',lookback:3},
                {kind:'MA_CROSS',tfs:['1m'],ma_left:'WMA17',ma_right:'SMA20'},
                {kind:'FVG_STATE',tfs:['5m'],side:'BULL',state:'AREA',bar_state:'CLOSED'},
                {kind:'FVG_NEW',tfs:['15m'],side:'BEAR'}, {kind:'FVG_TOUCH',tfs:['3m'],side:'BULL'},
                {kind:'WONBI_TOUCH',tfs:['3m'],side:'UPPER'},
                {kind:'EXTERNAL_LIQUIDITY_TOUCH',tfs:['4h'],side:'LOW',level:'PDL'},
                {kind:'SESSION_START',tfs:['1m'],session:'MAIN_LONDON'},
                {kind:'OZ_ALERT',tfs:['1m'],validation_mode:'BLIND',trigger_mode:'BREAKER'},
                {kind:'REGIME_BAND',tfs:['5m'],regime_families:['PRICE','RSI'],family_combine:'ALL',relation:'ABOVE'},
                {kind:'PRICE_LEVEL',tfs:['1m'],level:1930,relation:'BREAK_UP'},
                {kind:'LIQUIDITY_LEVEL',tfs:['1m'],level:'PWH',relation:'TOUCH'}];
            const before=JSON.stringify(steps);
            const values=steps.map(step=>part3IntentDisplay.condition(step,'LONG'));
            return {values,unchanged:before===JSON.stringify(steps)};
        });
        assert.equal(translations.unchanged,true);
        assert.match(translations.values[0],/1시간봉이 하락추세/);
        assert.match(translations.values[1],/EMA50이 EMA200 위/);
        assert.match(translations.values[4],/상향 교차/);
        assert.match(translations.values[5],/FVG 영역 접촉.*확정봉 기준/);
        assert.match(translations.values[11],/무지성 브레이커/);
        assert.doesNotMatch(translations.values.join('\n'),/MA_STATE|FVG_STATE|SESSION_START|REGIME_BAND|\bBULL\b|\bBEAR\b|\bBLIND\b/);
        pass('All 15 condition kinds are translated without changing source data or candle semantics');

        const complex=await page.evaluate(()=>{
            const value={result:{supported:true,interpretation:{direction:'SHORT',symbols:['NAS100'],
                steps:[{kind:'FVG_NEW',tfs:['15m'],side:'BEAR',bar_state:'FORMING'},
                    {kind:'WONBI_TOUCH',tfs:['3m'],side:'UPPER'}],order_mode:'SEQUENTIAL',global_combine:'ALL',
                within_sec:350,final_window_sec:120,final_after:'FVG_NEW',persistent:false,
                final:{kind:'OZ',tfs:['1m'],validation_mode:'BLIND',trigger_mode:'BREAKER'},
                cancel_conditions:[{kind:'TREND',tfs:['15m'],direction:'LONG'}],
                after_conditions:[{kind:'FVG_STATE',tfs:['15m'],side:'BEAR'}],
                final_conditions:[{kind:'MA_PRICE_STATE',tfs:['1m'],ma_family:'EMA',slow_period:50,side:'BELOW'}],
                branches:[{direction:'LONG',steps:[{kind:'TREND',tfs:['15m','30m'],tf_combine:'ALL'}],
                    order_mode:'SIMULTANEOUS',global_combine:'ANY',within_sec:null,final:{kind:'NOTIFY'}}]}}};
            const before=JSON.stringify(value);const card=part3IntentDisplay.render(value);
            card.querySelector('details').remove();return {text:card.textContent,unchanged:before===JSON.stringify(value)};
        });
        for(const word of ['조건 순서대로 충족','5분 50초','최종 감시 유효시간 2분','1회 감시','매도',
            '무지성 브레이커','진행봉 기준','감시 취소 조건','선행 사건 충족 후','최종 행동 시 다시 확인',
            '독립 분기 1','하나 이상의 조건을 만족','모든 시간봉','조건 충족 시 알림']) assert.ok(complex.text.includes(word),word);
        assert.equal(complex.unchanged,true);
        pass('Sequence, time limits, branches, cancellation, candle state and one-time watch remain distinct');

        const escaped=await page.evaluate(()=>{
            const value={result:{supported:true,interpretation:{direction:'LONG',symbols:['<img src=x onerror=alert(1)>'],
                steps:[],final:{kind:'NOTIFY'}}}};
            const card=part3IntentDisplay.render(value);document.querySelector('#ai-messages').append(card);
            const result={images:card.querySelectorAll('img').length,literal:card.textContent.includes('<img')};card.remove();return result;
        });
        assert.deepEqual(escaped,{images:0,literal:true});
        pass('Interpretation and raw JSON are rendered as text, including model-supplied markup');

        await page.locator('#ai-confirm').click();
        await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled);
        assert.deepEqual(requests.find(x=>x.path==='/api/preview').data.recipe,recipe);
        await page.locator('#generate-button').click();
        await page.waitForFunction(()=>document.querySelector('#code-preview').textContent.includes('generated fixture'));
        assert.deepEqual(requests.find(x=>x.path==='/api/generate').data.recipe,recipe);
        assert.deepEqual(errors,[]);
        pass('Applying and generating forward the original Recipe v2 and canonical intent unchanged');
        await page.close();
    } finally {
        await browser.close();
        fs.writeFileSync(path.join(evidence,'ui_tests.json'),JSON.stringify({tests:results.length,results,
            environment:'full current UI in isolated Edge; mocked APIs'},null,2)+'\n');
    }
    console.log(`PASS ${results.length} AI interpretation and model-selection UI tests`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
