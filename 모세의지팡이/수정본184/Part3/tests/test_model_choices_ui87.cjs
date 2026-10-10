/* Actual editable fields, arrows and save handlers. All APIs are synthetic. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'../..'),proof=path.join((process.env.MOSES_EVIDENCE_ROOT || (process.env.MOSES_EVIDENCE_ROOT || path.join(root, '검증결과'))), '모델선택87');
const html=fs.readFileSync(path.join(root,'Part3/web/index.html'),'utf8')
 .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<link\b[^>]*>/gi,'');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[];
 const pass=name=>checks.push(name);
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url()==='http://127.0.0.1:8763/'?
   route.fulfill({contentType:'text/html; charset=utf-8',body:html}):route.abort());
  await page.goto('http://127.0.0.1:8763/');
  await page.addStyleTag({content:fs.readFileSync(path.join(root,'Part3/web/style.css'),'utf8')});
  await page.evaluate(()=>{
   window.config={live:[],part2:{work_size:'AUTO',capture_start:'keyframe'},connections:{warehouse:'warehouse'},
    ai:{provider:'local_gguf',gemini_model:'gemini-current',gemini_api_key_configured:true,timeout:90,
     gguf_model_path:'models/gguf/current.gguf',gguf_context_size:16384,gguf_gpu_layers:-1}};
   window.calls=[];window.listOK=true;window.setInterval=()=>0;
   // Workspace recipe invalidation belongs to app.js, outside this settings fixture.
   window.part3InvalidateAIRecipe=()=>{};
   window.fetch=async()=>({ok:true,json:async()=>({settings:{...config.ai}})});
   window.api=async(name,body)=>{
    calls.push({name,body});
    if(name==='mo/settings'&&body){Object.assign(config[body.group],body.changes);return {ok:true,message:'저장 완료'};}
    if(name==='mo/settings')return JSON.parse(JSON.stringify(config));
    if(name==='ai/gemini-models')return listOK?{available:true,models:['gemini-current','gemini-future'],error:null}:
     {available:false,models:[],error:'목록 조회 실패. 직접 입력하세요.'};
    if(name==='ai/models')return {available:true,models:['installed:model']};
    if(name==='ai/gguf-models')return {directory:'models/gguf',models:[{name:'current.gguf',path:'models/gguf/current.gguf',bytes:100}]};
    if(name==='mo/backtest/options')return {symbol:'XAUUSD+',symbols:['EURUSD','USTEC','XAUUSD+'],start:'2026-01-01',end:'2026-02-01',
     mode:'BAR',specials:[],special_settings:{},warehouse_set:true};
    return {modules:{},lines:[]};
   };
  });
  for(const file of ['ai_chat.js','unified.js'])await page.addScriptTag({content:fs.readFileSync(path.join(root,'Part3/web',file),'utf8')});
  await page.evaluate(async()=>{document.dispatchEvent(new Event('DOMContentLoaded'));await moView('settings');});
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.name==='ai/gemini-models').length),0);
  pass('다른 provider 선택 시 Gemini 목록 조회 없음');
  await page.locator('#ai-provider-select').selectOption('gemini');
  await page.waitForFunction(()=>document.querySelector('#ai-gemini-model-select')?.disabled===false);
  const model=page.locator('[data-setting="gemini_model"]'),select=page.locator('#ai-gemini-model-select');
  assert.deepEqual(await select.locator('option').evaluateAll(nodes=>nodes.map(n=>n.value)),['gemini-current','gemini-future']);
  assert.equal(await model.isEditable(),true);
  await model.click({position:{x:40,y:18}});await model.fill('manual-future');
  assert.equal(await model.inputValue(),'manual-future');
  assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('[data-setting="gemini_model"]')),true);
  await select.selectOption('gemini-future');assert.equal(await model.inputValue(),'gemini-future');
  pass('한 입력칸: 문자 클릭 직접 수정·화살표 목록 선택·빈 옵션 없음');
  const bounds=await page.evaluate(()=>{
   const input=document.querySelector('[data-setting="gemini_model"]'),select=document.querySelector('#ai-gemini-model-select');
   const r=input.getBoundingClientRect(),s=select.getBoundingClientRect(),arrow=select.nextElementSibling.getBoundingClientRect();
   return {textHit:document.elementFromPoint(r.x+40,r.y+r.height/2)===input,
    arrowHit:document.elementFromPoint(s.x+s.width/2,s.y+s.height/2)===select,
    centered:Math.abs((r.y+r.height/2)-(arrow.y+arrow.height/2))<1};
  });
  assert.deepEqual(bounds,{textHit:true,arrowHit:true,centered:true});pass('텍스트·화살표 클릭 영역과 수직 중앙 정렬');
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>config.ai.provider==='gemini'&&config.ai.gemini_model==='gemini-future');
  assert.equal(await model.inputValue(),'gemini-future');pass('선택 모델 저장·설정 재조회 유지');
  const key=page.locator('[data-setting="gemini_api_key"]');
  await key.fill('AQ.synthetic.preview');await key.dispatchEvent('change');
  await page.waitForFunction(()=>calls.some(c=>c.name==='ai/gemini-models'&&c.body?.gemini_api_key==='AQ.synthetic.preview'));
  assert.equal(await page.locator('#ai-gemini-list-status').textContent().then(text=>text.includes('AQ.synthetic.preview')),false);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.name==='mo/settings'&&c.body).length),1);
  pass('저장 전 새 키로 목록 조회·키 표시와 자동 저장 없음');
  await page.evaluate(()=>{listOK=false;});await key.fill('AQ.synthetic.next');await key.dispatchEvent('change');
  await page.waitForFunction(()=>document.querySelector('#ai-gemini-model-select').disabled);
  await page.waitForFunction(()=>document.querySelector('#ai-gemini-list-status').textContent.includes('조회 실패'));
  await model.fill('gemini-manual');assert.equal(await model.isEditable(),true);
  assert.equal(await model.inputValue(),'gemini-manual');pass('목록 조회 실패 시 직접 입력 유지');
  await page.evaluate(()=>{config.ai.gemini_api_key_configured=false;listOK=true;});
  await page.evaluate(()=>moSettingsLoad());
  assert.equal(await select.isDisabled(),true);assert.equal(await model.isEditable(),true);
  pass('등록 키가 없어도 직접 입력 가능');
  for(const provider of ['ollama','local_gguf','gemini']){
   await page.locator('#ai-provider-select').selectOption(provider);
   await page.waitForTimeout(40);
   assert.equal(await page.locator('#settings-ai-fields select').evaluateAll(nodes=>nodes.every(node=>
    node.classList.contains('moses-choice-select')||node.parentElement.classList.contains('moses-select-field'))),true);
  }
  pass('설정 드롭다운은 같은 ▼ 아이콘 사용');
  await page.setViewportSize({width:375,height:812});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  fs.mkdirSync(proof,{recursive:true});await page.screenshot({path:path.join(proof,'gemini_mobile.png'),fullPage:true});
  pass('좁은 화면 가로 넘침 없음');
  await page.setViewportSize({width:1280,height:900});
  await page.evaluate(()=>moView('backtest'));
  assert.deepEqual(await page.locator('#mo-bt-symbol-select option').evaluateAll(nodes=>nodes.map(n=>n.value)),['EURUSD','USTEC','XAUUSD+']);
  assert.equal(await page.locator('#mo-bt-symbol').inputValue(),'XAUUSD+');
  await page.locator('#mo-bt-symbol-select').selectOption('EURUSD');assert.equal(await page.locator('#mo-bt-symbol').inputValue(),'EURUSD');
  await page.locator('#mo-bt-symbol').fill('MANUAL.symbol');await page.evaluate(()=>moBacktestOptions());
  assert.equal(await page.locator('#mo-bt-symbol').inputValue(),'MANUAL.symbol');
  pass('백테스트 빈 슬롯 제거·첫 종목 선택·직접 수정·새로고침 보존');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(proof,'ui_checks.json'),JSON.stringify({checks,errors},null,2));
  console.log('PASS '+checks.length+' UI scenarios');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
