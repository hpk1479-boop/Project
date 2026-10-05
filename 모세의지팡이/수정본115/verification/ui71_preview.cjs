const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.argv[2]);
const root=process.cwd();
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:980,height:800}});const errors=[],posts=[];let lists=0;
  let ai={provider:'local_lora',model:'previous-ollama-model',base_model:'sample/base',adapter_path:'models/sample_adapter',load_in_4bit:true,max_new_tokens:777,timeout:90};
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.abort());
  await page.route('http://127.0.0.1:8763/**',async route=>{
   const name=new URL(route.request().url()).pathname, body=route.request().postDataJSON();
   if(!name.startsWith('/api/')){const file=name==='/'?'index.html':name.slice(1);return route.fulfill({body:fs.readFileSync(path.join(root,'Part3/web',file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});}
   if(name==='/api/init')return route.fulfill({json:{connections:{warehouse:''},recent:[]}});
   if(name==='/api/mo/settings'&&!body)return route.fulfill({json:{live:[],part2:{},connections:{},ai:{...ai}}});
   if(name==='/api/mo/settings'){posts.push(body);Object.assign(ai,body.changes);return route.fulfill({json:{ok:true,message:'저장 완료'}});}
   if(name==='/api/ai/settings')return route.fulfill({json:{settings:{...ai},base_url:'local'}});
   if(name==='/api/ai/models'){lists++;return route.fulfill({json:{available:true,models:['previous-ollama-model']}});}
   return route.fulfill({json:{ok:true,modules:{},lines:[]}});
  });
  await page.goto('http://127.0.0.1:8763/#token=mock');await page.waitForFunction(()=>!!state.meta);await page.evaluate(()=>moView('settings'));
  await page.waitForSelector('#ai-local-lora-fields:not(.hidden)');assert.equal(lists,0);assert.equal(await page.locator('#ai-ollama-fields').isVisible(),false);assert.match(await page.locator('#ai-settings-state').textContent(),/로컬 LoRA.*sample\/base/);
  await page.locator('[data-setting="max_new_tokens"]').fill('');await page.locator('[data-setting="load_in_4bit"]').selectOption('false');await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('[data-setting="load_in_4bit"]').dataset.original==='false');
  assert.deepEqual(posts.at(-1),{group:'ai',changes:{load_in_4bit:false,max_new_tokens:null}});assert.equal(ai.model,'previous-ollama-model');
  await page.locator('[data-setting="adapter_path"]').fill('../outside');await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('#settings-message').textContent.includes('상대 위치'));assert.equal(posts.length,1);
  await page.locator('[data-setting="adapter_path"]').fill('models/sample_adapter');await page.locator('#ai-provider-select').selectOption('ollama');await page.waitForSelector('#ai-model-select:not(:disabled)');assert.equal(await page.locator('#ai-local-lora-fields').isVisible(),false);assert.equal(lists,1);
  await page.locator('[data-save-settings="ai"]').click();await page.waitForFunction(()=>document.querySelector('#ai-provider-select').dataset.original==='ollama');assert.equal(ai.base_model,'sample/base');assert.match(await page.locator('#ai-settings-state').textContent(),/Ollama.*previous-ollama-model/);
  await page.locator('#ai-provider-select').selectOption('local_lora');assert.equal(await page.locator('[data-setting="base_model"]').inputValue(),'sample/base');
  await page.setViewportSize({width:420,height:850});await page.locator('#settings-ai-fields').locator('..').screenshot({path:path.join(root,'검증결과/local_lora_ui71_mobile.png')});
  assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  console.log('PASS local provider display, inactive values, typed save, path guard, provider switch, mobile layout');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});

