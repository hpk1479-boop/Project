/* Render actual assets with isolated API responses: no AI model/MT5/engine launch. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/AI전략연구79');
fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[],posts=[];
 let failure=null,completed=false;
 const a='a'.repeat(32),b='b'.repeat(32),c='c'.repeat(32);
 const strategy=JSON.parse(fs.readFileSync(path.join(evidence,'fixture.json'),'utf8'));
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const req=route.request(),url=new URL(req.url()),name=url.pathname,body=req.postDataJSON();
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(body)posts.push({name,body});
   const sequence={sequence_id:a,phase:completed?'complete':'run',jobs:completed?[
    {job_id:b,phase:'complete',symbol:'XAUUSD+',result:{alert_statistics:{total:10},by_rr:{'1.0':{total_trades:10,win_rate:60,total_r:2}}}},
    {job_id:c,phase:'complete',symbol:'NAS100',result:{alert_statistics:{total:0},by_rr:{'1.0':{total_trades:0,win_rate:null,total_r:0}}}}]:[
    {job_id:b,phase:'confirm',symbol:'XAUUSD+'}]};
   let value={ok:true,modules:{},lines:[]};
   if(name==='/api/init')value={connections:{warehouse:'fixture'},recent:[]};
   if(name==='/api/ai/settings')value={settings:{provider:'ollama',model:'sample'}};
   if(name==='/api/ai/chat')value={kind:'BACKTEST',action:'SEQUENTIAL',revision:'reviewed',can_confirm:true,strategy,
    message_ko:'앞선 백테스트가 완료된 뒤 다음 작업을 실행합니다.',
    preview:'1. 종목: XAUUSD+\n기간: 2025-10-02 ~ 2026-10-01 (종료일 미포함)\n전략: 현재 연구 전략\n결과: 가상 진입\n\n2. 종목: NAS100\n기간: 2025-10-02 ~ 2026-10-01 (종료일 미포함)\n전략: 동일한 연구 전략\n결과: 가상 진입'};
   if(name==='/api/ai/apply')value={kind:'BACKTEST',result:sequence};
   if(name==='/api/ai/sequence')value=sequence;
   if(name==='/api/mo/backtest/options')value={symbols:['XAUUSD+','NAS100'],symbol:'XAUUSD+',specials:['SPECIAL1'],selected_specials:['SPECIAL1'],
    special_settings:{},trigger_choices:[],default_triggers:{},start:'2025-10-02',end:'2026-10-02',result_mode:'VIRTUAL_ENTRY',spread_points:{}};
   if(name==='/api/mo/backtest/recent')value={items:[{job_id:b,phase:'complete',active:false,symbol:'XAUUSD+'},
    {job_id:c,phase:'error',active:false,symbol:'NAS100'},{job_id:'d'.repeat(32),phase:'run',active:true}]};
   if(name==='/api/mo/backtest/reconnect'||name==='/api/mo/backtest/status')value={job_id:b,phase:'confirm',active:false,
    message:'구축 승인 대기',plan_revision:'plan',plan:{record:[],convert:[],estimate:{}},warnings:[]};
   if(name==='/api/mo/backtest/log')value={text:'테스트용 진행 로그'};
   return route.fulfill({json:value});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  assert.equal(await page.locator('button[data-moses-view="strategy"]').textContent(),'AI 전략연구');
  await page.locator('button[data-moses-view="strategy"]').click();
  await page.locator('#ai-text').fill('15분 상승추세와 3분 하단 원비 터치 후 10분간 1분 브레이커 올존 반복 감시 전략을 골드 1년 백테스트하고 끝나면 나스닥도 1년');
  await page.locator('#ai-send').click();await page.waitForSelector('#ai-pending:not(.hidden)');
  assert(!posts.some(x=>x.name==='/api/ai/apply'));assert.equal(await page.locator('#ai-confirm').textContent(),'조건 확인 후 실행');
  await page.screenshot({path:path.join(evidence,'desktop_plan.png'),fullPage:true});checks.push('전략 해석·순차 실행 계획·확인 전 미실행');
  await page.locator('#ai-confirm').click();await page.waitForSelector('#ai-messages button:has-text("순차 실행 중지")');
  assert.equal(posts.filter(x=>x.name==='/api/ai/apply').length,1);
  assert((await page.locator('#ai-messages').textContent()).includes('데이터 구축 승인이 필요'));
  await page.locator('#ai-messages button:has-text("진행 / 결과 보기")').first().click();
  await page.waitForSelector('#mo-bt-progress-page:not(.hidden)');checks.push('구축 승인 유지·기존 진행 화면 연결');
  await page.locator('button[data-moses-view="backtest"]').click();
  assert.equal(await page.locator('[id^="bt-command-"]').count(),0);
  await page.locator('#bt-jobs-select-all').click();assert.equal(await page.locator('#bt-jobs-select-all').textContent(),'전체해제');
  assert.equal(await page.locator('#bt-jobs-list input:checked').count(),2);
  await page.locator('#bt-jobs-select-all').click();assert.equal(await page.locator('#bt-jobs-list input:checked').count(),0);
  assert.equal(await page.locator('#bt-jobs-select-all').textContent(),'전체선택');
  await page.screenshot({path:path.join(evidence,'desktop_backtest.png'),fullPage:true});checks.push('백테스트 별도 채팅 제거·전체선택/해제·활성 작업 제외');
  completed=true;await page.locator('button[data-moses-view="strategy"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-messages').textContent.includes('승률 60%'));
  assert((await page.locator('#ai-messages').textContent()).includes('총 알림 0건'));
  await page.screenshot({path:path.join(evidence,'desktop_results.png'),fullPage:true});checks.push('완료 결과 기존 통계 표시·0건 처리');
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  await page.screenshot({path:path.join(evidence,'mobile_research.png'),fullPage:true});checks.push('390px 창 가로 넘침 없음');
  await page.locator('#ai-reset').click();await page.waitForFunction(()=>document.querySelector('#ai-messages').textContent==='');assert.equal(await page.locator('#ai-messages').textContent(),'');
  assert(!posts.some(x=>x.name==='/api/ai/sequence/stop'));checks.push('새 대화 시작은 작업 중지 없이 대화만 초기화');
  assert.equal(errors.length,0);checks.push('브라우저 실행 오류 없음');
 }catch(error){failure=error.message;process.exitCode=1;}
 finally{
  fs.writeFileSync(path.join(evidence,'ui_report.json'),JSON.stringify({checks,errors,failure},null,2));
  await browser.close();console.log(JSON.stringify({passed:checks.length,failure},null,2));
 }
})();
