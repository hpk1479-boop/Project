/* Actual shipped UI, isolated model/API responses; no market jobs or model loading. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/AI모드80');
const strategy=JSON.parse(fs.readFileSync(path.join(evidence,'fixture.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const checks=[],posts=[],errors=[];let failure=null;
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const req=route.request(),name=new URL(req.url()).pathname,body=req.postDataJSON();
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),contentType:
     file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(body)posts.push({name,body});
   let value={ok:true,modules:{},lines:[]};
   if(name==='/api/init')value={connections:{warehouse:'fixture'},recent:[]};
   if(name==='/api/ai/settings')value={settings:{provider:'ollama',model:'sample'}};
   if(name==='/api/ai/chat'){
    if(body.message==='그 조건으로 만들어')value={...strategy,kind:'STRATEGY',revision:'strategy',can_apply:true};
    else if(body.message==='이 전략 골드 1년 백테스트해')value={kind:'BACKTEST',revision:'plan',can_confirm:true,
     message_ko:'현재 연구 전략을 기존 백테스트 엔진으로 검증합니다.',preview:'종목: XAUUSD+\n기간: 2025-10-02 ~ 2026-10-02\n전략: 현재 연구 전략\n결과: 가상 진입'};
    else {
     if(body.message==='대기')await new Promise(resolve=>setTimeout(resolve,350));
     value={kind:'CHAT',message_ko:'15분봉 상승추세를 기준으로 3분봉 하단 WONBI 터치를 기다리는 구성을 논의할 수 있습니다.\n\n짧은 봉에서 터치를 확인하는 방식과 15분봉에서 확인하는 방식은 신호 빈도와 진입 시점이 달라질 수 있습니다. 실제 차이는 같은 기간의 백테스트로 확인하세요.\n\n이 조건으로 전략을 작성할까요?',pending_preserved:true,can_confirm:false};
    }
   }
   if(name==='/api/ai/apply')value={kind:'BACKTEST',result:{sequence_id:'a'.repeat(32),phase:'complete',jobs:[
    {job_id:'b'.repeat(32),symbol:'XAUUSD+',phase:'complete',result:{alert_statistics:{total:0}}}]}};
   if(name==='/api/mo/backtest/recent')value={items:[]};
   if(name==='/api/mo/backtest/options')value={symbols:[],symbol:'',specials:[],selected_specials:[],
    special_settings:{},trigger_choices:[],default_triggers:{},start:'2025-10-02',end:'2026-10-02',spread_points:{}};
   return route.fulfill({json:value});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.locator('button[data-moses-view="strategy"]').click();
  const dimensions=await page.locator('.ai-mode-switch').boundingBox();
  assert(dimensions.height<=30&&dimensions.width<=125,JSON.stringify(dimensions));
  assert.equal(await page.locator('#ai-research-strategy').getAttribute('aria-pressed'),'true');
  await page.screenshot({path:path.join(evidence,'desktop_strategy.png'),fullPage:true});
  checks.push({name:'시안보다 작은 모드 버튼',width:dimensions.width,height:dimensions.height});
  await page.locator('#ai-research-chat').click();
  await page.locator('#ai-text').fill('15분 원비와 3분 원비를 조건으로 쓰면 어떤 차이가 있을까?');
  await page.locator('#ai-send').click();await page.waitForSelector('.ai-assistant');
  assert.equal(posts.filter(x=>['/api/ai/apply','/api/generate'].includes(x.name)).length,0);
  assert.equal(posts.filter(x=>x.name==='/api/ai/chat').at(-1).body.mode,'chat');
  await page.screenshot({path:path.join(evidence,'desktop_chat.png'),fullPage:true});
  checks.push({name:'챗 대화 표시·확인 전 적용/생성 없음'});
  const count=await page.locator('#ai-messages').textContent();
  await page.locator('#ai-research-strategy').click();assert.equal(await page.locator('#ai-messages').textContent(),count);
  await page.locator('#ai-text').fill('그 조건으로 만들어');await page.locator('#ai-send').click();
  await page.waitForSelector('#ai-pending:not(.hidden)');
  assert.equal(posts.filter(x=>x.name==='/api/ai/apply').length,0);
  await page.locator('#ai-research-chat').click();assert(await page.locator('#ai-pending').isVisible());
  await page.screenshot({path:path.join(evidence,'desktop_interpretation.png'),fullPage:true});
  checks.push({name:'모드 전환은 대화와 해석 확인 유지·조건 작성은 재확인'});
  await page.locator('#ai-text').fill('이 전략 골드 1년 백테스트해');await page.locator('#ai-send').click();
  await page.waitForFunction(()=>document.querySelector('#ai-confirm').textContent==='조건 확인 후 실행');
  assert.equal(posts.filter(x=>x.name==='/api/ai/apply').length,0);
  await page.locator('#ai-confirm').click();await page.waitForFunction(()=>document.querySelector('#ai-messages').textContent.includes('총 알림 0건'));
  assert.equal(posts.filter(x=>x.name==='/api/ai/apply').length,1);
  checks.push({name:'챗 백테스트 확인 뒤 기존 결과 표시'});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert(await page.locator('#ai-research-chat').isVisible());assert(await page.locator('#ai-research-strategy').isVisible());
  await page.screenshot({path:path.join(evidence,'mobile_chat.png'),fullPage:true});
  checks.push({name:'390px 창 모드 버튼 표시·가로 넘침 없음'});
  await page.locator('#ai-reset').click();await page.waitForFunction(()=>document.querySelector('#ai-messages').textContent==='');
  assert.equal(await page.locator('#ai-research-chat').getAttribute('aria-pressed'),'true');
  assert(!posts.some(x=>x.name==='/api/ai/sequence/stop'));checks.push({name:'대화 초기화는 선택 모드·실행 작업 유지'});
  await page.locator('#ai-text').fill('대기');await page.locator('#ai-send').click();
  assert(await page.locator('#ai-research-strategy').isDisabled());
  await page.waitForFunction(()=>!document.querySelector('#ai-send').disabled);
  assert.equal(await page.locator('#ai-research-chat').getAttribute('aria-pressed'),'true');
  assert.equal(errors.length,0);checks.push({name:'요청 중 전환 차단·브라우저 오류 없음'});
 }catch(error){failure=error.message;process.exitCode=1;}
 finally{
  fs.writeFileSync(path.join(evidence,'ui_report.json'),JSON.stringify({checks,errors,failure},null,2));
  await browser.close();console.log(JSON.stringify({passed:checks.length,failure},null,2));
 }
})();
