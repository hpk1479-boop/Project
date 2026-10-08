/* Current presentation in isolated Edge. Canonical JSON stays in details. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'../..'),evidence=path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '공통전략언어60');
fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const results=[];
 try {
  const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<main id="example"></main>');
  await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web/style.css'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web/ai_display.js'),'utf8')});
  const cross={kind:'MA_CROSS',tfs:['1m'],ma_left:'EMA50',ma_right:'EMA200',direction:'LONG'};
  const examples=[
   {name:'unlimited_fvg',direction:'LONG',steps:[cross,{kind:'FVG_NEW',tfs:['5m'],direction:'LONG',side:'BULL'}],
    order_mode:'SEQUENTIAL',final_window_sec:3600,final_after:'FVG_NEW',
    cancel_conditions:[{kind:'FVG_NEW',tfs:['5m'],direction:'SHORT',side:'BEAR'}],
    final:{kind:'OZ',tfs:['1m','2m','3m'],validation_mode:'NORMAL',trigger_mode:'OZ'},
    expected:['대기 시간 제한 없음','최종 감시 유효시간 60분','5분봉 상승 FVG 신규 발생','감시 취소 조건']},
   {name:'daily_price_cross',direction:'BOTH',steps:[{kind:'MA_PRICE_CROSS',tfs:['1d'],ma_family:'SMA',slow_period:20,relation:'BOTH'}],
    order_mode:'SIMULTANEOUS',final:{kind:'NOTIFY'},expected:['1일봉 가격이 SMA20 상향 또는 하향 교차','확정봉 기준']},
   {name:'cross_then_touch',direction:'LONG',steps:[cross,{kind:'MA_PRICE_TOUCH',tfs:['1m'],ma_family:'EMA',slow_period:200}],
    order_mode:'SEQUENTIAL',within_sec:null,final:{kind:'NOTIFY'},expected:['1분봉 가격이 EMA200 터치','대기 시간 제한 없음','확정봉 기준']},
   {name:'bounded_variable_period',direction:'SHORT',steps:[{kind:'MA_PRICE_CROSS',tfs:['15m'],ma_family:'WMA',slow_period:43,relation:'BREAK_DOWN',bar_state:'FORMING'},
     {kind:'MA_PRICE_TOUCH',tfs:['3m'],ma_family:'HMA',slow_period:71}],order_mode:'SEQUENTIAL',within_sec:900,
    final:{kind:'NOTIFY'},expected:['WMA43 하향 돌파','HMA71 터치','조건 사건 사이 15분 이내','진행봉 기준']}
  ];
  for(const fixture of examples){
   const {name,expected,...item}=fixture;
   item.symbols=[name==='daily_price_cross'?'NAS100':'XAUUSD+'];
   const canonical=JSON.stringify(item);
   await page.evaluate(value=>{
    const before=JSON.stringify(value);
    document.querySelector('#example').replaceChildren(window.part3IntentDisplay.render({result:{supported:true,interpretation:value}}));
    if(JSON.stringify(value)!==before)throw Error('Presentation mutated the intent');
   },item);
   const visible=await page.locator('#example').innerText();
   for(const text of expected)assert(visible.includes(text),text+' missing in '+visible);
   assert(!visible.includes('MA_PRICE_CROSS')&&!visible.includes('MA_PRICE_TOUCH')&&!visible.includes('SEQUENTIAL'));
   assert.equal(await page.locator('details').evaluate(e=>e.open),false);
   await page.screenshot({path:path.join(evidence,name+'.png'),fullPage:true});
   await page.locator('summary').click();
   const detail=await page.locator('details pre').textContent();
   assert.deepEqual(JSON.parse(detail).result.interpretation,JSON.parse(canonical));
   results.push({name,passed:true});
  }
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
 fs.writeFileSync(path.join(evidence,'ui_tests.json'),JSON.stringify({tests:results.length,results},null,2));
 console.log(`PASS ${results.length} common Recipe UI tests`);
})().catch(e=>{console.error(e);process.exitCode=1});
