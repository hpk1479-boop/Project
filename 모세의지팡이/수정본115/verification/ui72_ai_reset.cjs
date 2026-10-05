/* Edge UI -> authenticated local Part3 -> scripted model -> isolated files. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),readline=require('node:readline');
const {chromium}=require(process.argv[2]);
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'검증결과/AI대화초기화72');
fs.mkdirSync(evidence,{recursive:true});
const checks=[];const pass=name=>checks.push({name,passed:true});
(async()=>{
 const fixture=spawn(process.argv[3],['-B',path.join(root,'Part3/tests/test_ai_revision59.py'),'--ui-server'],{
  windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 const lines=[],pending=[];let stderr='';
 fixture.stderr.on('data',d=>stderr+=d.toString());
 const output=readline.createInterface({input:fixture.stdout});
 output.on('line',line=>pending.length?pending.shift()(JSON.parse(line)):lines.push(JSON.parse(line)));
 const next=()=>lines.length?Promise.resolve(lines.shift()):new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(new Error('fixture timeout '+stderr)),10000);
  pending.push(value=>{clearTimeout(timer);resolve(value);});});
 const status=async()=>{fixture.stdin.write('status\n');return next();};
 let browser;
 try{
  const {port,token}=await next(),origin=`http://127.0.0.1:${port}`;
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/api/mo/live/status**',route=>route.fulfill({json:{modules:{},lines:[]}}));
  await page.route(origin+'/api/mo/backtest/recent**',route=>route.fulfill({json:{items:[]}}));
  await page.goto(origin+'/#token='+token);await page.waitForFunction(()=>!!state.meta);await page.evaluate(()=>moView('strategy'));
  assert.equal(await page.locator('.ai-settings').getAttribute('open'),null);
  assert.equal(await page.locator('#ai-reset').isVisible(),true);
  let box=await page.locator('#ai-reset').boundingBox();assert(box.y>=0&&box.y+box.height<900);
  await page.locator('.ai-heading').screenshot({path:path.join(evidence,'desktop_top.png')});
  pass('대화 새로 시작 버튼이 접힌 연결 설정 밖 상단에 항상 표시됨');
  await page.setViewportSize({width:420,height:900});box=await page.locator('#ai-reset').boundingBox();
  assert(box.y>=0&&box.y+box.height<900);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('.ai-heading').screenshot({path:path.join(evidence,'mobile_top.png')});
  pass('좁은 420px 화면에서도 상단 버튼 표시 및 가로 넘침 없음');
  await page.setViewportSize({width:1440,height:900});
  const send=async text=>{await page.locator('#ai-text').fill(text);await page.locator('#ai-send').click();await page.waitForFunction(()=>!document.querySelector('#ai-send').disabled);};
  await send('15분 상승추세일 때 3분 하단 원비 터치하면 1분 브레이커');
  await send('아니 원비는 15분이야');assert.equal((await status()).revision,2);
  await page.locator('#ai-text').fill('아직 보내지 않은 수정 초안');
  await page.locator('#ai-reset').click();await page.waitForFunction(()=>!document.querySelector('#ai-reset').disabled);
  assert.equal(await page.locator('#ai-messages').innerText(),'');assert.equal(await page.locator('#ai-text').inputValue(),'');
  assert.equal(await page.locator('#ai-pending-text').innerText(),'');assert.equal(await page.locator('#ai-pending').isVisible(),false);
  assert.equal(await page.locator('#generate-button').isDisabled(),true);assert.equal(await page.evaluate(()=>state.recipe),null);
  assert.equal(await page.locator('#code-preview').innerText(),'');assert.equal(await page.locator('#ai-mode').innerText(),'새 전략 대화');
  assert.equal(await page.locator('#ai-text').evaluate(e=>e===document.activeElement),true);
  assert.equal((await status()).revision,null);
  pass('초기화가 대화·확인 요청·입력 초안·미리보기를 지우고 입력창으로 복귀함');
  await send('별도 신규 전략');const newChat=await status();assert.equal(newChat.revision,1);assert.equal(newChat.provider_context,null);
  assert.equal(newChat.intent.interpretation.steps[1].tfs[0],'3m');
  pass('초기화 후 신규 전략은 이전 intent 수정 문맥 없이 첫 해석으로 시작함');
  await page.locator('#ai-confirm').click();await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled&&!document.querySelector('#ai-send').disabled);
  await page.locator('#generate-button').click();await page.waitForFunction(()=>!state.busy&&!!state.lastGenerated);
  assert.equal((await status()).generated_count,1);
  await page.locator('#ai-reset').click();await page.waitForFunction(()=>!document.querySelector('#ai-reset').disabled);
  assert.equal((await status()).generated_count,1);assert.equal(await page.locator('#generate-button').isDisabled(),true);
  pass('생성 전략 파일은 보존되고 기존 해석 재적용만 차단됨');
  await page.evaluate(()=>window.mosesWindowClosing('백테스트를 중지하고 결과를 저장하는 중입니다.',false));
  assert.equal(await page.locator('#window-close-message').isVisible(),true);
  assert.match(await page.locator('#window-close-message').innerText(),/백테스트/);
  await page.locator('#window-close-message').screenshot({path:path.join(evidence,'close_notice.png')});
  pass('전략 연구 화면에서도 종료 저장 안내가 상단에 표시됨');
  assert.deepEqual(errors,[]);
 }finally{
  if(browser)await browser.close();fixture.stdin.write('stop\n');fixture.stdin.end();
  await new Promise(resolve=>fixture.once('exit',resolve));output.close();
  fs.writeFileSync(path.join(evidence,'ui_results.json'),JSON.stringify({tests:checks.length,checks,
   environment:'Edge headless -> real authenticated local HTTP -> scripted model -> temporary storage'},null,2)+'\n');
  if(stderr)console.error(stderr);
 }
 console.log(`PASS ${checks.length} AI reset UI checks`);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
