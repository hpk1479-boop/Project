/* Render shipped assets against isolated responses. Never start MT5 or engines. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),oldRoot=path.resolve(root,'../수정본76');
const evidence=path.join(root,'검증결과/백테스트목록77');fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[];
 const pass=name=>checks.push({name,passed:true});let failure=null;
 async function pageFor(project){
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),calls=[];
  let items=[{job_id:'a'.repeat(32),phase:'complete',active:false,symbol:'GOLDm',start:'2026-09-01',end:'2026-10-01',strategies:['SPECIAL2']},
   {job_id:'b'.repeat(32),phase:'error',active:false,symbol:'USTEC',strategies:['SPECIAL3']},
   {job_id:'c'.repeat(32),phase:'run',active:true,symbol:'EURUSD',strategies:['SPECIAL2']}];
  page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const name=new URL(route.request().url()).pathname;calls.push(name);
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(project,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:'fixture'},recent:[]}});
   if(name==='/api/mo/backtest/options')return route.fulfill({json:{symbol:'GOLDm',symbols:['GOLDm','USTEC'],
    start:'2026-09-01',end:'2026-10-01',mode:'BAR',specials:[],special_settings:{},watch_text:'',
    watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true}});
   if(name==='/api/mo/backtest/recent')return route.fulfill({json:{items}});
   if(name==='/api/mo/backtest/delete'){
    const ids=route.request().postDataJSON().job_ids;
    assert.deepEqual(ids,['a'.repeat(32),'b'.repeat(32)]);
    items=items.filter(item=>!ids.includes(item.job_id));
    return route.fulfill({json:{ok:true,deleted:ids,errors:[]}});
   }
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('backtest'));await page.waitForFunction(()=>!!document.querySelector('#mo-bt-start').value);
  return {page,calls};
 }
 try{
  const old=await pageFor(oldRoot),now=await pageFor(root),page=now.page;
  for(const id of ['mo-bt-symbol','mo-bt-start','mo-bt-end','mo-bt-mode','mo-bt-start-button','mo-bt-data-settings'])
   assert.deepEqual(await page.locator('#'+id).boundingBox(),await old.page.locator('#'+id).boundingBox(),id+' layout');
  assert.deepEqual(await page.locator('.moses-bt-input-card').boundingBox(),await old.page.locator('.moses-bt-input-card').boundingBox());
  pass('76의 기존 입력칸·카드·시작 버튼 위치와 크기 유지');await old.page.close();
  const arrowMetrics=await page.locator('.moses-bt-symbol-arrow').evaluateAll(nodes=>nodes.map(node=>{
   const s=getComputedStyle(node);return {text:node.textContent,color:s.color,size:s.fontSize,right:s.right};}));
  assert.equal(arrowMetrics.length,2);assert.deepEqual(arrowMetrics[0],arrowMetrics[1]);
  await page.locator('#mo-bt-mode').selectOption('EVENT');assert.equal(await page.evaluate(()=>moBacktestRequest().mode),'EVENT');
  await page.locator('#mo-bt-symbol-select').selectOption('USTEC');await page.locator('#mo-bt-symbol').fill('custom.actual+');
  assert.equal(await page.evaluate(()=>moBacktestRequest().symbol),'custom.actual+');
  pass('종목과 재생 모드의 동일한 화살표·선택 및 직접 입력 유지');
  assert.equal(await page.locator('.moses-job-row').count(),3);
  assert.deepEqual(await page.locator('.moses-job-check').evaluateAll(nodes=>nodes.map(n=>n.disabled)),[false,false,true]);
  await page.locator('#bt-jobs-select-all').click();
  assert.deepEqual(await page.locator('.moses-job-check').evaluateAll(nodes=>nodes.map(n=>n.checked)),[true,true,false]);
  assert.equal(await page.locator('#bt-jobs-delete').isEnabled(),true);
  await page.screenshot({path:path.join(evidence,'desktop.png'),fullPage:true});
  pass('백테스트 목록·행 맨 앞 체크박스·전체선택·실행 중 삭제 차단');
  await page.setViewportSize({width:420,height:900});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(evidence,'mobile.png'),fullPage:true});
  pass('420px 좁은 화면에서 가로 넘침 없음');
  page.once('dialog',async dialog=>{assert.match(dialog.message(),/원본 captures 데이터는 삭제하지 않습니다/);await dialog.accept();});
  await page.locator('#bt-jobs-delete').click();await page.waitForFunction(()=>document.querySelectorAll('.moses-job-row').length===1);
  assert.equal(await page.locator('#bt-jobs-delete').isDisabled(),true);assert.equal(await page.locator('#bt-jobs-select-all').isDisabled(),true);
  assert.match(await page.locator('#bt-jobs-message').textContent(),/2개 백테스트 기록/);
  assert.deepEqual(errors,[]);assert(now.calls.every(name=>!name.includes('mt5')));
  pass('확인 후 선택한 결과만 삭제·목록 갱신·버튼 상태 복귀·브라우저 오류 없음');
  await page.close();
 }catch(error){failure=error.message;throw error;}
 finally{
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({
   passed:checks.length,failed:failure?1:0,checks,failure,browser_errors:errors,
   environment:'Edge headless 1440px/420px; isolated local APIs'},null,2)+'\n');
 }
 console.log(`PASS ${checks.length} backtest list UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
