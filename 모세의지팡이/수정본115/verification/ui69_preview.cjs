/* Real browser visual QA with memory-only synthetic APIs. No engines/settings are touched. */
'use strict';
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..'), web = path.join(root, 'Part3', 'web');
const output = path.join(root, '검증결과', 'UI69');
const {chromium} = require(process.env.MOSES_QA_PLAYWRIGHT || 'playwright');
const profiles = ['올존', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존'];
const labels = {MAIN_ASIA:'아시아', MAIN_LONDON:'런던', MAIN_NEWYORK:'뉴욕'};
const times = {MAIN_ASIA:'0900-1800', MAIN_LONDON:'1600-0100', MAIN_NEWYORK:'2200-0600'};
const clone = value => JSON.parse(JSON.stringify(value));
const names = Array.from({length:8}, (_, i) => 'SPECIAL' + (i+1));
let liveItems = Object.fromEntries(names.map((name,i) => [name,{enabled:i<6, trigger:null,
    time_filters:null, default_trigger:profiles[i%4],
    default_time_filters:i===0?['MAIN_ASIA','MAIN_LONDON','MAIN_NEWYORK']:0}]));
const btItems = Object.fromEntries(names.map((name,i) => [name,{enabled:i<2,trigger:null,time_filters:null}]));
let running = true, loadError = false;
const requests = [], errors = [], externalRequests = [], checks = [], viewports = [];
const modules = {STAFF:'시장 데이터',ENGINE:'이벤트 엔진',OZ:'올존',SWEEP:'스윕',FVG:'FVG',INDICATOR:'지표',WATCH:'개인 감시',KIM:'알림 담당'};
const data = route => {
    if(route === '/api/init') return {connections:{},recent:[]};
    if(route === '/api/mo/live/status') return {modules:Object.fromEntries(Object.entries(modules).map(([key,name]) =>
        [key,{name,state:running?'연결중':'연결 대기',monitoring:running&&key==='WATCH'}])),pipe_connected:running,
        lines:['합성 검증 로그 · 실제 엔진 실행 없음'],error:''};
    if(route === '/api/mo/live/specials') {const items=clone(liveItems); if(loadError) items.SPECIAL1.load_error='이전 프로필은 사용할 수 없습니다. 현재 프로필을 선택하세요.';
        return {items,trigger_choices:profiles,session_labels:labels,session_times:times,settings_error:'',needs_recovery:false};}
    if(route === '/api/mo/backtest/options') return {symbol:'XAUUSD+',symbols:['XAUUSD+','NAS100'],start:'2026-09-01',end:'2026-10-01',mode:'BAR',warehouse_set:true,
        specials:names,special_settings:clone(btItems),trigger_choices:profiles,default_triggers:Object.fromEntries(names.map((name,i)=>[name,profiles[i%4]])),
        default_time_filters:Object.fromEntries(names.map(name=>[name,0])),session_labels:labels,session_times:times,
        watch_text:'15분 상승추세 계속 감시',watch_chat_id:'BACKTEST',spread_points:{'XAUUSD+':0}};
    if(route === '/api/mo/backtest/recent') return {items:[],warnings:[]};
    if(route === '/api/ai/settings') return {settings:{model:'qwen3.5:4b',timeout:90},base_url:'http://127.0.0.1:11434'};
    if(route === '/api/ai/models') return {available:true,models:['qwen3.5:4b','qwen3:8b']};
    if(route === '/api/mo/settings') return {live:[],part2:{cores:4},connections:{warehouse:'synthetic',live_records:'synthetic',python_executable:''},ai:{model:'qwen3.5:4b',timeout:90}};
    return {};
};
const server = http.createServer(async (req,res) => {
    const route = new URL(req.url,'http://127.0.0.1').pathname;
    if(route.startsWith('/api/')) {
        let raw='';for await(const chunk of req) raw+=chunk;
        const body=raw?JSON.parse(raw):undefined;
        requests.push({route,method:req.method,...(body?{body}: {})});
        let result=data(route),status=200;
        if(req.method==='POST') {
            if(route==='/api/mo/live/stop') {running=false;await new Promise(resolve=>setTimeout(resolve,80));result={ok:true,message:'라이브 엔진 종료 완료',warnings:[]};}
            else if(route==='/api/mo/live/specials') {for(const [name,row] of Object.entries(body.items)) Object.assign(liveItems[name],clone(row));result={ok:true,message:'전략 설정 저장 완료'};}
            else {status=400;result={error:'합성 화면에서는 이 실행을 허용하지 않습니다.'};}
        }
        res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(result));return;
    }
    const target=path.resolve(web, route==='/'?'index.html':'.'+decodeURIComponent(route));
    if(!target.startsWith(web+path.sep)) {res.writeHead(403);res.end();return;}
    if(!fs.existsSync(target)||!fs.statSync(target).isFile()){res.writeHead(404);res.end();return;}
    const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'};
    res.writeHead(200,{'Content-Type':types[path.extname(target)]||'application/octet-stream','Cache-Control':'no-store'});res.end(fs.readFileSync(target));
});
async function pass(name, callback) {await callback();checks.push({name,pass:true});}
async function geometry(page) {return page.evaluate(()=>{
    const rect=id=>{const r=document.querySelector(id).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    return {dashboard:rect('#live-main-page'),module:rect('.moses-live-module-card'),status:rect('.moses-live-status'),
        action:rect('#live-action-message'),cards:[...document.querySelectorAll('#live-modules .moses-module')].map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};}),
        children:document.querySelector('#live-main-page').children.length,
        liveMain:!document.querySelector('#live-main-page').classList.contains('hidden'),
        logHidden:document.querySelector('#live-log-page').classList.contains('hidden'),overflow:document.documentElement.scrollWidth>innerWidth};
});}
async function modalGeometry(page,id) {return page.evaluate(id=>{
    const el=document.querySelector(id),r=el.getBoundingClientRect();const grid=document.querySelector('#strategy-settings-cards');
    return {open:el.open,x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom,viewportWidth:innerWidth,viewportHeight:innerHeight,
        columns:getComputedStyle(grid).gridTemplateColumns,bodyScrollable:document.querySelector('.moses-strategy-dialog-body').scrollHeight>document.querySelector('.moses-strategy-dialog-body').clientHeight,
        documentOverflow:document.documentElement.scrollWidth>innerWidth};
},id);}
(async()=>{
    fs.mkdirSync(output,{recursive:true});
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const origin='http://127.0.0.1:'+server.address().port;
    const browser=await chromium.launch({headless:true,...(process.env.MOSES_QA_CHROME ?
        {executablePath:process.env.MOSES_QA_CHROME} : {channel:'chrome'})});
    try {
        const context=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1});
        const page=await context.newPage();
        page.on('pageerror',error=>errors.push(error.message));
        await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)) {externalRequests.push(route.request().url());route.abort();}else route.continue();});
        await page.goto(origin+'/#token=synthetic-ui69');await page.waitForSelector('#live-modules .moses-module');
        for(const viewport of [{width:1440,height:900},{width:2086,height:1371},{width:390,height:844}]) {
            await page.setViewportSize(viewport);await page.evaluate(()=>moLiveActionMessage(''));running=true;await page.evaluate(()=>moLiveStatus());
            const before=await geometry(page);await page.locator('#live-stop').click();
            await page.waitForFunction(()=>document.querySelector('#live-action-message').textContent==='라이브 엔진 종료 완료');
            await page.evaluate(()=>moLiveStatus());const after=await geometry(page);
            await pass('stop_preserves_layout_'+viewport.width,async()=>{
                assert.equal(after.children,4);assert(after.liveMain&&after.logHidden);assert.equal(after.cards.length,8);
                assert(!after.overflow);assert(after.status.height<=80,'status is compact');
                assert(after.module.height>=400,'module panel retains useful height');assert(after.status.y>=after.module.bottom-1);
                assert.equal(after.module.y,before.module.y);assert(after.cards.every(card=>card.height>=83));
                for(let i=0;i<after.cards.length;i++)for(let j=i+1;j<after.cards.length;j++) {
                    const a=after.cards[i],b=after.cards[j];assert(!(a.x<b.x+b.width&&b.x<a.x+a.width&&a.y<b.bottom&&b.y<a.bottom),'module cards overlap');
                }
            });
            await page.screenshot({path:path.join(output,'live_stop_'+viewport.width+'.png'),fullPage:true});
            await page.locator('#live-open-settings').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            const popup=await modalGeometry(page,'#strategy-settings-dialog');
            await pass('live_popup_geometry_'+viewport.width,async()=>{
                assert(popup.open);assert(popup.x>=0&&popup.y>=0&&popup.right<=viewport.width+1&&popup.bottom<=viewport.height+1);assert(!popup.documentOverflow);
                assert.equal(popup.columns.split(' ').length,viewport.width>620?2:1);
                assert.equal(await page.locator('#strategy-settings-cards [data-strategy]').count(),8);
                assert.equal(await page.locator('.moses-strategy-toggle').first().evaluate(el=>getComputedStyle(el).flexDirection),'row');
            });
            await page.screenshot({path:path.join(output,'live_strategy_'+viewport.width+'.png'),fullPage:true});
            if(viewport.width===390) {
                await page.locator('[data-strategy="SPECIAL1"] .moses-strategy-profile').click();
                await pass('mobile_profile_editor_geometry',async()=>{
                    const box=await modalGeometry(page,'#strategy-editor-dialog');assert(box.x>=0&&box.right<=viewport.width+1&&box.y>=0&&box.bottom<=viewport.height+1);
                    assert.equal(await page.locator('.moses-strategy-profile-choices label').first().evaluate(el=>getComputedStyle(el).flexDirection),'row');
                    assert((await page.locator('#strategy-editor-body input[type=radio]').first().boundingBox()).width<=20);
                });await page.screenshot({path:path.join(output,'profile_editor_390.png'),fullPage:true});await page.locator('#strategy-editor-cancel').click();
                await page.locator('[data-strategy="SPECIAL1"] .moses-strategy-time').click();
                await pass('mobile_time_editor_geometry',async()=>{
                    const box=await modalGeometry(page,'#strategy-editor-dialog');assert(box.x>=0&&box.right<=viewport.width+1&&box.y>=0&&box.bottom<=viewport.height+1);
                    for(const span of await page.locator('.moses-strategy-time-editor label>span:first-child').all()) {
                        assert.equal(await span.evaluate(el=>getComputedStyle(el).whiteSpace),'nowrap');
                        assert((await span.locator('input').boundingBox()).width<=20);
                    }
                });await page.screenshot({path:path.join(output,'time_editor_390.png'),fullPage:true});await page.locator('#strategy-editor-cancel').click();
            }
            viewports.push({viewport,before,after,popup});await page.locator('#strategy-settings-cancel').click();
        }
        await page.setViewportSize({width:1440,height:900});
        await pass('window_closing_holds_footer_and_disables_actions',async()=>{
            await page.evaluate(()=>window.mosesWindowClosing('기록 저장과 엔진 종료를 확인하고 있습니다.'));
            assert(await page.locator('#live-start').isDisabled());assert(await page.locator('#live-stop').isDisabled());
            await page.evaluate(()=>moLiveStatus());await page.waitForTimeout(1700);
            assert.equal(await page.locator('#live-action-message').textContent(),'기록 저장과 엔진 종료를 확인하고 있습니다.');
            assert((await geometry(page)).status.height<=80);await page.screenshot({path:path.join(output,'window_closing.png'),fullPage:true});
            await page.evaluate(()=>window.mosesWindowClosing('닫기를 완료하지 못했습니다. 다시 시도해 주세요.',true));
            assert(await page.locator('#live-start').isEnabled());assert(await page.locator('#live-stop').isEnabled());
            assert(await page.locator('#live-action-message').evaluate(el=>el.classList.contains('moses-warning')));
        });
        const card=name=>page.locator('#strategy-settings-cards [data-strategy="'+name+'"]');
        const writes=()=>requests.filter(row=>row.method==='POST'&&row.route==='/api/mo/live/specials');
        await page.locator('#live-open-settings').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
        await pass('child_profile_cancel_does_not_modify_draft',async()=>{
            const before=await card('SPECIAL1').locator('.moses-strategy-profile').textContent();
            await card('SPECIAL1').locator('.moses-strategy-profile').click();
            assert.equal(await page.locator('#strategy-editor-body input[type=radio]').count(),4);
            await page.locator('#strategy-editor-body input[value="브레이커 올존"]').check();await page.locator('#strategy-editor-cancel').click();
            assert.equal(await card('SPECIAL1').locator('.moses-strategy-profile').textContent(),before);
            await card('SPECIAL1').locator('.moses-strategy-profile').click();
            assert(await page.locator('#strategy-editor-body input[value="올존"]').isChecked());await page.locator('#strategy-editor-cancel').click();
        });
        await pass('child_profile_save_then_parent_cancel_is_transactional',async()=>{
            await card('SPECIAL1').locator('.moses-strategy-profile').click();await page.locator('#strategy-editor-body input[value="무지성 브레이커 올존"]').check();
            await page.screenshot({path:path.join(output,'profile_editor.png'),fullPage:true});await page.locator('#strategy-editor-save').click();
            assert.equal(await card('SPECIAL1').locator('.moses-strategy-profile').textContent(),'무지성 브레이커 올존');
            await page.locator('#strategy-settings-cancel').click();assert.equal(writes().length,0);
            await page.locator('#live-open-settings').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            assert.equal(await card('SPECIAL1').locator('.moses-strategy-profile').textContent(),'올존');
        });
        await pass('child_time_cancel_reopens_original_values',async()=>{
            await card('SPECIAL1').locator('.moses-strategy-time').click();await page.locator('#strategy-editor-body').getByLabel('아시아 시작 시각',{exact:true}).fill('10:15');
            await page.locator('#strategy-editor-cancel').click();await card('SPECIAL1').locator('.moses-strategy-time').click();
            assert.equal(await page.locator('#strategy-editor-body').getByLabel('아시아 시작 시각',{exact:true}).inputValue(),'09:00');
            await page.locator('#strategy-editor-cancel').click();assert.equal(writes().length,0);
        });
        await pass('array_default_sessions_survive_unchanged_child_and_parent_save',async()=>{
            await card('SPECIAL1').locator('.moses-strategy-time').click();
            assert.equal(await page.locator('#strategy-editor-body input[type=checkbox]:checked').count(),3);
            await page.locator('#strategy-editor-save').click();await page.locator('#strategy-settings-apply').click();
            await page.waitForFunction(()=>!document.querySelector('#strategy-settings-dialog').open);
            assert.deepEqual(writes().at(-1).body.items.SPECIAL1.time_filters,{
                MAIN_ASIA:{enabled:true},MAIN_LONDON:{enabled:true},MAIN_NEWYORK:{enabled:true}});
            await page.locator('#live-open-settings').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            await card('SPECIAL1').locator('.moses-strategy-time').click();
            assert.equal(await page.locator('#strategy-editor-body input[type=checkbox]:checked').count(),3);
            assert.equal(await page.locator('#strategy-editor-body').getByLabel('아시아 시작 시각',{exact:true}).inputValue(),'09:00');
            await page.locator('#strategy-editor-cancel').click();
        });
        await pass('live_parent_save_preserves_main_session_payload',async()=>{
            await card('SPECIAL1').locator('.moses-strategy-profile').click();await page.locator('#strategy-editor-body input[value="브레이커 올존"]').check();await page.locator('#strategy-editor-save').click();
            await card('SPECIAL1').locator('.moses-strategy-time').click();await page.locator('#strategy-editor-body').getByLabel('아시아 시작 시각',{exact:true}).fill('10:15');
            await page.screenshot({path:path.join(output,'time_editor.png'),fullPage:true});await page.locator('#strategy-editor-save').click();
            await card('SPECIAL2').locator('input[type=checkbox]').uncheck();await page.locator('#strategy-settings-apply').click();
            await page.waitForFunction(()=>!document.querySelector('#strategy-settings-dialog').open);
            const row=writes().at(-1).body.items.SPECIAL1;assert.equal(row.trigger,'브레이커 올존');
            assert.deepEqual(Object.keys(row.time_filters),Object.keys(labels));assert.equal(row.time_filters.MAIN_ASIA.start,'1015');
            assert.equal(writes().at(-1).body.items.SPECIAL2.enabled,false);assert((await geometry(page)).liveMain);
            await page.locator('#live-open-settings').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            assert.equal(await card('SPECIAL1').locator('.moses-strategy-profile').textContent(),'브레이커 올존');await page.locator('#strategy-settings-cancel').click();
        });
        await pass('profile_load_error_requires_reselection_and_recovers',async()=>{
            loadError=true;await page.locator('#live-open-settings').click();await page.waitForSelector('.moses-strategy-profile-needs-selection');
            await page.locator('#strategy-settings-apply').click();assert(await page.locator('#strategy-settings-dialog').evaluate(el=>el.open));
            assert.match(await page.locator('#strategy-settings-message').textContent(),/현재 프로필/);
            await card('SPECIAL1').locator('.moses-strategy-profile').click();await page.locator('#strategy-editor-body input[value="올존"]').check();
            await page.locator('#strategy-editor-save').click();assert.equal(await page.locator('.moses-strategy-profile-needs-selection').count(),0);
            loadError=false;await page.locator('#strategy-settings-apply').click();await page.waitForFunction(()=>!document.querySelector('#strategy-settings-dialog').open);
            assert.equal(writes().at(-1).body.items.SPECIAL1.trigger,null);
        });
        await page.locator('[data-moses-view=backtest]').click();await page.waitForFunction(()=>!document.querySelector('#view-backtest').classList.contains('hidden'));
        const priorWrites=writes().length;
        await pass('backtest_popup_cancel_keeps_previous_request',async()=>{
            const before=await page.evaluate(()=>moBacktestRequest());await page.locator('#mo-bt-edit-specials').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            assert.equal(await page.locator('#strategy-settings-title').textContent(),'백테스트 전략 설정');
            await card('SPECIAL1').locator('input[type=checkbox]').uncheck();await page.locator('#strategy-settings-cancel').click();
            assert.deepEqual(await page.evaluate(()=>moBacktestRequest()),before);assert.equal(writes().length,priorWrites);
        });
        await pass('backtest_popup_apply_only_changes_backtest_request',async()=>{
            await page.locator('#mo-bt-edit-specials').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            await card('SPECIAL1').locator('input[type=checkbox]').uncheck();await card('SPECIAL3').locator('input[type=checkbox]').check();
            await card('SPECIAL3').locator('.moses-strategy-profile').click();await page.locator('#strategy-editor-body input[value="무지성 올존"]').check();await page.locator('#strategy-editor-save').click();
            await card('SPECIAL3').locator('.moses-strategy-time').click();await page.locator('#strategy-editor-body').getByLabel('뉴욕 종료 시각',{exact:true}).fill('07:30');await page.locator('#strategy-editor-save').click();
            await page.screenshot({path:path.join(output,'backtest_strategy_1440.png'),fullPage:true});await page.locator('#strategy-settings-apply').click();
            await page.waitForFunction(()=>!document.querySelector('#strategy-settings-dialog').open);const request=await page.evaluate(()=>moBacktestRequest());
            assert.deepEqual(request.specials,['SPECIAL2','SPECIAL3']);assert.equal(request.special_settings.SPECIAL3.trigger,'무지성 올존');
            assert.equal(request.special_settings.SPECIAL3.time_filters.MAIN_NEWYORK.end,'0730');assert.equal(writes().length,priorWrites);
            assert(await page.locator('#mo-bt-setup-page').isVisible());
            await page.locator('#mo-bt-edit-specials').click();await page.waitForSelector('#strategy-settings-cards [data-strategy]');
            assert.equal(await card('SPECIAL3').locator('.moses-strategy-profile').textContent(),'무지성 올존');await page.locator('#strategy-settings-cancel').click();
        });
        await pass('no_page_errors_or_external_network',async()=>{assert.deepEqual(errors,[]);assert.deepEqual(externalRequests,[]);});
        fs.writeFileSync(path.join(output,'ui_check.json'),JSON.stringify({pass:true,checks,viewports,errors,externalRequests,
            writes:writes(),screenshots:fs.readdirSync(output).filter(name=>name.endsWith('.png')).map(name=>'검증결과/UI69/'+name),
            fixture:'verification/ui69_preview.cjs',note:'Memory-only synthetic APIs; actual engines/configuration never accessed.'},null,2));
        if(fs.existsSync(path.join(output,'ui_failure.json'))) fs.unlinkSync(path.join(output,'ui_failure.json'));
        console.log(JSON.stringify({pass:true,checks:checks.length,screenshots:fs.readdirSync(output).filter(name=>name.endsWith('.png')).length,errors},null,2));
        await context.close();
    } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'ui_failure.json'),JSON.stringify({error:error.message,checks,errors,externalRequests,viewports},null,2));server.close();process.exitCode=1;});
