/* Actual Chrome UI QA against memory-only API fixtures. Never connects to engines. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require(process.env.MOSES_QA_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'..'),web=path.join(root,'Part3','web'),out=path.join(root,'검증결과','UI70');
const clone=v=>JSON.parse(JSON.stringify(v)),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const checks=[],errors=[],external=[],screenshots=[],records=[];
const modules={STAFF:'시장 데이터',ENGINE:'이벤트 엔진',OZ:'올존',SWEEP:'스윕',FVG:'FVG',INDICATOR:'지표',WATCH:'개인 감시',KIM:'알림 담당'};
const options={symbol:'XAUUSD+',symbols:['XAUUSD+','NAS100'],start:'2026-09-01',end:'2026-10-01',mode:'BAR',warehouse_set:true,
 specials:['SPECIAL1','SPECIAL2'],special_settings:{SPECIAL1:{enabled:true,trigger:null,time_filters:null},SPECIAL2:{enabled:false,trigger:null,time_filters:null}},
 trigger_choices:['올존','무지성 올존','브레이커 올존','무지성 브레이커 올존'],default_triggers:{SPECIAL1:'올존',SPECIAL2:'무지성 올존'},
 default_time_filters:{SPECIAL1:['MAIN_ASIA'],SPECIAL2:0},session_labels:{MAIN_ASIA:'아시아',MAIN_LONDON:'런던',MAIN_NEWYORK:'뉴욕'},
 session_times:{MAIN_ASIA:'0900-1800',MAIN_LONDON:'1600-0100',MAIN_NEWYORK:'2200-0600'},watch_text:'합성 개인 감시',watch_chat_id:'BACKTEST',spread_points:{}};
const server=http.createServer((req,res)=>{
 const route=new URL(req.url,'http://127.0.0.1').pathname,target=path.resolve(web,route==='/'?'index.html':'.'+decodeURIComponent(route));
 if(!target.startsWith(web+path.sep)||!fs.existsSync(target)||!fs.statSync(target).isFile()){res.writeHead(404);res.end();return;}
 const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(target)]||'application/octet-stream';
 res.writeHead(200,{'Content-Type':type,'Cache-Control':'no-store'});res.end(fs.readFileSync(target));
});
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function fixture(){return {calls:[],gates:{},failures:{},statuses:{},messages:{},recent:[],startCount:0,defaultPhase:'error',resultReady:false,options:clone(options)};}
function response(mock,route,url,body){
 if(route==='/api/init')return {connections:{warehouse:'synthetic warehouse'},recent:[]};
 if(route==='/api/recent')return {items:[{filename:'Test_SPECIAL001.py'}]};
 if(route==='/api/mo/backtest/options')return clone(mock.options);
 if(route==='/api/mo/backtest/start'||route==='/api/backtest')return {job_id:(++mock.startCount).toString(16).padStart(32,'a'),phase:'planning',message:'합성 작업 시작'};
 if(route==='/api/mo/backtest/reconnect')return {job_id:body.job_id,phase:'error',message:'합성 재연결'};
 if(route==='/api/ai/backtest/chat')return {action:'START',revision:'synthetic-revision',can_confirm:true,message_ko:'조건을 확인하세요.',preview:'합성 백테스트 명령'};
 if(route==='/api/ai/backtest/confirm')return {action:'START',result:{job_id:'c'.repeat(32),phase:'planning'},message_ko:'합성 실행'};
 if(route==='/api/mo/backtest/status'){
   const id=new URL(url).searchParams.get('id'),phase=mock.statuses[id]||mock.defaultPhase;
   return {phase,active:phase==='run',result_ready:mock.resultReady,message:mock.messages[id]||(phase==='error'?'MT5를 실행해 주세요.':phase==='complete'?'완료':'작업 중'),
     progress:{phase:phase==='error'?'오류':phase==='complete'?'완료':'작업 중',build:0,replay:0,virtual:0,remaining:'',warnings:[],lines:['합성 검증 로그 · 실제 MT5/엔진 실행 없음']}};
 }
 if(route==='/api/mo/backtest/result')return {status:'error',run_id:'synthetic',warnings:['합성 오류 결과'],pieces:[],alerts_preview:[],alert_statistics:{total:0,daily_average:0,weekly_average:0,monthly_average:0}};
 if(route==='/api/mo/backtest/recent')return {items:clone(mock.recent)};
 if(route==='/api/mo/live/status')return {modules:Object.fromEntries(Object.entries(modules).map(([key,name])=>[key,{name,state:'연결 대기',monitoring:false}])),lines:[],error:'',pipe_connected:false};
 if(route==='/api/ai/settings')return {settings:{model:'synthetic',timeout:90},base_url:'http://127.0.0.1:11434'};
 if(route==='/api/mo/settings')return {live:[],part2:{cores:4},connections:{warehouse:'synthetic warehouse',live_records:'synthetic records',python_executable:''},ai:{model:'synthetic',timeout:90}};
 return {};
}
async function capture(page,label){return page.evaluate(label=>({label,view:moState.view,page:moState.backtestPage,job:moState.job,
 starting:moBacktestPoll.starting,done:moBacktestPoll.done,phase:moBacktestPoll.phase,request:moBacktestPoll.request,
 startDisabled:document.querySelector('#mo-bt-start-button').disabled,stopDisabled:document.querySelector('#mo-bt-stop-button').disabled,
 resultDisabled:document.querySelector('#mo-bt-progress-result').disabled,retryDisabled:document.querySelector('#mo-bt-retry').disabled,
 recoveryVisible:!document.querySelector('#mo-bt-recovery-card').classList.contains('hidden'),
 setupError:document.querySelector('#mo-bt-setup-error').textContent,warning:document.querySelector('#mo-bt-warnings').textContent,
 summary:document.querySelector('#mo-bt-progress-summary').textContent,jobSelectionRevision:moState.jobSelectionRevision,
 horizontalOverflow:document.documentElement.scrollWidth>innerWidth}),label);}
async function screenshot(page,name){await page.screenshot({path:path.join(out,name),fullPage:true});screenshots.push('검증결과/UI70/'+name);}
async function enterBacktest(page){await page.locator('button[data-moses-view=backtest]').click();await page.waitForFunction(()=>document.querySelector('#mo-bt-symbol').dataset.initialized==='1');}
async function startError(page){await page.locator('[data-bt-result=BUILD_ONLY]').click();await page.locator('#mo-bt-start-button').click();await page.waitForFunction(()=>moBacktestPoll.done&&!moBacktestPoll.starting);}
async function test(browser,origin,name,run,viewport={width:1440,height:900}){
 const mock=fixture(),context=await browser.newContext({viewport}),page=await context.newPage();
 page.on('pageerror',e=>errors.push({test:name,error:e.message}));
 await page.route('**/*',async route=>{
   const url=route.request().url();if(!url.startsWith(origin)){external.push(url);return route.abort();}
   const pathname=new URL(url).pathname;if(!pathname.startsWith('/api/'))return route.continue();
   const call={route:pathname,method:route.request().method(),body:route.request().postDataJSON()};mock.calls.push(call);
   const gate=mock.gates[pathname+'#'+(call.body?.job_id||'')]||mock.gates[pathname];if(gate)await gate.promise;
   const failure=mock.failures[pathname];
   try{await route.fulfill({status:failure?500:200,contentType:'application/json',body:JSON.stringify(failure?{error:failure}:response(mock,pathname,url,call.body))});call.fulfilled=true;}
   catch(error){if(!page.isClosed())throw error;}
 });
 try{
   await page.goto(origin+'/#token=synthetic-ui70');await page.waitForSelector('#live-modules .moses-module');
   await run({page,mock});checks.push({name,pass:true});records.push({name,calls:mock.calls.filter(call=>call.method==='POST'),state:await capture(page,name)});
 }finally{Object.values(mock.gates).forEach(gate=>gate.resolve());await context.close();}
}
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,...(process.env.MOSES_QA_CHROME?{executablePath:process.env.MOSES_QA_CHROME}:{channel:'chrome'})});
 try{
   for(const width of [1440,390])await test(browser,origin,'error_recovery_layout_'+width,async({page})=>{
     await enterBacktest(page);await startError(page);const state=await capture(page,'error');
     assert.equal(state.page,'progress');assert(state.recoveryVisible&&!state.retryDisabled&&!state.startDisabled&&state.stopDisabled&&state.resultDisabled);
     assert(!state.horizontalOverflow);assert.match(state.warning,/MT5/);
     const card=page.locator('#mo-bt-recovery-card'),setup=page.locator('#mo-bt-recovery-setup'),retry=page.locator('#mo-bt-retry');
     const geometry=await card.evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,right:r.right,width:r.width};});assert(geometry.x>=0&&geometry.right<=width+1);
     assert(await setup.isVisible());assert(await retry.isVisible());await screenshot(page,'error_recovery_'+width+'.png');
     await setup.click();assert.equal((await capture(page,'setup')).page,'setup');
     assert(await page.locator('#mo-bt-start-button').isEnabled());await screenshot(page,'returned_setup_'+width+'.png');
   },{width,height:width===390?844:900});
   await test(browser,origin,'retry_uses_original_snapshot_and_prevents_double_start',async({page,mock})=>{
     await enterBacktest(page);await page.locator('#mo-bt-symbol').fill('NAS100');await page.locator('#mo-bt-start').fill('2026-06-02');
     await page.locator('#mo-bt-end').fill('2026-06-25');await page.locator('#mo-bt-mode').selectOption('TICK');
     await page.locator('[data-bt-target=WATCH]').click();await page.locator('#mo-bt-watch').fill('합성 원래 조건');await page.locator('#mo-bt-watch-chat').fill('ORIGINAL');
     await page.locator('[data-bt-result=BUILD_ONLY]').click();await page.locator('#mo-bt-rebuild').check();await page.locator('#mo-bt-start-button').click();
     await page.waitForFunction(()=>moBacktestPoll.done&&!moBacktestPoll.starting);const original=clone(mock.calls.find(call=>call.route==='/api/mo/backtest/start').body);
     await page.locator('#mo-bt-recovery-setup').click();await page.locator('#mo-bt-symbol').fill('XAUUSD+');await page.locator('#mo-bt-start').fill('2026-08-01');
     await page.locator('#mo-bt-watch').fill('다른 새 조건');await page.locator('#mo-bt-open-progress').click();
     const gate=mock.gates['/api/mo/backtest/start']=deferred();await page.evaluate(()=>{document.querySelector('#mo-bt-retry').click();document.querySelector('#mo-bt-retry').click();});
     await page.waitForFunction(()=>moBacktestPoll.starting);await sleep(70);assert.equal(mock.calls.filter(call=>call.route==='/api/mo/backtest/start').length,2);
     assert(await page.locator('#mo-bt-retry').isDisabled());assert(await page.locator('#mo-bt-stop-button').isDisabled());
     gate.resolve();delete mock.gates['/api/mo/backtest/start'];await page.waitForFunction(()=>moBacktestPoll.done&&!moBacktestPoll.starting);
     assert.deepEqual(mock.calls.filter(call=>call.route==='/api/mo/backtest/start').at(-1).body,original);assert(await page.locator('#mo-bt-retry').isEnabled());
   });
   await test(browser,origin,'navigation_is_local_with_options_pending_or_failure',async({page,mock})=>{
     await enterBacktest(page);await startError(page);const gate=mock.gates['/api/mo/backtest/options']=deferred();
     await page.locator('button[data-moses-view=backtest]').click();assert.equal((await capture(page,'pending')).page,'setup');
     await page.locator('button[data-moses-view=live]').click();assert.equal((await capture(page,'live')).view,'live');
     mock.options.warehouse_set=false;gate.resolve();delete mock.gates['/api/mo/backtest/options'];await sleep(80);assert.equal((await capture(page,'late_options')).view,'live');
     mock.failures['/api/mo/backtest/options']='합성 옵션 조회 실패';await page.locator('button[data-moses-view=backtest]').click();
     await page.waitForFunction(()=>document.querySelector('#mo-bt-setup-error').textContent.includes('합성 옵션 조회 실패'));
     assert.equal((await capture(page,'failed_options')).page,'setup');assert(await page.locator('#mo-bt-start-button').isEnabled());
   });
   await test(browser,origin,'late_start_response_keeps_newest_live_view',async({page,mock})=>{
     await enterBacktest(page);const gate=mock.gates['/api/mo/backtest/start']=deferred();await page.locator('#mo-bt-start-button').click();
     await page.locator('button[data-moses-view=live]').click();gate.resolve();delete mock.gates['/api/mo/backtest/start'];
     await page.waitForFunction(()=>!!moState.job&&!moBacktestPoll.starting);assert.equal((await capture(page,'late_start')).view,'live');
     await page.locator('button[data-moses-view=backtest]').click();await page.waitForFunction(()=>moBacktestPoll.done);assert.equal((await capture(page,'backtest')).page,'setup');
   });
   await test(browser,origin,'late_retry_response_keeps_manual_setup_page',async({page,mock})=>{
     await enterBacktest(page);await startError(page);const gate=mock.gates['/api/mo/backtest/start']=deferred();await page.locator('#mo-bt-retry').click();
     await page.locator('#mo-bt-recovery-setup').click();assert.equal((await capture(page,'setup_during_retry')).page,'setup');
     gate.resolve();delete mock.gates['/api/mo/backtest/start'];await page.waitForFunction(()=>moBacktestPoll.done&&!moBacktestPoll.starting);
     assert.equal((await capture(page,'late_retry')).page,'setup');
   });
   await test(browser,origin,'error_result_pending_does_not_block_recovery',async({page,mock})=>{
     mock.resultReady=true;const gate=mock.gates['/api/mo/backtest/result']=deferred();await enterBacktest(page);await startError(page);
     assert(await page.locator('#mo-bt-retry').isEnabled());assert(await page.locator('#mo-bt-stop-button').isDisabled());
     await page.locator('button[data-moses-view=live]').click();gate.resolve();delete mock.gates['/api/mo/backtest/result'];
     await page.waitForFunction(()=>moState.resultShown);assert.equal((await capture(page,'late_error_result')).view,'live');
     await page.locator('button[data-moses-view=backtest]').click();await page.locator('#mo-bt-open-progress').click();
     assert(await page.locator('#mo-bt-progress-result').isEnabled());assert(await page.locator('#mo-bt-retry').isEnabled());
     await screenshot(page,'error_with_partial_result.png');
   });
   await test(browser,origin,'error_result_failure_keeps_retry_available',async({page,mock})=>{
     mock.resultReady=true;mock.failures['/api/mo/backtest/result']='합성 결과 조회 실패';await enterBacktest(page);await startError(page);
     await page.waitForFunction(()=>document.querySelector('#mo-bt-warnings').textContent==='합성 결과 조회 실패');
     const state=await capture(page,'result_failure');assert(state.done&&!state.startDisabled&&!state.retryDisabled&&state.stopDisabled&&state.resultDisabled);
   });
   await test(browser,origin,'late_completed_result_keeps_manual_setup_page',async({page,mock})=>{
     mock.defaultPhase='complete';mock.resultReady=true;const gate=mock.gates['/api/mo/backtest/result']=deferred();await enterBacktest(page);await page.locator('#mo-bt-start-button').click();
     await page.waitForFunction(()=>moBacktestPoll.done);await page.locator('#mo-global-back').click();gate.resolve();delete mock.gates['/api/mo/backtest/result'];
     await page.waitForFunction(()=>moState.resultShown);assert.equal((await capture(page,'late_complete')).page,'setup');
   });
   await test(browser,origin,'recent_active_autoattach_keeps_setup_and_does_not_invent_retry',async({page,mock})=>{
     mock.defaultPhase='run';mock.recent=[{job_id:'d'.repeat(32),phase:'run',active:true,symbol:'XAUUSD+',kind:'normal'}];await enterBacktest(page);
     await page.waitForFunction(()=>!!moState.job);assert.equal((await capture(page,'autoattach')).page,'setup');
     mock.defaultPhase='error';await page.evaluate(()=>moBacktestStatus());await page.locator('#mo-bt-open-progress').click();
     assert(await page.locator('#mo-bt-retry').isDisabled());assert.match(await page.locator('#mo-bt-recovery-help').textContent(),/원래 실행 조건/);
   });
   for(const stale of [false,true])await test(browser,origin,'explicit_reconnect_'+(stale?'late_navigation_preserved':'opens_progress'),async({page,mock})=>{
     mock.recent=[{job_id:'b'.repeat(32),phase:'error',active:false,symbol:'XAUUSD+',kind:'normal'}];await enterBacktest(page);await page.waitForSelector('#bt-jobs-list button');
     const gate=mock.gates['/api/mo/backtest/reconnect']=deferred();await page.locator('#bt-jobs-list button').click();
     if(stale)await page.locator('button[data-moses-view=live]').click();gate.resolve();delete mock.gates['/api/mo/backtest/reconnect'];
     await page.waitForFunction(()=>moState.job==='b'.repeat(32));const state=await capture(page,'reconnect');assert.equal(state.view,stale?'live':'backtest');if(!stale)assert.equal(state.page,'progress');
   });
   await test(browser,origin,'newest_reconnect_B_survives_late_reconnect_A',async({page,mock})=>{
     const a='b'.repeat(32),b='e'.repeat(32);mock.recent=[a,b].map(job_id=>({job_id,phase:'error',active:false,symbol:'XAUUSD+',kind:'normal'}));
     mock.messages[a]='이전 작업 A 오류';mock.messages[b]='최종 선택 작업 B 오류';await enterBacktest(page);await page.waitForSelector('#bt-jobs-list button');
     const gateA=mock.gates['/api/mo/backtest/reconnect#'+a]=deferred(),gateB=mock.gates['/api/mo/backtest/reconnect#'+b]=deferred();
     await page.locator('#bt-jobs-list button').nth(0).click();await page.locator('#bt-jobs-list button').nth(1).click();
     gateB.resolve();delete mock.gates['/api/mo/backtest/reconnect#'+b];await page.waitForFunction(b=>moState.job===b&&moBacktestPoll.done,b);
     const before=await capture(page,'newest_B');assert.equal(before.summary,'최종 선택 작업 B 오류');
     gateA.resolve();delete mock.gates['/api/mo/backtest/reconnect#'+a];await sleep(100);const after=await capture(page,'late_A');
     assert.equal(after.job,b);assert.equal(after.summary,before.summary);assert.equal(after.warning,before.warning);assert.equal(after.phase,before.phase);
     assert.equal(after.request,null);await screenshot(page,'latest_reconnect_keeps_B.png');
   });
   await test(browser,origin,'newest_reconnect_B_survives_late_normal_start_A',async({page,mock})=>{
     const b='e'.repeat(32);mock.recent=[{job_id:b,phase:'error',active:false,symbol:'XAUUSD+',kind:'normal'}];mock.messages[b]='시작 A 이후 선택한 작업 B 오류';
     await enterBacktest(page);await page.waitForSelector('#bt-jobs-list button');const gate=mock.gates['/api/mo/backtest/start']=deferred();
     await page.locator('#mo-bt-start-button').click();await page.locator('#bt-jobs-list button').click();
     await page.waitForFunction(b=>moState.job===b&&moBacktestPoll.done,b);const before=await capture(page,'B_selected_while_start_A_pending');
     gate.resolve();delete mock.gates['/api/mo/backtest/start'];await page.waitForFunction(()=>!moBacktestPoll.starting);const after=await capture(page,'start_A_late');
     assert.equal(mock.startCount,1);assert.equal(after.job,b);assert.equal(after.summary,before.summary);assert.equal(after.warning,before.warning);
     assert.equal(after.request,null);assert(after.retryDisabled&&!after.startDisabled&&after.stopDisabled);await screenshot(page,'latest_start_keeps_B.png');
   });
   for(const stale of [false,true])await test(browser,origin,'generated_backtest_'+(stale?'late_navigation_preserved':'opens_progress'),async({page,mock})=>{
     await page.locator('button[data-moses-view=strategy]').click();await page.locator('[data-action=backtest]').click();await page.waitForSelector('#bt-file');
     const gate=mock.gates['/api/backtest']=deferred();await page.locator('[data-action=bt-start][data-mode=plan]').click();
     if(stale){await page.locator('#modal [data-action=close-modal]').first().click();await page.locator('button[data-moses-view=live]').click();}
     gate.resolve();delete mock.gates['/api/backtest'];await page.waitForFunction(()=>!!moState.job);const state=await capture(page,'generated');
     assert.equal(state.view,stale?'live':'backtest');if(!stale)assert.equal(state.page,'progress');
   });
   for(const stale of [false,true])await test(browser,origin,'AI_confirm_'+(stale?'late_navigation_preserved':'opens_progress'),async({page,mock})=>{
     await enterBacktest(page);await page.locator('#bt-command-text').fill('합성 백테스트 명령');await page.locator('#bt-command-send').click();
     await page.waitForSelector('#bt-command-pending:not(.hidden)');const gate=mock.gates['/api/ai/backtest/confirm']=deferred();await page.locator('#bt-command-confirm').click();
     if(stale)await page.locator('button[data-moses-view=live]').click();gate.resolve();delete mock.gates['/api/ai/backtest/confirm'];
     await page.waitForFunction(()=>moState.job==='c'.repeat(32));const state=await capture(page,'ai_confirm');assert.equal(state.view,stale?'live':'backtest');if(!stale)assert.equal(state.page,'progress');
   });
   assert.deepEqual(errors,[]);assert.deepEqual(external,[]);checks.push({name:'no_page_errors_or_external_network',pass:true});
   fs.writeFileSync(path.join(out,'ui_check.json'),JSON.stringify({pass:true,checks,screenshots,errors,external,records,fixture:'verification/ui70_preview.cjs',
     note:'Actual Chrome with memory-only API fixtures; no real MT5/backend/settings calls.'},null,2));
   if(fs.existsSync(path.join(out,'ui_failure.json')))fs.unlinkSync(path.join(out,'ui_failure.json'));
   console.log(JSON.stringify({pass:true,checks:checks.length,screenshots:screenshots.length,errors,external},null,2));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack);fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'ui_failure.json'),JSON.stringify({error:error.message,checks,errors,external},null,2));server.close();process.exitCode=1;});
