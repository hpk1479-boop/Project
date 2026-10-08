/* Shipped settings assets, isolated API data; no engine or AI launch. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/자동설정78');
fs.mkdirSync(evidence,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true}),checks=[],errors=[],posts=[],calls=[];
 const pass=name=>checks.push({name,passed:true});let failure=null;
 const config={live:[],connections:{warehouse:'fixture',live_records:'fixture',python_executable:''},
  part2:{cores:null,work_size:'AUTO',overlap_trading_days:3,capture_start:'keyframe',oz_evaluation:'selected'},
  ai:{provider:'local_gguf',watch_enabled:true,model:'installed:model',base_url:'http://127.0.0.1:11434',timeout:90,
   gguf_model_path:'models/gguf/current.gguf',gguf_context_size:16384,gguf_gpu_layers:0,
   base_model:'saved/base',adapter_path:'models/saved_lora',load_in_4bit:false,
   gemini_model:'saved-gemini',gemini_api_key_configured:true}};
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8764/**',async route=>{
   const name=new URL(route.request().url()).pathname,body=route.request().postDataJSON();calls.push(name);
   if(!name.startsWith('/api/')){
    const file=name==='/'?'index.html':name.slice(1);
    return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),
     contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
   }
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:'fixture'},recent:[]}});
   if(name==='/api/mo/settings'&&!body)return route.fulfill({json:config});
   if(name==='/api/mo/settings'){
    posts.push(body);Object.assign(config[body.group],body.changes);return route.fulfill({json:{ok:true,message:'저장 완료'}});
   }
   if(name==='/api/ai/models')return route.fulfill({json:{available:true,models:['installed:model']}});
   if(name==='/api/ai/gguf-models')return route.fulfill({json:{directory:'models/gguf',models:[
    {name:'current.gguf',path:'models/gguf/current.gguf',bytes:1000}]}});
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8764/#token=mock');await page.waitForFunction(()=>!!state.meta);
  await page.evaluate(()=>moView('settings'));await page.waitForSelector('#ai-gguf-model-select');
  const back=page.locator('#settings-part2-details'),ai=page.locator('#ai-settings-details');
  const field=(group,key)=>page.locator('#settings-'+group+'-fields [data-setting="'+key+'"]');
  assert.equal(await back.evaluate(node=>node.open),false);assert.equal(await ai.evaluate(node=>node.open),false);
  assert.equal(await page.locator('#settings-ai-fields details').count(),1);
  assert.equal(await field('part2','cores').inputValue(),'');assert.equal(await field('part2','work_size').inputValue(),'AUTO');
  assert.deepEqual(await field('part2','work_size').locator('option').allTextContents(),['자동','한 달씩','2주씩','1주씩','하루씩']);
  assert.equal(await field('part2','cores').isVisible(),false);assert.equal(await field('ai','gguf_model_path').isVisible(),false);
  await page.screenshot({path:path.join(evidence,'desktop_collapsed.png'),fullPage:true});
  pass('백테스트 자동 기본값·두 항목 상세 이동·공통 AI 상세설정 하나·최초 접힘');
  await back.locator('summary').click();await field('part2','cores').fill('24');await field('part2','work_size').selectOption('WEEK');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-part2-details').open===false);
  assert.deepEqual(posts.at(-1),{group:'part2',changes:{cores:24,work_size:'WEEK'}});
  await back.locator('summary').click();await field('part2','cores').fill('');await field('part2','work_size').selectOption('AUTO');
  await page.locator('[data-save-settings="part2"]').click();
  await page.waitForFunction(()=>document.querySelector('#settings-part2-details').open===false);
  assert.deepEqual(posts.at(-1),{group:'part2',changes:{cores:null,work_size:'AUTO'}});
  pass('상세에서 수동 조정 저장·자동으로 복귀 저장·저장 후 접힘');
  await ai.locator('summary').click();await field('ai','gguf_gpu_layers').fill('-1');await field('ai','timeout').fill('120');
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>!document.querySelector('#ai-settings-details').open);
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{gguf_gpu_layers:-1,timeout:120}});
  for(const provider of ['ollama','gemini','local_gguf']){
   await field('ai','provider').selectOption(provider);assert.equal(await page.locator('#settings-ai-fields details').count(),1);
   assert.equal(await ai.evaluate(node=>node.open),false);
   assert.equal(await page.locator('#ai-gguf-advanced-group').evaluate(node=>node.classList.contains('hidden')),provider!=='local_gguf');
  }
  await ai.locator('summary').click();await page.locator('#ai-lora-enabled').check();
  assert.equal(await ai.evaluate(node=>node.open),true);assert.equal(await field('ai','provider').isDisabled(),true);
  assert.equal(await field('ai','adapter_path').isVisible(),true);
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>!document.querySelector('#ai-settings-details').open);
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{provider:'local_lora'}});
  assert.equal(config.ai.gguf_model_path,'models/gguf/current.gguf');assert.equal(await field('ai','gemini_api_key').inputValue(),'');
  pass('한 상세설정에서 실행 방식별 표시·GGUF 설정 저장·LoRA 전환·다른 모델과 비밀값 보존');
  await ai.locator('summary').click();await page.screenshot({path:path.join(evidence,'desktop_expanded.png'),fullPage:true});
  await page.setViewportSize({width:420,height:900});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(evidence,'mobile_expanded.png'),fullPage:true});
  await page.evaluate(()=>moCollapseSettingsDetails());
  await page.screenshot({path:path.join(evidence,'mobile_collapsed.png'),fullPage:true});
  pass('1440px/420px 기본·상세 화면 가로 넘침 없음');
  assert.deepEqual(errors,[]);assert(calls.every(name=>!name.includes('/chat')&&!name.includes('/start')));
  pass('브라우저 오류 0·실제 모델/엔진 요청 없음');await page.close();
 }catch(error){failure=error.message;throw error;}
 finally{
  await browser.close();fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({
   passed:checks.length,failed:failure?1:0,checks,failure,browser_errors:errors,
   environment:'Edge headless 1440px/420px; isolated APIs'},null,2)+'\n');
 }
 console.log(`PASS ${checks.length} settings UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
