/* No saved model: current settings screen remains usable without choosing a model automatically. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'../..'),evidence=path.join(root,'검증결과/AI기본모델제거59');
fs.mkdirSync(evidence,{recursive:true});
const results=[],requests=[];const pass=name=>results.push({name,passed:true});
let config={provider:'ollama',model:null,timeout:90,base_url:'http://127.0.0.1:11434'};
let models={available:true,models:['qwen3.5:4b','qwen3:8b']};
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8763/**',async route=>{
   const name=new URL(route.request().url()).pathname;
   if(!name.startsWith('/api/')) {
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   const data=route.request().postDataJSON();requests.push({name,data});let body={ok:true};
   if(name==='/api/init')body={connections:{warehouse:''},recent:[]};
   else if(name==='/api/mo/live/status')body={modules:{},lines:[]};
   else if(name==='/api/ai/settings')body={settings:{...config},base_url:config.base_url};
   else if(name==='/api/ai/models')body=models;
   else if(name==='/api/mo/settings'&&!data)body={live:[],part2:{},connections:{},ai:{...config}};
   else if(name==='/api/mo/settings') {
    const updated={...config,...data.changes};
    if(!updated.model?.trim())return route.fulfill({status:400,json:{error:'Ollama 모델명을 입력하세요.'}});
    Object.assign(config,updated);body={ok:true,message:'저장 완료 - 다음 AI 요청부터 적용'};
   } else if(name==='/api/ai/chat') {
    if(!config.model)return route.fulfill({status:400,json:{error:'AI 모델이 설정되지 않았습니다.'}});
    body={result:{supported:false,reason:'MOSES_SCOPE_ONLY',message_ko:'시험 응답'},can_apply:false,actions:[]};
   }
   return route.fulfill({json:body});
  });
  await page.goto('http://127.0.0.1:8763/#token=no-default-test');await page.waitForFunction(()=>!!state.meta);
  assert.equal(await page.locator('#ai-reset').innerText(),'초기화');
  await page.evaluate(()=>moView('settings'));await page.waitForSelector('#ai-model-select:not(:disabled)');
  assert.equal(await page.locator('#ai-model-select option:checked').innerText(),'모델을 선택하세요');
  assert.equal(await page.locator('#ai-model-select option:checked').evaluate(option=>option.disabled),true);
  assert.equal(await page.locator('[data-setting="model"]').inputValue(),'');
  assert.match(await page.locator('#ai-model-list-status').innerText(),/직접 수정하거나 화살표로 선택/);
  assert.equal(await page.locator('[data-setting="model"]').isVisible(),true);
  assert.equal(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).length,0);
  assert.equal(config.model,null);
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'unconfigured_model.png')});
  pass('No saved model shows an explicit placeholder and empty setting; installed models are never auto-selected or saved');

  await page.locator('.ai-model-controls button').click();
  await page.waitForSelector('#ai-model-select:not(:disabled)');
  assert.equal(await page.locator('[data-setting="model"]').inputValue(),'');
  assert.equal(config.model,null);
  pass('Refreshing installed models retains the unconfigured state');

  await page.locator('[data-setting="timeout"]').fill('120');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('모델명을 입력'));
  assert.equal(config.model,null);assert.equal(config.timeout,90);
  await page.locator('[data-setting="timeout"]').fill('90');
  pass('Saving timeout without first selecting a model does not create an implicit default');

  await page.evaluate(()=>moView('strategy'));
  await page.locator('#ai-text').fill('전략 해석');await page.locator('#ai-send').click();
  await page.waitForSelector('.ai-error');
  assert.equal(await page.locator('.ai-error').last().innerText(),'AI 모델이 설정되지 않았습니다.');
  assert.equal(await page.locator('#generate-button').isDisabled(),true);
  pass('Chat displays the missing-model error and stays unable to apply or generate');

  await page.evaluate(()=>moView('settings'));await page.waitForSelector('#ai-model-select:not(:disabled)');
  await page.locator('#ai-model-select').selectOption('qwen3.5:4b');
  assert.equal(config.model,null); // Selecting alone is not saving.
  const refreshed=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/ai/settings');
  await page.locator('[data-save-settings="ai"]').click();
  assert.equal((await (await refreshed).json()).settings.model,'qwen3.5:4b');
  await page.waitForFunction(()=>document.querySelector('[data-setting="model"]').dataset.original==='qwen3.5:4b');
  assert.deepEqual(requests.filter(x=>x.name==='/api/mo/settings'&&x.data).at(-1).data,{group:'ai',changes:{model:'qwen3.5:4b'}});
  assert.equal(config.model,'qwen3.5:4b');
  pass('Explicit selection and save store only the chosen model string and refresh AI settings');

  config.model=null;models={available:false,models:[],error:'Ollama 모델 목록을 불러오지 못했습니다. 모델명을 직접 입력하세요.'};
  await page.evaluate(()=>moSettingsLoad());
  assert.equal(await page.locator('[data-setting="model"]').isVisible(),true);
  assert.equal(await page.locator('[data-setting="model"]').inputValue(),'');
  await page.locator('[data-setting="model"]').fill('organization/custom:q4_K_M');
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="model"]').dataset.original==='organization/custom:q4_K_M');
  assert.equal(config.model,'organization/custom:q4_K_M');
  assert.deepEqual(errors,[]);
  pass('Ollama offline still permits manual model selection and explicit saving without a fallback model');
  await page.close();
 } finally {
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_tests.json'),JSON.stringify({tests:results.length,results,
   environment:'current Edge UI with mocked APIs; no production configuration edited'},null,2)+'\n');
 }
 console.log(`PASS ${results.length} no-default model UI tests`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
