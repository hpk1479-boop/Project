/* Optional Edge check. Network, model execution, and real settings stay isolated. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/GGUF_UI73');
fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const checks=[],pass=name=>checks.push({name,passed:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],posts=[];let lists=0,releaseChat;
  let ai={provider:'local_gguf',model:'previous-ollama-model',base_model:'sample/base',adapter_path:'models/sample_adapter',
   load_in_4bit:false,max_new_tokens:777,timeout:90,gguf_model_path:'models/sample.gguf',
   llama_server_path:'runtimes/llama/llama-server.exe',gguf_context_size:16384,gguf_gpu_layers:0};
  page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8763/**',async route=>{
   const name=new URL(route.request().url()).pathname,body=route.request().postDataJSON();
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:''},recent:[]}});
   if(name==='/api/mo/settings'&&!body)return route.fulfill({json:{live:[],part2:{},connections:{},ai:{...ai}}});
   if(name==='/api/mo/settings'){posts.push(body);Object.assign(ai,body.changes);return route.fulfill({json:{ok:true,message:'저장 완료'}});}
   if(name==='/api/ai/settings')return route.fulfill({json:{settings:{...ai},base_url:'local'}});
   if(name==='/api/ai/gguf-models')return route.fulfill({json:{directory:'models/gguf',selected:ai.gguf_model_path,models:[
    {name:'MOSES_Qwen3.5_4B_Q4_K_M.gguf',path:'models/sample.gguf',bytes:2400000000},
    {name:'MOSES_Qwen3.5_9B_Q4_K_M.gguf',path:'models/sample-9b.gguf',bytes:5600000000},
    {name:'replacement model.gguf',path:'models/gguf/replacement model.gguf',bytes:8000000000}]}});
   if(name==='/api/ai/models'){lists++;return route.fulfill({json:{available:true,models:['previous-ollama-model']}});}
   if(name==='/api/ai/chat')return new Promise(resolve=>{releaseChat=()=>route.fulfill({status:400,json:{error:'GGUF 모델 파일이 없습니다. 설정에서 파일 위치를 확인하세요.'}}).then(resolve);});
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8763/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('settings'));await page.waitForSelector('#ai-local-gguf-fields:not(.hidden)');
  assert.equal(lists,0);assert.equal(await page.locator('#ai-settings-details').getAttribute('open'),null);
  assert.equal(await page.locator('#ai-gguf-model-select').isVisible(),true);
  assert.equal(await page.locator('[data-setting="gguf_model_path"]').isVisible(),false);
  assert.deepEqual((await page.locator('#ai-gguf-model-select option').allTextContents()).slice(0,3),[
   'MOSES_Qwen3.5_4B_Q4_K_M.gguf','MOSES_Qwen3.5_9B_Q4_K_M.gguf','replacement model.gguf']);
  assert.doesNotMatch(await page.locator('#ai-gguf-model-select').textContent(),/저사양용|고사양용|모델 규모 확인 필요/);
  assert.equal(await page.locator('[data-setting="llama_server_path"]').isVisible(),true);
  assert.equal(await page.locator('[data-setting="gguf_context_size"]').isVisible(),false);
  assert.match(await page.locator('#ai-settings-state').textContent(),/로컬 GGUF.*sample\.gguf/);
  pass('GGUF 폴더에서 읽은 파일명만 드롭다운에 표시, 이름 분류나 별도 사양 문구 없음');
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'desktop_settings.png')});
  await page.locator('#ai-gguf-model-select').selectOption('models/sample-9b.gguf');assert.equal(posts.length,0);
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('[data-setting="gguf_model_path"]').dataset.original==='models/sample-9b.gguf');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{gguf_model_path:'models/sample-9b.gguf'}});
  pass('파일명으로 선택한 모델은 저장 후 선택 경로 유지');
  await page.locator('#ai-settings-details summary').click();await page.locator('[data-setting="gguf_gpu_layers"]').fill('-1');
  await page.locator('[data-setting="gguf_threads"]').fill('8');await page.locator('[data-setting="max_new_tokens"]').fill('900');
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('[data-setting="gguf_gpu_layers"]').dataset.original==='-1');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{gguf_gpu_layers:-1,gguf_threads:8,max_new_tokens:900}});
  assert.equal(ai.model,'previous-ollama-model');assert.equal(ai.base_model,'sample/base');assert.equal(await page.locator('#ai-settings-details').getAttribute('open'),null);
  pass('상세 숫자 저장·재조회 후 기존 Ollama와 LoRA 설정 보존');
  await page.locator('[data-setting="llama_server_path"]').fill('../outside.exe');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('상대 위치'));assert.equal(posts.length,2);
  await page.locator('[data-setting="llama_server_path"]').fill('runtimes/llama/llama-server.exe');
  pass('프로젝트 밖 경로 입력은 저장 전에 차단');
  await page.locator('#ai-provider-select').selectOption('local_lora');assert.equal(await page.locator('#ai-local-lora-fields').isVisible(),true);
  assert.equal(await page.locator('[data-setting="max_new_tokens"]').isVisible(),true);
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='local_lora');
  assert.equal(ai.gguf_model_path,'models/sample-9b.gguf');assert.match(await page.locator('#ai-settings-state').textContent(),/로컬 LoRA.*sample\/base/);
  await page.locator('#ai-provider-select').selectOption('ollama');await page.waitForSelector('#ai-model-select:not(:disabled)');assert.equal(lists,1);
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='ollama');
  await page.locator('#ai-provider-select').selectOption('local_gguf');await page.locator('[data-save-settings="ai"]').click();
  await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='local_gguf');
  pass('Ollama·LoRA·GGUF 전환과 비활성 모델 값 유지');
  await page.setViewportSize({width:420,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(evidence,'mobile_settings.png')});
  pass('420px 좁은 화면에서 가로 넘침 없이 설정 표시');
  await page.evaluate(()=>moView('strategy'));await page.locator('#ai-text').fill('15분 상승추세에서 하단 원비 터치하면 매수');await page.locator('#ai-send').click();
  await page.waitForFunction(()=>document.querySelector('#ai-messages').textContent.includes('GGUF 응답을 기다리는 중'));
  assert.equal(await page.locator('#ai-send').isDisabled(),true);assert(releaseChat);await releaseChat();
  await page.waitForFunction(()=>!document.querySelector('#ai-send').disabled);
  assert.match(await page.locator('#ai-messages').textContent(),/GGUF 모델 파일이 없습니다/);
  assert.doesNotMatch(await page.locator('#ai-messages').textContent(),/GGUF 응답을 기다리는 중/);
  assert.equal(await page.locator('#ai-reset').isDisabled(),false);
  await page.locator('#ai-messages').screenshot({path:path.join(evidence,'model_error.png')});
  pass('GGUF 첫 응답 대기 안내 및 로딩 오류 후 입력·초기화 버튼 복귀');
  assert.deepEqual(errors,[]);
 }finally{
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({tests:checks.length,checks,
   environment:'Edge headless 1440px/420px; local routes and API mocked; no model execution'},null,2)+'\n');
 }
 console.log(`PASS ${checks.length} GGUF settings UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
