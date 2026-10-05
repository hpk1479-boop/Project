/* Actual web assets and isolated API responses. No engine, AI or MT5 launch. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),oldRoot=path.resolve(root,'../수정본75');
const evidence=path.join(root,'검증결과/종목입력_UI76');fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[];
 const pass=name=>checks.push({name,passed:true});let failure=null;
 async function pageFor(project,symbols=['EURUSD','GOLDm','USTEC']){
  const page=await browser.newPage({viewport:{width:1440,height:900}}),calls=[];
  page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const name=new URL(route.request().url()).pathname;calls.push(name);
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(project,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:'fixture'},recent:[]}});
   if(name==='/api/mo/backtest/options')return route.fulfill({json:{symbol:symbols[0]||'',symbols,
    start:'2026-09-01',end:'2026-10-01',mode:'BAR',specials:[],special_settings:{},watch_text:'',
    watch_chat_id:'BACKTEST',spread_points:{},warehouse_set:true}});
   if(name==='/api/mo/backtest/recent')return route.fulfill({json:{items:[]}});
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('backtest'));await page.waitForFunction(()=>!!document.querySelector('#mo-bt-start').value);
  return {page,calls};
 }
 try{
  const old=await pageFor(oldRoot),now=await pageFor(root);
  const ids=['mo-bt-symbol','mo-bt-start','mo-bt-end','mo-bt-mode','mo-bt-start-button','mo-bt-data-settings'];
  for(const id of ids)assert.deepEqual(await now.page.locator('#'+id).boundingBox(),await old.page.locator('#'+id).boundingBox(),id+' layout');
  assert.deepEqual(await now.page.locator('.moses-bt-input-card').boundingBox(),await old.page.locator('.moses-bt-input-card').boundingBox());
  pass('75의 종목·시작일·종료일·재생모드와 입력 카드·실행 버튼 위치 및 크기 유지');
  await old.page.close();
  const page=now.page,select=page.locator('#mo-bt-symbol-select'),input=page.locator('#mo-bt-symbol');
  assert.deepEqual(await select.locator('option').allTextContents(),['','EURUSD','GOLDm','USTEC']);
  await select.click();await page.keyboard.press('Escape');await select.selectOption('USTEC');
  assert.equal(await input.inputValue(),'USTEC');assert.equal(await select.inputValue(),'');
  await input.fill('custom.actual+');assert.equal(await page.evaluate(()=>moBacktestRequest().symbol),'custom.actual+');
  pass('입력칸 오른쪽 화살표 클릭 가능·실제 종목 선택·선택 후 직접 수정');
  await page.screenshot({path:path.join(evidence,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:420,height:900});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(evidence,'mobile.png'),fullPage:true});
  pass('420px 좁은 창에서 기존 배치 유지·가로 넘침 없음');
  assert(now.calls.every(name=>!name.includes('mt5')));await page.close();
  const empty=await pageFor(root,[]);
  assert.equal(await empty.page.locator('#mo-bt-symbol-select').isDisabled(),true);
  await empty.page.locator('#mo-bt-symbol').fill('manually.entered');
  assert.equal(await empty.page.evaluate(()=>moBacktestRequest().symbol),'manually.entered');
  assert.deepEqual(errors,[]);await empty.page.close();
  pass('빈 창고에서도 직접 입력 유지·브라우저 오류 및 MT5 조회 없음');
 }catch(error){failure=error.message;throw error;}
 finally{
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({
   passed:checks.length,failed:failure?1:0,checks,failure,browser_errors:errors,
   environment:'Edge headless 1440px/420px; isolated local APIs'},null,2)+'\n');
 }
 console.log(`PASS ${checks.length} minimal symbol input UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
