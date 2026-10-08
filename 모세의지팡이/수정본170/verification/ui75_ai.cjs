/* Shared AI UI check: isolated local API routes, no AI requests or real keys. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/AI설정_UI75');
fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const checks=[],pass=name=>checks.push({name,passed:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],posts=[];let ollamaLists=0;
  let ai={model:'saved-ollama-model',base_model:'saved/base',adapter_path:'models/saved_adapter',
   load_in_4bit:false,max_new_tokens:777,timeout:90,gguf_model_path:'models/gguf/sample-4b.gguf',
   gguf_context_size:16384,gguf_gpu_layers:0,
   gemini_model:'selected-gemini-model',gemini_api_key_configured:true};
  page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const name=new URL(route.request().url()).pathname,body=route.request().postDataJSON();
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:''},recent:[]}});
   if(name==='/api/mo/settings'&&!body)return route.fulfill({json:{live:[],part2:{},connections:{},ai:{...ai}}});
   if(name==='/api/mo/settings'){
    posts.push(body);const changes={...body.changes};
    if(Object.hasOwn(changes,'gemini_api_key')){
     ai.gemini_api_key_configured=Boolean(changes.gemini_api_key);delete changes.gemini_api_key;
    }
    Object.assign(ai,changes);return route.fulfill({json:{ok:true,message:'저장 완료'}});
   }
   if(name==='/api/ai/settings')return route.fulfill({json:{settings:{...ai},base_url:'local'}});
   if(name==='/api/ai/gguf-models')return route.fulfill({json:{directory:'models/gguf',models:[
    {name:'sample-4b.gguf',path:'models/gguf/sample-4b.gguf',bytes:1000},
    {name:'replacement model.gguf',path:'models/gguf/replacement model.gguf',bytes:2000}]}});
   if(name==='/api/ai/models'){ollamaLists++;return route.fulfill({json:{available:true,models:['saved-ollama-model']}});}
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('settings'));await page.waitForSelector('#ai-local-gguf-fields:not(.hidden)');
  const provider=page.locator('#ai-provider-select'),key=page.locator('[data-setting="gemini_api_key"]');
  assert.equal(await provider.inputValue(),'local_gguf');
  assert.equal(await provider.locator('option[value="local_gguf"]').textContent(),'로컬 GGUF');
  assert.equal(ollamaLists,0);assert.equal(await page.locator('[data-setting="watch_enabled"]').inputValue(),'true');
  assert.deepEqual((await page.locator('#ai-gguf-model-select option').allTextContents()).slice(0,2),[
   'sample-4b.gguf','replacement model.gguf']);
  assert.match(await page.locator('#ai-settings-state').textContent(),/로컬 GGUF.*sample-4b\.gguf/);
  assert.deepEqual(await provider.locator('option').evaluateAll(options=>options.map(x=>x.value)),['ollama','local_gguf','gemini']);
  assert.equal(await page.locator('#ai-settings-details').evaluate(x=>x.open),false);
  assert.equal(await page.locator('#ai-settings-details').evaluate(x=>x.open),false);
  assert.equal(await page.locator('[data-setting="llama_server_path"]').isVisible(),false);
  assert.equal(await page.locator('[data-setting="llama_server_path"]').getAttribute('required'),null);
  pass('기본 선택 세 가지, 로컬 GGUF 기본값, 파일명 표시, 실행기·LoRA 상세 설정 접힘');
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'desktop_gguf.png')});
  await page.locator('[data-setting="watch_enabled"]').selectOption('false');
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="watch_enabled"]').dataset.original==='false');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{watch_enabled:false}});
  pass('WATCH AI 보조 해석은 공통 설정에서 켜기·끄기를 bool로 저장');
  await provider.selectOption('gemini');await page.waitForSelector('#ai-gemini-fields:not(.hidden)');
  assert.equal(await key.getAttribute('type'),'password');assert.equal(await key.inputValue(),'');
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='gemini');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{provider:'gemini'}});
  assert.match(await page.locator('#ai-settings-state').textContent(),/Gemini.*selected-gemini-model.*공통 사용/);
  assert.equal(ai.gguf_model_path,'models/gguf/sample-4b.gguf');assert.equal(ai.model,'saved-ollama-model');
  assert.equal(ai.base_model,'saved/base');assert.equal(ai.gemini_api_key_configured,true);
  pass('Gemini 선택 저장 후 상태 반영, 비활성 GGUF·Ollama·LoRA 설정과 연결 키 유지');
  await page.locator('[data-setting="gemini_model"]').fill('');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('Gemini 모델명'));
  assert.equal(posts.length,2);await page.locator('[data-setting="gemini_model"]').fill('selected-gemini-model');
  await key.fill('synthetic-key-74');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="gemini_api_key"]').value==='');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{gemini_api_key:'synthetic-key-74'}});
  assert.doesNotMatch(await page.locator('#settings-ai-fields').textContent(),/synthetic-key-74/);
  pass('Gemini 필수 모델 검사, 새 키만 저장, 저장한 비밀키 화면 노출 차단');
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'desktop_gemini.png')});
  await provider.selectOption('ollama');await page.waitForSelector('#ai-model-select:not(:disabled)');
  assert.equal(ollamaLists,1);
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='ollama');
  assert.equal(ai.provider,'ollama');assert.equal(await provider.inputValue(),'ollama');assert.equal(ollamaLists,2);
  pass('명시적으로 저장한 Ollama 선택은 GGUF 기본값으로 덮어쓰지 않음');
  await provider.selectOption('local_gguf');await page.locator('#ai-gguf-model-select').selectOption('models/gguf/replacement model.gguf');
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-setting="gguf_model_path"]').dataset.original==='models/gguf/replacement model.gguf');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{provider:'local_gguf',gguf_model_path:'models/gguf/replacement model.gguf'}});
  pass('GGUF로 복귀해 새 파일명을 선택·저장하고 기존 Gemini 모델 설정 보존');
  await page.locator('#ai-settings-details summary').click();
  await page.locator('#ai-lora-enabled').check();
  assert.equal(await provider.isDisabled(),true);
  assert.equal(await page.locator('[data-setting="base_model"]').isVisible(),true);
  await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='local_lora');
  assert.equal(ai.provider,'local_lora');assert.deepEqual(posts.at(-1),{group:'ai',changes:{provider:'local_lora'}});
  assert.equal(await page.locator('#ai-settings-details').evaluate(x=>x.open),false);
  await page.locator('#ai-settings-details summary').click();
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'desktop_lora_details.png')});
  await page.locator('#ai-lora-enabled').uncheck();
  await provider.selectOption('local_gguf');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='local_gguf');
  assert.equal(ai.base_model,'saved/base');assert.equal(ai.adapter_path,'models/saved_adapter');
  pass('상세 설정에서 LoRA 선택·저장·재접힘, 사용 해제 후 GGUF 복귀와 기존 설정 유지');
  for(const selected of ['local_gguf','gemini']){
   await provider.selectOption(selected);await page.setViewportSize({width:420,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,`mobile_${selected}.png`)});
   await page.setViewportSize({width:1440,height:900});
  }
  await provider.selectOption('local_gguf');await page.setViewportSize({width:420,height:900});
  await page.locator('#ai-settings-details summary').click();
  assert.equal(await page.locator('[data-setting="llama_server_path"]').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'mobile_gguf_details.png')});
  await page.locator('#ai-settings-details summary').click();await page.locator('#ai-lora-enabled').check();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'mobile_lora_details.png')});
  pass('420px 좁은 화면에서 GGUF·Gemini·LoRA 및 펼친 상세 설정에 가로 넘침 없음');
  assert.deepEqual(errors,[]);
 }finally{
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({tests:checks.length,checks,
   environment:'Edge headless 1440px/420px; synthetic local routes; no external AI or real settings'},null,2)+'\n');
 }
 console.log(`PASS ${checks.length} shared AI settings UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
